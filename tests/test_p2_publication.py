"""P2 lane C unit/band tests — gg_publication.

Covers the C-owned acceptance items: publication receipt schema/binding
(P2-P01), staged payload verification and no-replace rename (P2-P02),
no-clobber file publish semantics (P2-P09), idempotent find_request replay
(P2-P03/P17 preimage rules), protocol/workspace binding + recovery lineage
verification (P2-P13/P16), observation immutable publish (P2-P10), paper
--out requested-path publish (P2-P14), import workspace move (P2-P11/P12),
and the port/no-follow refusal path.

These are lane-local tests on the POSIX port with the real B primitives;
fault injection uses unittest.mock. They do not claim product PASS,
integration coverage of A's call sites, or native Windows behaviour.
"""
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests._harness import ContractCase, runtime

gg_fs = runtime("gg_fs")
gg_lock = runtime("gg_lock")
gg_pub = runtime("gg_publication")

PublicationError = gg_pub.PublicationError


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _sha_obj(obj):
    return _sha(gg_fs.canonical_json_bytes(obj))


def _ws(tmp_root, name="ws"):
    root = Path(tmp_root) / name
    root.mkdir()
    protocol = gg_lock.install_new(root)
    return root, protocol


def _protocol_sha(protocol):
    return _sha(gg_fs.canonical_json_bytes(protocol))


def _file(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"path": None, "sha256": _sha(data), "size": len(data)}


def _entries(base, skip=(".gg-lock",)):
    out = []
    for dirpath, dirs, names in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in skip)
        for name in sorted(names):
            p = Path(dirpath) / name
            rel = p.relative_to(base).as_posix()
            data = p.read_bytes()
            out.append({"path": rel, "sha256": _sha(data),
                        "size": len(data)})
    out.sort(key=lambda e: e["path"].encode("utf-8"))
    return out


def _export_intent(revision=3, export_kind="draft", refs=None):
    fp = _sha_obj({"fp": 1})
    request = {
        "kind": "export",
        "export_kind": export_kind,
        "input_revision": revision,
        "project_sha256": _sha(b"project"),
        "target_refs": refs if refs is not None else [],
        "input_fingerprint": fp,
    }
    rsha = _sha_obj(request)
    return {
        "request": request,
        "request_id": "gg:export:" + rsha,
        "request_sha256": rsha,
        "input_revision": revision,
        "target_refs": request["target_refs"],
        "input_fingerprint": fp,
    }


def _paper_intent(requested_path="out/논문.md", revision=4):
    fp = _sha_obj({"fp": 2})
    request = {
        "kind": "paper",
        "input_revision": revision,
        "target_refs": [],
        "input_fingerprint": fp,
        "spec_path": "spec/req.md",
        "spec_sha256": _sha(b"spec"),
        "requested_path": requested_path,
        "output_format": "md",
    }
    rsha = _sha_obj(request)
    return {
        "request": request,
        "request_id": "gg:paper:" + rsha,
        "request_sha256": rsha,
        "input_revision": revision,
        "target_refs": [],
        "input_fingerprint": fp,
    }


def _adopt_intent(request_id="req:adopt:1", revision=5,
                  out_path="work/draft.md", companions=()):
    fp = _sha_obj({"fp": 3})
    request = {
        "kind": "adopt_output",
        "expected_revision": revision,
        "output_value": {
            "id": "out-1",
            "label": "초안",
            "path": out_path,
            "format": "md",
        },
        "companion_files": list(companions),
    }
    rsha = _sha_obj(request)
    return {
        "request": request,
        "request_id": request_id,
        "request_sha256": rsha,
        "input_revision": revision,
        "target_refs": [{"collection": "sections", "id": "s-1"}],
        "input_fingerprint": fp,
    }


def _import_intent(destination, inventory):
    request = {
        "kind": "import",
        "source_inventory": inventory,
        "destination": str(destination),
    }
    rsha = _sha_obj(request)
    return {
        "request": request,
        "request_id": "gg:import:" + rsha,
        "request_sha256": rsha,
        "input_revision": None,
        "target_refs": None,
        "input_fingerprint": None,
    }


