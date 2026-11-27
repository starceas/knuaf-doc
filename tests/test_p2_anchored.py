"""POSIX anchored recovery: real worker, deterministic pipe rendezvous."""
import errno
import inspect
import json
import os
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from tests._harness import SCRIPTS, runtime

gg_fs = runtime("gg_fs")
gg_lock = runtime("gg_lock")
CHILD = SCRIPTS / "gg_lock_child.py"
PYTHON = sys.executable
REAL_POPEN = subprocess.Popen
STAGES = ("anchored", "after_active", "after_00-prepared", "after_quarantine",
          "after_10-quarantined", "after_install", "after_20-installed", "after_30-completed")

# Run the shipped entry point with instrumentation installed INSIDE its
# interpreter. Only the extra test socket communicates the pause/audit data.
WORKER = r"""
import functools
import json
import os
import socket
import sys
from pathlib import Path

scripts, channel_fd, stage, audit, relative_path = sys.argv[1:-2]
sys.path.insert(0, scripts)
import gg_fs
import gg_lock
import gg_lock_child

channel = socket.socket(fileno=int(channel_fd)) if channel_fd else None
armed = False
violations = []
seen = set()
chdir_events = []  # every os.chdir/os.fchdir, armed or not (proves the hook is live)
getcwd_counts = [0, 0]  # inside inventory, outside inventory
inventory_code = gg_lock._product_inventory.__code__

def check(name, *paths):
    if not armed:
        return
    seen.add(name)
    for path in paths:
        if path is None or isinstance(path, int):
            continue
        p = Path(os.fsdecode(path))
        if p.is_absolute() or '..' in p.parts:
            violations.append([name, str(p)])

def hook(event, args):
    if event == 'os.rename':
        check(event, *args[:2])
    elif event in ('open', 'os.mkdir', 'os.remove', 'os.rmdir', 'os.listdir', 'os.scandir'):
        check(event, args[0])

def wrap(module, name, count=1):
    original = getattr(module, name)
    @functools.wraps(original)
    def observed(*args, **kw):
        keys = ('src', 'dst') if count == 2 else ('path',)
        check(name, *args[:count], *(kw.get(key) for key in keys[len(args):]))
        return original(*args, **kw)
    setattr(module, name, observed)

def wrap_chdir(name):
    # A change of working base is recorded whether or not the audit is
    # armed; the flag says which. os.fchdir takes an fd.
    original = getattr(os, name)
    @functools.wraps(original)
    def observed(*args, **kw):
        chdir_events.append((name, armed))
        if armed:
            violations.append([name, str(args[0])])
        return original(*args, **kw)
    setattr(os, name, observed)

if audit == 'audit':
    sys.addaudithook(hook)
    for name in ('stat', 'lstat', 'unlink'):
        wrap(os, name)
    wrap(os.path, 'lexists')
    wrap(gg_fs, '_os_rename_noreplace', 2)
    wrap_chdir('chdir')
    wrap_chdir('fchdir')
    original_getcwd = os.getcwd
    def getcwd():
        if armed:
            frame = sys._getframe(1)
            while frame is not None and frame.f_code is not inventory_code:
                frame = frame.f_back
            getcwd_counts[frame is None] += 1
            if frame is None:
                violations.append(['getcwd', 'outside _product_inventory'])
        return original_getcwd()
    os.getcwd = getcwd

if relative_path == 'relative':
    sys.path.append('.')

def fault(name):
    global armed
    if name == 'anchored':
        armed = audit == 'audit'
        if audit == 'noise':
            print('worker stdout', flush=True)
            print('worker stderr', file=sys.stderr, flush=True)
    if name == stage:
        channel.sendall(b'!')
        if channel.recv(1) != b'+':
            raise RuntimeError('test did not release worker')

sys.argv[1:] = sys.argv[-2:]
gg_lock._FAULT = fault
code = gg_lock_child.main()
if audit == 'audit':
    channel.sendall(json.dumps([sorted(seen), violations, getcwd_counts, chdir_events]).encode('ascii'))
if channel is not None:
    channel.close()
sys.exit(code)
"""


