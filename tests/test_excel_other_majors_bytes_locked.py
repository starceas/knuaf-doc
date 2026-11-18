"""전공별 엑셀 산출물이 다른 전공의 양식을 넘보지 않는지 고정한다.

두 겹이다.

1. **17시트 두 판**(`school_17_sheet_v1`·`_v2`)은 특용작물의 양식이고, 새
   논문이 받는 번들은 2판이다. create는 전체 파일 SHA를, blank_copy와
   patch_copy는 모든 ZIP 항목의 순서·이름·원문 바이트·메타데이터를 판별로
   고정한다 — 2판 사본이 1판 행·수식으로 돌아가면 여기서 깨진다.
2. **다른 전공**은 자기 양식만 쓴다. 식량작물·낙농은 17시트 번들을 구조에서
   거절하고(자기 시트 수와 다름), 산업곤충·원예는 산출물을 아예 만들지
   못하며(정책 B 거절) 파일도 남기지 않는다 — 그것도 고정한다.

원본 자산과 create의 전체 파일 SHA는 그대로 잠근다. 재작성 사본의 고정
manifest는 압축 해제 원문 바이트를 정규화 없이 SHA로 잠그고 ZIP 메타데이터도
대조한다. 압축 스트림·압축 크기·그에 따른 파일 오프셋만 압축 구현에 맡긴다.
"""

import hashlib
import copy
import json
import tempfile
import zipfile
from pathlib import Path

from tests._harness import ContractCase, bind_major, runtime

BUNDLED_SHA256 = "76300305fdaf165bcce54fc3de2b476c91f4e8e368bc0455a8c69aa3eee9cd1d"
LEGACY_SHA256 = "6d3c9fe5e5992108a8bab18c0098edb0d808f0d14c099878a53939b17de88c7d"
SPECIALTY_CREATE = "76300305fdaf165bcce54fc3de2b476c91f4e8e368bc0455a8c69aa3eee9cd1d"
LOCKS = Path(__file__).parent / "fixtures" / "other-majors-workbook-locks.json"
SPECIALTY_PATCH_CACHES = 1644
SPECIALTY_BLANK_UNCLASSIFIED_TEXT = 12
LEGACY_PATCH_CACHES = 1643
SALES = " 5. 판매계획"
HELD_MAJORS = ("fruit_trees", "hort_env_systems")


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _workbook_manifest(path):
    fields = ("date_time", "flag_bits", "external_attr", "internal_attr",
              "compress_type", "create_system", "create_version",
              "extract_version", "reserved", "volume")
    with zipfile.ZipFile(path) as archive:
        entries = []
        names = set()
        for info in archive.infolist():
            if info.filename in names:
                raise AssertionError("duplicate ZIP member: " + info.filename)
            names.add(info.filename)
            entry = {key: getattr(info, key) for key in fields}
            entry["date_time"] = list(info.date_time)
            entry.update(name=info.filename,
                         sha256=hashlib.sha256(archive.read(info)).hexdigest(),
                         size=info.file_size, comment=info.comment.hex(),
                         extra=info.extra.hex())
            entries.append(entry)
        return {"comment": archive.comment.hex(), "entries": entries}


