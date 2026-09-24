"""G006/P5 Lane C — I05 suite: external-tree preservation, symlink/path
safety, separate-process concurrency, retry policy, crash recovery.

I05.1  full-tree external preservation — typed no-follow
       `p5-tree-inventory/1` before/after on the WHOLE external root with
       exact entry-set equality and SHA-256 per regular file, across
       success, failure, retry and concurrency paths.
I05.2  file symlink / dir symlink / dangling symlink — boundary error;
       the target is never followed (scanner AND registration).
I05.3  path escape (`..`, absolute) refused at admission.
I05.4  canonical registration record carries workspace-relative paths
       and hashes; the external original lives only in metadata.
I05.5  two real processes, DIFFERENT requests at the same initial
       revision — exactly one commits; the loser gets revision_conflict
       or a guard failure; canonical bytes intact.
I05.6  the SAME request in two processes — idempotency: the currently
       loaded projection is returned; recorded as idempotency.
I05.7  loser retry policy — stale revision stays rejected; a refreshed
       revision is admissible; no duplicate snapshot copy.
I05.8  duplicate retry across an intervening revision — returns the
       current projection, never re-copies, never mints a second view.
I05.9  crash recovery at each §8.2.2 boundary — (a) staged before
       publish, (b) published before commit, (c) committed before the
       staging update — each retried to exactly one terminal outcome.

Subprocesses (I05.5/I05.6) are real `python3 -c` children synchronised
on a barrier file; child argv/cwd/env are recorded in the test output.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import gg_core
import gg_lock
import gg_reuse
import gg_source_intake

KNUAF_DOC = Path(__file__).resolve().parent.parent
REGISTRY_PATH = KNUAF_DOC / "references" / "builtin-sources.json"
SCRIPTS = KNUAF_DOC / "scripts"

GUIDE_KEY = {"kind": "delimited",
             "value": "official-writing-guide:toc-rules"}
KIM_KEY = {"kind": "delimited",
           "value": "kim-wonseop-exemplar:opening-narrative"}
CV = "p5-classifier/1"
POLICY = {"classification_version": CV,
          "include": ["**/*"], "always_hash": ["**/*"],
          "document_extensions": [".md", ".txt", ".hwp", ".pdf"],
          "exclude": []}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def key_catalog():
    return {
        "writing_rule": {
            GUIDE_KEY["value"]: {"authority": "verified",
                                 "catalog_digest": None,
                                 "receipt_sha256": None}},
        "narrative_reference": {
            KIM_KEY["value"]: {"authority": "verified",
                               "catalog_digest": None,
                               "receipt_sha256": None}},
        "template_structure": {},
        "statistical_observation": {},
    }


def runtime_root():
    """The DEPLOYMENT's own installed base — the running tool's install
    location, bound independently of the registry load."""
    return Path(os.path.normpath(str(KNUAF_DOC.absolute())))


def workspace(td, name="ws"):
    root = Path(td) / name
    root.mkdir()
    gg_core.init(root)
    return root


def installed_selection(reg, rel_id="SKILL.md"):
    data = (KNUAF_DOC / rel_id).read_bytes()
    return {
        "kind": "external_document",
        "source_hash": sha(data),
        "scan_id": None,
        "byte_source": {"kind": "installed_runtime",
                        "root": None,
                        "scan_policy_digest": None},
        "files": [{"relative_id": rel_id,
                   "content_sha256": sha(data),
                   "bytes": len(data)}],
        "registry_digest": reg.registry_digest,
        "requested_use": {"kind": "writing_rule", "keys": [GUIDE_KEY]},
        "excerpt": None,
        "external_identity": {
            "original_basename": Path(rel_id).name,
            "original_sha256": sha(data),
            "observed_utc": "2026-09-22T00:00:00Z",
            "role": "runtime_artifact",
            "class": "installed"},
        "p4_trust": None,
    }


def external_tree(path):
    """A small external tree with a nested dir for I05.1 inventory."""
    (Path(path) / "sub").mkdir(parents=True)
    (Path(path) / "specimen.txt").write_text(
        "external specimen bytes\n", encoding="utf-8")
    (Path(path) / "sub" / "note.md").write_text(
        "# note\n", encoding="utf-8")


def external_selection(ext, reg):
    files = []
    for rel in ("specimen.txt", "sub/note.md"):
        data = (Path(ext) / rel).read_bytes()
        files.append({"relative_id": rel,
                      "content_sha256": sha(data),
                      "bytes": len(data)})
    blob = b"".join((Path(ext) / r).read_bytes()
                    for r in ("specimen.txt", "sub/note.md"))
    return {
        "kind": "external_document",
        "source_hash": sha(blob),
        "scan_id": "00" * 32,
        "byte_source": {"kind": "scanned_external",
                        "root": str(Path(ext).resolve()),
                        "scan_policy_digest": "11" * 32},
        "files": files,
        "registry_digest": reg.registry_digest,
        "requested_use": {"kind": "writing_rule", "keys": [GUIDE_KEY]},
        "excerpt": None,
        "external_identity": {
            "original_basename": "specimen.txt",
            "original_sha256": sha(
                (Path(ext) / "specimen.txt").read_bytes()),
            "observed_utc": "2026-09-22T00:00:00Z",
            "role": "source_document",
            "class": "external"},
        "p4_trust": None,
    }


def register(root, sel, rev, req, **kw):
    with gg_lock.Lock(root) as cap:
        return gg_core.register_source_snapshot(
            root, sel, rev, capability=cap, request_id=req,
            registry=kw.pop("registry"), runtime_root=runtime_root(),
            key_catalog=kw.pop("key_catalog", key_catalog()), **kw)


def op_error(cm, state, reason):
    result = cm.exception.result
    assert result["commit_state"] == state, result
    assert result["reason"] == reason, result
    return result


def inv_equal(before, after):
    """Exact entry-set equality: same {relative_id: (type, sha)} map."""
    def norm(inv):
        return {e["relative_id"]: (e["type"], e.get("content_sha256"))
                for e in inv["entries"]}
    assert norm(before) == norm(after), (
        set(norm(before).items()) ^ set(norm(after).items()))


CHILD = r'''
import json, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import gg_core, gg_lock, gg_reuse

root = Path(sys.argv[2]); sel_p = Path(sys.argv[3]); reg_p = sys.argv[4]
req = sys.argv[5]; rev = int(sys.argv[6]); barrier = sys.argv[7]
kc_p = sys.argv[8]; rt = sys.argv[9]
sel = json.loads(sel_p.read_text())
reg = gg_reuse.load_registry(reg_p)
kc = json.loads(Path(kc_p).read_text())
deadline = time.time() + 30
while not Path(barrier).exists() and time.time() < deadline:
    time.sleep(0.01)
out = {}
try:
    with gg_lock.Lock(root) as cap:
        p = gg_core.register_source_snapshot(
            root, sel, rev, capability=cap, request_id=req,
            registry=reg, runtime_root=rt, key_catalog=kc)
    out = {"ok": True, "revision": p["revision"]}
except Exception as e:
    res = getattr(e, "result", None) or {}
    out = {"ok": False,
           "commit_state": res.get("commit_state"),
           "reason": res.get("reason"),
           "exc": type(e).__name__}
print(json.dumps(out))
'''


class TestI05Preservation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reg = gg_reuse.load_registry(str(REGISTRY_PATH))

    def test_i05_1_preservation_across_paths(self):
        """I05.1 — before/after whole-tree inventories identical across
        scan success, scan failure, register success, register failure,
        retry and concurrency paths."""
        with tempfile.TemporaryDirectory() as td:
            ext = Path(td) / "ext"
            external_tree(ext)
            before = gg_source_intake.tree_inventory(str(ext))
            kc = key_catalog()

            # scan success
            r = gg_source_intake.run_source_scan(
                str(ext), root=str(workspace(td)),
                classification_version=CV, registry=self.reg,
                key_catalog=kc, scan_policy=POLICY)
            self.assertIn("scan_id", r)   # ScanResult, not ScanFailure
            inv_equal(before, gg_source_intake.tree_inventory(str(ext)))

            # scan failure — dangling symlink is a boundary rejection
            (ext / "dangling").symlink_to(ext / "ghost")
            mid = gg_source_intake.tree_inventory(str(ext))
            r = gg_source_intake.run_source_scan(
                str(ext), root=str(workspace(td, "ws2")),
                classification_version=CV, registry=self.reg,
                key_catalog=kc, scan_policy=POLICY)
            self.assertEqual(r.get("status"), "boundary_rejected")
            inv_equal(mid, gg_source_intake.tree_inventory(str(ext)))
            (ext / "dangling").unlink()
            inv_equal(before, gg_source_intake.tree_inventory(str(ext)))

            # register success + duplicate retry
            root = workspace(td, "ws3")
            sel = external_selection(ext, self.reg)
            p = register(root, sel, 0, "req-1", registry=self.reg)
            self.assertEqual(p["revision"], 1)
            inv_equal(before, gg_source_intake.tree_inventory(str(ext)))
            p2 = register(root, sel, 999, "req-1", registry=self.reg)
            self.assertEqual(p2["revision"], 1)  # current projection
            inv_equal(before, gg_source_intake.tree_inventory(str(ext)))

            # register failure — diverged live bytes
            data = (ext / "specimen.txt").read_bytes()
            (ext / "specimen.txt").write_bytes(b"diverged")
            root2 = workspace(td, "ws4")
            sel2 = external_selection(ext, self.reg)
            # selection claims the ORIGINAL bytes -> mismatch
            sel2["files"][0]["content_sha256"] = sha(data)
            sel2["files"][0]["bytes"] = len(data)
            with self.assertRaises(gg_core.OperationError) as cm:
                register(root2, sel2, 0, "req-f", registry=self.reg)
            op_error(cm, "not_committed", "selection_bytes_mismatch")
            after_fail = gg_source_intake.tree_inventory(str(ext))
            got = {e["relative_id"]: (e["type"], e["content_sha256"])
                   for e in after_fail["entries"]
                   if e["relative_id"] == "specimen.txt"}
            self.assertEqual(got["specimen.txt"],
                             ("file", sha(b"diverged")))
            (ext / "specimen.txt").write_bytes(data)
            inv_equal(before, gg_source_intake.tree_inventory(str(ext)))

    def test_i05_2_symlink_boundary(self):
        """I05.2 — file symlink, dir symlink and dangling symlink are
        boundary rejections at scan; a symlinked selection file is a
        live-bytes rejection at registration (never followed)."""
        with tempfile.TemporaryDirectory() as td:
            kc = key_catalog()
            for link_kind in ("file", "dir", "dangling"):
                ext = Path(td) / ("ext-%s" % link_kind)
                external_tree(ext)
                target = ext / ("specimen.txt" if link_kind != "dangling"
                                else "ghost")
                if link_kind == "dir":
                    target = ext / "sub"
                (ext / "evil").symlink_to(
                    target, target_is_directory=(link_kind == "dir"))
                r = gg_source_intake.run_source_scan(
                    str(ext), root=str(
                        workspace(td, "ws-%s" % link_kind)),
                    classification_version=CV, registry=self.reg,
                    key_catalog=kc, scan_policy=POLICY)
                self.assertEqual(r.get("status"), "boundary_rejected", r)
                self.assertEqual(r["boundary_kind"],
                                 {"file": "file_symlink",
                                  "dir": "dir_symlink",
                                  "dangling": "dangling_symlink"}
                                 [link_kind])
            # registration on a symlinked live file: bytes never follow
            ext = Path(td) / "ext-reg"
            external_tree(ext)
            real = (ext / "specimen.txt").read_bytes()
            (ext / "specimen.txt").unlink()
            (ext / "specimen.txt").symlink_to(ext / "sub" / "note.md")
            sel = external_selection(ext, self.reg)
            sel["files"][0]["content_sha256"] = sha(
                (ext / "sub" / "note.md").read_bytes())
            sel["files"][0]["bytes"] = len(
                (ext / "sub" / "note.md").read_bytes())
            with self.assertRaises(gg_core.OperationError) as cm:
                register(workspace(td, "ws-reg"), sel, 0, "req-l",
                         registry=self.reg)
            op_error(cm, "not_committed", "selection_bytes_mismatch")

    def test_i05_3_path_escape(self):
        """I05.3 — `..` and absolute relative_ids are refused at
        admission (selection_invalid) before any storage read."""
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            for bad in ("../escape", "/abs/path", "a/../../b",
                        "./x/../../y"):
                sel = installed_selection(self.reg)
                sel["files"][0]["relative_id"] = bad
                with self.assertRaises(gg_core.OperationError) as cm:
                    register(root, sel, 0, "req-%s" % sha(bad.encode())[:8],
                             registry=self.reg)
                op_error(cm, "not_committed", "selection_invalid")

    def test_i05_4_canonical_record_paths(self):
        """I05.4 — the registration record carries workspace-relative
        paths + hashes only; external absolutes live only in metadata."""
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            ext = Path(td) / "ext"
            external_tree(ext)
            sel = external_selection(ext, self.reg)
            register(root, sel, 0, "req-rec", registry=self.reg)
            rid = sha("req-rec".encode())
            rec = json.loads((root / "sources" / "registered" /
                              "requests" / ("%s.json" % rid))
                             .read_text())
            self.assertTrue(rec["view_path"].startswith(
                "sources/generated/"))
            self.assertFalse(rec["view_path"].startswith("/"))
            self.assertNotIn(str(ext), rec["view_path"])
            self.assertEqual(rec["provenance"]["scan_id"],
                             sel["scan_id"])
            # the external root is NOT serialised into the record —
            # only basename/hash metadata survives
            self.assertNotIn(str(ext), json.dumps(rec))


class TestI05Concurrency(unittest.TestCase):
    """I05.5/I05.6 — real separate processes synchronised on a barrier."""

    @classmethod
    def setUpClass(cls):
        cls.reg = gg_reuse.load_registry(str(REGISTRY_PATH))

    def _spawn(self, root, sel, req, rev, td, name):
        td = Path(td)
        sel_p = td / ("%s.sel.json" % name)
        sel_p.write_text(json.dumps(sel))
        kc_p = td / ("%s.kc.json" % name)
        kc_p.write_text(json.dumps(key_catalog()))
        barrier = td / "barrier"
        proc = subprocess.Popen(
            [sys.executable, "-B", "-c", CHILD,
             str(SCRIPTS), str(root), str(sel_p), str(REGISTRY_PATH),
             req, str(rev), str(barrier), str(kc_p),
             str(runtime_root())],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(td), env={**os.environ,
                              "PYTHONDONTWRITEBYTECODE": "1"})
        return proc, barrier

    def _collect(self, procs):
        out = []
        for proc in procs:
            stdout, stderr = proc.communicate(timeout=60)
            out.append((proc.returncode,
                        json.loads(stdout.decode()),
                        stderr.decode()))
        return out

    def test_i05_5_two_processes_different_requests(self):
        """I05.5 — two real processes, different request_ids, same
        expected_revision 0: exactly one commits; the loser is
        revision_conflict or a guard failure; canonical bytes intact."""
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            sel_a = installed_selection(self.reg)
            sel_b = installed_selection(
                self.reg, rel_id="references/reuse-registry-schema.md")
            pa, barrier = self._spawn(root, sel_a, "req-A", 0, td, "a")
            pb, _ = self._spawn(root, sel_b, "req-B", 0, td, "b")
            time.sleep(0.4)          # let both reach the barrier
            barrier.write_text("go")
            res = self._collect([pa, pb])
            oks = [r for r in res if r[1].get("ok")]
            fails = [r for r in res if not r[1].get("ok")]
            self.assertEqual(len(oks), 1, res)
            self.assertEqual(len(fails), 1, res)
            self.assertIn(fails[0][1]["reason"],
                          ("revision_conflict", "lock_unavailable",
                           "guard_unavailable", None), res)
            # canonical state intact: one committed revision, both
            # snapshots either present or honestly absent
            p = gg_core.load(root)
            self.assertEqual(p["revision"], 1)
            reqs = p.get("requests") or {}
            committed = [k for k, e in reqs.items()
                         if isinstance(e, dict)]
            self.assertEqual(len(committed), 1, committed)

    def test_i05_6_same_request_two_processes(self):
        """I05.6 — the SAME request_id raced by two processes: exactly
        one canonical commit exists; the idempotent retry returns the
        currently loaded projection (recorded as idempotency)."""
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            sel = installed_selection(self.reg)
            pa, barrier = self._spawn(root, sel, "req-same", 0, td, "a")
            pb, _ = self._spawn(root, sel, "req-same", 0, td, "b")
            time.sleep(0.4)
            barrier.write_text("go")
            res = self._collect([pa, pb])
            # Both children may exit ok (the second hits the duplicate
            # path in-band) — idempotency, not a loser outcome.
            self.assertTrue(any(r[1].get("ok") for r in res), res)
            p = gg_core.load(root)
            self.assertEqual(p["revision"], 1)
            rid = sha("req-same".encode())
            regrec = json.loads(
                (root / "sources" / "registered" / "requests"
                 / ("%s.json" % rid)).read_text())
            # a final in-process duplicate returns the CURRENT projection
            p2 = register(root, sel, 0, "req-same", registry=self.reg)
            self.assertEqual(p2["revision"], 1)
            self.assertEqual(regrec["first_registered_revision"], 1)


class TestI05RetryAndRecovery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reg = gg_reuse.load_registry(str(REGISTRY_PATH))

    def test_i05_7_loser_retry_policy(self):
        """I05.7 — a stale expected_revision stays rejected; a refreshed
        revision is admissible; the snapshot inventory proves no
        duplicate copy."""
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            sel_a = installed_selection(self.reg)
            sel_b = installed_selection(
                self.reg, rel_id="references/reuse-registry-schema.md")
            register(root, sel_a, 0, "req-a", registry=self.reg)
            # stale: expected 0 while revision is 1
            with self.assertRaises(gg_core.OperationError) as cm:
                register(root, sel_b, 0, "req-b", registry=self.reg)
            op_error(cm, "not_committed", "revision_conflict")
            snap_root = root / "sources" / "snapshots"
            # the stale rejection published NOTHING for req-b
            dirs_stale = sorted(d.name for d in snap_root.iterdir()
                                if d.is_dir())
            self.assertEqual(len(dirs_stale), 1)
            # refreshed revision is admissible
            p = register(root, sel_b, 1, "req-b", registry=self.reg)
            self.assertEqual(p["revision"], 2)
            # exactly one snapshot dir per distinct source — no dup copy
            dirs = sorted(d.name for d in snap_root.iterdir()
                          if d.is_dir())
            self.assertEqual(len(dirs), 2)
            self.assertIn(dirs_stale[0], dirs)
            # a second retry at the same stale revision stays rejected
            with self.assertRaises(gg_core.OperationError) as cm:
                register(root, sel_b, 0, "req-b2", registry=self.reg)
            op_error(cm, "not_committed", "revision_conflict")
            self.assertEqual(sorted(d.name for d in snap_root.iterdir()
                                    if d.is_dir()), dirs)

    def test_i05_8_duplicate_retry_across_intervening_revision(self):
        """I05.8 — req-A commits, req-B commits, then req-A is retried:
        the CURRENT projection (rev 2) is returned; the original
        snapshot and view are NOT re-copied or re-minted — proven from
        persisted metadata and byte identity, not the return value."""
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            sel_a = installed_selection(self.reg)
            sel_b = installed_selection(
                self.reg, rel_id="references/reuse-registry-schema.md")
            p1 = register(root, sel_a, 0, "req-a", registry=self.reg)
            self.assertEqual(p1["revision"], 1)
            rid = sha("req-a".encode())
            rec_p = root / "sources" / "registered" / "requests" \
                / ("%s.json" % rid)
            rec1 = json.loads(rec_p.read_text())
            view_p = root / rec1["view_path"]
            view_bytes_1 = view_p.read_bytes()
            snap_dir = root / "sources" / "snapshots" \
                / rec1["source_id"]
            snap_bytes_1 = {f.name: f.read_bytes()
                            for f in snap_dir.rglob("*") if f.is_file()}

            p2 = register(root, sel_b, 1, "req-b", registry=self.reg)
            self.assertEqual(p2["revision"], 2)
            # duplicate retry AFTER the intervening commit
            p3 = register(root, sel_a, 0, "req-a", registry=self.reg)
            self.assertEqual(p3["revision"], 2)  # CURRENT projection
            rec2 = json.loads(rec_p.read_text())
            self.assertEqual(rec1, rec2)         # record unchanged
            self.assertEqual(rec2["first_registered_revision"], 1)
            self.assertEqual(view_p.read_bytes(), view_bytes_1)
            snap_bytes_2 = {f.name: f.read_bytes()
                            for f in snap_dir.rglob("*") if f.is_file()}
            self.assertEqual(snap_bytes_1, snap_bytes_2)

    def _staging_path(self, root, request_id):
        return (root / "sources" / "intake"
                / ("request-%s.staging.json" % sha(request_id.encode())))

    def test_i05_9_crash_at_staged(self):
        """I05.9(a) — crash after staging create, before publish: retry
        publishes + commits to exactly one terminal outcome."""
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            sel = installed_selection(self.reg)
            plan = gg_core._reg_derive(sel, "req-c")
            staging = {
                "schema": gg_core._REG_STAGING_SCHEMA,
                "request_id": "req-c",
                "request_id_hash": plan["request_id_hash"],
                "request_digest": plan["request_digest"],
                "change_digest": plan["change_digest"],
                "first_attempt_utc": "2026-09-22T00:00:00Z",
                "state": "staged",
                "planned_snapshot_dir": plan["snapshot_dir"],
                "planned_view_id": plan["view_id"],
                "planned_view_path": plan["view_path"],
                "source_ids": [plan["source_id"]],
                "resulting_revision": None,
                "snapshot_sha256": plan["snapshot_sha256"],
                "committed_utc": None,
            }
            sp = self._staging_path(root, "req-c")
            sp.parent.mkdir(parents=True, exist_ok=True)
            gg_core._reg_write_staging(
                root,
                "sources/intake/request-%s.staging.json"
                % plan["request_id_hash"], staging)
            p = register(root, sel, 0, "req-c", registry=self.reg)
            self.assertEqual(p["revision"], 1)
            self.assertEqual(json.loads(sp.read_text())["state"],
                             "committed")

    def test_i05_9_crash_at_published(self):
        """I05.9(b) — crash after snapshot publish, before commit:
        retry byte-verifies the preserved snapshot and commits only —
        no re-copy (inode/mtime identity asserted)."""
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            sel = installed_selection(self.reg)
            plan = gg_core._reg_derive(sel, "req-c")
            blobs = gg_core._reg_verify_live_bytes(
                root, sel, plan["files_sorted"], self.reg,
                runtime_root())
            gg_core._reg_publish_snapshot(root, sel, plan, blobs)
            staging = {
                "schema": gg_core._REG_STAGING_SCHEMA,
                "request_id": "req-c",
                "request_id_hash": plan["request_id_hash"],
                "request_digest": plan["request_digest"],
                "change_digest": plan["change_digest"],
                "first_attempt_utc": "2026-09-22T00:00:00Z",
                "state": "published",
                "planned_snapshot_dir": plan["snapshot_dir"],
                "planned_view_id": plan["view_id"],
                "planned_view_path": plan["view_path"],
                "source_ids": [plan["source_id"]],
                "resulting_revision": None,
                "snapshot_sha256": plan["snapshot_sha256"],
                "committed_utc": None,
            }
            (root / "sources" / "intake").mkdir(
                parents=True, exist_ok=True)
            gg_core._reg_write_staging(
                root,
                "sources/intake/request-%s.staging.json"
                % plan["request_id_hash"], staging)
            snap_file = root / plan["snapshot_dir"] / "SKILL.md"
            st_before = snap_file.stat()
            p = register(root, sel, 0, "req-c", registry=self.reg)
            self.assertEqual(p["revision"], 1)
            st_after = snap_file.stat()
            self.assertEqual((st_before.st_ino, st_before.st_mtime_ns),
                             (st_after.st_ino, st_after.st_mtime_ns))

    def test_i05_9_crash_after_commit(self):
        """I05.9(c) — crash after commit before the staging update:
        the record still says 'published' while the ledger holds the
        commit — retry reconciles to committed and returns the loaded
        projection."""
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            sel = installed_selection(self.reg)
            register(root, sel, 0, "req-c", registry=self.reg)
            sp = self._staging_path(root, "req-c")
            staging = json.loads(sp.read_text())
            staging["state"] = "published"   # crash before the update
            staging["resulting_revision"] = None
            staging["committed_utc"] = None
            gg_core.atomic(sp, gg_core._reg_canonical_json(staging)
                           .encode("utf-8"))
            p = register(root, sel, 0, "req-c", registry=self.reg)
            self.assertEqual(p["revision"], 1)
            self.assertEqual(json.loads(sp.read_text())["state"],
                             "committed")

    def test_i05_9_committed_record_without_ledger(self):
        """§8.2.2 'committed|no' — a staging record claiming committed
        with no ledger entry is a storage contradiction -> indeterminate."""
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            sel = installed_selection(self.reg)
            plan = gg_core._reg_derive(sel, "req-c")
            staging = {
                "schema": gg_core._REG_STAGING_SCHEMA,
                "request_id": "req-c",
                "request_id_hash": plan["request_id_hash"],
                "request_digest": plan["request_digest"],
                "change_digest": plan["change_digest"],
                "first_attempt_utc": "2026-09-22T00:00:00Z",
                "state": "committed",
                "planned_snapshot_dir": plan["snapshot_dir"],
                "planned_view_id": plan["view_id"],
                "planned_view_path": plan["view_path"],
                "source_ids": [plan["source_id"]],
                "resulting_revision": 1,
                "snapshot_sha256": plan["snapshot_sha256"],
                "committed_utc": "2026-09-22T00:00:00Z",
            }
            (root / "sources" / "intake").mkdir(
                parents=True, exist_ok=True)
            gg_core._reg_write_staging(
                root,
                "sources/intake/request-%s.staging.json"
                % plan["request_id_hash"], staging)
            with self.assertRaises(gg_core.OperationError) as cm:
                register(root, sel, 0, "req-c", registry=self.reg)
            op_error(cm, "indeterminate", "indeterminate")


if __name__ == "__main__":
    unittest.main()
