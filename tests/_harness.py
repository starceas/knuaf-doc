"""Shared harness for the public validation suite.

Every test imports the deployed runtime exclusively from
``<repo>/skills/knuaf-doc/scripts``.  Modules already imported from any
other location are evicted first, so a polluted ``PYTHONPATH`` cannot
shadow the candidate runtime.  Import failures, missing dependencies and
fixture errors are ordinary test failures — nothing falls back to a
development tree or a snapshot copy.
"""
import ast
import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
SCRIPTS = (REPO_ROOT / "skills" / "knuaf-doc" / "scripts").resolve()
INVENTORY_PATH = TESTS_DIR / "regression_inventory.json"

# Top-level module names shipped by the candidate, discovered from the
# deployed scripts directory itself.
_MODULE_NAMES = {p.stem for p in SCRIPTS.glob("*.py")} if SCRIPTS.is_dir() else set()

# test.id() -> {"defect": ..., "gate": ..., "observed": ...}; filled while the
# suite runs and read afterwards by tools/run_validation.py.
KNOWN_DEFECTS = {}


def _purge_foreign_modules():
    for key, module in list(sys.modules.items()):
        if key not in _MODULE_NAMES:
            continue
        path = getattr(module, "__file__", None)
        if path is None or not Path(path).resolve().is_relative_to(SCRIPTS):
            del sys.modules[key]


def runtime(name):
    """Import a deployed module, verifying its real path is the candidate."""
    if name not in _MODULE_NAMES:
        raise ImportError(f"{name} is not a deployed module of this candidate")
    _purge_foreign_modules()
    if str(SCRIPTS) in sys.path:
        sys.path.remove(str(SCRIPTS))
    sys.path.insert(0, str(SCRIPTS))
    module = importlib.import_module(name)
    resolved = Path(getattr(module, "__file__", "")).resolve()
    if not resolved.is_relative_to(SCRIPTS):
        raise ImportError(f"{name} resolved outside candidate tree: {resolved}")
    return module


def inventory():
    return json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))


class ContractCase(unittest.TestCase):
    """unittest base with explicit known-baseline-defect classification.

    ``expect_baseline_defect`` never hides a result: if the documented
    defect signature is observed the test is recorded as
    ``known_baseline_defect``; if the desired contract already holds the
    test fails as an unexpected pass (XPASS); any other observation fails
    as an unexpected failure form.
    """

    def expect_baseline_defect(self, defect_id, *, gate, spec, observed,
                             defect_match, desired_match):
        if desired_match(observed):
            self.fail(
                f"unexpected pass: {defect_id} baseline defect not reproduced "
                f"({spec}); observed={observed!r}"
            )
        if not defect_match(observed):
            self.fail(
                f"{defect_id} unexpected failure form: {observed!r}; "
                f"documented defect: {spec}"
            )
        KNOWN_DEFECTS[self.id()] = {
            "defect": defect_id,
            "gate": gate,
            "observed": repr(observed)[:800],
        }

    def make_project(self):
        """Fresh initialized project folder under a real temp directory."""
        tmp = tempfile.TemporaryDirectory(prefix="knuaf-public-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        runtime("gg_core").init(root)
        return root

    def _tree_bytes(self, root):
        """Full on-disk file inventory+bytes for a project root — used to
        prove rejection/failure paths leave every persisted artifact
        (canonical, history/migration snapshots, ledger, evidence)
        byte-unchanged, not just the top-level project.json file."""
        return {
            str(p.relative_to(root)): p.read_bytes()
            for p in sorted(Path(root).rglob("*")) if p.is_file()
        }


def write_text(root, rel, text):
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def source_op(text_path="answer.txt", claims=None, claim_review="synthetic-review"):
    return {"collection": "sources", "value": {
        "id": "answer", "path": text_path,
        "claims": claims or {}, "claim_review": claim_review}}


def fact_op(fid, field_id, value, unit, *, value_type="text", period=None,
            scope="farm", kind="reported_fact", answer_state="provided",
            verification="source_located", source_id="answer", claim_id=None):
    ref = {"id": source_id, "locator": "line 1", "revision": 1}
    if claim_id:
        ref["claim_id"] = claim_id
    return {"collection": "facts", "value": {
        "id": fid, "field_id": field_id, "kind": kind, "value": value,
        "unit": unit, "value_type": value_type, "period": period,
        "scope": scope, "answer_state": answer_state,
        "verification": verification, "source_refs": [ref]}}


def claim_of(fact_value, claim_id="c1"):
    return {claim_id: {k: fact_value[k] for k in (
        "field_id", "value", "unit", "period", "scope", "answer_state", "kind")}}


def section_claim(fact_value, quote):
    """Body-claim record: the fact fields checks() compares, plus a quote
    string that must appear in the section text."""
    return {"fact_id": fact_value["id"], "quote": quote,
            **{k: fact_value[k] for k in
               ("field_id", "value", "unit", "period", "scope", "kind")}}


def section_op(sid, title, path, order=1, claims=None, **extra):
    value = {"id": sid, "title": title, "path": path, "order": order,
             "status": "drafting"}
    if claims is not None:
        value["claims"] = claims
    value.update(extra)
    return {"collection": "sections", "value": value}


def bind_major(root, major_id="specialty_crops", *, request_id=None):
    """Record an explicit, claim-backed ``common.major_id`` binding through
    the ordinary ``gg_core.apply`` transaction (policy B output guard).

    Uses its own source (``major-answer``) so a test's ``answer`` source
    stays untouched; the fact is text-typed with a unit string and a claim,
    so it adds no semantic/claim blocker to the submission gate.  Returns
    the new canonical revision."""
    core = runtime("gg_core")
    module = runtime("gg_major_contract").default_registry().resolve(major_id)
    write_text(root, "major-answer.txt", "전공 선택: " + major_id + "\n")
    fact = fact_op("selected_major", "common.major_id", major_id, "",
                   scope="project", verification="claim_supported",
                   source_id="major-answer", claim_id="major")
    fact["value"]["module_version"] = module.module_version
    source = source_op("major-answer.txt", claims=claim_of(fact["value"], "major"))
    source["value"]["id"] = "major-answer"
    revision = core.load(root)["revision"]
    core.apply(root, {"request_id": request_id or "bind-major:" + major_id,
                      "ops": [source, fact]}, revision)
    return core.load(root)["revision"]


def script_source(name):
    return (SCRIPTS / name).read_text(encoding="utf-8")


def copy_public_tree(dest):
    """Copy the whole public tree into ``dest`` for disposable
    negative-control/meta-test runs — bundle checks inside the copy need
    every declared file present.  The real candidate is never touched."""
    import shutil
    dest = Path(dest)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")
    shutil.copytree(REPO_ROOT, dest, ignore=ignore)
    return dest


def text_calls_without_encoding(source):
    """AST scan: Path.read_text()/write_text() calls lacking ``encoding``."""
    hits = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr in {"read_text", "write_text"}
            and not any(k.arg == "encoding" for k in node.keywords)
        ):
            hits.append(node.lineno)
    return hits
