"""Policy B output guard on the finance / workbook / DOCX / Office paths
(G-B, 2026-09-25).  Case IDs refer to COMMON-MAJOR-GUARD-B.md.

Path x case matrix (every cell is a subTest in the named test):

| entry point \\ case                   | T1 | T2 | T3 | T4 | T5 | rev |
|--------------------------------------|----|----|----|----|----|-----|
| build (function) / build CLI         | x  | x  | x  |    | x  | x   |
| gg_finance.calculate                 | x  | x  | x  |    | x  |     |
| gg_finance.workbook (single profile) | x  | x  | x  |    | x  | x   |
| gg_school_excel.school_workbook      | x  | x  | x  |    | x  | x   |
| clear CLI / blank_copy               | x  | x  | x  | x  | x  | x   |
| fill CLI / fill_copy                 | x  | x  | x  |    | x  | x   |
| formula_patch CLI / patch_copy       | x  | x  | x  |    | x  | x   |
| print CLI / apply                    | x  | x  | x  |    | x  | x   |
| build_docx CLI / convert             | x  | x  | x  |    | x  | x   |
| gg_office word/excel/batch (+CLI)    | x  | x  | x  |    | x  | x   |

T1: insect binding (declared without the output) -> unsupported_output;
    an unregistered ID -> unknown_major; the registered fruit_trees module
    (no deliverable output declared) -> unsupported_output, directly and
    through every CLI; a fruit ID against a specialty binding ->
    major_binding_mismatch.  No output,
    receipt or staging file; inputs and canonical bytes unchanged.
T2: no ID -> major_id_required; unbound -> major_binding_required; explicit
    null -> major_id_invalid; spec vs context/--major -> major_id_conflict
    (spec-taking entry points only).
T3: explicit specialty + binding -> the existing output, with the passed
    authorization recorded in its receipt/manifest.
T4: inspect is open and grants nothing; the following unmarked clear is held.
T5: a forged ``allowed: True`` in the context mapping is ignored; the map
    hash check still fires under a valid major; every writer holds on its
    own (renamed/intermediate copies do not bypass it).
rev: the canonical binding moves after authorization -> the final file is
    not published (``project_revision_changed``).
school_workbook T3/rev use a synthetic school_17_sheet_v1 spec that
    satisfies gg_school_excel.validate (directly and through
    gg_finance.workbook's school profile).
gg_office T3/rev replace the native engine with a synthetic PDF writer
    (no Office is launched); T1/T2 assert the engine and staging are never
    reached.
"""
import io
import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

from tests._harness import (
    SCRIPTS, ContractCase, bind_major, runtime, write_text,
)

SPECIALTY = "specialty_crops"
INSECTS = "industrial_insects"
FRUIT = "fruit_trees"
# Registered majors are specialty_crops, industrial_insects, fruit_trees.
UNREGISTERED = "hort_env_systems"


def _cli(script, *args):
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / script), *map(str, args)],
        capture_output=True, text=True)


def _finance_spec(**over):
    spec = dict(
        profile="single_annual_cash_v1", crops=["합성작목"],
        accounting_basis="cash_pre_tax_no_inventory",
        source_refs=["synthetic:합성-원답변"], unit="원", quantity_unit="kg",
        land="100", facility="300", equity="200", loan="200", loan_rate=".05",
        discount_rate=".05", salvage="0",
        investment_basis="school_farm_new_business",
        owner_labor_in_costs=False, start_year=2030, years=3, life=3,
        grace=0, term=3, repayment="equal_principal",
        periods=[dict(year=2030 + i, quantity="100", sold="90", loss="10",
                      price="5", variable_cost="1", fixed_cost="100",
                      household="50") for i in range(3)],
    )
    spec.update(over)
    return spec


def _school_spec(**over):
    """Synthetic school_17_sheet_v1 spec satisfying gg_school_excel.validate
    (no real school or student data)."""
    spec = dict(
        profile="school_17_sheet_v1", writing_year=2026,
        source_refs=["synthetic:합성-원답변"], unit="천원", quantity_unit="kg",
        production_evidence={k: "합성 근거" for k in
                             ("area", "seedlings", "yield", "growth",
                              "commodity")},
        crops=["합성작목"], area_m2="1000", production_kg="2000",
        purchase_price="3", direct_price="5", purchase_share="0.5",
        direct_share="0.5", land="0", facility="10000", equipment="2000",
        equity="6000", loan="6000", loan_rate="0.02", salvage="1000",
        materials="500", labor="800", packing="100", transport="100",
        household="2000", repair_facility_rate="0.01",
        repair_equipment_rate="0.02", utility_per_10a="50",
        inflation="1.02", grace=1, term=5, life=10)
    spec.update(over)
    return spec


