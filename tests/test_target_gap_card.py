"""B2 v6: synthetic publication lifecycle, exact values and no-follow IO."""
import copy
import io
import json
import os
import time
import unittest
from decimal import Decimal
from unittest.mock import patch

from tests._harness import ContractCase, runtime, write_text
from tests import test_finance_gate as finance_fixture
from tests.test_student_language import FORBIDDEN_STUDENT_TERMS


class TargetGapCardTests(ContractCase):
    def setUp(self):
        self.core = runtime("gg_core")
        self.gap = runtime("gg_target_gap")
        self.pub = runtime("gg_publication")
        self.notices = runtime("gg_notices")
        self.lock = runtime("gg_lock")
        self.fixture = finance_fixture.BodyFinanceGateTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.fixture.record("surplus", "18000000")
        self.spec = {"author": "합성", "writing_year": 2026, "start_year": 2026,
                     "target_profit_thousand": "30000",
                     "school_profile": {"mode": "school", "school": "합성대학교",
                                        "department": "특용작물학과"}}
        self.save_spec()

    def save_spec(self, raw=None):
        write_text(self.root, "입력/논문.json", raw if raw is not None else
                   json.dumps(self.spec, ensure_ascii=False))

    def publish(self, out="build/out.md", **kwargs):
        return self.core.paper(self.root, "입력/논문.json",
                               (self.root / "입력/논문.json").read_bytes(), out,
                               major_id="specialty_crops", **kwargs)

    def read(self):
        return self.gap.read(self.root, self.core.load(self.root))

    def cards(self):
        return [r for r in json.loads((self.root / ".knuaf-notices.json").read_text())["notices"]
                if r["kind"] == self.gap.KIND]

    def refused(self, reason=None, **kwargs):
        with self.assertRaises(self.core.OperationError) as caught:
            self.publish(**kwargs)
        self.assertEqual("not_committed", caught.exception.result["commit_state"])
        if reason:
            self.assertEqual(reason, caught.exception.result["reason"])

    def test_success_edit_commit_and_new_publication(self):
        self.publish()
        old = self.cards()[0]
        self.assertIn("12,000,000", old["title"])
        self.spec["target_profit_thousand"] = "40000"
        self.save_spec()
        self.assertEqual({"status": "none", "reason": "spec_changed"}, self.read())
        self.fixture.apply([{"collection": "tasks", "value": {"id": "revision-step"}}])
        self.assertEqual([], self.cards())
        self.publish(out="build/new.md")
        self.assertIn("22,000,000", self.cards()[0]["title"])
        self.assertNotEqual(old["card_key"], self.cards()[0]["card_key"])

    def test_refusal_refreshes_once_and_removes_changed_card(self):
        self.publish()
        old = self.cards()[0]
        write_text(self.root, "build/conflict.md", "keep")
        with patch.object(self.core, "_rewrite_sidecars", wraps=self.core._rewrite_sidecars) as rewrite:
            self.refused("requested_output_conflict", out="build/conflict.md")
        self.assertEqual(1, rewrite.call_count)
        self.assertEqual(old, self.cards()[0])
        self.spec["target_profit_thousand"] = "40000"
        self.save_spec()
        with patch.object(self.core, "_rewrite_sidecars", wraps=self.core._rewrite_sidecars) as rewrite:
            self.refused(out="build/new.md")
        self.assertEqual(1, rewrite.call_count)
        self.assertEqual([], self.cards())

    def test_preflight_refusals_and_busy_retry(self):
        self.publish()
        for raw, reason in (("{", "invalid_spec"),
                            (json.dumps(dict(self.spec, target_profit_thousand="bad")), "native_field_invalid")):
            with self.subTest(reason=reason):
                self.save_spec(raw)
                before = (self.root / ".knuaf-notices.json").read_bytes()
                with self.lock.Lock(self.root):
                    with patch.object(self.core, "_rewrite_sidecars") as rewrite:
                        with self.assertRaises(self.lock.LockError):
                            self.publish()
                        self.assertEqual(0, rewrite.call_count)
                self.assertEqual(before, (self.root / ".knuaf-notices.json").read_bytes())
                with patch.object(self.core, "_rewrite_sidecars", wraps=self.core._rewrite_sidecars) as rewrite:
                    self.refused(reason)
                self.assertEqual(1, rewrite.call_count)
                self.assertEqual([], self.cards())

    def test_absent_project_preserves_invalid_spec(self):
        root = self.root / "uninitialized"
        root.mkdir()
        with self.assertRaises(self.core.OperationError) as caught:
            self.core.paper(root, "s.json", b"{", "out.md", major_id="specialty_crops")
        self.assertEqual("invalid_spec", caught.exception.result["reason"])

    def test_success_existing_and_cleanup_pending_refresh_once(self):
        with patch.object(self.core, "_rewrite_sidecars", wraps=self.core._rewrite_sidecars) as rewrite:
            self.publish()
        self.assertEqual(1, rewrite.call_count)
        with patch.object(self.core, "_rewrite_sidecars", wraps=self.core._rewrite_sidecars) as rewrite:
            self.assertEqual("existing", self.publish()["status"])
        self.assertEqual(1, rewrite.call_count)
        (self.root / "build/out.md").unlink()
        with patch.object(self.core, "_rewrite_sidecars", wraps=self.core._rewrite_sidecars) as rewrite:
            self.assertEqual("generated", self.publish()["status"])
        self.assertEqual(1, rewrite.call_count)
        (self.root / "build/out.md").unlink()
        with patch.object(self.core, "_requested_publish_or_preserve", side_effect=self.core._op(
                "paper", "requested_output", "committed_cleanup_pending", "publish_cleanup_pending")):
            with patch.object(self.core, "_rewrite_sidecars", wraps=self.core._rewrite_sidecars) as rewrite:
                with self.assertRaises(self.core.OperationError) as caught:
                    self.publish()
        self.assertEqual("committed_cleanup_pending", caught.exception.result["commit_state"])
        self.assertEqual(1, rewrite.call_count)
        self.assertEqual(1, len(self.cards()))

    def test_exact_and_display_contract(self):
        p = self.core.load(self.root)
        p["facts"]["gate-profit"]["value"] = "18000000.5"
        p["sources"]["calculation"]["claims"]["profit"]["value"] = "18000000.5"
        self.publish()
        value = self.gap.read(self.root, p)
        self.assertEqual({"status", "reason", "year", "goal_exact_won", "plan_exact_won",
                          "gap_exact_won", "shown", "card_key", "evidence"}, set(value))
        self.assertEqual(Decimal("18000000.5"), value["plan_exact_won"])
        self.assertEqual(Decimal("11999999.5"), value["gap_exact_won"])
        self.assertEqual(Decimal("11999999"), value["shown"]["gap_won"])
        card = self.gap.row(self.root, p)
        self.assertEqual(value["card_key"], card["card_key"])
        self.assertIn("18,000,001", card["items"][0]["current"])
        self.assertFalse(card["blocking"])
        self.assertFalse(card["dismissible"])
        self.assertEqual([], card["record_refs"])
        self.assertEqual(value["card_key"], card["notice_identity"])
        self.assertEqual(self.core._advice_project_id(self.root), card["project_id"])
        for key in ("state", "redisplay_rule", "receipt_path"):
            self.assertNotIn(key, card)
        visible = json.dumps(card, ensure_ascii=False)
        for term in FORBIDDEN_STUDENT_TERMS:
            self.assertNotIn(term, visible)
        p["facts"]["gate-profit"]["value"] = "29999999.5"
        p["sources"]["calculation"]["claims"]["profit"]["value"] = "29999999.5"
        self.assertEqual({"status": "none", "reason": "gap_not_positive"}, self.gap.read(self.root, p))

    def test_goal_aliases(self):
        for spec, expected in (({}, "goal_missing"), ({"target_profit_thousand": "NaN"}, "goal_missing"),
                ({"target_profit_thousand": "Infinity"}, "goal_missing"),
                ({"target_profit_thousand": "[확인 필요]"}, "goal_missing"),
                ({"target_profit_thousand": "0.0001"}, "goal_not_integer_won"),
                ({"target_profit_thousand": "1", "goal_profit_thousand": "2"}, "goal_ambiguous")):
            self.assertEqual(expected, self.gap._goal_won(spec)[1])
        for spec in ({"goal_profit_thousand": "30,000"},
                     {"target_profit_thousand": "", "goal_profit_thousand": 30000},
                     {"target_profit_thousand": "30000", "goal_profit_thousand": 30000}):
            self.assertEqual((Decimal("30000000"), None), self.gap._goal_won(spec))

    def test_plan_year_body_and_missing_conditions(self):
        self.publish()
        p = self.core.load(self.root)
        p["facts"]["gate-profit"]["period"] = "2031"
        p["facts"]["gate-state"]["period"] = "2031"
        p["sources"]["calculation"]["claims"]["profit"]["period"] = "2031"
        p["sources"]["calculation"]["claims"]["state"]["period"] = "2031"
        self.assertEqual("year_mismatch", self.gap.read(self.root, p)["reason"])
        for amount in ("0", "-1", "30000000", "40000000"):
            p = self.core.load(self.root)
            p["facts"]["gate-profit"]["value"] = amount
            p["sources"]["calculation"]["claims"]["profit"]["value"] = amount
            self.assertEqual("none", self.gap.read(self.root, p)["status"])
        receipt = self.pub.latest_paper_receipt(self.root, max_revision=p["revision"])[1]
        receipt = copy.deepcopy(receipt)
        receipt["request"]["body_sha256"] = "x"
        with patch.object(self.pub, "latest_paper_receipt", return_value=("valid", receipt, {})):
            self.assertEqual("body_replaced", self.read()["reason"])

    def test_spec_no_follow_limits_hash_and_strict_json(self):
        self.publish()
        path = self.root / "입력/논문.json"
        original = path.read_bytes()
        path.unlink()
        outside = self.root / "other.json"
        outside.write_bytes(original)
        for target in (outside, self.root):
            path.symlink_to(target)
            self.assertEqual("spec_unreadable", self.read()["reason"])
            path.unlink()
        path.mkdir()
        self.assertEqual("spec_unreadable", self.read()["reason"])
        path.rmdir()
        if hasattr(os, "mkfifo"):
            os.mkfifo(path)
            self.assertEqual("spec_unreadable", self.read()["reason"])
            path.unlink()
        path.write_bytes(b"x" * (self.gap.SPEC_LIMIT + 1))
        self.assertEqual("spec_unreadable", self.read()["reason"])
        path.write_bytes(original + b" ")
        self.assertEqual("spec_changed", self.read()["reason"])
        receipt = self.pub.latest_paper_receipt(self.root, max_revision=self.core.load(self.root)["revision"])[1]
        for raw in (b'[]', b'{"a":1,"a":2}'):
            path.write_bytes(raw)
            changed = copy.deepcopy(receipt)
            changed["request"]["spec_sha256"] = self.core.digest(raw)
            with patch.object(self.pub, "latest_paper_receipt", return_value=("valid", changed, {})):
                self.assertEqual("spec_rejected", self.read()["reason"])
        path.write_bytes(original)
        with patch.object(self.pub, "_read_file", wraps=self.pub._read_file) as reader:
            self.assertEqual("ok", self.read()["status"])
        self.assertEqual(1, sum(c.args[1] == "입력/논문.json" for c in reader.call_args_list))

    def test_latest_slots_never_fall_back(self):
        self.publish()
        revision = self.core.load(self.root)["revision"]
        top = self.root / "build" / str(revision + 1)
        for shape in ("file", "link", "paper_file", "paper_link", "missing_receipt", "bad_json", "receipt_dir", "receipt_big"):
            with self.subTest(shape=shape):
                if shape == "file":
                    top.write_text("x")
                elif shape == "link":
                    top.symlink_to(self.root)
                else:
                    top.mkdir()
                    paper = top / "paper"
                    if shape == "paper_file":
                        paper.write_text("x")
                    elif shape == "paper_link":
                        paper.symlink_to(self.root)
                    else:
                        paper.mkdir()
                        receipt = paper / ".publication.json"
                        if shape == "bad_json":
                            receipt.write_text("{")
                        elif shape == "receipt_dir":
                            receipt.mkdir()
                        elif shape == "receipt_big":
                            receipt.write_bytes(b"x" * ((1 << 20) + 1))
                self.assertEqual("invalid", self.pub.latest_paper_receipt(self.root, max_revision=revision + 1)[0])
                if top.is_symlink() or top.is_file():
                    top.unlink()
                else:
                    for child in sorted(top.rglob("*"), reverse=True):
                        if child.is_symlink() or child.is_file():
                            child.unlink()
                        else:
                            child.rmdir()
                    top.rmdir()
        top.mkdir()
        self.assertEqual("valid", self.pub.latest_paper_receipt(self.root, max_revision=revision + 1)[0])
        alias = self.root / "build" / ("0" + str(revision))
        alias.mkdir()
        self.assertEqual("rev_ambiguous", self.pub.latest_paper_receipt(self.root, max_revision=revision)[1])

    def test_absent_invalid_and_other_notices_survive_errors(self):
        with patch.object(self.core, "_sidecar_report") as report:
            self.assertEqual({"status": "absent", "reason": "no_publication"}, self.read())
        report.assert_not_called()
        with patch.object(self.pub, "latest_paper_receipt", side_effect=RuntimeError("broken")):
            with patch.object(self.core, "_sidecar_report") as report:
                self.assertEqual({"status": "invalid", "reason": "internal_error"}, self.read())
            report.assert_called_once_with("notice target_gap skipped - internal_error\n")
            marker = {"kind": "other"}
            with patch.object(self.core, "_workbook_notice_row_for_project", return_value=marker):
                self.assertIn(marker, self.notices.notice_rows(self.root))

    def test_build_and_receipt_corruption_reason_codes(self):
        self.publish()
        p = self.core.load(self.root)
        status, receipt, ref = self.pub.latest_paper_receipt(self.root, max_revision=p["revision"])
        self.assertEqual("valid", status)
        receipt_path = self.root / ref["path"]
        original = receipt_path.read_bytes()
        for mutate in (lambda r: r.update(kind="export"),
                       lambda r: r["request"].update(input_revision=p["revision"] + 1),
                       lambda r: r["files"][0].update(sha256="0" * 64)):
            changed = copy.deepcopy(receipt)
            mutate(changed)
            receipt_path.write_text(json.dumps(changed))
            self.assertEqual(("invalid", "receipt_rejected", None),
                             self.pub.latest_paper_receipt(self.root, max_revision=p["revision"]))
        receipt_path.write_bytes(original)
        receipt_path.unlink()
        receipt_path.symlink_to(self.root / "입력/논문.json")
        self.assertEqual("receipt_unreadable", self.read()["reason"])
        receipt_path.unlink()
        receipt_path.write_bytes(original)
        build = self.root / "build"
        moved = self.root / "saved-build"
        build.rename(moved)
        for shape in ("file", "link"):
            if shape == "file":
                build.write_text("not a folder")
            else:
                build.symlink_to(moved)
            self.assertEqual({"status": "invalid", "reason": "build_unreadable"}, self.read())
            build.unlink()
        moved.rename(build)
        artifacts = self.root / ".gg-artifacts"
        if artifacts.exists():
            artifacts.rename(self.root / "saved-artifacts")
        artifacts.symlink_to(self.root / "absent")
        self.assertEqual("ok", self.read()["status"])

    def test_spec_parent_symlink_and_row_exception_stay_local(self):
        self.publish()
        parent = self.root / "입력"
        target = self.root / "다른입력"
        parent.rename(target)
        parent.symlink_to(target)
        self.assertEqual("spec_unreadable", self.read()["reason"])
        parent.unlink()
        target.rename(parent)
        with patch.object(self.gap, "_text", side_effect=ValueError("bad")):
            self.assertIsNone(self.gap.row(self.root, self.core.load(self.root)))

    def test_future_revision_only_is_absent_and_key_uses_display_text(self):
        revision = self.core.load(self.root)["revision"]
        (self.root / "build" / str(revision + 1) / "paper").mkdir(parents=True)
        self.assertEqual(("absent", None, None),
                         self.pub.latest_paper_receipt(self.root, max_revision=revision))
        self.publish()
        before = self.read()
        p = self.core.load(self.root)
        p["revision"] += 1
        after = self.gap.read(self.root, p)
        # A future damaged slot must win, rather than reviving an older card.
        self.assertEqual("invalid", after["status"])
        p["revision"] -= 1
        p["facts"]["gate-profit"]["revision"] += 1
        after = self.gap.read(self.root, p)
        self.assertEqual(before["card_key"], after["card_key"])
        self.assertNotEqual(before["evidence"], after["evidence"])

    def test_build_200_timing(self):
        self.publish()
        for index in range(200):
            (self.root / "build" / str(1000 + index)).mkdir()
        p = self.core.load(self.root)
        p["revision"] = 1200
        started = time.perf_counter()
        self.assertEqual("ok", self.gap.read(self.root, p)["status"])
        print("B2 read 200 build folders seconds:", time.perf_counter() - started)