def _staging_dir(tmp_root, name="stage", contents=None):
    stage = Path(tmp_root) / name
    stage.mkdir()
    for rel, data in (contents or {}).items():
        p = stage / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return stage


def _lock(root):
    return gg_lock.Lock(root)


class CanonicalVectors(ContractCase):
    """Canonical-JSON serialization rules the request/receipt digests rely
    on: sorted keys, no whitespace, UTF-8 verbatim, trailing newline, and
    rejection of non-finite/unsupported values."""

    def test_canonical_bytes_shape(self):
        data = gg_fs.canonical_json_bytes({"b": 1, "a": [2, "x", True]})
        self.assertEqual(data, b'{"a":[2,"x",true],"b":1}\n')

    def test_canonical_bytes_unicode_verbatim(self):
        data = gg_fs.canonical_json_bytes({"k": "한글\u2028"})
        self.assertNotIn(b"\\u", data)
        self.assertTrue(data.endswith(b"\n"))

    def test_canonical_bytes_rejects_nonfinite_and_types(self):
        for bad in (float("nan"), float("inf"), object(), {1: 2}):
            with self.assertRaises((TypeError, ValueError)):
                gg_fs.canonical_json_bytes({"x": bad})

    def test_request_sha_is_recomputed_not_trusted(self):
        intent = _export_intent()
        intent["request_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            gg_pub.publish_directory(
                "/nonexistent", object(), staging="/nonexistent",
                destination="build/1/x", intent=intent, files=[])


class RequestValidation(ContractCase):
    """API §6/§9 preimage + reference validation — pure checks happen before
    any capability or filesystem work."""

    def _pub_dir(self, intent=None, **kw):
        args = dict(staging="/nonexistent-stage",
                    destination="build/1/x",
                    intent=intent or _export_intent(), files=[])
        args.update(kw)
        return gg_pub.publish_directory("/nonexistent", object(), **args)

    def test_unknown_kind_and_bad_keys(self):
        intent = _export_intent()
        intent["request"]["kind"] = "bogus"
        with self.assertRaises(ValueError):
            self._pub_dir(intent)
        intent = _export_intent()
        del intent["request"]["export_kind"]
        intent["request_sha256"] = _sha_obj(intent["request"])
        intent["request_id"] = "gg:export:" + intent["request_sha256"]
        with self.assertRaises(ValueError):
            self._pub_dir(intent)
        intent = _export_intent()
        intent["request"]["surprise"] = 1
        with self.assertRaises(ValueError):
            self._pub_dir(intent)

    def test_request_id_rule(self):
        intent = _export_intent()
        intent["request_id"] = "gg:export:wrong"
        with self.assertRaises(ValueError):
            self._pub_dir(intent)
        intent = _paper_intent()
        intent["request_id"] = "gg:export:" + intent["request_sha256"]
        with self.assertRaises(ValueError):
            self._pub_dir(intent, destination="build/4/paper")

    def test_intent_request_field_mismatch(self):
        intent = _export_intent()
        intent["input_revision"] = 999
        with self.assertRaises(ValueError):
            self._pub_dir(intent)

    def test_target_refs_rules(self):
        good = [
            {"collection": "facts", "id": "price:purchase"},
            {"collection": "sections", "id": "draft-1"},
        ]
        self._pub_dir(_export_intent(refs=good)) if False else None
        intent = _export_intent(refs=good)  # must not raise at validation
        gg_pub._check_request(intent["request"])

        bad_refs = [
            [{"collection": "sections", "id": "b"},
             {"collection": "facts", "id": "a"}],           # unsorted
            [{"collection": "facts", "id": "x"},
             {"collection": "facts", "id": "x"}],            # duplicate
            [{"collection": "bogus", "id": "x"}],            # unknown coll
            [{"collection": "facts:x", "id": "y"}],          # colon coll
            [{"collection": "facts", "id": "x", "z": 1}],    # extra key
            [{"collection": "facts", "id": " x"}],           # edge space
            [{"collection": "facts", "id": "a\nb"}],         # control char
            [{"collection": "facts", "id": 5}],              # non-string
            ["facts:x"],                                     # not object
            [{"collection": "facts", "id": "x\ud800"}],      # surrogate
            [{"collection": "facts", "id": "é"},
             {"collection": "facts", "id": "é"}],   # NFC pair
        ]
        for refs in bad_refs:
            request = _export_intent()["request"]
            request["target_refs"] = refs
            with self.assertRaises(ValueError, msg=repr(refs)):
                gg_pub._check_request(request)

    def test_file_entries_rules(self):
        base = {"sha256": _sha(b"x"), "size": 1}
        for bad in ("../x", "/abs", "a//b", "a/./b", "a/../b", "C:/x",
                    "C:\\x", "a\\b", "x\x00y"):
            files = [dict(base, path=bad)]
            with self.assertRaises(ValueError, msg=bad):
                gg_pub._check_file_entries(
                    files, require_destination=False, what="files")
        dup = [dict(base, path="a"), dict(base, path="a")]
        with self.assertRaises(ValueError):
            gg_pub._check_file_entries(
                dup, require_destination=False, what="files")
        nfc = [dict(base, path="가.md"), dict(base, path="가.md")]
        with self.assertRaises(ValueError):
            gg_pub._check_file_entries(
                nfc, require_destination=False, what="files")
        unsorted = [dict(base, path="b"), dict(base, path="a")]
        with self.assertRaises(ValueError):
            gg_pub._check_file_entries(
                unsorted, require_destination=False, what="files")

    def test_import_request_rules(self):
        with self.assertRaises(ValueError):
            gg_pub._check_request({
                "kind": "import", "source_inventory": [],
                "destination": "relative/path"})
        with self.assertRaises(ValueError):
            gg_pub._check_request({
                "kind": "import", "source_inventory": [],
                "destination": "/tmp/../x"})

    def test_paper_request_rules(self):
        req = _paper_intent()["request"]
        req["output_format"] = "docx"
        with self.assertRaises(ValueError):
            gg_pub._check_request(req)