class _Base(ContractCase):
    def setUp(self):
        self.mc = runtime("gg_major_contract")
        self.core = runtime("gg_core")

    def _project(self, major=SPECIALTY):
        root = self.make_project()
        if major is not None:
            bind_major(root, major)
        return root

    def _ctx(self, root, major=None):
        return self.mc.output_context(root, major)

    def _fruit_project(self):
        """A project bound to the real registered fruit_trees module."""
        return self._project(FRUIT)

    def _registry_for(self, label):
        """Every case uses the shipped default registry (fruit_trees is a
        registered peer, so CLI and in-process paths see the same module)."""
        return mock.patch.object(self.mc, "default_registry",
                                 self.mc.default_registry)

    def _swap_to_insects(self, root):
        fact = dict(self.core.load(root)["facts"]["selected_major"])
        fact.update(value=INSECTS, module_version="0.1.0")
        self.core.apply(root, {"request_id": "swap", "ops": [
            {"collection": "facts", "value": fact}]},
            self.core.load(root)["revision"])

    def _xlsx(self, path, *, formula=False):
        from openpyxl import Workbook
        wb = Workbook()
        wb.active.title = "Visible"
        wb.active["A1"] = "header"
        wb.active["B2"] = 7
        if formula:
            wb.active["C3"] = "=1+1"
        wb.save(path)
        return Path(path)

    def _held(self, reason, fn, *args, **kw):
        with self.assertRaises(self.mc.OutputHeldError) as caught:
            fn(*args, **kw)
        self.assertEqual(caught.exception.reason, reason,
                         caught.exception.detail)
        return caught.exception

    def _cli_held(self, result, reason):
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["reason"], reason,
                         result.stdout)