class BoundedReads(ContractCase):
    def test_posix_bound_default_and_growth(self):
        pub = runtime("gg_publication")
        root = self.make_project()
        write_text(root, "s.json", "abc")
        self.assertEqual(b"abc", pub._read_file(root, "s.json"))
        self.assertEqual(b"abc", pub._read_file(root, "s.json", limit=3))
        with self.assertRaises(ValueError):
            pub._read_file(root, "s.json", limit=2)
        sizes = []
        def growing(fd, size):
            sizes.append(size)
            return b"abcd"[:size]
        with patch.object(pub.os, "read", side_effect=growing):
            with self.assertRaises(ValueError):
                pub._read_file(root, "s.json", limit=3)
        self.assertEqual([4], sizes)

    def test_windows_read_bound_and_forwarding(self):
        win = runtime("gg_winfs")
        pub = runtime("gg_publication")
        root = self.make_project()
        write_text(root, "s", "abcdef")
        sizes = []
        class Handle(io.BytesIO):
            def read(self, size=-1):
                sizes.append(size)
                return super().read(size)
        with patch.object(win, "open_file", return_value=123), patch.object(win, "fd_identity", return_value="id"), \
                patch.object(win.os, "fdopen", return_value=Handle(b"abcdef")), patch.object(win.os, "close"):
            with self.assertRaises(ValueError):
                win.read_bytes(root / "s", limit=3)
        self.assertEqual([4], sizes)
        with patch.object(win, "open_file", return_value=123), patch.object(win, "fd_identity", return_value="id"), \
                patch.object(win.os, "fdopen", return_value=Handle(b"abcdef")), patch.object(win.os, "close"):
            self.assertEqual((b"abcdef", "id"), win.read_bytes(root / "s"))
        with patch.object(pub.sys, "platform", "win32"), patch.object(win, "read_bytes", return_value=(b"abc", "id")) as reader:
            self.assertEqual(b"abc", pub._read_file(root, "s", limit=3))
            reader.assert_called_once_with(root / "s", limit=3)


if __name__ == "__main__":
    unittest.main()