class PublishDirectory(ContractCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root, self.protocol = _ws(self._tmp.name)

    def _publish(self, intent=None, destination="build/3/draft",
                 contents=None, files=None, cap_root=None):
        contents = contents if contents is not None else {
            "a.txt": b"alpha", "sub/b.txt": b"beta"}
        stage = _staging_dir(self._tmp.name, contents=contents)
        files = files if files is not None else _entries(stage)
        with _lock(cap_root or self.root) as cap:
            return gg_pub.publish_directory(
                self.root, cap, staging=stage,
                destination=destination,
                intent=intent or _export_intent(), files=files)

    def test_happy(self):
        res = self._publish()
        self.assertEqual(res["state"], "published")
        ref = res["publication_ref"]
        self.assertEqual(ref["schema"], "gg-publication-ref/1")
        self.assertEqual(ref["path"], "build/3/draft/.publication.json")
        dest = self.root / "build/3/draft"
        self.assertTrue(dest.is_dir())
        self.assertEqual((dest / "a.txt").read_bytes(), b"alpha")
        self.assertFalse((self._tmp.name and Path(self._tmp.name) /
                          "stage").exists())
        receipt = gg_pub.verify_publication(
            self.root, publication_ref=ref)
        self.assertEqual(receipt["kind"], "export")
        self.assertEqual(receipt["workspace_id"],
                         self.protocol["workspace_id"])
        self.assertEqual(receipt["protocol_sha256"],
                         _protocol_sha(self.protocol))
        expected_pid = _sha(gg_fs.canonical_json_bytes({
            "workspace_id": self.protocol["workspace_id"],
            "kind": "export",
            "request_id": receipt["request_id"],
            "request_sha256": receipt["request_sha256"]}))
        self.assertEqual(receipt["publication_id"], expected_pid)
        self.assertEqual(ref["id"], expected_pid)
        self.assertEqual(ref["sha256"],
                         _sha((dest / ".publication.json").read_bytes()))

    def test_idempotent_replay(self):
        res1 = self._publish()
        # Retry after the staging dir moved: same inputs -> same ref.
        res2 = self._publish()
        self.assertEqual(res2["state"], "published")
        self.assertEqual(res1["publication_ref"], res2["publication_ref"])

    def test_staging_inventory_mismatch(self):
        contents = {"a.txt": b"alpha"}
        stage = _staging_dir(self._tmp.name, contents=contents)
        wrong = _entries(stage) + [
            {"path": "b.txt", "sha256": _sha(b"b"), "size": 1}]
        wrong.sort(key=lambda e: e["path"].encode("utf-8"))
        with _lock(self.root) as cap:
            with self.assertRaises(PublicationError) as ctx:
                gg_pub.publish_directory(
                    self.root, cap, staging=stage,
                    destination="build/3/draft",
                    intent=_export_intent(), files=wrong)
        self.assertEqual(ctx.exception.result["state"], "not_published")
        self.assertFalse((self.root / "build").exists())
        self.assertTrue(stage.exists())

    def test_staging_undeclared_extra(self):
        stage = _staging_dir(self._tmp.name,
                             contents={"a.txt": b"alpha"})
        files = _entries(stage)
        (stage / "sneak.txt").write_bytes(b"x")
        with _lock(self.root) as cap:
            with self.assertRaises(PublicationError):
                gg_pub.publish_directory(
                    self.root, cap, staging=stage,
                    destination="build/3/draft",
                    intent=_export_intent(), files=files)

    def test_destination_conflict_foreign_object(self):
        dest = self.root / "build/3/draft"
        dest.mkdir(parents=True)
        (dest / "user.txt").write_bytes(b"mine")
        with self.assertRaises(PublicationError) as ctx:
            self._publish()
        self.assertEqual(ctx.exception.result["state"], "not_published")
        self.assertEqual((dest / "user.txt").read_bytes(), b"mine")

    def test_destination_conflict_different_request(self):
        self._publish()
        stage = _staging_dir(self._tmp.name, name="stage2",
                             contents={"a.txt": b"alpha"})
        other = _export_intent(export_kind="final")
        with _lock(self.root) as cap:
            with self.assertRaises(PublicationError) as ctx:
                gg_pub.publish_directory(
                    self.root, cap, staging=stage,
                    destination="build/3/draft", intent=other,
                    files=_entries(stage))
        self.assertEqual(ctx.exception.result["state"], "not_published")
        self.assertTrue((self.root / "build/3/draft/.publication.json")
                        .is_file())

    def test_capability_enforced_before_writes(self):
        stage = _staging_dir(self._tmp.name,
                             contents={"a.txt": b"alpha"})
        with self.assertRaises(gg_lock.LockError):
            gg_pub.publish_directory(
                self.root, object(), staging=stage,
                destination="build/3/draft",
                intent=_export_intent(), files=_entries(stage))
        self.assertFalse((self.root / "build").exists())
        with _lock(self.root) as cap:
            pass  # released on exit
        with self.assertRaises(gg_lock.LockError):
            gg_pub.publish_directory(
                self.root, cap, staging=stage,
                destination="build/3/draft",
                intent=_export_intent(), files=_entries(stage))
        self.assertFalse((self.root / "build").exists())

    def test_unsupported_port_refused_before_writes(self):
        stage = _staging_dir(self._tmp.name,
                             contents={"a.txt": b"alpha"})
        with _lock(self.root) as cap:
            with mock.patch.object(gg_pub, "_port_supported",
                                   return_value=False):
                with self.assertRaises(PublicationError) as ctx:
                    gg_pub.publish_directory(
                        self.root, cap, staging=stage,
                        destination="build/3/draft",
                        intent=_export_intent(), files=_entries(stage))
        self.assertEqual(ctx.exception.reason,
                         "unsupported_publication_port")
        self.assertFalse((self.root / "build").exists())

    def test_rename_unknown_is_indeterminate(self):
        stage = _staging_dir(self._tmp.name,
                             contents={"a.txt": b"alpha"})
        real_rename = gg_fs.rename_noreplace

        def flaky(src, dst, **kw):
            if str(src).endswith("/stage"):
                return gg_fs.FsWriteResult(
                    "unknown", "post_rename_unverifiable", dst)
            return real_rename(src, dst, **kw)

        with _lock(self.root) as cap:
            with mock.patch.object(
                    gg_pub.gg_fs, "rename_noreplace",
                    side_effect=flaky):
                with self.assertRaises(PublicationError) as ctx:
                    gg_pub.publish_directory(
                        self.root, cap, staging=stage,
                        destination="build/3/draft",
                        intent=_export_intent(), files=_entries(stage))
        self.assertEqual(ctx.exception.result["state"], "indeterminate")
        self.assertTrue(stage.exists())

    def test_paper_receipt_carries_requested_path(self):
        intent = _paper_intent("out/논문.md")
        res = self._publish(intent=intent, destination="build/4/paper")
        receipt = gg_pub.verify_publication(
            self.root, publication_ref=res["publication_ref"])
        self.assertEqual(receipt["requested_path"], "out/논문.md")
        self.assertEqual(receipt["output_format"], "md")


class PublishOutput(ContractCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root, self.protocol = _ws(self._tmp.name)

    def _sources(self, companions=True):
        (self.root / "work").mkdir()
        (self.root / "work/draft.md").write_bytes(b"draft-body")
        (self.root / "work/side.json").write_bytes(b'{"note":1}')
        srcs = [
            {"path": "work/draft.md", "sha256": _sha(b"draft-body"),
             "size": 10, "destination": "draft.md"},
            {"path": "work/side.json", "sha256": _sha(b'{"note":1}'),
             "size": 10, "destination": "meta/side.json"},
        ]
        srcs.sort(key=lambda s: s["path"].encode("utf-8"))
        return srcs

    def test_happy_and_immutables(self):
        srcs = self._sources()
        intent = _adopt_intent(
            companions=[{"path": "work/side.json",
                         "sha256": _sha(b'{"note":1}')}])
        with _lock(self.root) as cap:
            res = gg_pub.publish_output(self.root, cap,
                                        intent=intent, sources=srcs)
        self.assertEqual(res["state"], "published")
        bundle = self.root / res["destination"]
        self.assertTrue(bundle.is_dir())
        self.assertEqual((bundle / "draft.md").read_bytes(), b"draft-body")
        self.assertEqual((bundle / "meta/side.json").read_bytes(),
                         b'{"note":1}')
        receipt = gg_pub.verify_publication(
            self.root, publication_ref=res["publication_ref"])
        self.assertEqual(receipt["kind"], "adopt_output")
        self.assertEqual(receipt["primary_path"], "draft.md")
        self.assertEqual(receipt["output_id"], "out-1")
        self.assertEqual(receipt["output_format"], "md")
        self.assertEqual(len(receipt["source_files"]), 2)
        self.assertEqual(receipt["source_files"][0]["destination"],
                         "draft.md" if receipt["source_files"][0]["path"]
                         == "work/draft.md" else "meta/side.json")
        # Originals untouched.
        self.assertEqual((self.root / "work/draft.md").read_bytes(),
                         b"draft-body")

    def test_idempotent(self):
        srcs = self._sources()
        intent = _adopt_intent()
        with _lock(self.root) as cap:
            res1 = gg_pub.publish_output(self.root, cap,
                                         intent=intent, sources=srcs)
        with _lock(self.root) as cap:
            res2 = gg_pub.publish_output(self.root, cap,
                                         intent=intent, sources=srcs)
        self.assertEqual(res1["publication_ref"], res2["publication_ref"])

    def test_find_request_and_conflict(self):
        srcs = self._sources()
        intent = _adopt_intent(request_id="req:adopt:1")
        with _lock(self.root) as cap:
            res = gg_pub.publish_output(self.root, cap,
                                        intent=intent, sources=srcs)
        found = gg_pub.find_request(self.root, request_id="req:adopt:1")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["publication_id"],
                         res["publication_ref"]["id"])
        self.assertEqual(
            gg_pub.find_request(self.root, request_id="req:adopt:nope"), [])
        # Same request_id, different request preimage -> conflict.
        other = _adopt_intent(request_id="req:adopt:1", revision=6)
        with _lock(self.root) as cap:
            with self.assertRaises(PublicationError) as ctx:
                gg_pub.publish_output(self.root, cap,
                                      intent=other, sources=srcs)
        self.assertIn("request_id_conflict", ctx.exception.reason)

    def test_source_hash_mismatch(self):
        srcs = self._sources()
        srcs[0]["sha256"] = "0" * 64
        with _lock(self.root) as cap:
            with self.assertRaises(PublicationError) as ctx:
                gg_pub.publish_output(
                    self.root, cap, intent=_adopt_intent(), sources=srcs)
        self.assertEqual(ctx.exception.result["state"], "not_published")

    def test_companion_declared_must_match(self):
        srcs = self._sources()
        intent = _adopt_intent(companions=[{"path": "work/side.json",
                                          "sha256": "0" * 64}])
        with self.assertRaises(ValueError):
            with _lock(self.root) as cap:
                gg_pub.publish_output(self.root, cap,
                                      intent=intent, sources=srcs)


class PublishRequestedFile(ContractCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root, self.protocol = _ws(self._tmp.name)
        bundle = self.root / ".gg-artifacts" / ("ab" * 32)
        bundle.mkdir(parents=True)
        (bundle / "paper.md").write_bytes(b"paper-body")
        self.managed = {"path": ".gg-artifacts/" + "ab" * 32 + "/paper.md",
                        "sha256": _sha(b"paper-body")}

    def test_happy_nested_path(self):
        with _lock(self.root) as cap:
            res = gg_pub.publish_requested_file(
                self.root, cap, managed=self.managed,
                requested_path="out/deep/논문.md")
        self.assertEqual(res["state"], "published")
        self.assertEqual(res["target_sha256"], self.managed["sha256"])
        self.assertEqual(
            (self.root / "out/deep/논문.md").read_bytes(), b"paper-body")
        self.assertFalse(list(self.root.glob("out/**/.gg-requested-*")))

    def test_idempotent_and_conflict(self):
        with _lock(self.root) as cap:
            gg_pub.publish_requested_file(
                self.root, cap, managed=self.managed,
                requested_path="p.md")
        with _lock(self.root) as cap:
            res = gg_pub.publish_requested_file(
                self.root, cap, managed=self.managed,
                requested_path="p.md")
        self.assertEqual(res["state"], "published")
        (self.root / "q.md").write_bytes(b"foreign")
        with _lock(self.root) as cap:
            with self.assertRaises(PublicationError) as ctx:
                gg_pub.publish_requested_file(
                    self.root, cap, managed=self.managed,
                    requested_path="q.md")
        self.assertEqual((self.root / "q.md").read_bytes(), b"foreign")

    def test_managed_hash_mismatch(self):
        bad = dict(self.managed, sha256="0" * 64)
        with _lock(self.root) as cap:
            with self.assertRaises(PublicationError):
                gg_pub.publish_requested_file(
                    self.root, cap, managed=bad, requested_path="p.md")
        self.assertFalse((self.root / "p.md").exists())

    def test_symlink_parent_rejected(self):
        real = self.root / "real"
        real.mkdir()
        os.symlink("real", self.root / "link")
        with _lock(self.root) as cap:
            with self.assertRaises((PublicationError, ValueError)):
                gg_pub.publish_requested_file(
                    self.root, cap, managed=self.managed,
                    requested_path="link/p.md")
        self.assertFalse((real / "p.md").exists())


class PublishWorkspace(ContractCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _staging_ws(self):
        stage = Path(self._tmp.name) / "staging-ws"
        stage.mkdir()
        gg_lock.install_new(stage)
        (stage / "project.json").write_bytes(b'{"revision":0}')
        (stage / "sections").mkdir()
        (stage / "sections/a.md").write_bytes(b"# a\n")
        return stage

    def test_happy_move_and_rebind(self):
        stage = self._staging_ws()
        files = _entries(stage)
        dest = Path(self._tmp.name) / "new-home"
        intent = _import_intent(dest, _entries(stage))
        with _lock(stage) as cap:
            res = gg_pub.publish_workspace(
                stage, cap, destination=str(dest), intent=intent,
                files=files)
            self.assertEqual(res["state"], "published")
            self.assertEqual(res["cleanup_errors"], [])
            self.assertFalse(stage.exists())
            self.assertTrue((dest / ".gg-lock").is_dir())
            self.assertTrue(
                (dest / ".gg-import-publication.json").is_file())
            self.assertEqual((dest / "sections/a.md").read_bytes(), b"# a\n")
            # Capability rebound to the new root: assert_held still passes.
            gg_lock.assert_held(cap, dest)
        receipt = _loads((dest / ".gg-import-publication.json")
                         .read_bytes())
        self.assertEqual(receipt["kind"], "import")
        self.assertEqual(receipt["request"]["destination"], str(dest))
        self.assertEqual(len(receipt["files"]), len(files))

    def test_existing_destination_conflict(self):
        stage = self._staging_ws()
        dest = Path(self._tmp.name) / "new-home"
        dest.mkdir()
        intent = _import_intent(dest, _entries(stage))
        with _lock(stage) as cap:
            with self.assertRaises(PublicationError) as ctx:
                gg_pub.publish_workspace(
                    stage, cap, destination=str(dest), intent=intent,
                    files=_entries(stage))
        self.assertEqual(ctx.exception.result["state"], "not_published")
        self.assertTrue(stage.exists())
        self.assertEqual(list(dest.iterdir()), [])

    def test_source_receipt_path_conflict(self):
        stage = self._staging_ws()
        (stage / ".gg-import-publication.json").write_bytes(b"{}")
        dest = Path(self._tmp.name) / "new-home"
        intent = _import_intent(dest, _entries(stage))
        with _lock(stage) as cap:
            with self.assertRaises(PublicationError):
                gg_pub.publish_workspace(
                    stage, cap, destination=str(dest), intent=intent,
                    files=_entries(stage))

    def test_payload_mismatch(self):
        stage = self._staging_ws()
        dest = Path(self._tmp.name) / "new-home"
        intent = _import_intent(dest, [])
        with _lock(stage) as cap:
            with self.assertRaises(PublicationError):
                gg_pub.publish_workspace(
                    stage, cap, destination=str(dest), intent=intent,
                    files=[])


def _loads(data):
    return json.loads(data)


class Observations(ContractCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root, self.protocol = _ws(self._tmp.name)
        (self.root / "reports").mkdir()
        (self.root / "reports/r1.md").write_bytes(b"report-body")
        self.recorded = {
            "schema": "gg-review-observation/2",
            "observed_by": "sess-obs",
            "author_session": "sess-author",
            "reviewer_session": "sess-rev",
            "author_id": "author-1",
            "reviewer_id": "rev-1",
            "review_kinds": ["contract", "red-team"],
            "target_refs": [{"collection": "sections", "id": "s-1"}],
            "input_fingerprint": _sha_obj({"fp": 9}),
            "report_path": "reports/r1.md",
            "report_hash": _sha(b"report-body"),
            "input_revision": 7,
            "workspace_id": self.protocol["workspace_id"],
            "protocol_sha256": _protocol_sha(self.protocol),
        }

    def test_happy_and_immutable(self):
        with _lock(self.root) as cap:
            res = gg_pub.publish_observation(
                self.root, cap, recorded=self.recorded)
        sha = _sha(gg_fs.canonical_json_bytes(self.recorded))
        self.assertEqual(res["hash"], sha)
        self.assertEqual(res["path"], ".gg-observations/" + sha + ".json")
        data = (self.root / res["path"]).read_bytes()
        self.assertEqual(data, gg_fs.canonical_json_bytes(self.recorded))
        with _lock(self.root) as cap:
            res2 = gg_pub.publish_observation(
                self.root, cap, recorded=self.recorded)
        self.assertEqual(res, res2)

    def test_invalid_and_no_dir_left(self):
        bad = dict(self.recorded, reviewer_session="sess-author")
        with _lock(self.root) as cap:
            with self.assertRaises(PublicationError):
                gg_pub.publish_observation(self.root, cap, recorded=bad)
        self.assertFalse((self.root / ".gg-observations").exists())

    def test_report_hash_mismatch_no_dir(self):
        bad = dict(self.recorded, report_hash="0" * 64)
        with _lock(self.root) as cap:
            with self.assertRaises(PublicationError):
                gg_pub.publish_observation(self.root, cap, recorded=bad)
        self.assertFalse((self.root / ".gg-observations").exists())

    def test_wrong_workspace_rejected(self):
        bad = dict(self.recorded, workspace_id="other-ws")
        with _lock(self.root) as cap:
            with self.assertRaises(PublicationError):
                gg_pub.publish_observation(self.root, cap, recorded=bad)

    def test_unsorted_review_kinds(self):
        bad = dict(self.recorded, review_kinds=["z", "a"])
        with _lock(self.root) as cap:
            with self.assertRaises(PublicationError):
                gg_pub.publish_observation(self.root, cap, recorded=bad)


class VerifyAndFind(ContractCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root, self.protocol = _ws(self._tmp.name)

    def _publish_export(self):
        stage = _staging_dir(self._tmp.name, contents={"a.txt": b"alpha"})
        with _lock(self.root) as cap:
            res = gg_pub.publish_directory(
                self.root, cap, staging=stage,
                destination="build/3/draft",
                intent=_export_intent(), files=_entries(stage))
        return res

    def test_tampered_payload_and_receipt(self):
        res = self._publish_export()
        ref = res["publication_ref"]
        (self.root / "build/3/draft/a.txt").write_bytes(b"evil")
        with self.assertRaises(ValueError):
            gg_pub.verify_publication(self.root, publication_ref=ref)
        (self.root / "build/3/draft/a.txt").write_bytes(b"alpha")
        self.assertTrue(gg_pub.verify_publication(
            self.root, publication_ref=ref))
        (self.root / ref["path"]).write_bytes(b"{}")
        with self.assertRaises(ValueError):
            gg_pub.verify_publication(self.root, publication_ref=ref)

    def test_foreign_workspace_receipt_not_returned(self):
        pid = "cd" * 32
        bundle = self.root / ".gg-artifacts" / pid
        bundle.mkdir(parents=True)
        fake_receipt = {
            "schema": "gg-publication/1",
            "publication_id": pid,
            "workspace_id": "foreign-ws",
            "request_id": "gg:export:abc",
        }
        (bundle / ".publication.json").write_bytes(
            json.dumps(fake_receipt).encode())
        self.assertEqual(
            gg_pub.find_request(self.root, request_id="gg:export:abc"),
            [])

    def test_malformed_receipt_is_ambiguous(self):
        bundle = self.root / ".gg-artifacts" / ("ef" * 32)
        bundle.mkdir(parents=True)
        (bundle / ".publication.json").write_bytes(b"not-json{")
        with self.assertRaises(ValueError):
            gg_pub.find_request(self.root, request_id="x")

    def test_lineage_check_for_old_workspace(self):
        res = self._publish_export()
        ref = res["publication_ref"]
        receipt_path = self.root / ref["path"]
        receipt = _loads(receipt_path.read_bytes())
        receipt["workspace_id"] = "old-ws"
        receipt["protocol_sha256"] = "0" * 64
        receipt_path.write_bytes(
            gg_fs.canonical_json_bytes(receipt))
        ref2 = dict(ref, sha256=_sha(receipt_path.read_bytes()))
        with self.assertRaises(ValueError):
            gg_pub.verify_publication(self.root, publication_ref=ref2)


if __name__ == "__main__":
    unittest.main()