class WorkbookWriterCase(_Base):
    """One synthetic setup per writer: ``run(ctx)`` calls the direct
    function into ``out``; ``cli(project, major)`` runs the CLI."""

    def _writers(self, work):
        tpl = runtime("gg_excel_template")
        fill = runtime("gg_excel_fill")
        patch = runtime("gg_excel_formula_patch")
        printing = runtime("gg_excel_print")
        src = self._xlsx(work / "source.xlsx", formula=True)
        clear_map = work / "clear-map.json"
        clear_map.write_text(json.dumps({
            "schema": "gg-xlsx-template-map/v1",
            "source": {"sha256": tpl.sha256(src)},
            "layout": {"variant": "unknown",
                       "mapOrigin": "reviewed_custom"},
            "entries": [{"sheet": "Visible", "cell": "B2", "action": "clear",
                         "semanticField": "합성", "reason": "합성",
                         "explicit": True}]}, ensure_ascii=False),
            encoding="utf-8")
        fill_map = work / "fill-map.json"
        fill_map.write_text(json.dumps({
            "schema": "gg-xlsx-fill-map/v1",
            "template": {"sha256": tpl.sha256(src)},
            "entries": [{"sheet": "Visible", "cell": "B2", "role": "input",
                         "period": "2030년", "source_note": "합성",
                         "editable": True}]}, ensure_ascii=False),
            encoding="utf-8")
        values = work / "values.json"
        values.write_text(json.dumps(
            {"schema": "gg-xlsx-fill-values/v1", "values": []}),
            encoding="utf-8")
        patch_map = work / "patch-map.json"
        patch_map.write_text(json.dumps({
            "schema": patch.MAP_SCHEMA,
            "source": {"sha256": tpl.sha256(src)},
            "patches": [{"sheet": "Visible", "cell": "C3",
                         "expected_formula": "=1+1", "new_formula": "=1+2",
                         "reason": "합성 보정",
                         "evidence_ref": {"source_id": "s", "revision": 1,
                                          "locator": "합성"}}]},
            ensure_ascii=False), encoding="utf-8")
        print_map = work / "print-map.json"
        print_map.write_text(json.dumps({
            "schema": printing.SCHEMA,
            "source": {"sha256": printing.sha256(src)},
            "sheets": [{"sheet": "Visible", "reason": "합성", "fit_width": 1,
                        "fit_height": 0}]}, ensure_ascii=False),
            encoding="utf-8")
        return {
            "clear": dict(
                run=lambda ctx, out: tpl.blank_copy(src, clear_map, out,
                                                    context=ctx),
                cli=lambda out, *extra: _cli(
                    "gg_excel_template.py", "clear", "--source", src,
                    "--map", clear_map, "--out", out,
                    "--receipt", str(out) + ".receipt.json", *extra),
                key="majorAuthorization"),
            "fill": dict(
                run=lambda ctx, out: fill.fill_copy(src, fill_map, values,
                                                    out, context=ctx),
                cli=lambda out, *extra: _cli(
                    "gg_excel_fill.py", "--template", src, "--map", fill_map,
                    "--values", values, "--out", out,
                    "--receipt", str(out) + ".receipt.json", *extra),
                key="majorAuthorization"),
            "formula_patch": dict(
                run=lambda ctx, out: patch.patch_copy(src, patch_map, out,
                                                      context=ctx),
                cli=lambda out, *extra: _cli(
                    "gg_excel_formula_patch.py", "--source", src,
                    "--map", patch_map, "--out", out,
                    "--receipt", str(out) + ".receipt.json", *extra),
                key="majorAuthorization"),
            "print": dict(
                run=lambda ctx, out: printing.apply(
                    src, print_map, out, Path(str(out) + ".receipt.json"),
                    context=ctx),
                cli=lambda out, *extra: _cli(
                    "gg_excel_print.py", "--source", src, "--map", print_map,
                    "--out", out, "--receipt", str(out) + ".receipt.json",
                    *extra),
                key="majorAuthorization"),
        }

    def _work(self):
        work = self.make_project() / "work"   # plain folder, not a project
        work.mkdir()
        return work

    def _no_outputs(self, work, out):
        leftovers = [p.name for p in work.iterdir()
                     if p.name.startswith((out.name, "." + out.name))]
        self.assertEqual(leftovers, [])

    def test_t1_t2_writers_hold_before_any_file(self):
        work = self._work()
        writers = self._writers(work)
        insect = self._project(INSECTS)
        unbound = self._project(None)
        bound = self._project()
        fruit = self._fruit_project()
        cases = (
            ("t1-insect", insect, INSECTS, "unsupported_output"),
            ("t1-unregistered", bound, UNREGISTERED, "unknown_major"),
            ("t1-fruit-mismatch", bound, FRUIT, "major_binding_mismatch"),
            ("t1-fruit-registered", fruit, FRUIT, "unsupported_output"),
            ("t2-no-id", bound, None, "major_id_required"),
            ("t2-unbound", unbound, SPECIALTY, "major_binding_required"),
        )
        inputs = {p.name: p.read_bytes() for p in work.iterdir()}
        projects = {r: self._tree_bytes(r) for r in (insect, unbound, bound,
                                                     fruit)}
        for name, writer in writers.items():
            for label, root, major, reason in cases:
                with self.subTest(writer=name, case=label, via="direct"), \
                        self._registry_for(label):
                    out = work / ("%s-%s.xlsx" % (name, label))
                    self._held(reason, writer["run"], self._ctx(root, major),
                               out)
                    self._no_outputs(work, out)
                with self.subTest(writer=name, case=label, via="cli"):
                    out = work / ("%s-%s-cli.xlsx" % (name, label))
                    extra = ["--project", root]
                    if major is not None:
                        extra += ["--major", major]
                    self._cli_held(writer["cli"](out, *extra), reason)
                    self._no_outputs(work, out)
            with self.subTest(writer=name, case="t2-null"):
                out = work / ("%s-null.xlsx" % name)
                self._held("major_id_invalid", writer["run"],
                           self.mc.OutputContext(bound, None), out)
            with self.subTest(writer=name, case="no-context"):
                out = work / ("%s-noctx.xlsx" % name)
                self._held("output_context_required", writer["run"], None,
                           out)
                self._cli_held(writer["cli"](out, "--major", SPECIALTY),
                               "output_context_required")
                self._no_outputs(work, out)
        self.assertEqual({p.name: p.read_bytes() for p in work.iterdir()},
                         inputs)
        for root, before in projects.items():
            self.assertEqual(self._tree_bytes(root), before)

    def test_t3_specialty_writes_with_authorization(self):
        work = self._work()
        root = self._project()
        for name, writer in self._writers(work).items():
            with self.subTest(writer=name, via="direct"):
                out = work / ("%s-ok.xlsx" % name)
                receipt = writer["run"](self._ctx(root, SPECIALTY), out)
                self.assertTrue(out.exists())
                self.assertEqual(receipt[writer["key"]]["major_id"],
                                 SPECIALTY)
            with self.subTest(writer=name, via="cli"):
                out = work / ("%s-cli-ok.xlsx" % name)
                result = writer["cli"](out, "--project", root,
                                       "--major", SPECIALTY)
                self.assertEqual(result.returncode, 0,
                                 result.stdout + result.stderr)
                receipt = json.loads(Path(str(out) + ".receipt.json")
                                     .read_text(encoding="utf-8"))
                self.assertEqual(receipt[writer["key"]]["project_revision"],
                                 self.core.load(root)["revision"])

    def test_t4_inspect_is_open_but_grants_nothing(self):
        tpl = runtime("gg_excel_template")
        work = self._work()
        src = self._xlsx(work / "source.xlsx")
        result = _cli("gg_excel_template.py", "inspect", "--source", src,
                      "--out-map", work / "map.json",
                      "--report", work / "report.json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((work / "map.json").exists())
        root = self._project()
        self._cli_held(_cli("gg_excel_template.py", "clear", "--source", src,
                            "--map", work / "map.json",
                            "--out", work / "blank.xlsx",
                            "--project", root), "major_id_required")
        self.assertFalse((work / "blank.xlsx").exists())
        del tpl

    def test_t5_forged_context_and_hash_checks(self):
        work = self._work()
        unbound = self._project(None)
        bound = self._project()
        writers = self._writers(work)
        for name, writer in writers.items():
            with self.subTest(writer=name, case="forged"):
                out = work / ("%s-forged.xlsx" % name)
                self._held("major_binding_required", writer["run"],
                           {"project_root": unbound, "major_id": SPECIALTY,
                            "allowed": True, "authorized": True}, out)
                self._no_outputs(work, out)
        # Under a valid major the existing source-hash check still fires.
        (work / "source.xlsx").write_bytes(
            (work / "source.xlsx").read_bytes() + b"\0")
        for name, writer in writers.items():
            with self.subTest(writer=name, case="hash"):
                out = work / ("%s-hash.xlsx" % name)
                with self.assertRaises((ValueError, RuntimeError, OSError)):
                    writer["run"](self._ctx(bound, SPECIALTY), out)
                self.assertFalse(out.exists())

    def test_rev_binding_change_before_publish_blocks_output(self):
        work = self._work()
        for name, writer in self._writers(work).items():
            with self.subTest(writer=name):
                root = self._project()
                real = self.mc.authorize_output
                fired = []

                def authorize_then_swap(*args, _root=root, **kw):
                    result = real(*args, **kw)
                    if not fired:
                        fired.append(1)
                        self._swap_to_insects(_root)
                    return result

                out = work / ("%s-raced.xlsx" % name)
                with mock.patch.object(self.mc, "authorize_output",
                                       authorize_then_swap):
                    self._held("project_revision_changed", writer["run"],
                               self._ctx(root, SPECIALTY), out)
                self._no_outputs(work, out)


    def test_rev_binding_move_while_receipt_is_staged(self):
        """The CLI reconfirms after the receipt is staged, right before the
        first final replace: a binding removed while the receipt is being
        written leaves neither output nor receipt (exit 2)."""
        work = self._work()
        writers = self._writers(work)
        entry = {
            "clear": runtime("gg_excel_template").cli,
            "fill": runtime("gg_excel_fill").cli,
            "formula_patch": runtime("gg_excel_formula_patch").cli,
            "print": runtime("gg_excel_print").main,
        }
        real_write = Path.write_text
        for name, cli in entry.items():
            with self.subTest(writer=name):
                root = self._project()
                out = work / ("%s-staged.xlsx" % name)
                # Reuse the writer's CLI argument list, run in-process.
                captured = []
                with mock.patch.object(subprocess, "run",
                                       lambda args, **kw: captured.append(
                                           [str(a) for a in args[3:]])):
                    writers[name]["cli"](out, "--project", root,
                                         "--major", SPECIALTY)
                args = captured[0]
                fired = []

                def write_then_swap(path, *a, _root=root, **kw):
                    result = real_write(path, *a, **kw)
                    if ".receipt.json" in path.name and not fired:
                        fired.append(1)
                        self._swap_to_insects(_root)
                    return result

                stdout = io.StringIO()
                with mock.patch.object(Path, "write_text", write_then_swap), \
                        mock.patch.object(sys, "stdout", stdout):
                    code = cli(args)
                self.assertEqual(fired, [1])
                self.assertEqual(code, 2, stdout.getvalue())
                self.assertEqual(json.loads(stdout.getvalue())["reason"],
                                 "project_revision_changed")
                self._no_outputs(work, out)


class FinanceAndDocxGuardTests(_Base):
    def _build_cli(self, root, *extra):
        return _cli("build_excel_finance_template.py", root,
                    "--input", "spec.json", "--out", "out.xlsx", *extra)

    def test_t1_t2_t5_spec_taking_entry_points(self):
        finance = runtime("gg_finance")
        school = runtime("gg_school_excel")
        build = runtime("build_excel_finance_template")
        insect, unbound, bound = (self._project(INSECTS),
                                  self._project(None), self._project())
        fruit = self._fruit_project()
        cases = (
            ("t1-insect", insect, INSECTS, {}, "unsupported_output"),
            ("t1-unregistered", bound, UNREGISTERED, {}, "unknown_major"),
            ("t1-fruit-mismatch", bound, FRUIT, {},
             "major_binding_mismatch"),
            ("t1-fruit-registered", fruit, FRUIT, {}, "unsupported_output"),
            ("t2-no-id", bound, None, {}, "major_id_required"),
            ("t2-unbound", unbound, SPECIALTY, {}, "major_binding_required"),
            ("t2-null", bound, None, {"major_id": None}, "major_id_invalid"),
            ("t2-conflict", bound, SPECIALTY, {"major_id": INSECTS},
             "major_id_conflict"),
        )
        for label, root, major, extra, reason in cases:
            with self._registry_for(label):
                spec = _finance_spec(**extra)
                ctx = self._ctx(root, major)
                before = self._tree_bytes(root)
                with self.subTest(case=label, entry="calculate"):
                    self._held(reason, finance.calculate, spec, context=ctx)
                with self.subTest(case=label, entry="workbook"):
                    self._held(reason, finance.workbook, spec,
                               Path(root) / "wb.xlsx", context=ctx)
                with self.subTest(case=label, entry="school_workbook"):
                    self._held(reason, school.school_workbook,
                               dict(spec, profile="school_17_sheet_v1"),
                               Path(root) / "school.xlsx", context=ctx)
                with self.subTest(case=label, entry="build"):
                    write_text(root, "spec.json",
                               json.dumps(spec, ensure_ascii=False))
                    before = self._tree_bytes(root)
                    self._held(reason, build.build, root, "spec.json",
                               "out.xlsx", major_id=major)
                    extra_args = ["--major", major] if major else []
                    self._cli_held(self._build_cli(root, *extra_args),
                                   reason)
                self.assertEqual(self._tree_bytes(root), before)
        with self.subTest(case="t5-forged"):
            self._held("major_binding_required", finance.calculate,
                       _finance_spec(major_id=SPECIALTY),
                       context={"project_root": unbound, "allowed": True})
        with self.subTest(case="no-context"):
            self._held("output_context_required", finance.calculate,
                       _finance_spec(major_id=SPECIALTY))

    def test_t3_specialty_calculates_and_builds(self):
        finance = runtime("gg_finance")
        build = runtime("build_excel_finance_template")
        root = self._project()
        result = finance.calculate(_finance_spec(),
                                   context=self._ctx(root, SPECIALTY))
        self.assertEqual(result["status"], "calculated")
        self.assertEqual(result, finance._calculate(_finance_spec()))
        finance.workbook(_finance_spec(major_id=SPECIALTY),
                         Path(root) / "wb.xlsx", context=self._ctx(root))
        manifest = json.loads((Path(root) / "wb.manifest.json")
                              .read_text(encoding="utf-8"))
        self.assertEqual(manifest["major_authorization"]["major_id"],
                         SPECIALTY)
        write_text(root, "spec.json",
                   json.dumps(_finance_spec(), ensure_ascii=False))
        value = build.build(root, "spec.json", "b.xlsx", major_id=SPECIALTY)
        self.assertEqual(value["status"], "calculated")
        cli = self._build_cli(root, "--major", SPECIALTY)
        self.assertEqual(cli.returncode, 0, cli.stdout + cli.stderr)
        # composite_contract is an input-contract query, not a calculation.
        self.assertIn("status", finance.composite_contract(_finance_spec()))

    def test_t3_rev_school_17_sheet(self):
        school = runtime("gg_school_excel")
        finance = runtime("gg_finance")
        root = self._project()
        ctx = self._ctx(root, SPECIALTY)
        for label, build in (
                ("direct", lambda path: school.school_workbook(
                    _school_spec(), path, context=ctx)),
                ("via_finance", lambda path: finance.workbook(
                    _school_spec(), path, context=ctx))):
            with self.subTest(path=label):
                out = Path(root) / ("school-%s.xlsx" % label)
                build(out)
                self.assertTrue(out.exists())
                manifest = json.loads(out.with_suffix(".manifest.json")
                                      .read_text(encoding="utf-8"))
                self.assertEqual(manifest["major_authorization"]["major_id"],
                                 SPECIALTY)
        real = self.mc.authorize_output

        def authorize_then_swap(*args, **kw):
            result = real(*args, **kw)
            self._swap_to_insects(root)
            return result

        with mock.patch.object(self.mc, "authorize_output",
                               authorize_then_swap):
            self._held("project_revision_changed", school.school_workbook,
                       _school_spec(), Path(root) / "school-raced.xlsx",
                       context=ctx)
        self.assertFalse((Path(root) / "school-raced.xlsx").exists())

    def test_rev_workbook_binding_change_blocks_save(self):
        finance = runtime("gg_finance")
        root = self._project()
        real = self.mc.authorize_output

        def authorize_then_swap(*args, **kw):
            result = real(*args, **kw)
            self._swap_to_insects(root)
            return result

        with mock.patch.object(self.mc, "authorize_output",
                               authorize_then_swap):
            self._held("project_revision_changed", finance.workbook,
                       _finance_spec(), Path(root) / "wb.xlsx",
                       context=self._ctx(root, SPECIALTY))
        self.assertFalse((Path(root) / "wb.xlsx").exists())

    def test_docx_convert_and_cli(self):
        build = runtime("build_docx")
        md = "# 제목\n\n문단.\n"
        insect, bound = self._project(INSECTS), self._project()
        for label, root, major, reason in (
                ("t1-insect", insect, INSECTS, "unsupported_output"),
                ("t1-unregistered", bound, UNREGISTERED, "unknown_major"),
                ("t1-fruit-mismatch", bound, FRUIT, "major_binding_mismatch"),
                ("t1-fruit-registered", self._fruit_project(), FRUIT,
                 "unsupported_output"),
                ("t2-no-id", bound, None, "major_id_required"),
                ("t2-unbound", self._project(None), SPECIALTY,
                 "major_binding_required")):
            with self.subTest(case=label):
                self._held(reason, build.convert, md,
                           context=self._ctx(root, major))
                write_text(root, "body.md", md)
                before = self._tree_bytes(root)
                args = [root, "--in", "body.md", "--out", "out.docx"]
                if major:
                    args += ["--major", major]
                self._cli_held(_cli("build_docx.py", *args), reason)
                self.assertEqual(self._tree_bytes(root), before)
        self._held("output_context_required", build.convert, md)
        self._held("major_binding_required", build.convert, md,     # T5
                   context={"project_root": self._project(None),
                            "major_id": SPECIALTY, "allowed": True})
        doc = build.convert(md, context=self._ctx(bound, SPECIALTY))
        self.assertTrue(doc.paragraphs)
        write_text(bound, "body.md", md)
        ok = _cli("build_docx.py", bound, "--in", "body.md", "--out",
                  "out.docx", "--major", SPECIALTY)
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        manifest = json.loads((Path(bound) / "out.manifest.json")
                              .read_text(encoding="utf-8"))
        self.assertEqual(manifest["major_authorization"]["major_id"],
                         SPECIALTY)

    def test_rev_docx_binding_change_blocks_save(self):
        root = self._project()
        write_text(root, "body.md", "# 제목\n")
        build = runtime("build_docx")
        real_convert = build.convert

        def convert_then_swap(*args, **kw):
            doc = real_convert(*args, **kw)
            self._swap_to_insects(root)
            return doc

        with mock.patch.object(build, "convert", convert_then_swap), \
                mock.patch.object(sys, "argv", ["build_docx.py", str(root),
                                                "--in", "body.md", "--out",
                                                "out.docx", "--major",
                                                SPECIALTY]):
            stdout = io.StringIO()
            with mock.patch.object(sys, "stdout", stdout):
                code = build.main()
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stdout.getvalue())["reason"],
                         "project_revision_changed")
        self.assertFalse((Path(root) / "out.docx").exists())


