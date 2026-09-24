"""G006/P5 lane C — canonical registration suite.

Real temp workspaces throughout: ``gg_core.init`` installs the v2 lock,
``gg_lock.Lock`` supplies the held capability, the shipped registry is
loaded through ``load_registry`` (the ONLY ``RegistryResolution``
producer), and selections verify against real bytes — the deployment's
own ``SKILL.md`` for ``installed_runtime`` and a real scan + sealed
receipt through ``gg_source_intake`` for ``scanned_external``.

Covers: the §8.3 step-1b admission table in listed order (row 0 BEFORE
any storage read), the §8.2.2 reconciliation rows, the §11 rechecked-state
selector (rows 1, 2a–2g), registration-addressed view identity, the
key-catalog binding (I03.17), deterministic view reconstruction (I03.18),
the two-independent-sources ``runtime_root`` check (I03.21/Calls K–M),
combined-failure precedence (I03.22), the p4_snapshot discriminator
(I03.23) and the per-site packs-dir contracts (I03.24 sites 2/3).
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
KNUAF_DOC = TESTS.parent
SCRIPTS = KNUAF_DOC / "scripts"
LANE_ROOT = KNUAF_DOC.parents[2]          # work/g006-p5-lane-c-1
REGISTRY_PATH = KNUAF_DOC / "references" / "builtin-sources.json"
PACKS_DIR = KNUAF_DOC / "references" / "benchmark-packs"
ACCEPTED_ROOT = (LANE_ROOT.parent
                 / "g005-p4-source-catalog-transform-corrections-1"
                 / "references" / "benchmark-packs")
GG = SCRIPTS / "gg.py"

sys.path.insert(0, str(SCRIPTS))
import gg_core          # noqa: E402
import gg_fs            # noqa: E402
import gg_lock          # noqa: E402
import gg_reuse         # noqa: E402
import gg_source_intake  # noqa: E402
import gg_rda_research as research  # noqa: E402

GUIDE_KEY = {"kind": "delimited",
             "value": "official-writing-guide:toc-rules"}
KIM_KEY = {"kind": "delimited",
           "value": "kim-wonseop-exemplar:opening-narrative"}
AUTHORITY = {
    "rda.income.national.2024":
        "accepted-catalog-20260921/rda.income.national.2024",
}
POLICY = {"classification_version": "p5-classifier/1",
          "include": ["**/*"], "always_hash": ["**/*"],
          "document_extensions": [".md", ".txt", ".hwp", ".pdf"],
          "exclude": []}


def sha(b):
    return hashlib.sha256(b).hexdigest()


def registry():
    return gg_reuse.load_registry(REGISTRY_PATH)


def runtime_root():
    """The DEPLOYMENT's own installed base — the running tool's install
    location (``scripts/``'s parent), bound independently of the
    registry load."""
    return Path(os.path.normpath(str(KNUAF_DOC.absolute())))


def key_catalog(**extra):
    kc = {
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
    for kind, ser, claim in extra.get("claims", []):
        kc.setdefault(kind, {})[ser] = claim
    return kc


def workspace(td, name="ws"):
    root = Path(td) / name
    root.mkdir()
    gg_core.init(root)
    return root


def file_entry(abs_path, rel_id):
    data = Path(abs_path).read_bytes()
    return {"relative_id": rel_id, "content_sha256": sha(data),
            "bytes": len(data)}


def installed_selection(reg, rel_id="SKILL.md", *, keys=None,
                        requested_kind="writing_rule",
                        digest=None):
    """A valid installed_runtime selection over a real file under the
    deployment's own installed base."""
    f = file_entry(reg.resolved_base / rel_id, rel_id)
    return {
        "kind": "external_document",
        "source_hash": f["content_sha256"],
        "scan_id": None,
        "byte_source": {"kind": "installed_runtime", "root": None,
                        "scan_policy_digest": None},
        "files": [f],
        "registry_digest": digest or reg.registry_digest,
        "requested_use": {"kind": requested_kind,
                          "keys": list(keys or [GUIDE_KEY])},
        "excerpt": None,
        "external_identity": {
            "original_basename": Path(rel_id).name,
            "original_sha256": f["content_sha256"],
            "observed_utc": "2026-09-21T00:00:00Z",
            "role": "official_guideline",
            "class": "authority_document"},
    }


def scanned_fixture(td, reg):
    """A real scan of a real external root + its sealed receipt + the
    caller-built selection binding that scan_id."""
    ext = Path(td) / "ext"
    (ext / "sub").mkdir(parents=True)
    (ext / "doc.md").write_text("# 외부 문서\n본문 내용\n",
                                encoding="utf-8")
    (ext / "sub" / "note.txt").write_text("노트\n", encoding="utf-8")
    kc = key_catalog()
    with gg_lock.Lock(Path(td) / "ws") as cap:
        scan = gg_source_intake.run_source_scan(
            ext, root=Path(td) / "ws",
            classification_version="p5-classifier/1",
            registry=reg, key_catalog=kc, scan_policy=POLICY,
            requested_use={"kind": "narrative_reference",
                           "keys": [KIM_KEY]})
        assert "sealed" in scan, scan
        gg_source_intake.write_scan_receipt(
            Path(td) / "ws", scan, capability=cap)
    files = [{"relative_id": f["relative_id"],
              "content_sha256": f["content_sha256"],
              "bytes": len((ext / f["relative_id"]).read_bytes())}
             for f in scan["sealed"]["files"]
             if f["type"] == "file" and f["content_sha256"]]
    doc_sha = [f["content_sha256"] for f in files
               if f["relative_id"] == "doc.md"][0]
    sel = {
        "kind": "external_document",
        "source_hash": doc_sha,
        "scan_id": scan["scan_id"],
        "byte_source": {"kind": "scanned_external", "root": str(ext),
                        "scan_policy_digest":
                            scan["sealed"]["scan_policy_digest"]},
        "files": files,
        "registry_digest": reg.registry_digest,
        "requested_use": {"kind": "narrative_reference",
                          "keys": [KIM_KEY]},
        "excerpt": {"relative_id": "doc.md", "start_line": 1,
                    "end_line": 2},
        "external_identity": {
            "original_basename": "doc.md",
            "original_sha256": doc_sha,
            "observed_utc": "2026-09-21T00:00:00Z",
            "role": "narrative_exemplar",
            "class": "user_document"},
    }
    return ext, sel


def stat_key_of(reg, pack_id, which="verified"):
    for e in reg.document["entries"]:
        if e["source_id"] != pack_id:
            continue
        for claim in e["coverage"]["statistical_observation"]["keys"]:
            if claim["authority"] == which:
                return claim["key_ref"]
    raise KeyError("%s has no %s claim" % (pack_id, which))


def write_export(tmp, pack_id):
    """A real p5-catalog-export/1 from the accepted manifest rows — the
    only trusted route is load_accepted_catalog -> accept_catalog."""
    rel = {"rda.income.national.2024": "common/rda-income-national-2024",
           "rda.income.regional.2024": "common/rda-income-regional-2024",
           "rda.econ.2025": "common/rda-econ-2025",
           "mafra.specialty.production.2024":
               "majors/specialty_crops/mafra-specialty-production-2024",
           }[pack_id]
    manifest = json.loads(
        (ACCEPTED_ROOT / rel / "manifest.json").read_text())
    rows = manifest["rows"]
    entries = {json.dumps(r["audit_key"], sort_keys=True,
                          ensure_ascii=False): r for r in rows}
    path = Path(tmp) / ("catalog-%s.json" % pack_id)
    path.write_text(json.dumps(
        {"schema": "p5-catalog-export/1",
         "authority": AUTHORITY[pack_id],
         "entries": entries}, ensure_ascii=False), encoding="utf-8")
    return path


def p4_selection(td, reg, catalog):
    """A valid p4_snapshot selection: the pack's records.jsonl under the
    installed base, a registry-claimed statistical key, and the §4.2
    p4_trust block bound to the constructed catalog."""
    pack_id = "rda.income.national.2024"
    rel = ("references/benchmark-packs/common/"
           "rda-income-national-2024/records.jsonl")
    key = stat_key_of(reg, pack_id)
    f = file_entry(reg.resolved_base / rel, rel)
    return {
        "kind": "p4_snapshot",
        "source_hash": f["content_sha256"],
        "scan_id": None,
        "byte_source": {"kind": "installed_runtime", "root": None,
                        "scan_policy_digest": None},
        "files": [f],
        "registry_digest": reg.registry_digest,
        "requested_use": {"kind": "statistical_observation",
                          "keys": [key]},
        "excerpt": None,
        "external_identity": {
            "original_basename": "records.jsonl",
            "original_sha256": f["content_sha256"],
            "observed_utc": "2026-09-21T00:00:00Z",
            "role": "statistical_pack",
            "class": "common_public_book"},
        "p4_trust": {
            "pack_id": pack_id,
            "pack_revision": "2024",
            "audit_key": dict(key),
            "catalog_authority": AUTHORITY[pack_id],
            "catalog_digest": catalog.digest,
            "observation_receipt_sha256": "00" * 32},
    }


def register(root, sel, revision, request_id, reg=None, **kw):
    reg = reg or registry()
    args = dict(capability=None, request_id=request_id,
                registry=reg, runtime_root=runtime_root(),
                key_catalog=key_catalog())
    args.update(kw)
    with gg_lock.Lock(root) as cap:
        args["capability"] = cap
        return gg_core.register_source_snapshot(
            root, sel, revision, **args)


def staged_register(root, sel, revision, request_id, reg=None, **kw):
    """Register, then read back the durable staging/registration/view
    artifacts for assertions."""
    p = register(root, sel, revision, request_id, reg=reg, **kw)
    rid = hashlib.sha256(request_id.encode("utf-8")).hexdigest()
    staging = json.loads((root / "sources/intake"
                          / ("request-%s.staging.json" % rid)).read_text())
    regrec = json.loads((root / "sources/registered/requests"
                         / (rid + ".json")).read_text())
    view_path = root / regrec["view_path"]
    return p, staging, regrec, view_path


def op_error(cm, commit_state, reason):
    r = cm.exception.result
    assert r["commit_state"] == commit_state, r
    assert r["reason"] == reason, r
    return r


class TestAdmission(unittest.TestCase):
    """§8.3 step 1b — the admission table, in listed order, before ANY
    storage read (§11 precedence row 0)."""

    def setUp(self):
        self.reg = registry()

    def test_request_id_required(self):
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            sel = installed_selection(self.reg)
            for bad in (None, "", 0, 17):
                with self.assertRaises(gg_core.OperationError) as cm:
                    register(root, sel, 0, bad)
                op_error(cm, "not_committed", "request_id_required")

    def test_registry_must_be_constructed_resolution(self):
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            sel = installed_selection(self.reg)
            for bad in (None, self.reg.document,
                        self.reg.registry_digest, "x"):
                with self.assertRaises(gg_core.OperationError) as cm:
                    register(root, sel, 0, "r1", registry=bad)
                op_error(cm, "not_committed",
                         "adoption_context_invalid")

    def test_runtime_root_required(self):
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            sel = installed_selection(self.reg)
            for bad in (None, 17):
                with self.assertRaises(gg_core.OperationError) as cm:
                    register(root, sel, 0, "r1", runtime_root=bad)
                op_error(cm, "not_committed",
                         "adoption_context_invalid")

    def test_key_catalog_none_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            sel = installed_selection(self.reg)
            with self.assertRaises(gg_core.OperationError) as cm:
                register(root, sel, 0, "r1", key_catalog=None)
            op_error(cm, "not_committed", "selection_invalid")

    def test_key_catalog_omission_is_typeerror(self):
        """V8-1: no default — Python raises TypeError at binding."""
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            sel = installed_selection(self.reg)
            reg = self.reg
            with self.assertRaises(TypeError):
                with gg_lock.Lock(root) as cap:
                    gg_core.register_source_snapshot(
                        root, sel, 0, capability=cap,
                        request_id="r1", registry=reg,
                        runtime_root=runtime_root())

    def test_selection_grammar(self):
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            base = installed_selection(self.reg)
            cases = []

            s = dict(base); s["kind"] = "workspace"; cases.append(s)
            s = dict(base); s["source_hash"] = "0" * 63; cases.append(s)
            s = dict(base); s.pop("scan_id"); cases.append(s)
            s = dict(base); s["scan_id"] = "ab" * 32; cases.append(s)
            s = json.loads(json.dumps(base))
            s["byte_source"] = {"kind": "scanned_external",
                                "root": None, "scan_policy_digest": None}
            cases.append(s)
            s = json.loads(json.dumps(base))
            s["byte_source"] = {"kind": "installed_runtime",
                                "root": "/tmp/x", "scan_policy_digest": None}
            cases.append(s)
            s = json.loads(json.dumps(base))
            s["scan_id"] = "ab" * 32  # non-null scan_id w/ null policy
            cases.append(s)
            s = json.loads(json.dumps(base))
            s["byte_source"]["scan_policy_digest"] = "cd" * 32
            cases.append(s)          # null-pairing violated the other way
            s = json.loads(json.dumps(base))
            s["files"][0]["relative_id"] = "../escape"; cases.append(s)
            s = json.loads(json.dumps(base))
            s["files"][0]["content_sha256"] = "zz"; cases.append(s)
            s = json.loads(json.dumps(base))
            s["files"].append(dict(s["files"][0]))  # duplicate relative_id
            cases.append(s)
            s = json.loads(json.dumps(base))
            s["requested_use"]["keys"] = []; cases.append(s)
            s = json.loads(json.dumps(base))
            s["requested_use"]["keys"] = [{"kind": "delimited",
                                           "value": "zz:aa"},
                                          GUIDE_KEY]  # unsorted
            cases.append(s)
            s = json.loads(json.dumps(base))
            s["requested_use"]["keys"] = [GUIDE_KEY, GUIDE_KEY]
            cases.append(s)          # duplicate keys
            s = json.loads(json.dumps(base))
            s["registry_digest"] = "ee" * 32
            cases.append(s)          # check-8 failure is still
            s = json.loads(json.dumps(base))
            s["p4_trust"] = {"pack_id": "x"}  # bound to wrong kind
            cases.append(s)
            for i, sel in enumerate(cases):
                with self.assertRaises(gg_core.OperationError,
                                       msg="case %d" % i) as cm:
                    register(root, sel, 0, "r%d" % i)
                op_error(cm, "not_committed", "selection_invalid")

    def test_key_catalog_schema_invalid(self):
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            sel = installed_selection(self.reg)
            bad = {"writing_rule": {GUIDE_KEY["value"]: {"authority":
                                                       "bogus"}}}
            with self.assertRaises(gg_core.OperationError) as cm:
                register(root, sel, 0, "r1", key_catalog=bad)
            op_error(cm, "not_committed", "selection_invalid")

    def test_combined_failure_first_check_wins(self):
        """I03.22: schema-invalid selection AND registry=None ->
        adoption_context_invalid (check 2 precedes check 5)."""
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            sel = {"kind": "bogus"}          # fails check 5
            with self.assertRaises(gg_core.OperationError) as cm:
                register(root, sel, 0, "r1", registry=None)
            op_error(cm, "not_committed", "adoption_context_invalid")

    def test_registry_digest_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            sel = installed_selection(self.reg, digest="ff" * 32)
            with self.assertRaises(gg_core.OperationError) as cm:
                register(root, sel, 0, "r1")
            op_error(cm, "not_committed", "selection_invalid")

    def test_runtime_root_two_independent_sources(self):
        """I03.21 Call L: identical args, runtime_root != resolved_base ->
        selection_invalid.  Call K: a DISTINCT external root still passes
        check 9 — byte_source.root is never the operand."""
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            sel = installed_selection(self.reg)
            with self.assertRaises(gg_core.OperationError) as cm:
                register(root, sel, 0, "r1",
                         runtime_root=Path(td) / "elsewhere")
            op_error(cm, "not_committed", "selection_invalid")

        # Call K — scanned_external with a DIFFERENT live external root
        # while runtime_root == resolved_base: check 9 still passes.
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            ext, sel = scanned_fixture(td, self.reg)
            self.assertNotEqual(Path(ext), Path(self.reg.resolved_base))
            p = register(root, sel, 0, "req-ext")
            self.assertEqual(p["revision"], 1)
            # Call L — the SAME call with runtime_root bound to the
            # EXTERNAL root instead fails selection_invalid.
            root2 = workspace(td, "ws2")
            with self.assertRaises(gg_core.OperationError) as cm:
                register(root2, sel, 0, "req-ext2",
                         runtime_root=Path(ext))
            op_error(cm, "not_committed", "selection_invalid")

    def test_runtime_root_relocated_registry(self):
        """I03.21 Call M — byte-identical registry at a foreign location:
        same document digest, different resolved_base; the deployment's
        own runtime_root mismatches the A-loaded resolution."""
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            foreign = Path(td) / "foreign"
            (foreign / "references" / "benchmark-packs").mkdir(
                parents=True)
            shutil.copy2(REGISTRY_PATH,
                         foreign / "references" / "builtin-sources.json")
            (foreign / "SKILL.md").write_bytes(
                (KNUAF_DOC / "SKILL.md").read_bytes())
            # the p4_trust.packs_dir marker must exist at the resolved
            # root — load_registry fails closed without it
            (foreign / "references" / "benchmark-packs"
             / "catalog.json").write_text("{}")
            reg_b = gg_reuse.load_registry(
                foreign / "references" / "builtin-sources.json")
            self.assertEqual(reg_b.registry_digest,
                             self.reg.registry_digest)   # same document
            self.assertNotEqual(reg_b.resolved_base,
                                self.reg.resolved_base)  # different base
            sel = installed_selection(self.reg)
            with self.assertRaises(gg_core.OperationError) as cm:
                register(root, sel, 0, "r1", registry=reg_b)
            op_error(cm, "not_committed", "selection_invalid")

    def test_p4_snapshot_requires_constructed_catalog(self):
        """I03.23 single failure: kind=p4_snapshot with catalog/indexes
        absent or raw dicts -> adoption_context_invalid (check 7)."""
        if not ACCEPTED_ROOT.exists():
            self.skipTest("accepted g005 catalog tree not present")
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            export = write_export(td, "rda.income.national.2024")
            catalog, indexes = gg_reuse.load_accepted_catalog(
                export, registry=self.reg,
                packs_dir=self.reg.resolved_packs_dir)
            sel = p4_selection(td, self.reg, catalog)
            with self.assertRaises(gg_core.OperationError) as cm:
                register(root, sel, 0, "r1")          # both absent
            op_error(cm, "not_committed",
                     "adoption_context_invalid")
            with self.assertRaises(gg_core.OperationError) as cm:
                register(root, sel, 0, "r2",
                         catalog={"entries": {}}, indexes=indexes)
            op_error(cm, "not_committed",
                     "adoption_context_invalid")      # a dict is no trust
            with self.assertRaises(gg_core.OperationError) as cm:
                register(root, sel, 0, "r3",
                         catalog=catalog, indexes=None)
            op_error(cm, "not_committed",
                     "adoption_context_invalid")

    def test_p4_combined_failure_check7_precedes_check8(self):
        """I03.23 combined: p4_snapshot missing catalog AND registry_digest
        mismatch -> exactly one reason: adoption_context_invalid."""
        if not ACCEPTED_ROOT.exists():
            self.skipTest("accepted g005 catalog tree not present")
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            export = write_export(td, "rda.income.national.2024")
            catalog, _ = gg_reuse.load_accepted_catalog(
                export, registry=self.reg,
                packs_dir=self.reg.resolved_packs_dir)
            sel = p4_selection(td, self.reg, catalog)
            sel["registry_digest"] = "ff" * 32
            with self.assertRaises(gg_core.OperationError) as cm:
                register(root, sel, 0, "r1")
            op_error(cm, "not_committed", "adoption_context_invalid")

    def test_admission_before_any_storage_read(self):
        """I03.20 combined case: a committed request_id + changed
        selection + malformed key_catalog -> selection_invalid, NEVER
        request_id_conflict; the historical commit is untouched."""
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            sel = installed_selection(self.reg)
            register(root, sel, 0, "req-1")
            changed = dict(sel)
            changed["source_hash"] = "aa" * 32   # different identity
            with self.assertRaises(gg_core.OperationError) as cm:
                register(root, changed, 1, "req-1",
                         key_catalog={"bad": 1})
            op_error(cm, "not_committed", "selection_invalid")
            # and the valid committed duplicate still succeeds
            p = register(root, sel, 1, "req-1")
            self.assertEqual(p["revision"], 1)


class TestOutcomeMapping(unittest.TestCase):
    """§11 rows 1, 2a–2g — the rechecked-state selector, total."""

    def setUp(self):
        self.reg = registry()

    def test_committed_duplicate_returns_current_projection(self):
        """I03.7: identical retry returns the CURRENTLY LOADED projection
        — even across an intervening revision — without re-copying."""
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            sel = installed_selection(self.reg)
            p1, staging, regrec, view_path = staged_register(
                root, sel, 0, "req-1")
            self.assertEqual(p1["revision"], 1)
            view_bytes = view_path.read_bytes()
            # intervening canonical revision by an unrelated request
            sel2 = installed_selection(
                self.reg, rel_id="references/reuse-registry-schema.md",
                keys=[GUIDE_KEY])
            register(root, sel2, 1, "req-2")
            # retry of the first request — duplicate path
            p2 = register(root, sel, 0, "req-1")   # stale rev NOT read
            self.assertEqual(p2["revision"], 2)    # CURRENT projection
            self.assertEqual(view_path.read_bytes(), view_bytes)
            self.assertEqual(staging["state"], "committed")

    def test_request_id_conflict_over_verified_history(self):
        """I03.14: committed request_id + different selection ->
        request_id_conflict even with a verified historical view."""
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            sel = installed_selection(self.reg)
            staged_register(root, sel, 0, "req-1")
            changed = dict(sel)
            changed["source_hash"] = "aa" * 32
            with self.assertRaises(gg_core.OperationError) as cm:
                register(root, changed, 1, "req-1")
            r = op_error(cm, "not_committed", "request_id_conflict")
            self.assertEqual(r["request_id"], "req-1")

    def test_committed_duplicate_view_missing(self):
        """I03.15: committed entry + absent view -> indeterminate /
        committed_publication_missing."""
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            sel = installed_selection(self.reg)
            _, _, regrec, view_path = staged_register(
                root, sel, 0, "req-1")
            view_path.unlink()
            with self.assertRaises(gg_core.OperationError) as cm:
                register(root, sel, 0, "req-1")
            op_error(cm, "indeterminate",
                     "committed_publication_missing")

    def test_committed_duplicate_view_divergent(self):
        """I03.15: committed entry + edited view -> indeterminate /
        committed_publication_unverified; bytes never overwritten."""
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            sel = installed_selection(self.reg)
            _, _, regrec, view_path = staged_register(
                root, sel, 0, "req-1")
            view_path.write_bytes(b'{"human": "edited"}')
            with self.assertRaises(gg_core.OperationError) as cm:
                register(root, sel, 0, "req-1")
            op_error(cm, "indeterminate",
                     "committed_publication_unverified")
            self.assertEqual(view_path.read_bytes(),
                             b'{"human": "edited"}')

    def test_view_conflict_is_pre_commit(self):
        """I03.10 + §11 row 2e: a view path pre-occupied by foreign bytes
        with NO committed entry -> not_committed / view_conflict, and no
        commit occurs."""
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            sel = installed_selection(self.reg)
            # pre-create the exact view path with foreign bytes
            plan_view = gg_core._reg_derive(sel, "req-1")["view_path"]
            vp = root / plan_view
            vp.parent.mkdir(parents=True)
            vp.write_bytes(b'{"foreign": true}')
            with self.assertRaises(gg_core.OperationError) as cm:
                register(root, sel, 0, "req-1")
            op_error(cm, "not_committed", "view_conflict")
            self.assertEqual(vp.read_bytes(), b'{"foreign": true}')
            self.assertNotIn("req-1", gg_core.load(root)["requests"])

    def test_published_unregistered_via_view(self):
        """I03.11 + §11 row 2f: publish_source_view with no committed
        ledger entry -> not_committed / published_unregistered."""
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            sel = installed_selection(self.reg)
            _, _, regrec, _ = staged_register(root, sel, 0, "req-1")
            # wipe the ledger entry by rebuilding canonical state without
            # it: simulate an uncommitted registration record by crafting
            # a second request whose registration record exists but whose
            # ledger entry does not — publish on it directly.
            sel2 = installed_selection(
                self.reg,
                rel_id="references/reuse-registry-schema.md")
            rid2 = hashlib.sha256("req-2".encode()).hexdigest()
            fake = dict(regrec)
            fake["request_id"] = "req-2"
            fake["request_id_hash"] = rid2
            fake["request_digest"] = gg_core._reg_digest(sel2)
            # keep the identity fields internally consistent: view_id
            # recomputes from the same source_ids preimage
            fake["view_id"] = gg_core._reg_digest({
                "scan_id": None,
                "request_digest": fake["request_digest"],
                "request_id": "req-2",
                "registry_digest": fake["registry_digest"],
                "source_ids": [fake["source_id"]],
            })
            fake["view_path"] = ("sources/generated/%s/source-index.json"
                                 % fake["view_id"])
            rec_rel = "sources/registered/requests/%s.json" % rid2
            rp = root / rec_rel
            rp.write_text(gg_core._reg_canonical_json(fake),
                          encoding="utf-8")
            with gg_lock.Lock(root) as cap:
                with self.assertRaises(gg_core.OperationError) as cm:
                    gg_core.publish_source_view(root, fake, capability=cap)
            op_error(cm, "not_committed", "published_unregistered")

    def test_cleanup_pending_on_verified_duplicate(self):
        """I03.19 + §11 row 2c: committed entry + verified view + a
        failing staging reconcile -> committed_cleanup_pending /
        staged_release_failed, exit 3 — NEVER exit 0."""
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            sel = installed_selection(self.reg)
            register(root, sel, 0, "req-1")
            rid = hashlib.sha256(b"req-1").hexdigest()
            sp = (root / "sources" / "intake"
                  / ("request-%s.staging.json" % rid))
            sp.unlink()   # durable staging record absent -> reconcile
            # will create+write it; make the write fail
            sp.parent.chmod(0o555)
            try:
                with self.assertRaises(gg_core.OperationError) as cm:
                    register(root, sel, 0, "req-1")
            finally:
                sp.parent.chmod(0o755)
            r = op_error(cm, "committed_cleanup_pending",
                         "staged_release_failed")
            self.assertEqual(r.get("revision"), 1)

    def test_revision_conflict(self):
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            sel = installed_selection(self.reg)
            register(root, sel, 0, "req-1")
            sel2 = installed_selection(
                self.reg,
                rel_id="references/reuse-registry-schema.md")
            with self.assertRaises(gg_core.OperationError) as cm:
                register(root, sel2, 0, "req-2")   # stale: rev is 1
            r = op_error(cm, "not_committed", "revision_conflict")
            self.assertEqual(r["revision"], 1)

    def test_selection_bytes_mismatch(self):
        """§11 row 2g: live bytes diverge from the selection's claims."""
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            sel = installed_selection(self.reg)
            sel["files"][0]["bytes"] += 1
            with self.assertRaises(gg_core.OperationError) as cm:
                register(root, sel, 0, "req-1")
            op_error(cm, "not_committed",
                     "selection_bytes_mismatch")

    def test_source_id_conflict(self):
        """§9.2: divergent durable content record under the same
        source_id -> not_committed / source_id_conflict, pre-commit."""
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            sel = installed_selection(self.reg)
            _, _, regrec, _ = staged_register(root, sel, 0, "req-1")
            # forge divergent bytes at the content record path
            cpath = root / ("sources/registered/%s.json"
                            % regrec["source_id"])
            cpath.write_text('{"schema":"p5-source-content/1","x":1}',
                             encoding="utf-8")
            sel2 = dict(sel)
            sel2["source_hash"] = sel["source_hash"]   # same content
            with self.assertRaises(gg_core.OperationError) as cm:
                register(root, sel2, 1, "req-2")
            op_error(cm, "not_committed", "source_id_conflict")


class TestArtifacts(unittest.TestCase):
    """The durable records: §9.1 snapshot, §9.2 records, §9.3 view."""

    def setUp(self):
        self.reg = registry()

    def test_records_and_view_bytes(self):
        """I03.18: the view is byte-reproducible from durable records
        alone (no live registry/catalog/filesystem)."""
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            sel = installed_selection(self.reg)
            p, staging, regrec, view_path = staged_register(
                root, sel, 0, "req-1")
            self.assertEqual(staging["schema"],
                             "p5-registration-staging/3")
            self.assertEqual(staging["state"], "committed")
            self.assertEqual(staging["resulting_revision"], 1)
            self.assertEqual(regrec["schema"], "p5-source-registration/2")
            self.assertEqual(regrec["key_catalog_digest"],
                             gg_core._reg_digest(key_catalog()))
            content = json.loads(
                (root / ("sources/registered/%s.json"
                         % regrec["source_id"])).read_text())
            self.assertEqual(content["schema"], "p5-source-content/1")
            # deterministic reconstruction from durable bytes only
            rebuilt = gg_core._reg_canonical_json(
                gg_core._reg_view_payload(regrec, content)).encode()
            self.assertEqual(view_path.read_bytes(), rebuilt)
            view = json.loads(view_path.read_text())
            self.assertEqual(view["schema"], "p5-source-view/2")
            self.assertEqual(view["view_id"], regrec["view_id"])
            self.assertIsNone(view["scan_policy_digest"])
            # snapshot manifest
            man = json.loads(
                (root / regrec["source_id"].replace(
                    "p5src-", "sources/snapshots/p5src-")
                 / "manifest.json").read_text())
            self.assertEqual(man["schema"], "p5-source-snapshot/1")

    def test_coverage_evaluation_min(self):
        """I03.17: coverage_evaluation = min(registry claim, key-catalog
        authority); a registry-claimed key absent from key_catalog is
        recorded 'unverified' — a fact, not a rejection."""
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            sel = installed_selection(self.reg)     # GUIDE_KEY verified
            _, _, regrec, _ = staged_register(root, sel, 0, "req-1")
            ser = gg_reuse.canonical_key(GUIDE_KEY, "writing_rule")
            self.assertEqual(regrec["coverage_evaluation"][ser],
                             "verified")
            # same key with a weaker key_catalog authority
            kc = key_catalog()
            kc["writing_rule"][GUIDE_KEY["value"]]["authority"] = \
                "unverified"
            root2 = workspace(td, "ws2")
            sel2 = installed_selection(self.reg)
            _, _, regrec2, _ = staged_register(
                root2, sel2, 0, "req-1", key_catalog=kc)
            self.assertEqual(
                regrec2["coverage_evaluation"][ser], "unverified")
            # registry-claimed key absent from key_catalog -> unverified
            kc3 = key_catalog()
            del kc3["writing_rule"][GUIDE_KEY["value"]]
            root3 = workspace(td, "ws3")
            _, _, regrec3, _ = staged_register(
                root3, installed_selection(self.reg), 0, "req-1",
                key_catalog=kc3)
            self.assertEqual(
                regrec3["coverage_evaluation"][ser], "unverified")

    def test_registration_addressed_view_identity(self):
        """I03.13: two request_ids selecting identical content publish
        TWO distinct view paths while sharing the content record."""
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            sel = installed_selection(self.reg)
            _, _, rec1, v1 = staged_register(root, sel, 0, "req-A")
            p2, _, rec2, v2 = staged_register(root, sel, 1, "req-B")
            self.assertNotEqual(rec1["view_id"], rec2["view_id"])
            self.assertNotEqual(v1, v2)
            self.assertTrue(v1.exists() and v2.exists())
            self.assertEqual(rec1["source_id"], rec2["source_id"])
            self.assertEqual(v1.read_bytes().replace(
                rec1["view_id"].encode(), b"").replace(
                b'"req-A"', b""), v2.read_bytes().replace(
                    rec2["view_id"].encode(), b"").replace(
                        b'"req-B"', b""))  # payloads differ only in ids
            # one content record, two per-registration records
            self.assertEqual(len(list(
                (root / "sources/registered").glob("p5src-*.json"))), 1)
            self.assertEqual(len(list(
                (root / "sources/registered/requests").glob("*.json"))),
                2)
            self.assertIn("req-A", p2["requests"])
            self.assertIn("req-B", p2["requests"])

    def test_scanned_external_registers(self):
        """The scanned_external byte source registers end-to-end through
        a real sealed receipt."""
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            ext, sel = scanned_fixture(td, self.reg)
            p, staging, regrec, view = staged_register(
                root, sel, 0, "req-scan")
            self.assertEqual(p["revision"], 1)
            self.assertEqual(regrec["provenance"]["scan_id"],
                             sel["scan_id"])
            self.assertEqual(regrec["scan_policy_digest"],
                             sel["byte_source"]["scan_policy_digest"])
            snap = root / "sources/snapshots" / regrec["source_id"]
            self.assertTrue((snap / "doc.md").exists())
            self.assertTrue((snap / "sub/note.txt").exists())

    def test_p4_snapshot_registers(self):
        """p4_snapshot through the constructed catalog route, live-trust
        evaluation recorded at commit."""
        if not ACCEPTED_ROOT.exists():
            self.skipTest("accepted g005 catalog tree not present")
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            export = write_export(td, "rda.income.national.2024")
            catalog, indexes = gg_reuse.load_accepted_catalog(
                export, registry=self.reg,
                packs_dir=self.reg.resolved_packs_dir)
            self.assertTrue(catalog.accepted)
            sel = p4_selection(td, self.reg, catalog)
            ser = gg_reuse.canonical_key(
                sel["requested_use"]["keys"][0],
                "statistical_observation")
            kc = key_catalog()
            kc["statistical_observation"][ser] = {
                "authority": "verified", "catalog_digest": None,
                "receipt_sha256": None}
            p, staging, regrec, view = staged_register(
                root, sel, 0, "req-p4", catalog=catalog, indexes=indexes,
                key_catalog=kc)
            self.assertEqual(p["revision"], 1)
            self.assertEqual(regrec["p4_trust"]["pack_id"],
                             "rda.income.national.2024")
            self.assertEqual(
                regrec["coverage_evaluation"][ser], "verified")

    def test_no_replace_preserves_user_files(self):
        """I03.1/I03.2: a human-edited 03_sources.md and a pre-existing
        generated view path are never overwritten."""
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            user_md = root / "03_sources.md"
            user_md.write_text("# human curated\n", encoding="utf-8")
            sel = installed_selection(self.reg)
            register(root, sel, 0, "req-1")
            self.assertEqual(user_md.read_text(encoding="utf-8"),
                             "# human curated\n")
            # the generated tree never carries a 03_sources.md
            self.assertEqual(
                list((root / "sources").rglob("03_sources.md")), [])


class TestCli(unittest.TestCase):
    """§3.3/§3.4 — the CLI adapter: explicit paths only, per-operation
    failure vocabulary."""

    def setUp(self):
        self.reg = registry()

    def _cli(self, args, cwd=None):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                   PYTHONIOENCODING="utf-8")
        return subprocess.run(
            [sys.executable, "-B", str(GG)] + args,
            capture_output=True, text=True, env=env, cwd=cwd)

    def test_source_register_and_view_cli(self):
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            sel = installed_selection(self.reg)
            kc_path = Path(td) / "kc.json"
            kc_path.write_text(json.dumps(key_catalog()))
            r = self._cli([
                "source-register", str(root),
                "--selection", json.dumps(sel),
                "--expected-revision", "0",
                "--request-id", "req-cli",
                "--registry", str(REGISTRY_PATH),
                "--key-catalog", str(kc_path)])
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            r = self._cli(["source-view", str(root),
                           "--request-id", "req-cli"])
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertEqual(json.loads(r.stdout)["commit_state"],
                             "committed")

    def test_source_register_runtime_mismatch_cli(self):
        """§3.3 note + Call M at the CLI: a foreign-located byte-identical
        registry resolves a different base than the running tool's own
        install location -> not_committed / selection_invalid, exit 2."""
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            foreign = Path(td) / "foreign"
            (foreign / "references" / "benchmark-packs").mkdir(
                parents=True)
            shutil.copy2(REGISTRY_PATH,
                         foreign / "references" / "builtin-sources.json")
            (foreign / "SKILL.md").write_bytes(
                (KNUAF_DOC / "SKILL.md").read_bytes())
            (foreign / "references" / "benchmark-packs"
             / "catalog.json").write_text("{}")
            sel = installed_selection(self.reg)
            kc_path = Path(td) / "kc.json"
            kc_path.write_text(json.dumps(key_catalog()))
            r = self._cli([
                "source-register", str(root),
                "--selection", json.dumps(sel),
                "--expected-revision", "0",
                "--request-id", "req-cli",
                "--registry",
                str(foreign / "references" / "builtin-sources.json"),
                "--key-catalog", str(kc_path)])
            self.assertEqual(r.returncode, 2)
            out = json.loads(r.stdout)
            self.assertEqual(out["commit_state"], "not_committed")
            self.assertEqual(out["reason"], "selection_invalid")

    def test_source_register_unconstructible_catalog_cli(self):
        """I03.24 site 3: a --packs-dir that mismatches the registry's
        bound resolved_packs_dir fails the load_accepted_catalog
        construction -> not_committed / adoption_context_invalid,
        exit 2 — never the resolver's 'blocked'."""
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            sel = installed_selection(self.reg)
            kc_path = Path(td) / "kc.json"
            kc_path.write_text(json.dumps(key_catalog()))
            cat_path = Path(td) / "cat.json"
            cat_path.write_text(json.dumps(
                {"schema": "p5-catalog-export/1",
                 "authority": "x", "entries": {}}))
            r = self._cli([
                "source-register", str(root),
                "--selection", json.dumps(sel),
                "--expected-revision", "0",
                "--request-id", "req-cli",
                "--registry", str(REGISTRY_PATH),
                "--key-catalog", str(kc_path),
                "--catalog", str(cat_path),
                "--packs-dir", str(Path(td) / "wrong")])
            self.assertEqual(r.returncode, 2)
            out = json.loads(r.stdout)
            self.assertEqual(out["commit_state"], "not_committed")
            self.assertEqual(out["reason"], "adoption_context_invalid")
            self.assertNotEqual(out["reason"], "catalog_not_accepted")

    def test_source_resolve_cli(self):
        """The resolver path through the CLI — explicit registry +
        key-catalog paths; a packs-dir mismatch maps to the resolver
        vocabulary (blocked / catalog_not_accepted), never a commit
        state."""
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            kc_path = Path(td) / "kc.json"
            kc_path.write_text(json.dumps(key_catalog()))
            cat_path = Path(td) / "cat.json"
            cat_path.write_text(json.dumps(
                {"schema": "p5-catalog-export/1",
                 "authority": "x", "entries": {}}))
            r = self._cli([
                "source-resolve", str(root),
                "--source-hash", "0" * 64,
                "--requested-use", json.dumps(
                    {"kind": "writing_rule", "keys": [GUIDE_KEY]}),
                "--registry", str(REGISTRY_PATH),
                "--key-catalog", str(kc_path)])
            self.assertIn(r.returncode, (0, 2, 3))
            out = json.loads(r.stdout)
            self.assertIn("status", out)
            # mismatched --packs-dir at the input-loading boundary
            r = self._cli([
                "source-resolve", str(root),
                "--source-hash", "0" * 64,
                "--requested-use", json.dumps(
                    {"kind": "writing_rule", "keys": [GUIDE_KEY]}),
                "--registry", str(REGISTRY_PATH),
                "--key-catalog", str(kc_path),
                "--catalog", str(cat_path),
                "--packs-dir", str(Path(td) / "wrong")])
            self.assertEqual(r.returncode, 2)
            out = json.loads(r.stdout)
            self.assertEqual(out["status"], "blocked")
            self.assertIn("catalog_not_accepted", out["reasons"])

    def test_source_scan_cli_no_packs_dir(self):
        """V15-2: source-scan carries no --packs-dir operand."""
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            ext = Path(td) / "ext"
            ext.mkdir()
            (ext / "a.md").write_text("x\n", encoding="utf-8")
            kc_path = Path(td) / "kc.json"
            kc_path.write_text(json.dumps(key_catalog()))
            pol_path = Path(td) / "policy.json"
            pol_path.write_text(json.dumps(POLICY))
            r = self._cli([
                "source-scan", str(root), "--root", str(ext),
                "--registry", str(REGISTRY_PATH),
                "--key-catalog", str(kc_path),
                "--policy", str(pol_path)])
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            out = json.loads(r.stdout)
            self.assertEqual(out["status"], "ok")
            # argparse itself has no --packs-dir on the scan path
            r2 = self._cli([
                "source-scan", str(root), "--root", str(ext),
                "--registry", str(REGISTRY_PATH),
                "--key-catalog", str(kc_path),
                "--policy", str(pol_path),
                "--packs-dir", str(Path(td))])
            self.assertNotEqual(r2.returncode, 0)

    def test_source_view_unknown_request(self):
        with tempfile.TemporaryDirectory() as td:
            root = workspace(td)
            r = self._cli(["source-view", str(root),
                           "--request-id", "nobody"])
            self.assertEqual(r.returncode, 2)   # not_committed -> exit 2
            out = json.loads(r.stdout)
            self.assertEqual(out["commit_state"], "not_committed")
            self.assertEqual(out["reason"], "published_unregistered")


if __name__ == "__main__":
    unittest.main()