def identity(root):
    return gg_fs.identity(root, kind="directory")


class _Clock:
    """Virtual monotonic clock: the test decides how much time has passed."""

    def __init__(self, now=0.0):
        self.now = now

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class _StuckChild:
    """Popen stand-in for a worker that never greets and ignores SIGKILL.

    A real process stuck in uninterruptible I/O cannot be simulated portably,
    so the wait is faked: every ``wait`` raises ``TimeoutExpired`` no matter
    the timeout. The parent must still return, and must pass a deadline. The
    stub also keeps the result pipe open (as a wedged worker would), so the
    parent reaches its greeting deadline instead of seeing EOF."""

    def __init__(self, pass_fds=()):
        self.stdin = os.fdopen(os.open(os.devnull, os.O_WRONLY), "wb")
        self.held = os.dup(pass_fds[-1]) if pass_fds else None
        self.returncode = None
        self.killed = False
        self.waits = []

    def poll(self):
        return None

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        self.waits.append(timeout)
        raise subprocess.TimeoutExpired("stub", timeout)

    def close(self):
        if self.held is not None:
            os.close(self.held)
            self.held = None
        self.stdin.close()


class _EofChild:
    """Popen stand-in for a worker that greets, then never exits.

    The result pipe reports EOF at the very edge of the total (the test
    advances the virtual clock), but the process stays alive. Every ``wait``
    raises ``TimeoutExpired``, so both the wait for the exit and the reap
    after kill expire. The parent must not spend two fixed bounds on them."""

    def __init__(self, clock=None):
        self.stdin = os.fdopen(os.open(os.devnull, os.O_WRONLY), "wb")
        self.clock = clock
        self.returncode = None
        self.killed = False
        self.waits = []

    def poll(self):
        return None

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self.clock is not None:
            self.clock.now += timeout
        raise subprocess.TimeoutExpired("stub", timeout)

    def close(self):
        self.stdin.close()


def snapshot(root):
    return {str(p.relative_to(root)): None if p.is_dir() else p.read_bytes()
            for p in root.rglob("*")}