class OfficeGuardTests(_Base):
    """gg_office: the guard runs before staging or any Office engine."""

    def test_office_entry_points_hold_before_engine(self):
        office = runtime("gg_office")
        from docx import Document
        insect, bound, unbound = (self._project(INSECTS), self._project(),
                                  self._project(None))
        work = self.make_project() / "office"
        work.mkdir()
        docx_in = work / "논문.docx"
        Document().save(docx_in)
        xlsx_in = self._xlsx(work / "재무.xlsx")
        cases = (("t1-insect", insect, INSECTS, "unsupported_output"),
                 ("t1-unregistered", bound, UNREGISTERED, "unknown_major"),
                 ("t1-fruit-mismatch", bound, FRUIT, "major_binding_mismatch"),
                 ("t1-fruit-registered", self._fruit_project(), FRUIT,
                  "unsupported_output"),
                 ("t2-no-id", bound, None, "major_id_required"),
                 ("t2-unbound", unbound, SPECIALTY,
                  "major_binding_required"))
        engines = mock.patch.multiple(
            office, _run_word_engine=mock.DEFAULT,
            _run_excel_engine=mock.DEFAULT, stage_job=mock.DEFAULT)
        with engines as called:
            for label, root, major, reason in cases:
                ctx = self._ctx(root, major)
                out = work / ("out-" + label)
                with self.subTest(case=label, entry="word"):
                    self._held(reason, office.process_word, docx_in, out,
                               context=ctx)
                with self.subTest(case=label, entry="excel"):
                    self._held(reason, office.process_excel, xlsx_in, out,
                               context=ctx)
                with self.subTest(case=label, entry="batch"):
                    self._held(reason, office.process_batch,
                               [docx_in, xlsx_in], out, context=ctx)
                with self.subTest(case=label, entry="cli"):
                    args = ["word", str(docx_in), "--out-dir", str(out),
                            "--project", str(root)]
                    if major:
                        args += ["--major", major]
                    stdout = io.StringIO()
                    with mock.patch.object(sys, "stdout", stdout):
                        code = office.main(args)
                    self.assertEqual(code, 2)
                    self.assertEqual(json.loads(stdout.getvalue())["reason"],
                                     reason)
                self.assertFalse(out.exists())
            self._held("output_context_required", office.process_word,
                       docx_in, work / "none")
            for name in ("_run_word_engine", "_run_excel_engine",
                         "stage_job"):
                self.assertFalse(called[name].called, name)
        # The specialty-bound batch passes the guard for both files and
        # reaches staging (stubbed); nothing is published here.
        self.assertFalse(hasattr(office, "run_word_engine"))
        self.assertFalse(hasattr(office, "publish_outputs"))


