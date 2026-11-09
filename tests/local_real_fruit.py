"""과수 실제 원본 사본에 대한 명시적 게이트 검사(FY2 정정 진입점).

Run:
  KNUAF_REAL_FRUIT_XLSX=<private 사본 경로> \
    python3 -m unittest tests.local_real_fruit -v

Exit 0: FAIL 없음(입력이 없으면 SKIP); 1: FAIL 있음.
파일 이름은 `test*.py` 규칙 밖이라 unittest 자동 발견에 끼지 않는다. 실제 원본은
저장소에 없고, 이 검사는 읽기 전용 원본을 **임시 폴더로 복사한 사본**에만 쓴다.
Excel 실행은 하지 않는다(정정과 지문 대조만).
"""
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from tests._harness import ContractCase, bind_major, runtime


ENV = "KNUAF_REAL_FRUIT_XLSX"
SOURCE = Path(os.environ[ENV]) if os.environ.get(ENV) else None
BASE_SHA = "5f2896dd0c47d9b90a1ec3da30128ea0a9aa47f72aa79156a57bfd7bfd271374"
PROFILE = "fruit_16_sheet_v1_fy1"
BASE_PROFILE = "fruit_16_sheet_v1"
SHEET = "9.경비계획"


class LocalRealFruitChecks(ContractCase):
    """원본 지문 = 원본 프로필, 정정 사본 지문 = 정정 프로필."""

    def setUp(self):
        self.school = runtime("gg_school_excel")
        self.mc = runtime("gg_major_contract")
        self.entry = runtime("gg_fruit_correction")
        tmp = tempfile.TemporaryDirectory(prefix="fy2-real-")
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.copy = self.tmp / "fruit-copy.xlsx"
        self.copy.write_bytes(SOURCE.read_bytes())
        self.root = self.make_project()
        bind_major(self.root, "fruit_trees")
        self.context = self.mc.output_context(self.root, "fruit_trees")

    def check_source_is_the_pinned_one(self):
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(), BASE_SHA)
        # 임시 사본은 원본 프로필 지문을 그대로 만족해야 한다(입력 관문).
        self.school.require_profile_structure(self.copy, BASE_PROFILE)
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            self.school.require_profile_structure(self.copy, PROFILE)

    def check_correction_is_registered_and_minimal(self):
        before = self.copy.read_bytes()
        out = self.tmp / "corrected.xlsx"
        receipt_path = self.tmp / "corrected.json"
        receipt = self.entry.apply_fy1(self.copy, out, receipt_path,
                                       context=self.context)
        self.assertEqual(self.copy.read_bytes(), before)
        # 정정 사본은 등록된 정정 프로필 지문을 만족하고, 원본 지문은 만족하지 않는다.
        self.school.require_profile_structure(out, PROFILE)
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            self.school.require_profile_structure(out, BASE_PROFILE)
        # 16시트 중 바뀐 지문은 경비계획 하나뿐이다.
        base = self.school.profile_structure(BASE_PROFILE)
        corrected = self.school.profile_structure(PROFILE)
        from openpyxl import load_workbook
        book = load_workbook(out, read_only=True)
        self.addCleanup(book.close)
        observed = self.school.formula_fingerprints(book)
        self.assertEqual(list(observed), base["sheets"])
        self.assertEqual({name for name in observed
                          if observed[name] != base["formula_fingerprints"][name]},
                         {SHEET})
        self.assertEqual({name for name in observed
                          if observed[name] != corrected["formula_fingerprints"][name]},
                         set())
        # 영수증은 21칸과 등록 명세 해시를 기록한다.
        self.assertEqual(len(receipt["patched"]), 21)
        self.assertEqual(receipt["corrections"]["cells"], 21)
        self.assertEqual(receipt["map"]["sha256"],
                         receipt["corrections"]["spec"]["sha256"])
        self.assertEqual(json.loads(receipt_path.read_text(encoding="utf-8")),
                         receipt)
        # 영수증 바인딩과 사전 검사도 같은 프로필을 읽는다(사슬 시작은
        # clear/fill이므로 정정 영수증 단독으로 사슬을 시작하지 않는다).
        office = runtime("gg_office")
        self.assertEqual(
            office.template_receipt_binding(out, receipt_path)["workbook_profile"],
            PROFILE)
        authorization = self.mc.authorize_output(
            self.mc.OUTPUT_SCHOOL_WORKBOOK, self.context)
        _, profile = office._preflight_excel_profile(
            out, [authorization], self.context, template_receipt=receipt_path)
        self.assertEqual(profile, PROFILE)

    def check_a_second_correction_is_refused(self):
        out = self.tmp / "corrected.xlsx"
        self.entry.apply_fy1(self.copy, out, self.tmp / "c.json",
                             context=self.context)
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            self.entry.apply_fy1(out, self.tmp / "again.xlsx",
                                 self.tmp / "again.json",
                                 context=self.context)
        self.assertFalse((self.tmp / "again.xlsx").exists())


def load_tests(loader, tests, pattern):
    """없는 입력은 통과가 아니라 보이는 SKIP으로 보고한다."""
    if SOURCE is None:
        raise unittest.SkipTest(ENV + " is unset")
    if not SOURCE.is_file():
        raise unittest.SkipTest(ENV + " file is absent")

    def run(name):
        def check():
            case = LocalRealFruitChecks(methodName="runTest")
            case.setUp()
            try:
                getattr(case, name)()
            finally:
                case.doCleanups()
        return unittest.FunctionTestCase(check, description=name)

    return unittest.TestSuite([
        run("check_source_is_the_pinned_one"),
        run("check_correction_is_registered_and_minimal"),
        run("check_a_second_correction_is_refused"),
    ])


if __name__ == "__main__":
    unittest.main()