@unittest.skipUnless(sys.platform == "darwin" or sys.platform.startswith("linux"), "POSIX worker")
class AnchoredCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="gg-anchor-")
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.worker = self.base / "worker.py"
        self.worker.write_text(WORKER, encoding="utf-8")

    def workspace(self, name="approved"):
        root = self.base / name
        root.mkdir()
        (root / "project.json").write_bytes(b'{"schema_version":2,"revision":0}')
        # A legacy lock makes the directory quarantine operation run, too.
        lock = root / gg_lock.LOCK_DIR
        lock.mkdir()
        (lock / "legacy.txt").write_bytes(b"old lock")
        return root

    def upgrade(self, root, expected, resume_id=None, *, confirmed=True):
        return gg_lock._upgrade_offline_anchored(
            root, offline_confirmed=confirmed, expected_identity=expected, resume_id=resume_id)

    def assert_blocked(self, result, reason, state="not_committed"):
        self.assertEqual((result["status"], result["reason"], result["commit_state"]),
                         ("blocked", reason, state))

    def assert_complete(self, root, expected, result):
        self.assertEqual((result["status"], result["commit_state"]), ("completed", "committed"), result)
        rid = result["recovery_id"]
        rdir = root / gg_lock.RECOVERY_DIR / rid
        claim = (rdir / "claim.json").read_bytes()
        self.assertEqual(json.loads(claim)["root_identity"], expected)
        self.assertEqual((rdir / "original-lock" / "legacy.txt").read_bytes(), b"old lock")
        previous = None
        for stage in gg_lock.STAGES:
            data = (rdir / (stage + ".json")).read_bytes()
            receipt = json.loads(data)
            self.assertEqual((receipt["recovery_id"], receipt["stage"], receipt["previous_sha256"]),
                             (rid, stage, previous))
            previous = gg_fs.sha256_bytes(data)
        self.assertEqual(result["receipt_sha256"], previous)
        self.assertFalse((root / gg_lock.ACTIVE_NAME).exists())
        status = gg_lock.inspect_lock(root)
        self.assertEqual(status["structure"], "ready", status)
        self.assertEqual(status["protocol"]["recovery_id"], rid)

    def instrumented(self, root, expected, *, stage="", resume_id=None, action=None,
                     mode="", relative_path=""):
        """Launch a real worker; at a named hook, release, kill, or leave it waiting."""
        children = []
        observer, inherited = socket.socketpair()
        with observer, inherited:
            observer.settimeout(12)
            argv = ("-I", "-X", "utf8", str(self.worker), str(SCRIPTS),
                    str(inherited.fileno()), stage, mode, relative_path)

            def spawn(args, *, pass_fds, **kwargs):
                child = REAL_POPEN(args, pass_fds=(*pass_fds, inherited.fileno()), **kwargs)
                children.append(child)
                inherited.close()
                return child

            with patch.object(gg_lock, "_ANCHOR_CHILD_ARGV", argv), \
                    patch.object(gg_lock.subprocess, "Popen", spawn), ThreadPoolExecutor(1) as pool:
                outcome = pool.submit(self.upgrade, root, expected, resume_id)
                try:
                    if stage:
                        self.assertEqual(observer.recv(1), b"!", "worker did not reach " + stage)
                        if action == "kill":
                            children[0].kill()
                        elif action != "timeout":
                            if action is not None and action() is False:
                                children[0].kill()
                            else:
                                observer.sendall(b"+")
                    result = outcome.result(timeout=12)
                finally:
                    if not outcome.done():
                        observer.shutdown(socket.SHUT_RDWR)
                        if children and children[0].poll() is None:
                            children[0].kill()
            self.assertIsNotNone(children[0].returncode)
            if mode == "audit":
                with observer.makefile("rb") as trace:
                    return result, json.load(trace)
            return result, None

    def interrupted(self, root, expected, stage="after_active"):
        result, _ = self.instrumented(root, expected, stage=stage, action="kill")
        self.assert_blocked(result, "anchored_worker_failed", "indeterminate")
        return json.loads((root / gg_lock.ACTIVE_NAME).read_bytes())["recovery_id"]

    def swap(self, root, bait):
        moved = root.with_name(root.name + "-moved")
        root.rename(moved)
        bait.rename(root)
        return moved

    def assert_moved(self, result):
        self.assertEqual(result["anchor_notes"], ["target_path_moved"])
        self.assertIsNone(result["receipt_path"])
        self.assertEqual(result["preserved_paths"], [])
        self.assertTrue(all(item["path"] is None for item in result["cleanup_errors"]))

    def test_refuses_replacement_before_fd_open(self):  # T1
        root, bait = self.workspace(), self.workspace("bait")
        expected = identity(root)
        moved = self.swap(root, bait)
        before = snapshot(root), snapshot(moved)
        self.assert_blocked(self.upgrade(root, expected), "target_changed")
        self.assertEqual((snapshot(root), snapshot(moved)), before)

    def test_folder_swap_before_spawn_uses_open_fd(self):  # T2
        root, bait = self.workspace(), self.workspace("bait")
        expected, before = identity(root), snapshot(bait)
        def spawn(*args, **kwargs):
            self.swap(root, bait)
            return REAL_POPEN(*args, **kwargs)
        with patch.object(gg_lock.subprocess, "Popen", spawn):
            result = self.upgrade(root, expected)
        self.assertEqual(snapshot(root), before)
        self.assert_complete(root.with_name("approved-moved"), expected, result)
        self.assert_moved(result)

    def test_bait_unchanged_when_swapped_at_recovery_hooks(self):  # T3/T13
        for resume in (False, True):
            for stage in STAGES:
                if resume and stage == "after_active":
                    continue  # Covered separately: resume never republishes active.
                with self.subTest(resume=resume, stage=stage):
                    name = f"{resume}-{stage}"
                    root, bait = self.workspace(name), self.workspace(name + "-bait")
                    expected, before = identity(root), snapshot(bait)
                    rid = self.interrupted(root, expected) if resume else None
                    result, _ = self.instrumented(
                        root, expected, stage=stage, resume_id=rid, action=lambda: self.swap(root, bait))
                    self.assertEqual(snapshot(root), before)
                    self.assert_complete(root.with_name(name + "-moved"), expected, result)
                    self.assert_moved(result)

    def test_resume_after_swap_at_active_publication(self):  # T3/T4
        root, bait = self.workspace(), self.workspace("bait")
        expected, before = identity(root), snapshot(bait)
        moved = root.with_name("approved-moved")

        def swap_and_interrupt():
            self.swap(root, bait)
            # The harness kills the worker instead of releasing the hook.
            return False

        result, _ = self.instrumented(root, expected, stage="after_active", action=swap_and_interrupt)
        self.assert_blocked(result, "anchored_worker_failed", "indeterminate")
        rid = json.loads((moved / gg_lock.ACTIVE_NAME).read_bytes())["recovery_id"]
        self.assert_complete(moved, expected, self.upgrade(moved, expected, rid))
        self.assertEqual(snapshot(root), before)

    def test_recovers_after_worker_death_at_each_hook(self):  # T4/T13
        for stage in STAGES:
            with self.subTest(stage=stage):
                root = self.workspace(stage)
                expected = identity(root)
                if stage == "anchored":
                    before = snapshot(root)
                    result, _ = self.instrumented(root, expected, stage=stage, action="kill")
                    self.assert_blocked(result, "anchored_worker_failed", "indeterminate")
                    self.assertEqual(snapshot(root), before)
                    rid = None
                else:
                    rid = self.interrupted(root, expected, stage)
                result = self.upgrade(root, expected, rid)
                self.assert_complete(root, expected, result)
                self.assertNotIn("anchor_notes", result)
                self.assertEqual(Path(result["receipt_path"]).parent.parent, root / gg_lock.RECOVERY_DIR)
                self.assertIn(str(root / gg_lock.LOCK_DIR), result["preserved_paths"])

    def test_completed_recovery_is_replayed_without_writes(self):  # T4
        root = self.workspace()
        expected = identity(root)
        completed = self.upgrade(root, expected)
        self.assert_complete(root, expected, completed)
        before = snapshot(root)
        replay = self.upgrade(root, expected, completed["recovery_id"])
        self.assertEqual(replay, dict(completed, status="already_completed"))
        self.assertEqual(snapshot(root), before)

    def test_legacy_entry_never_spawns_anchored_worker(self):  # T6
        root = self.workspace()
        self.assertNotIn("expected_identity", inspect.signature(gg_lock.upgrade_offline).parameters)
        with patch.object(gg_lock, "_upgrade_offline_anchored", side_effect=AssertionError), \
                patch.object(gg_lock.subprocess, "Popen", side_effect=AssertionError):
            result = gg_lock.upgrade_offline(root, offline_confirmed=True)
        self.assert_complete(root, identity(root), result)

    def test_worker_unavailable_leaves_no_files(self):  # T7
        root = self.workspace()
        expected, before = identity(root), snapshot(root)
        with patch.object(gg_lock.sys, "executable", ""):
            self.assert_blocked(self.upgrade(root, expected), "anchor_unavailable")
        with patch.object(gg_fs.os, "O_NOFOLLOW"):
            del gg_fs.os.O_NOFOLLOW
            self.assert_blocked(self.upgrade(root, expected), "anchor_unavailable")
        with patch.object(gg_lock.sys, "executable", "/usr/bin/true"):
            self.assert_blocked(self.upgrade(root, expected), "anchor_unavailable")
        self.assertEqual(snapshot(root), before)

    def test_worker_protocol_failures_after_greeting_are_indeterminate(self):  # T7
        root = self.workspace()
        before, expected = snapshot(root), identity(root)
        result = gg_lock._recovery_result(None, "blocked", "not_committed")
        cases = {
            "no-result": (b"", 0), "garbage": (b"not json", 0),
            "shape": (b"{}", 0), "exit-code": (json.dumps(result).encode(), 2),
            "invalid-success": (json.dumps(dict(result, status="completed")).encode(), 0),
            "contradiction": (json.dumps(dict(result, commit_state="committed")).encode(), 0),
            "result-limit": (b" " * 4097, 0),
            "absolute": (json.dumps(dict(result, receipt_path="/tmp/outside")).encode(), 0),
            "parent": (json.dumps(dict(result, preserved_paths=["a/../b"])).encode(), 0),
            "cleanup": (json.dumps(dict(result, cleanup_errors=[
                {"reason": "cleanup", "operation": "unlink", "path": "../outside"}])).encode(), 0),
        }
        for name, (payload, code) in cases.items():
            with self.subTest(name=name):
                # Greets, waits for the parent's start byte, then answers.
                fake = ("import os,sys; sys.stdin.buffer.readline(); "
                        "f=os.fdopen(int(sys.argv[-1]),'wb'); "
                        f"f.write({gg_lock._ANCHOR_HELLO!r}); f.flush(); "
                        "sys.stdin.buffer.read(1); "
                        f"f.write({payload!r}); f.close(); "
                        f"sys.exit({code})")
                with patch.object(gg_lock, "_ANCHOR_CHILD_ARGV", ("-I", "-c", fake)), \
                        patch.object(gg_lock, "_ANCHOR_RESULT_LIMIT", 4096):
                    self.assert_blocked(self.upgrade(root, expected), "anchored_worker_failed", "indeterminate")
        self.assertEqual(snapshot(root), before)

    def test_parent_greeting_timeout_never_writes(self):  # C-01
        # The parent gives up on the greeting before it reads anything. The
        # real worker waits for the start byte that never comes, so its
        # "not committed" report is true rather than a lie.
        root = self.workspace()
        before, expected = snapshot(root), identity(root)
        reaped = []

        def let_child_finish(child):
            # Do not kill: close stdin (no start byte) and let the real child
            # run to its end, so a child that writes without the start byte
            # would change the folder before we look.
            child.stdin.close()
            reaped.append(child.wait(timeout=30))

        with patch.object(gg_lock, "_anchor_read", side_effect=TimeoutError), \
                patch.object(gg_lock, "_anchor_stop", let_child_finish):
            self.assert_blocked(self.upgrade(root, expected), "anchor_unavailable")
        self.assertEqual(reaped, [0])
        self.assertEqual(snapshot(root), before)

    def test_child_writes_nothing_without_start_signal(self):  # C-01
        # Drive the shipped child directly: request in, greeting read, then
        # EOF on stdin (the parent died or timed out). It must write no
        # result and touch no file in the approved folder.
        root = self.workspace()
        expected, before = identity(root), snapshot(root)
        request = json.dumps(dict(expected_identity=expected, resume_id=None)).encode() + b"\n"
        fd = gg_fs.open_directory_nofollow(root)
        read_fd, write_fd = os.pipe()
        try:
            proc = subprocess.Popen([PYTHON, "-I", "-X", "utf8", str(CHILD), str(fd), str(write_fd)],
                                    pass_fds=(fd, write_fd), stdin=subprocess.PIPE,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            os.close(write_fd)
            write_fd = None
            try:
                proc.stdin.write(request)
                proc.stdin.flush()
                hello = b""
                deadline = time.monotonic() + 12
                while len(hello) < len(gg_lock._ANCHOR_HELLO):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not select.select([read_fd], [], [], remaining)[0]:
                        self.fail("worker did not greet before the deadline")
                    chunk = os.read(read_fd, len(gg_lock._ANCHOR_HELLO) - len(hello))
                    if not chunk:
                        # EOF before the greeting: fail now instead of
                        # spinning on empty reads for ever.
                        self.fail("worker closed the result pipe without greeting")
                    hello += chunk
                self.assertEqual(hello, gg_lock._ANCHOR_HELLO)
                # No start byte: close stdin, which the child sees as EOF.
                proc.stdin.close()
                self.assertEqual(proc.wait(timeout=12), 0)
                data = b""
                # A fresh window: the greeting may already have spent some of
                # the first one, and the result must still get its own bound.
                deadline = time.monotonic() + 12
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not select.select([read_fd], [], [], remaining)[0]:
                        self.fail("worker left the result pipe open after exiting")
                    chunk = os.read(read_fd, 65536)
                    if not chunk:
                        break
                    data += chunk
            finally:
                if proc.poll() is None:
                    proc.kill()
                    try:
                        proc.wait(timeout=12)
                    except subprocess.TimeoutExpired:
                        pass
            self.assert_blocked(json.loads(data), "anchor_unavailable")
        finally:
            for handle in (fd, read_fd, write_fd):
                if handle is not None:
                    os.close(handle)
        self.assertEqual(snapshot(root), before)

    def test_failure_after_start_signal_is_indeterminate(self):  # C-01
        # Greeting read for real, start byte sent, then the result read fails.
        # The worker may already have committed, so this must not read as
        # "not committed".
        root = self.workspace()
        expected = identity(root)
        real_read = gg_lock._anchor_read

        def read_hello_then_fail(events, fd, deadline, limit):
            # The greeting asks for at most its own length; the result read
            # always asks for the full 64 KiB chunk.
            if limit <= len(gg_lock._ANCHOR_HELLO):
                return real_read(events, fd, deadline, limit)
            raise TimeoutError

        with patch.object(gg_lock, "_anchor_read", read_hello_then_fail):
            self.assert_blocked(self.upgrade(root, expected), "anchored_worker_failed", "indeterminate")
        # The same path without the injected failure completes, so the verdict
        # above came from the failure and not from a broken worker.
        other = self.workspace("control")
        self.assert_complete(other, identity(other), self.upgrade(other, identity(other)))

    def test_reap_is_bounded_when_worker_ignores_kill(self):  # I-01
        # A worker wedged in the kernel must not hold the caller past the
        # promised total: the reap after kill is bounded, not unbounded.
        root = self.workspace()
        expected, before = identity(root), snapshot(root)
        stuck = []

        def spawn(args, **kwargs):
            stub = _StuckChild(kwargs["pass_fds"])
            self.addCleanup(stub.close)
            stuck.append(stub)
            return stub

        with patch.object(gg_lock.subprocess, "Popen", spawn), \
                patch.object(gg_lock, "_ANCHOR_HELLO_TIMEOUT", 0.2):
            started = time.monotonic()
            result = self.upgrade(root, expected)
            elapsed = time.monotonic() - started
        self.assert_blocked(result, "anchor_unavailable")
        self.assertTrue(stuck[0].killed)
        # Every wait carried a deadline, and the caller did not wait it out.
        self.assertEqual(stuck[0].waits, [gg_lock._ANCHOR_REAP_TIMEOUT])
        self.assertLess(elapsed, 2)
        self.assertEqual(snapshot(root), before)

    def test_eof_near_total_spends_remaining_time_then_one_reap(self):  # I-01
        # The result pipe hits EOF at the very edge of the total while the
        # worker is still alive. The wait for the exit must spend only what
        # is left of the total, and the kill/reap its single fixed bound:
        # the caller returns within total + one reap bound, not two.
        root = self.workspace()
        expected, before = identity(root), snapshot(root)
        clock = _Clock()
        eof_at = gg_lock._ANCHOR_TOTAL_TIMEOUT - 0.1
        children = []

        def spawn(args, **kwargs):
            stub = _EofChild(clock)
            self.addCleanup(stub.close)
            children.append(stub)
            return stub

        def read(events, fd, deadline, limit):
            if limit <= len(gg_lock._ANCHOR_HELLO):
                return gg_lock._ANCHOR_HELLO  # greet at once, clock unchanged
            clock.now = eof_at  # the worker's last byte is at the edge
            return b""          # ... and the pipe is closed

        with patch.object(gg_lock.subprocess, "Popen", spawn), \
                patch.object(gg_lock, "time", clock), \
                patch.object(gg_lock, "_anchor_read", read):
            result = self.upgrade(root, expected)
        self.assert_blocked(result, "anchored_worker_failed", "indeterminate")
        stub = children[0]
        self.assertTrue(stub.killed)
        # Two waits, and the first one is the total's remainder, not a fresh
        # five seconds; only the reap after kill spends _ANCHOR_REAP_TIMEOUT.
        remaining = gg_lock._ANCHOR_TOTAL_TIMEOUT - eof_at
        self.assertEqual(len(stub.waits), 2)
        self.assertAlmostEqual(stub.waits[0], remaining, places=6)
        self.assertEqual(stub.waits[1], gg_lock._ANCHOR_REAP_TIMEOUT)
        # The two waits together spend the total's remainder plus one reap
        # bound - not a second fixed five seconds on top of the first.
        self.assertAlmostEqual(sum(stub.waits),
                               remaining + gg_lock._ANCHOR_REAP_TIMEOUT, places=6)
        self.assertLessEqual(clock.now,
                             gg_lock._ANCHOR_TOTAL_TIMEOUT + gg_lock._ANCHOR_REAP_TIMEOUT + 1e-6)
        self.assertEqual(snapshot(root), before)

    def test_result_shape_rejects_contradictory_combinations(self):  # R-01
        root = self.base / "shape"
        base = gg_lock._recovery_result(None, "blocked", "not_committed", "target_changed")
        rid = "a1234567-1234-1234-1234-123456789abc"
        complete = gg_lock._recovery_result(rid, "completed", "committed", None,
                                            "receipts/30-completed.json", "a" * 64)
        bad = [
            dict(base, commit_state="committed"),            # blocked + committed
            dict(base, reason=None),                          # blocked without a reason
            dict(base, reason=""),                            # empty reason
            dict(base, commit_state="committed", reason=None),
            dict(complete, receipt_path=None),                # completed without a receipt
            dict(complete, status="blocked"),                 # blocked + committed via status
            dict(base, status="completed"),                   # completed + not_committed
        ]
        for payload in bad:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    gg_lock._anchor_result(json.dumps(payload).encode(), root)
        good = [base,
                dict(base, commit_state="indeterminate", reason="anchored_worker_failed"),
                dict(base, commit_state="committed_cleanup_pending", reason="cleanup_failed"),
                complete]
        for payload in good:
            with self.subTest(payload=payload):
                gg_lock._anchor_result(json.dumps(payload).encode(), root)

    def test_worker_timeout_kills_paused_worker(self):  # T7
        root = self.workspace()
        before, expected = snapshot(root), identity(root)
        with patch.object(gg_lock, "_ANCHOR_TOTAL_TIMEOUT", 3):
            result, _ = self.instrumented(root, expected, stage="anchored", action="timeout")
        self.assert_blocked(result, "anchored_worker_failed", "indeterminate")
        self.assertEqual(snapshot(root), before)

    def test_invalid_resume_id_is_rejected_by_parent_and_child(self):  # T8
        root = self.workspace()
        expected, before = identity(root), snapshot(root)
        for rid in ("/x", "C:\\x", "\\x", "..", "", "A1234567-1234-1234-1234-123456789abc"):
            with self.subTest(rid=rid):
                self.assert_blocked(self.upgrade(root, expected, rid), "recovery_id_invalid")
                request = json.dumps(dict(expected_identity=expected, resume_id=rid)).encode() + b"\n"
                fd = gg_fs.open_directory_nofollow(root)
                read_fd, write_fd = os.pipe()
                try:
                    proc = subprocess.run([PYTHON, "-I", "-X", "utf8", str(CHILD), str(fd), str(write_fd)],
                                          pass_fds=(fd, write_fd), input=request, capture_output=True, timeout=12)
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    self.assert_blocked(json.loads(os.read(read_fd, 4096)), "recovery_id_invalid")
                finally:
                    for handle in (fd, read_fd, write_fd):
                        os.close(handle)
        self.assertEqual(snapshot(root), before)

    def test_worker_isolated_and_output_discarded(self):  # T9
        root = self.workspace()
        for module in ("json", "gg_fs", "fcntl"):
            (root / (module + ".py")).write_text("raise RuntimeError('workspace import')\n")
        before, expected = snapshot(root), identity(root)
        with patch.object(gg_lock, "_ANCHOR_CHILD_ARGV", (str(CHILD),)):
            self.assert_blocked(self.upgrade(root, expected), "anchor_unavailable")
        result, _ = self.instrumented(root, expected, relative_path="relative")
        self.assert_blocked(result, "anchor_unavailable")
        self.assertEqual(snapshot(root), before)

        def spawn(args, **kwargs):
            self.assertNotIn("cwd", kwargs)
            return REAL_POPEN(args, cwd=root, **kwargs)
        with patch.object(gg_lock.subprocess, "Popen", spawn), patch.dict(os.environ, PYTHONPATH=str(root)):
            self.assert_complete(root, expected, self.upgrade(root, expected))
        other = self.workspace("noisy")
        # Also inspect the real stream separation in a separate process:
        # supervisor stdout and stderr are captured by our parent, while
        # its worker deliberately prints on both and uses its result pipe.
        code = (f"import sys; sys.path.insert(0,{str(SCRIPTS)!r}); import gg_lock; "
                f"gg_lock._ANCHOR_CHILD_ARGV=('-I','-X','utf8',{str(self.worker)!r},"
                f"{str(SCRIPTS)!r},'','','noise',''); "
                f"r=gg_lock._upgrade_offline_anchored({str(other)!r},offline_confirmed=True,"
                f"expected_identity={identity(other)!r}); sys.exit(r['status']!='completed')")
        proc = subprocess.run([PYTHON, "-I", "-c", code], capture_output=True, timeout=12)
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, b"", b""))

    def test_anchored_worker_only_uses_relative_recovery_paths(self):  # T10
        for resume in (False, True):
            with self.subTest(resume=resume):
                root = self.workspace(str(resume))
                expected = identity(root)
                rid = self.interrupted(root, expected, "after_10-quarantined") if resume else None
                if resume:
                    # Exercise the empty-install recovery branch (listdir/rmdir).
                    (root / gg_lock.LOCK_DIR).mkdir()
                result, (seen, violations, cwd_counts, chdir_events) = self.instrumented(
                    root, expected, resume_id=rid, mode="audit")
                self.assertEqual(violations, [])
                self.assertEqual(cwd_counts[1], 0)
                # The worker changes its working base exactly once, with
                # fchdir, before the audit arms. No os.chdir at all, and no
                # change of base after arming. The list proves the hook is
                # live, so an empty armed set is meaningful.
                self.assertEqual(chdir_events, [["fchdir", False]])
                required = {"open", "os.mkdir", "os.scandir", "os.listdir", "lstat", "lexists",
                            "unlink", "os.remove", "_os_rename_noreplace"}
                if resume:
                    required.add("os.rmdir")
                self.assertEqual(required - set(seen), set())
                self.assert_complete(root, expected, result)

    def test_identity_round_trip_and_shape(self):  # T11
        root = self.workspace()
        expected, before = identity(root), snapshot(root)
        invalid = (dict(expected, inode=1.0), dict(expected, inode="1"), dict(expected, inode=True),
                   dict(expected, platform="other"), {k: v for k, v in expected.items() if k != "device"})
        for value in invalid:
            with self.subTest(value=value):
                self.assert_blocked(self.upgrade(root, value), "target_changed")
        self.assertEqual(snapshot(root), before)
        large = dict(expected, inode=2**53 + 1)
        self.assertEqual(json.loads(json.dumps(large)), large)
        round_trip = json.loads(json.dumps(dict(expected, extension=["keep"])))
        result = self.upgrade(root, round_trip)
        self.assertEqual(round_trip, dict(expected, extension=["keep"]))
        self.assert_complete(root, expected, result)

    def test_open_failure_reasons_do_not_fall_back(self):  # T12
        root = self.workspace()
        expected, before = identity(root), snapshot(root)
        link = self.base / "link"
        link.symlink_to(root, target_is_directory=True)
        for path, reason in ((link, "symlink_reparse"), (self.base / "missing", "target_changed"),
                             (root / "project.json", "target_changed")):
            with self.subTest(path=path):
                self.assert_blocked(self.upgrade(path, expected), reason)
        # Simulate the kernel denial independent of the runner's privileges.
        with patch.object(gg_fs.os, "open", side_effect=PermissionError(errno.EACCES, "denied")):
            self.assert_blocked(self.upgrade(root, expected), "stat_failed")
        self.assert_blocked(self.upgrade(root, expected, confirmed=False), "offline_confirmation_required")
        self.assertEqual(snapshot(root), before)