class OtherMajorsLockedTests(ContractCase):
    def setUp(self):
        self.tpl = runtime("gg_excel_template")
        self.patch = runtime("gg_excel_formula_patch")
        self.mc = runtime("gg_major_contract")
        self.bundled = self.tpl.BUNDLED_TEMPLATE
        self.assertEqual(_sha(self.bundled), BUNDLED_SHA256)
        self.root = self.make_project()

    def _assert_workbook_locked(self, path, key):
        expected = json.loads(LOCKS.read_text(encoding="utf-8"))[key]
        self.assertEqual(expected, _workbook_manifest(path))

    def _fixtures(self, root, source=None, profile="school_17_sheet_v2"):
        """A reviewed map. ``profile=None`` leaves the declaration out, so the
        map takes the bound major's own default: that is how another major's
        form is reached here, and why the structure check is the one that
        refuses it."""
        source = source or self.bundled
        clear_map = Path(root) / "clear.json"
        clear = {
            "schema": "gg-xlsx-template-map/v1",
            "source": {"sha256": _sha(source)},
            "layout": {"mapOrigin": "default_x01"},
            "mapStatus": "ok",
            "entries": [
                {"sheet": SALES, "cell": "B2", "action": "clear",
                 "role": "input.student_estimate", "explicit": True,
                 "reason": "합성"},
                {"sheet": SALES, "cell": "B4", "action": "preserve",
                 "role": "structure.source_note", "explicit": True,
                 "reason": "합성"},
            ]}
        if profile is not None:
            clear["workbook_profile"] = profile
        clear_map.write_text(json.dumps(clear, ensure_ascii=False),
                             encoding="utf-8")
        patch_map = Path(root) / "patch.json"
        patch_map.write_text(json.dumps({
            "schema": self.patch.MAP_SCHEMA,
            "source": {"sha256": _sha(source)},
            "patches": [{"sheet": "3.투자계획", "cell": "C8",
                         "expected_formula": "=SUM(D8:F8)",
                         "new_formula": "=SUM(D8:D8)",
                         "reason": "합성",
                         "evidence_ref": {"source_id": "s", "revision": 1,
                                          "locator": "합성"}}]},
            ensure_ascii=False), encoding="utf-8")
        return clear_map, patch_map

    def _context(self, major):
        bind_major(self.root, major)
        return self.mc.output_context(self.root, major)

    def test_the_bundled_17_sheet_path_is_the_second_edition(self):
        """A new paper's ordinary route lands on v2: the create copy is the
        corrected form's own bytes, and clearing/patching stay on v2 rows."""
        ctx = self._context("specialty_crops")
        with tempfile.TemporaryDirectory() as temp:
            clear_map, patch_map = self._fixtures(temp)
            created = Path(temp) / "create.xlsx"
            self.tpl.create_workbook(None, created, context=ctx,
                                     template_ref="X01",
                                     reference_inventory={"files": [],
                                                          "surveys": []})
            self.assertEqual(_sha(created), SPECIALTY_CREATE)
            self.assertEqual(_sha(created), _sha(self.bundled))

            blank = Path(temp) / "blank.xlsx"
            receipt = self.tpl.blank_copy(self.bundled, clear_map, blank,
                                          context=ctx)
            self._assert_workbook_locked(blank, "v2-blank")
            self.assertEqual(receipt["output"]["status"], "partial")
            self.assertEqual(receipt["unclassifiedTextCells"],
                             SPECIALTY_BLANK_UNCLASSIFIED_TEXT)
            self.assertEqual(receipt["workbook_profile"],
                             "school_17_sheet_v2")

            patched = Path(temp) / "patched.xlsx"
            receipt = self.patch.patch_copy(self.bundled, patch_map, patched,
                                            context=ctx)
            self._assert_workbook_locked(patched, "v2-patch")
            self.assertEqual(receipt["formulaCachesInvalidated"],
                             SPECIALTY_PATCH_CACHES)

            # An explicit 17-sheet choice is still this major's own profile.
            again = Path(temp) / "patched-explicit.xlsx"
            self.patch.patch_copy(self.bundled, patch_map, again, context=ctx,
                                  profile="school_17_sheet_v2")
            self._assert_workbook_locked(again, "v2-patch")

    def test_member_lock_rejects_content_order_and_metadata_changes(self):
        ctx = self._context("specialty_crops")
        with tempfile.TemporaryDirectory() as temp:
            clear_map, _ = self._fixtures(temp)
            original = Path(temp) / "original.xlsx"
            self.tpl.blank_copy(self.bundled, clear_map, original, context=ctx)
            with zipfile.ZipFile(original) as archive:
                members = [(copy.copy(info), archive.read(info))
                           for info in archive.infolist()]
            for mutation in ("content", "order", "metadata"):
                changed = [(copy.copy(info), data) for info, data in members]
                if mutation == "content":
                    info, data = changed[0]
                    changed[0] = (info, bytes([data[0] ^ 1]) + data[1:])
                elif mutation == "order":
                    changed[0], changed[1] = changed[1], changed[0]
                else:
                    changed[0][0].comment = b"changed"
                candidate = Path(temp) / (mutation + ".xlsx")
                with zipfile.ZipFile(candidate, "w") as archive:
                    for info, data in changed:
                        archive.writestr(info, data)
                with self.subTest(mutation=mutation):
                    with self.assertRaises(AssertionError):
                        self._assert_workbook_locked(candidate, "v2-blank")

    def _patch_one(self, root, source, name, expected, new="=SUM(D8:D8)",
                   profile=None):
        mapping = Path(root) / f"{name}.json"
        mapping.write_text(json.dumps({
            "schema": self.patch.MAP_SCHEMA,
            "source": {"sha256": _sha(source)},
            "patches": [{"sheet": "3.투자계획", "cell": "C8",
                         "expected_formula": expected,
                         "new_formula": new,
                         "reason": "합성",
                         "evidence_ref": {"source_id": "s", "revision": 1,
                                          "locator": "합성"}}]},
            ensure_ascii=False), encoding="utf-8")
        out = Path(root) / f"{name}.xlsx"
        receipt = self.patch.patch_copy(source, mapping, out, context=self.ctx,
                                        profile=profile)
        # The receipt names the bytes it wrote; a self-comparison would pass
        # for any content at all.
        self.assertTrue(out.is_file())
        self.assertEqual(receipt["output"]["sha256"], _sha(out))
        self.assertEqual([(p["sheet"], p["cell"], p["new_formula"])
                          for p in receipt["patched"]],
                         [("3.투자계획", "C8", new.lstrip("="))])
        return out, receipt

    def test_a_patched_copy_keeps_its_edition_for_the_next_patch(self):
        """Patching rewrites formulas, so a patched copy no longer matches the
        registered fingerprints. It still moves its edition's cells, and its
        receipt says which edition it is — otherwise the second patch would
        fall back to the other edition's label, and the copy engine would
        refuse the file it produced itself."""
        from openpyxl import load_workbook
        self.ctx = self._context("specialty_crops")
        with tempfile.TemporaryDirectory() as temp:
            first, receipt = self._patch_one(temp, self.bundled, "first",
                                             "=SUM(D8:F8)")
            self.assertEqual(receipt["workbook_profile"], "school_17_sheet_v2")
            book = load_workbook(first)
            self.assertEqual(book["3.투자계획"]["C8"].value, "=SUM(D8:D8)")
            book.close()

            second, receipt = self._patch_one(
                temp, first, "second", "=SUM(D8:D8)", new="=SUM(D8:E8)")
            self.assertEqual(receipt["workbook_profile"], "school_17_sheet_v2")
            book = load_workbook(second)
            self.assertEqual(book["3.투자계획"]["C8"].value, "=SUM(D8:E8)")
            book.close()

            # The same chain on the archived edition keeps that edition too.
            legacy, receipt = self._patch_one(
                temp, self.tpl.LEGACY_TEMPLATE, "legacy-1", "=SUM(D8:F8)")
            self.assertEqual(receipt["workbook_profile"], "school_17_sheet_v1")
            _, receipt = self._patch_one(
                temp, legacy, "legacy-2", "=SUM(D8:D8)", new="=SUM(D8:E8)")
            self.assertEqual(receipt["workbook_profile"], "school_17_sheet_v1")

    def test_a_patched_copy_cannot_be_declared_as_the_other_edition(self):
        """A patched copy is the hardest case: its formulas no longer match
        any registered fingerprint, so the strict gate cannot name it. The
        weaker gate still binds it to its own edition's cells."""
        from openpyxl import load_workbook
        self.ctx = self._context("specialty_crops")
        with tempfile.TemporaryDirectory() as temp:
            patched, _ = self._patch_one(temp, self.bundled, "patched",
                                         "=SUM(D8:F8)")
            wrong = Path(temp) / "mislabeled.xlsx"
            with self.assertRaises(self.mc.OutputHeldError) as held:
                self._patch_one(temp, patched, "mislabeled", "=SUM(D8:D8)",
                                new="=SUM(D8:E8)", profile="school_17_sheet_v1")
            self.assertEqual(held.exception.reason, "workbook_structure_mismatch")
            self.assertFalse(wrong.exists())
            # ...and the strict gate alone still refuses it: drift is not a
            # licence to relabel, it only lets this route re-patch its output.
            school = runtime("gg_school_excel")
            with self.assertRaises(ValueError):
                school.require_profile_structure(patched, "school_17_sheet_v1")

    def test_the_first_edition_keeps_its_own_bytes(self):
        """Papers already written on v1 retain their clear/patch member bytes
        and metadata; the lineage still decides their edition."""
        ctx = self._context("specialty_crops")
        legacy = self.tpl.LEGACY_TEMPLATE
        self.assertEqual(_sha(legacy), LEGACY_SHA256)
        with tempfile.TemporaryDirectory() as temp:
            clear_map, patch_map = self._fixtures(
                temp, source=legacy, profile="school_17_sheet_v1")

            blank = Path(temp) / "blank-v1.xlsx"
            receipt = self.tpl.blank_copy(legacy, clear_map, blank, context=ctx)
            self._assert_workbook_locked(blank, "v1-blank")
            self.assertEqual(receipt["workbook_profile"], "school_17_sheet_v1")
            self.assertEqual(receipt["unclassifiedTextCells"],
                             SPECIALTY_BLANK_UNCLASSIFIED_TEXT)

            # The engine reads the edition off the file, so the same call with
            # no profile named patches v1 as v1 — not as the new default.
            patched = Path(temp) / "patched-v1.xlsx"
            receipt = self.patch.patch_copy(legacy, patch_map, patched,
                                            context=ctx)
            self._assert_workbook_locked(patched, "v1-patch")
            self.assertEqual(receipt["formulaCachesInvalidated"],
                             LEGACY_PATCH_CACHES)

            # The two editions never borrow each other's name: v1 bytes named
            # as the second edition are refused on structure, before a write.
            wrong = Path(temp) / "patched-mislabeled.xlsx"
            with self.assertRaises(self.mc.OutputHeldError) as held:
                self.patch.patch_copy(legacy, patch_map, wrong, context=ctx,
                                      profile="school_17_sheet_v2")
            self.assertEqual(held.exception.reason, "workbook_structure_mismatch")
            self.assertFalse(wrong.exists())

    def test_peers_without_the_17_sheet_form_stay_closed_and_write_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            clear_map, patch_map = self._fixtures(temp, profile=None)
            for major in HELD_MAJORS:
                ctx = self._context(major)
                for name, call in (
                        ("create", lambda out: self.tpl.create_workbook(
                            None, out, context=ctx, template_ref="X01",
                            reference_inventory={"files": [], "surveys": []})),
                        ("blank", lambda out: self.tpl.blank_copy(
                            self.bundled, clear_map, out, context=ctx)),
                        ("patch", lambda out: self.patch.patch_copy(
                            self.bundled, patch_map, out, context=ctx)),
                        ("patch-explicit", lambda out: self.patch.patch_copy(
                            self.bundled, patch_map, out, context=ctx,
                            profile="school_17_sheet_v1"))):
                    out = Path(temp) / ("%s-%s.xlsx" % (major, name))
                    with self.subTest(major=major, call=name):
                        with self.assertRaises(self.mc.OutputHeldError) as caught:
                            call(out)
                        self.assertEqual(caught.exception.reason,
                                         "unsupported_finance_profile")
                        self.assertFalse(out.exists())

    def test_dairy_refuses_the_17_sheet_form_as_a_different_workbook(self):
        """Dairy's school form is 28 sheets, so the bundled 17-sheet file is
        refused on structure before anything is written — never copied as if
        it were the dairy form, and never silently cleared with its map.

        This is the dairy counterpart of the food-crops case below: the
        profile is now registered, so the refusal is the structural one and
        the source is read to prove it, instead of the old policy-B hold that
        only had to know the profile was unregistered.
        """
        ctx = self._context("dairy")
        with tempfile.TemporaryDirectory() as temp:
            clear_map, patch_map = self._fixtures(temp, profile=None)
            # Naming no edition is the bound major's own default, which the
            # bundled 17-sheet file does not match: the structural check
            # refuses it, exactly as before the second edition existed.
            unnamed = Path(temp) / "dairy-patch-default.xlsx"
            with self.assertRaises(self.mc.OutputHeldError) as held:
                self.patch.patch_copy(self.bundled, patch_map, unnamed,
                                      context=ctx)
            self.assertEqual(held.exception.reason,
                             "workbook_structure_mismatch")
            self.assertFalse(unnamed.exists())

            # Naming dairy's own form reaches the structural check, which is
            # what stops the 17-sheet file from standing in for 28 sheets.
            oform = Path(temp) / "dairy-patch-own-profile.xlsx"
            with self.assertRaises(self.mc.OutputHeldError) as held:
                self.patch.patch_copy(self.bundled, patch_map, oform,
                                      context=ctx,
                                      profile="dairy_hanwoo_28_sheet_v1")
            self.assertEqual(held.exception.reason,
                             "workbook_structure_mismatch")
            self.assertIn("28", str(held.exception))
            self.assertFalse(oform.exists())

            # A 17-sheet label is another major's form, not a narrower choice.
            for label in ("school_17_sheet_v1", "food_crops_16_sheet_v1"):
                refused = Path(temp) / ("dairy-named-%s.xlsx" % label)
                with self.subTest(call=label):
                    with self.assertRaises(self.mc.OutputHeldError) as held:
                        self.patch.patch_copy(self.bundled, patch_map, refused,
                                              context=ctx, profile=label)
                    self.assertEqual(held.exception.reason,
                                     "unsupported_finance_profile")
                    self.assertFalse(refused.exists())

            blank = Path(temp) / "dairy-blank.xlsx"
            with self.assertRaises(self.mc.OutputHeldError) as held:
                self.tpl.blank_copy(self.bundled, clear_map, blank, context=ctx)
            self.assertEqual(held.exception.reason,
                             "unsupported_finance_profile")
            self.assertFalse(blank.exists())

    def test_the_insect_major_has_no_school_workbook_output_at_all(self):
        ctx = self._context("industrial_insects")
        with tempfile.TemporaryDirectory() as temp:
            clear_map, patch_map = self._fixtures(temp, profile=None)
            out = Path(temp) / "insects.xlsx"
            for call in (
                    lambda: self.tpl.create_workbook(
                        None, out, context=ctx, template_ref="X01",
                        reference_inventory={"files": [], "surveys": []}),
                    lambda: self.tpl.blank_copy(self.bundled, clear_map, out,
                                                context=ctx),
                    lambda: self.patch.patch_copy(self.bundled, patch_map, out,
                                                  context=ctx)):
                with self.assertRaises(self.mc.OutputHeldError) as caught:
                    call()
                self.assertEqual(caught.exception.reason, "unsupported_output")
                self.assertFalse(out.exists())

    def test_food_crops_cannot_reuse_the_17_sheet_map(self):
        """The new profile is a different form, so the old map is refused
        rather than clearing the wrong cells — both ways in."""
        ctx = self._context("food_crops")
        with tempfile.TemporaryDirectory() as temp:
            clear_map, patch_map = self._fixtures(temp, profile=None)
            # A map that names no profile stays the bound major's own
            # default: the blank path refuses it as an unsupported profile,
            # and the patch path reads the source and refuses the structure —
            # the same two answers as before the second edition existed.
            blank = Path(temp) / "food-blank.xlsx"
            with self.assertRaises(self.mc.OutputHeldError) as caught:
                self.tpl.blank_copy(self.bundled, clear_map, blank, context=ctx)
            self.assertEqual(caught.exception.reason,
                             "unsupported_finance_profile")
            self.assertFalse(blank.exists())

            unnamed = Path(temp) / "food-patch-default.xlsx"
            with self.assertRaises(self.mc.OutputHeldError) as held:
                self.patch.patch_copy(self.bundled, patch_map, unnamed,
                                      context=ctx)
            self.assertEqual(held.exception.reason,
                             "workbook_structure_mismatch")
            self.assertFalse(unnamed.exists())

            named = Path(temp) / "food-patch-named.xlsx"
            with self.assertRaises(self.mc.OutputHeldError) as held:
                self.patch.patch_copy(self.bundled, patch_map, named,
                                      context=ctx,
                                      profile="food_crops_16_sheet_v1")
            self.assertEqual(held.exception.reason,
                             "workbook_structure_mismatch")
            self.assertFalse(named.exists())

            # Nothing but this major's own default profile is accepted: a
            # named profile never reopens another major's patch route.
            for major, named in (("fruit_trees", "fruit_16_sheet_v1"),
                                 ("specialty_crops", "food_crops_16_sheet_v1"),
                                 ("food_crops", "school_17_sheet_v1")):
                fresh = self.make_project()
                bind_major(fresh, major)
                named_ctx = self.mc.output_context(fresh, major)
                refused = Path(temp) / ("named-%s.xlsx" % major)
                with self.subTest(major=major, profile=named):
                    with self.assertRaises(self.mc.OutputHeldError) as held:
                        self.patch.patch_copy(self.bundled, patch_map, refused,
                                             context=named_ctx, profile=named)
                    self.assertEqual(held.exception.reason,
                                     "unsupported_finance_profile")
                    self.assertFalse(refused.exists())
