"""v0.4.8 ⑫: the ``paper()`` rejection JSON names WHAT was wrong.

A malformed spec field used to leave the student with only
``{"reason": "invalid_spec"}`` — the Korean message lived in the
exception string (``raise _op(..., detail=str(error))``) and the CLI
prints ``e.result``, so the actionable part never reached the screen.
The same held for ``native_field_invalid``: the ``issues=`` list is not a
``_result`` key.

The public result contract (API §1) stays closed: only ``detail`` may
carry diagnostics, and only code-defined codes / field names survive
``_diag_safe``.  Raw caller text and paths are still dropped — these
tests assert that too, so widening the allowlist never widens leakage.
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

from tests._harness import (
    ContractCase,
    SCRIPTS,
    bind_major,
    runtime,
    write_text,
)


def _spec(**extra):
    spec = {"author": "합성", "writing_year": 2026, "start_year": 2027,
            "school_profile": {"mode": "school", "school": "합성대학교",
                               "department": "특용작물학과"}}
    spec.update(extra)
    return spec


def _cli_paper(root, *, spec_name="paper-spec.json"):
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / "gg.py"), "paper", str(root),
         "--input", spec_name, "--out", "build/paper.md",
         "--major", "specialty_crops"],
        capture_output=True, text=True, encoding="utf-8")


class PaperDiagnosticContractTests(ContractCase):
    def _project(self):
        root = self.make_project()
        bind_major(root)
        return root

    def _write_spec(self, root, spec):
        write_text(root, "paper-spec.json",
                   json.dumps(spec, ensure_ascii=False))
        return root / "paper-spec.json"

    def _held(self, root, spec):
        core = runtime("gg_core")
        path = self._write_spec(root, spec)
        with self.assertRaises(core.OperationError) as caught:
            core.paper(root, "paper-spec.json", path.read_bytes(),
                       "build/paper.md", major_id="specialty_crops")
        return caught.exception.result

    def _held_raw(self, root, raw):
        """Refuse the exact bytes, so a non-object / duplicate-key spec
        reaches ``load_spec`` unchanged."""
        core = runtime("gg_core")
        write_text(root, "paper-spec.json", raw)
        with self.assertRaises(core.OperationError) as caught:
            core.paper(root, "paper-spec.json",
                       (Path(root) / "paper-spec.json").read_bytes(),
                       "build/paper.md", major_id="specialty_crops")
        return caught.exception.result

    def test_non_integer_start_year_names_the_field(self):
        result = self._held(self._project(), _spec(start_year="2027"))
        self.assertEqual("invalid_spec", result["reason"])
        self.assertEqual({"code": "spec_field_invalid",
                          "field": "start_year"}, result["detail"])

    def test_missing_writing_year_names_the_field(self):
        spec = _spec()
        del spec["writing_year"]
        result = self._held(self._project(), spec)
        self.assertEqual("invalid_spec", result["reason"])
        self.assertEqual({"code": "spec_field_invalid",
                          "field": "writing_year"}, result["detail"])

    def test_out_of_range_year_names_the_field(self):
        result = self._held(self._project(), _spec(writing_year=1800))
        self.assertEqual({"code": "spec_field_invalid",
                          "field": "writing_year"}, result["detail"])
        result = self._held(self._project(), _spec(start_year=12000))
        self.assertEqual({"code": "spec_field_invalid",
                          "field": "start_year"}, result["detail"])

    def test_start_year_checked_after_a_valid_writing_year(self):
        result = self._held(self._project(),
                            _spec(writing_year=2026, start_year=None))
        self.assertEqual({"code": "spec_field_invalid",
                          "field": "start_year"}, result["detail"])

    def test_diagnostic_carries_no_path_or_raw_value(self):
        root = self._project()
        result = self._held(root, _spec(start_year="2027년부터"))
        self.assertEqual({"code": "spec_field_invalid",
                          "field": "start_year"}, result["detail"])
        dumped = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("2027년부터", dumped)
        self.assertNotIn("paper-spec.json", dumped)
        self.assertNotIn(str(root), dumped)

    def test_native_field_names_the_wrong_key(self):
        result = self._held(self._project(),
                            _spec(start_sales_thousand="99999달러"))
        self.assertEqual("native_field_invalid", result["reason"])
        self.assertEqual({"code": "native_field_invalid",
                          "missing": ["start_sales_thousand"]},
                         result["detail"])

    def test_native_alias_key_is_reported_as_written(self):
        result = self._held(self._project(),
                            _spec(sales_thousand="1,2,3"))
        self.assertEqual({"code": "native_field_invalid",
                          "missing": ["sales_thousand"]}, result["detail"])

    def test_native_multiple_fields_follow_alias_order(self):
        result = self._held(self._project(), _spec(
            start_area_m2="약 5000", target_profit_thousand="₩12000"))
        self.assertEqual(
            {"code": "native_field_invalid",
             "missing": ["start_area_m2", "target_profit_thousand"]},
            result["detail"])

    def test_native_diagnostic_carries_no_raw_value(self):
        result = self._held(self._project(),
                            _spec(start_sales_thousand="99999달러"))
        dumped = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("99999달러", dumped)
        self.assertNotIn("단위 없는 십진 숫자여야 함", dumped)

    def test_undeterminable_spec_has_no_detail_key(self):
        root = self._project()
        result = self._held_raw(root, json.dumps([1, 2]))
        self.assertEqual("invalid_spec", result["reason"])
        self.assertNotIn("detail", result)
        # A duplicate JSON key is refused by load_spec — also undeterminable.
        result = self._held_raw(
            root, '{"writing_year": 2026, "writing_year": 2027}')
        self.assertEqual("invalid_spec", result["reason"])
        self.assertNotIn("detail", result)


class PaperCliDiagnosticTests(ContractCase):
    """The real student entry point: ``gg.py paper`` must print the
    diagnostic in its JSON body with exit code 2 (not_committed)."""

    def _project(self):
        root = self.make_project()
        bind_major(root)
        return root

    def _run(self, root, spec):
        write_text(root, "paper-spec.json",
                   json.dumps(spec, ensure_ascii=False))
        result = _cli_paper(root)
        self.assertEqual(2, result.returncode,
                         result.stdout + result.stderr)
        self.assertFalse((Path(root) / "build/paper.md").exists())
        return result, json.loads(result.stdout)

    def test_cli_spec_field_invalid(self):
        result, value = self._run(self._project(), _spec(start_year="2027"))
        self.assertEqual("invalid_spec", value["reason"])
        self.assertEqual({"code": "spec_field_invalid",
                          "field": "start_year"}, value["detail"])

    def test_cli_native_field_invalid(self):
        result, value = self._run(
            self._project(), _spec(start_sales_thousand="99999달러"))
        self.assertEqual("native_field_invalid", value["reason"])
        self.assertEqual({"code": "native_field_invalid",
                          "missing": ["start_sales_thousand"]},
                         value["detail"])
        # The refused value and the Korean exception text stay off-screen.
        self.assertNotIn("99999달러", result.stdout + result.stderr)
        self.assertNotIn("단위 없는 십진 숫자여야 함",
                         result.stdout + result.stderr)

    def _run_raw(self, root, raw):
        write_text(root, "paper-spec.json", raw)
        result = _cli_paper(root)
        self.assertEqual(2, result.returncode,
                         result.stdout + result.stderr)
        self.assertFalse((Path(root) / "build/paper.md").exists())
        return result, json.loads(result.stdout)

    def test_cli_non_object_spec_has_no_detail(self):
        _, value = self._run_raw(self._project(), json.dumps([1, 2]))
        self.assertEqual("invalid_spec", value["reason"])
        self.assertNotIn("detail", value)


class DiagAllowlistTests(ContractCase):
    """``_diag_safe`` keeps only code-defined names — widening the
    allowlist for the new codes/fields must not admit caller text."""

    def test_unknown_missing_names_are_dropped(self):
        core = runtime("gg_core")
        self.assertEqual(
            {"code": "native_field_invalid", "missing": ["start_area_m2"]},
            core._diag_safe(Path("/w3/work"), {
                "code": "native_field_invalid",
                "missing": ["start_area_m2", "/etc/passwd", "학생이름"]}))

    def test_no_surviving_missing_name_leaves_no_missing_key(self):
        core = runtime("gg_core")
        self.assertEqual(
            {"code": "native_field_invalid"},
            core._diag_safe(Path("/w3/work"), {
                "code": "native_field_invalid",
                "missing": ["/etc/passwd", "학생이름"]}))

    def test_new_codes_and_fields_are_allowlisted(self):
        core = runtime("gg_core")
        self.assertEqual(
            {"code": "spec_field_invalid", "field": "writing_year"},
            core._diag_safe(Path("/w3/work"), {
                "code": "spec_field_invalid", "field": "writing_year"}))
        self.assertEqual(
            {"code": "spec_field_invalid", "field": "start_year"},
            core._diag_safe(Path("/w3/work"), {
                "code": "spec_field_invalid", "field": "start_year"}))
        for name in (
            "start_area_m2", "area_m2", "target_area_m2", "goal_area_m2",
            "start_sales_thousand", "sales_thousand",
            "target_sales_thousand", "goal_sales_thousand",
            "start_profit_thousand", "profit_thousand",
            "target_profit_thousand", "goal_profit_thousand",
        ):
            with self.subTest(field=name):
                self.assertEqual(
                    {"missing": [name]},
                    core._diag_safe(Path("/w3/work"), {"missing": [name]}))


if __name__ == "__main__":
    unittest.main()