class OfficePublishGuardTests(_Base):
    """gg_office with the native engine replaced by a synthetic PDF writer
    (no Office is launched): CLI coverage, spec IDs, the recorded
    authorization, and a binding move on every post-engine branch."""

    def setUp(self):
        super().setUp()
        self.office = runtime("gg_office")
        from docx import Document
        self.work = self.make_project() / "office"
        self.work.mkdir()
        self.docx_in = self.work / "논문.docx"
        doc = Document()
        doc.add_paragraph("본문")
        doc.save(self.docx_in)
        self.xlsx_in = self._xlsx(self.work / "재무.xlsx")

    def _fake_engine(self, *, returncode=0, before_return=None,
                     timeout=False):
        def engine(staged_work, staged_pdf, timeout_s=90, **_):
            from pypdf import PdfWriter
            writer = PdfWriter()
            writer.add_blank_page(width=595, height=842)
            with open(staged_pdf, "wb") as handle:
                writer.write(handle)
            if before_return:
                before_return()
            if timeout:
                raise TimeoutError("synthetic timeout")
            return subprocess.CompletedProcess(["engine"], returncode,
                                               "ok", "err")
        return engine

    def _run(self, engine_kind, label, root, major, **engine_kw):
        """Run one Office entry with only the named engine replaced; the
        other engine is a strict mock that must never be called."""
        ws = self.work / ("ws-" + label)
        out = self.work / ("out-" + label)
        engine = mock.Mock(side_effect=self._fake_engine(**engine_kw))
        other = mock.Mock(side_effect=AssertionError("wrong engine"))
        word, excel = (engine, other) if engine_kind == "word" \
            else (other, engine)
        ctx = self._ctx(root, major)
        try:
            with mock.patch.object(self.office, "_run_word_engine", word), \
                    mock.patch.object(self.office, "_run_excel_engine",
                                      excel):
                if engine_kind == "word":
                    return (self.office.process_word(
                        self.docx_in, out, workspace=ws, context=ctx),
                        ws, out)
                return (self.office.process_excel(
                    self.xlsx_in, out, workspace=ws, context=ctx), ws, out)
        finally:
            self.assertEqual(engine.call_count, 1, engine_kind)
            self.assertEqual(other.call_count, 0, engine_kind)

    def test_t3_publish_records_authorization(self):
        for entry, kind in (("word", self.mc.OUTPUT_SCHOOL_PAPER),
                            ("excel", self.mc.OUTPUT_SCHOOL_WORKBOOK)):
            with self.subTest(entry=entry):
                root = self._project()
                result, ws, out = self._run(entry, entry, root, SPECIALTY)
                self.assertEqual(result["status"], "converted", result)
                recorded = result["major_authorization"]
                self.assertEqual({a["output"] for a in recorded}, {kind})
                manifest = json.loads(
                    next(out.glob("*.office.manifest.json"))
                    .read_text(encoding="utf-8"))
                self.assertEqual(manifest["major_authorization"], recorded)

    def test_rev_binding_move_on_every_post_engine_branch(self):
        """Success, engine failure and timeout branches all reconfirm
        before any receipt: a moved binding removes this call's staging
        and publishes nothing."""
        for entry in ("word", "excel"):
            for label, kw in (("success", {}),
                              ("engine_fail", {"returncode": 3}),
                              ("timeout", {"timeout": True})):
                with self.subTest(entry=entry, branch=label):
                    root = self._project()
                    kw = dict(kw, before_return=lambda r=root:
                              self._swap_to_insects(r))
                    with self.assertRaises(self.mc.OutputHeldError) as held:
                        self._run(entry, entry + "-" + label, root,
                                  SPECIALTY, **kw)
                    self.assertEqual(held.exception.reason,
                                     "project_revision_changed")
                    ws = self.work / ("ws-%s-%s" % (entry, label))
                    staged = [p for p in ws.rglob("*") if p.is_file()] \
                        if ws.exists() else []
                    self.assertEqual(staged, [])
                    self.assertFalse(
                        (self.work / ("out-%s-%s" % (entry, label))).exists())

    def test_excel_spec_ids_join_the_guard(self):
        """A46 T2 via ``--spec``: the spec's explicit major joins the check
        before staging (conflict and invalid null both hold)."""
        root = self._project()
        stage = mock.patch.object(self.office, "stage_job")
        for label, spec, reason in (
                ("conflict", {"major_id": INSECTS}, "major_id_conflict"),
                ("null", {"major_id": None}, "major_id_invalid")):
            spec_path = self.work / ("spec-%s.json" % label)
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            with self.subTest(case=label), stage as staged:
                self._held(reason, self.office.process_excel, self.xlsx_in,
                           self.work / "out-spec", spec_file=spec_path,
                           context=self._ctx(root, SPECIALTY))
                self.assertFalse(staged.called)

    def test_cli_word_excel_batch_and_forged_context(self):
        root = self._project()
        stage = mock.patch.object(self.office, "stage_job")
        with stage as staged:
            for args in (["word", str(self.docx_in)],
                         ["excel", str(self.xlsx_in)],
                         ["batch", str(self.docx_in), str(self.xlsx_in)]):
                with self.subTest(cli=args[0]):
                    out = self.work / ("cli-" + args[0])
                    stdout = io.StringIO()
                    with mock.patch.object(sys, "stdout", stdout):
                        code = self.office.main(
                            args + ["--out-dir", str(out),
                                    "--project", str(root)])
                    self.assertEqual(code, 2)
                    self.assertEqual(json.loads(stdout.getvalue())["reason"],
                                     "major_id_required")
                    self.assertFalse(out.exists())
            unbound = self._project(None)
            with self.subTest(case="t5-forged"):
                self._held("major_binding_required", self.office.process_word,
                           self.docx_in, self.work / "forged",
                           context={"project_root": unbound,
                                    "major_id": SPECIALTY, "allowed": True})
            self.assertFalse(staged.called)
