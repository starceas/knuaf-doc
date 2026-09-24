"""P2 lane B tests — recovery claim/stages/resume (SPEC §5.1, API §3).

Covers P2-L08-recovery/race and the B-owned parts of P2-P16 lineage:
dispositions, hash chain integrity, commit-point 0/2/3/4 semantics,
exclusive resume, tamper detection, original-lock preservation and
normal-write blocking while an active recovery exists.

Lane-local band tests; no product PASS or platform support claims.
"""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests._harness import ContractCase, runtime

gg_fs = runtime("gg_fs")
gg_lock = runtime("gg_lock")

STAGES = ("00-prepared", "10-quarantined", "20-installed",
          "30-completed")


def _boom(stage):
    def hit(name):
        if name == stage:
            raise RuntimeError("fault:" + stage)
    return hit


def _kill_recovery_caps():
    """Simulate process death: close every held recovery fd so the OS
    locks drop, then forget the capabilities."""
    for cap in list(gg_lock._RECOVERY_CAPS.values()):
        for fd in (cap._fd, cap._new_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        cap._fd = cap._new_fd = None
        cap.released = True
    gg_lock._RECOVERY_CAPS.clear()


class RecoveryCase(ContractCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p2b-rec-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        gg_lock._FAULT = None

    def tearDown(self):
        gg_lock._FAULT = None
        _kill_recovery_caps()

    # -------------------------------------------------------- helpers

    def _project(self, payload='{"schema_version":2, "revision": 0}'):
        (self.root / "project.json").write_text(payload)
        return payload.encode()

    def _rdir(self, rid):
        return self.root / ".gg-recovery" / rid

    def _read_receipt(self, rid, stage):
        return json.loads(
            (self._rdir(rid) / (stage + ".json")).read_bytes())

    def _chain(self, rid):
        out = {}
        for stage in STAGES:
            path = self._rdir(rid) / (stage + ".json")
            if path.exists():
                data = path.read_bytes()
                out[stage] = (json.loads(data), gg_fs.sha256_bytes(data))
        return out

    def _assert_valid_chain(self, rid):
        receipts = self._chain(rid)
        self.assertEqual(set(receipts), set(STAGES))
        prev = None
        for stage in STAGES:
            receipt, _sha = receipts[stage]
            self.assertEqual(receipt["schema"],
                             "gg-lock-recovery-stage/1")
            self.assertEqual(receipt["stage"], stage)
            self.assertEqual(receipt["recovery_id"], rid)
            self.assertEqual(receipt["previous_sha256"], prev)
            prev = receipt and receipts[stage][1]
        return receipts

    def _protocol_sha(self):
        return gg_fs.sha256_bytes(
            (self.root / ".gg-lock" / "protocol.json").read_bytes())

    # ---------------------------------------------------------- tests

    def test_legacy_full_recovery(self):
        original = self._project()
        result = gg_lock.upgrade_offline(self.root, offline_confirmed=True)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["commit_state"], "committed")
        rid = result["recovery_id"]
        receipts = self._assert_valid_chain(rid)
        claim = json.loads((self._rdir(rid) / "claim.json").read_bytes())
        self.assertEqual(claim["schema"], "gg-lock-recovery-claim/1")
        self.assertEqual(claim["disposition"], "legacy_or_unknown")
        self.assertIsNone(claim["previous_workspace_id"])
        self.assertEqual(
            claim["canonical_sha256"], gg_fs.sha256_bytes(original))
        self.assertEqual(claim["original_lock"]["structure"], "absent")
        # Canonical preserved byte-for-byte; active cleaned; new v2
        # control usable.
        self.assertEqual((self.root / "project.json").read_bytes(),
                         original)
        self.assertFalse(
            (self.root / ".gg-recovery-active.json").exists())
        with gg_lock.Lock(self.root):
            pass
        # Completion re-query is read-only.
        again = gg_lock.upgrade_offline(
            self.root, offline_confirmed=True, resume_id=rid)
        self.assertEqual(again["status"], "already_completed")
        self.assertEqual(again["commit_state"], "committed")

    def test_offline_flag_required(self):
        self._project()
        result = gg_lock.upgrade_offline(self.root, offline_confirmed=False)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["commit_state"], "not_committed")
        self.assertEqual(result["reason"], "offline_confirmation_required")
        self.assertFalse((self.root / ".gg-recovery").exists())

    def test_ready_and_empty_refused(self):
        gg_lock.install_new(self.root)
        (self.root / "project.json").write_text("{}")
        result = gg_lock.upgrade_offline(self.root, offline_confirmed=True)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["commit_state"], "not_committed")
        self.assertEqual(result["reason"], "already_installed")
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        result = gg_lock.upgrade_offline(empty, offline_confirmed=True)
        self.assertEqual(result["commit_state"], "not_committed")
        self.assertEqual(result["reason"], "nothing_to_recover")

    def test_same_workspace_repair_preserves_identity(self):
        proto = gg_lock.install_new(self.root)
        ws = proto["workspace_id"]
        old_dir_id = gg_fs.identity(self.root / ".gg-lock",
                                    kind="directory")
        self._project()
        # Damage: replace the guard.
        os.unlink(self.root / ".gg-lock" / "guard")
        (self.root / ".gg-lock" / "guard").write_bytes(b"X")
        result = gg_lock.upgrade_offline(self.root, offline_confirmed=True)
        self.assertEqual(result["status"], "completed")
        rid = result["recovery_id"]
        claim = json.loads((self._rdir(rid) / "claim.json").read_bytes())
        self.assertEqual(claim["disposition"], "same_workspace_repair")
        self.assertEqual(claim["previous_workspace_id"], ws)
        # Original control preserved with identical identity.
        preserved = self._rdir(rid) / "original-lock"
        self.assertTrue(gg_fs.same_identity(
            gg_fs.identity(preserved, kind="directory"), old_dir_id))
        self.assertTrue((preserved / "protocol.json").exists())
        # workspace_id survives; recovery_id recorded in new protocol.
        st = gg_lock.inspect_lock(self.root)
        self.assertEqual(st["protocol"]["workspace_id"], ws)
        self.assertEqual(st["protocol"]["recovery_id"], rid)
        # Lineage trusted for the same-workspace chain.
        prev_sha = gg_fs.sha256_bytes(
            (preserved / "protocol.json").read_bytes())
        res = gg_lock.verify_recovery_lineage(
            self.root,
            previous_workspace_id=ws,
            previous_protocol_sha256=prev_sha,
            current_workspace_id=ws,
            current_protocol_sha256=self._protocol_sha())
        self.assertTrue(res["trusted"], res)
        self.assertEqual(res["recovery_ids"], [rid])

    def test_copied_workspace_recovery(self):
        base = Path(self.tmp.name) / "base"
        base.mkdir()
        proto = gg_lock.install_new(base)
        (base / "project.json").write_text("{}")
        copy = Path(self.tmp.name) / "copy"
        shutil.copytree(base, copy)
        result = gg_lock.upgrade_offline(copy, offline_confirmed=True)
        self.assertEqual(result["status"], "completed")
        claim = json.loads(
            (copy / ".gg-recovery" / result["recovery_id"]
             / "claim.json").read_bytes())
        self.assertEqual(claim["disposition"], "copied_workspace")
        self.assertEqual(claim["previous_workspace_id"],
                         proto["workspace_id"])
        st = gg_lock.inspect_lock(copy)
        self.assertNotEqual(st["protocol"]["workspace_id"],
                            proto["workspace_id"])
        # A copied-workspace chain never re-trusts old publications.
        res = gg_lock.verify_recovery_lineage(
            copy,
            previous_workspace_id=proto["workspace_id"],
            previous_protocol_sha256=claim["previous_protocol_sha256"],
            current_workspace_id=st["protocol"]["workspace_id"],
            current_protocol_sha256=gg_fs.sha256_bytes(
                (copy / ".gg-lock" / "protocol.json").read_bytes()))
        self.assertFalse(res["trusted"])
        self.assertTrue(res["reason"].startswith("disposition_untrusted"))

    def test_active_blocks_new_recovery_and_writes(self):
        self._project()
        (self.root / ".gg-recovery-active.json").write_text(
            '{"schema":"gg-lock-recovery-claim/1","recovery_id":"x"}')
        result = gg_lock.upgrade_offline(self.root, offline_confirmed=True)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["commit_state"], "not_committed")
        # Corrupt active also fails closed.
        (self.root / ".gg-recovery-active.json").write_bytes(b"junk")
        result = gg_lock.upgrade_offline(self.root, offline_confirmed=True)
        self.assertEqual(result["commit_state"], "not_committed")

    def test_crash_before_any_stage_is_orphan_evidence(self):
        self._project()
        gg_lock._FAULT = _boom("after_active")
        with self.assertRaises(RuntimeError):
            gg_lock.upgrade_offline(self.root, offline_confirmed=True)
        _kill_recovery_caps()
        # Active published, no stage receipts: product unchanged.
        self.assertTrue(
            (self.root / ".gg-recovery-active.json").exists())
        self.assertTrue((self.root / "project.json").exists())
        rid = json.loads(
            (self.root / ".gg-recovery-active.json").read_bytes()
        )["recovery_id"]
        result = gg_lock.upgrade_offline(
            self.root, offline_confirmed=True, resume_id=rid)
        self.assertEqual(result["status"], "completed")
        self._assert_valid_chain(rid)

    def test_crash_after_quarantine_resume(self):
        gg_lock.install_new(self.root)
        self._project()
        # Damaged guard: recovery is needed and quarantine applies.
        os.unlink(self.root / ".gg-lock" / "guard")
        (self.root / ".gg-lock" / "guard").write_bytes(b"X")
        gg_lock._FAULT = _boom("after_quarantine")
        with self.assertRaises(RuntimeError):
            gg_lock.upgrade_offline(self.root, offline_confirmed=True)
        _kill_recovery_caps()
        rid = json.loads(
            (self.root / ".gg-recovery-active.json").read_bytes()
        )["recovery_id"]
        # Physical quarantine done, 10 receipt may exist or not; resume
        # must reach a complete chain either way.
        result = gg_lock.upgrade_offline(
            self.root, offline_confirmed=True, resume_id=rid)
        self.assertEqual(result["status"], "completed")
        self._assert_valid_chain(rid)
        self.assertTrue(
            (self._rdir(rid) / "original-lock" / "protocol.json")
            .exists())

    def test_crash_after_install_resume(self):
        self._project()
        gg_lock._FAULT = _boom("after_install")
        with self.assertRaises(RuntimeError):
            gg_lock.upgrade_offline(self.root, offline_confirmed=True)
        _kill_recovery_caps()
        rid = json.loads(
            (self.root / ".gg-recovery-active.json").read_bytes()
        )["recovery_id"]
        result = gg_lock.upgrade_offline(
            self.root, offline_confirmed=True, resume_id=rid)
        self.assertEqual(result["status"], "completed")
        self._assert_valid_chain(rid)
        st = gg_lock.inspect_lock(self.root)
        self.assertEqual(st["structure"], "ready")

    def test_crash_after_completed_cleanup_only(self):
        self._project()
        gg_lock._FAULT = _boom("after_30-completed")
        with self.assertRaises(RuntimeError):
            gg_lock.upgrade_offline(self.root, offline_confirmed=True)
        _kill_recovery_caps()
        rid = json.loads(
            (self.root / ".gg-recovery-active.json").read_bytes()
        )["recovery_id"]
        result = gg_lock.upgrade_offline(
            self.root, offline_confirmed=True, resume_id=rid)
        self.assertEqual(result["status"], "completed")
        self.assertFalse(
            (self.root / ".gg-recovery-active.json").exists())
        with gg_lock.Lock(self.root):
            pass

    def test_wrong_resume_id_and_busy_guard(self):
        self._project()
        gg_lock._FAULT = _boom("after_active")
        with self.assertRaises(RuntimeError):
            gg_lock.upgrade_offline(self.root, offline_confirmed=True)
        _kill_recovery_caps()
        rid = json.loads(
            (self.root / ".gg-recovery-active.json").read_bytes()
        )["recovery_id"]
        result = gg_lock.upgrade_offline(
            self.root, offline_confirmed=True, resume_id="not-" + rid)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "recovery_id_mismatch")
        # A held recovery guard means only one resume proceeds.
        port = gg_lock._PORT
        fd = port.open_guard(self._rdir(rid) / "guard")
        port.lock(fd)
        try:
            result = gg_lock.upgrade_offline(
                self.root, offline_confirmed=True, resume_id=rid)
            self.assertEqual(result["status"], "blocked")
            self.assertTrue(result["reason"].startswith("recovery_guard"))
        finally:
            port.unlock(fd)
            os.close(fd)
        # After release, resume proceeds.
        result = gg_lock.upgrade_offline(
            self.root, offline_confirmed=True, resume_id=rid)
        self.assertEqual(result["status"], "completed")

    def test_stage_tamper_blocked(self):
        self._project()
        gg_lock._FAULT = _boom("after_20-installed")
        with self.assertRaises(RuntimeError):
            gg_lock.upgrade_offline(self.root, offline_confirmed=True)
        _kill_recovery_caps()
        rid = json.loads(
            (self.root / ".gg-recovery-active.json").read_bytes()
        )["recovery_id"]
        receipt = self._rdir(rid) / "00-prepared.json"
        data = json.loads(receipt.read_bytes())
        data["canonical_sha256"] = "0" * 64
        receipt.write_bytes(gg_fs.canonical_json_bytes(data))
        result = gg_lock.upgrade_offline(
            self.root, offline_confirmed=True, resume_id=rid)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["commit_state"], "not_committed")
        self.assertTrue(
            result["reason"].startswith("stage_field_mismatch"))

    def test_commit_then_indeterminate_when_control_lost(self):
        self._project()
        gg_lock._FAULT = _boom("after_30-completed")
        with self.assertRaises(RuntimeError):
            gg_lock.upgrade_offline(self.root, offline_confirmed=True)
        _kill_recovery_caps()
        rid = json.loads(
            (self.root / ".gg-recovery-active.json").read_bytes()
        )["recovery_id"]
        # Commit happened (30 receipt + new control), then the control
        # vanished: post-commit state cannot be verified.
        shutil.rmtree(self.root / ".gg-lock")
        result = gg_lock.upgrade_offline(
            self.root, offline_confirmed=True, resume_id=rid)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["commit_state"], "indeterminate")

    def test_committed_cleanup_pending(self):
        self._project()
        original_unlink = os.unlink
        calls = {"failed": False}

        def flaky(path, *a, **k):
            if str(path).endswith(".gg-recovery-active.json") and not \
                    calls["failed"]:
                calls["failed"] = True
                raise OSError("simulated unlink failure")
            return original_unlink(path, *a, **k)

        os.unlink = flaky
        try:
            result = gg_lock.upgrade_offline(
                self.root, offline_confirmed=True)
        finally:
            os.unlink = original_unlink
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["commit_state"],
                         "committed_cleanup_pending")
        self.assertTrue(result["cleanup_errors"])

    def test_writes_blocked_during_active(self):
        self._project()
        # Legacy pre-v2 lock dir so recovery applies.
        (self.root / ".gg-lock").mkdir()
        (self.root / ".gg-lock" / "owner.json").write_text("{}")
        # Crash after the new control is installed: valid v2 lock AND
        # an active recovery coexist; writes must still be refused.
        gg_lock._FAULT = _boom("after_install")
        with self.assertRaises(RuntimeError):
            gg_lock.upgrade_offline(self.root, offline_confirmed=True)
        _kill_recovery_caps()
        self.assertEqual(gg_lock.inspect_lock(self.root)["structure"],
                         "ready")
        with self.assertRaises(gg_lock.LockError) as cm:
            gg_lock.Lock(self.root).__enter__()
        self.assertEqual(cm.exception.reason, "recovery_active")

    def test_recovery_capability_not_for_product(self):
        captured = {}
        original = gg_lock._verify_installed_control

        def spy(root, rec_cap):
            captured["cap"] = rec_cap
            return original(root, rec_cap)

        self._project()
        gg_lock._verify_installed_control = spy
        try:
            gg_lock.upgrade_offline(self.root, offline_confirmed=True)
        finally:
            gg_lock._verify_installed_control = original
        cap = captured["cap"]
        with self.assertRaises(gg_lock.LockError):
            gg_lock.assert_held(cap, self.root)

    def test_lineage_wrong_endpoints_and_broken_chain(self):
        proto = gg_lock.install_new(self.root)
        ws = proto["workspace_id"]
        self._project()
        os.unlink(self.root / ".gg-lock" / "guard")
        (self.root / ".gg-lock" / "guard").write_bytes(b"X")
        result = gg_lock.upgrade_offline(self.root, offline_confirmed=True)
        rid = result["recovery_id"]
        res = gg_lock.verify_recovery_lineage(
            self.root, previous_workspace_id="bogus",
            previous_protocol_sha256="0" * 64,
            current_workspace_id=ws,
            current_protocol_sha256=self._protocol_sha())
        self.assertFalse(res["trusted"])
        self.assertEqual(res["reason"], "chain_missing")
        # Break the chain: remove a stage.
        os.unlink(self._rdir(rid) / "10-quarantined.json")
        res = gg_lock.verify_recovery_lineage(
            self.root,
            previous_workspace_id=ws,
            previous_protocol_sha256="x" * 64,
            current_workspace_id=ws,
            current_protocol_sha256=self._protocol_sha())
        self.assertFalse(res["trusted"])
        self.assertEqual(res["reason"], "chain_missing")


if __name__ == "__main__":
    unittest.main()
