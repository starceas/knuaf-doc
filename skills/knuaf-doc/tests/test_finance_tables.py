"""Synthetic tests for the specialty-crops chapter-IV finance-table
extractor (W-C / D9, FIX-C).

No real student workbooks — a tiny openpyxl-built workbook plus a mini
table map exercise the contract:

- cached cell values become Markdown tables; blank/error/uncached-formula
  value cells become ``확인 불가`` with warnings (V1-08);
- a *named* row whose value cells are all blank stays as ``확인 불가`` —
  only ``optional_when_blank`` labels may be omitted (V1-08);
- percentage display follows the deployment map's explicit ``row_units``
  contract (``ratio`` = stored fraction, ``percent_value`` = stored
  percent) — never magnitude inference (V1-07);
- footnotes accept only structured inputs resolved through
  ``gg_price_assumptions``; free strings, stray keys and unverified
  rates are rejected and the emitted r is always the observed CAGR
  (V1-09);
- ``gg_document.check`` is a publication gate: policy failures are not
  written to disk (V1-09);
- a workbook whose sheets don't match the declared layout signatures is
  held as ``unsupported_layout``; required tables/anchors missing from a
  supported layout fail the run (V1-10);
- a non-specialty major is refused and the source workbook is never
  modified.
"""

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

import openpyxl

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / "scripts"
REPO = TESTS.parents[2]
POLICY = TESTS.parent / "references" / "plain-thesis-policy.json"

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(REPO))
import gg_finance_tables as ft  # noqa: E402
import gg_document as gd  # noqa: E402
import gg_price_assumptions as pa  # noqa: E402
from tests import _harness as th  # noqa: E402

MISSING = "확인 불가"

MINI_MAP = {
    "schema": "test-mini/v1",
    "chapter": "Ⅳ. 재무계획",
    "missing_value": MISSING,
    "source_credit_template": "출처 : 작성자 산출(통합문서 '{sheet}')",
    "lead_template": "{title}{josa} 표 {no}에 나타내었다.",
    "footnote_kinds": {
        "general_price": {"label": "일반물가 상승률", "role": "general"},
        "wage": {"label": "임금 상승률", "role": "wage"},
        "selling_price": {"label": "판매가 상승률", "role": "sales"},
    },
    "sections": [
        {
            "heading": "2. 투자계획 및 원리금 상환계획",
            "tables": [
                {
                    "id": "t_block",
                    "title": "시험 투자내역",
                    "sheet": "S1",
                    "unit": "천원",
                    "extract": {
                        "kind": "block",
                        "anchor": "가. 투자내역",
                        "anchor_col": "B",
                        "header_offsets": [1],
                        "data_offset": 2,
                        "stop_before": ["나. 빈블록"],
                        "cols": ["B", "C", "D"],
                        "value_cols": ["C", "D"],
                        "keep_rows": ["소계"],
                        "optional_when_blank": ["미사용비목"],
                        "col_formats": {
                            "B": "text",
                            "C": "int_comma",
                            "D": "int_comma",
                        },
                        "header_map": {
                            "B": "항목",
                            "C": "금액",
                            "D": "비고액",
                        },
                    },
                    "footnotes": ["general_price", "selling_price"],
                },
                {
                    "id": "t_empty",
                    "title": "빈 시험표",
                    "sheet": "S1",
                    "unit": "천원",
                    "extract": {
                        "kind": "block",
                        "anchor": "나. 빈블록",
                        "anchor_col": "B",
                        "header_offsets": [1],
                        "data_offset": 2,
                        "stop_before": ["다. 비율표"],
                        "cols": ["B", "C"],
                        "value_cols": ["C"],
                        "col_formats": {"B": "text", "C": "int_comma"},
                        "header_map": {"B": "항목", "C": "값"},
                    },
                },
                {
                    # V1-07: 크기 추론 금지 — 행별 선언 단위로만 표시.
                    "id": "t_percent",
                    "title": "비율 시험표",
                    "sheet": "S1",
                    "unit": "%",
                    "extract": {
                        "kind": "block",
                        "anchor": "다. 비율표",
                        "anchor_col": "B",
                        "header_offsets": [1],
                        "data_offset": 2,
                        "stop_before": ["라. 연도블록"],
                        "cols": ["B", "C", "D", "E", "F", "G"],
                        "value_cols": ["C", "D", "E", "F", "G"],
                        "col_formats": {"B": "text"},
                        "row_units": {
                            "잉여율": "percent_value",
                            "소득율": "ratio",
                            "제로율": "ratio",
                            "역성율": "ratio",
                        },
                        "header_map": {"B": "항목"},
                    },
                },
                {
                    "id": "t_years",
                    "title_template": "{year} 경비 시험표",
                    "sheet": "S1",
                    "unit": "천원",
                    "extract": {
                        "kind": "year_blocks",
                        "anchor_regex": "^[가-마]\\.\\d{4}년경비",
                        "anchor_col": "B",
                        "header_offsets": [1],
                        "data_offset": 2,
                        "stop_before": ["노동력 계획"],
                        "cols": ["B", "C"],
                        "value_cols": ["C"],
                        "col_formats": {"B": "text", "C": "int_comma"},
                        "header_map": {"B": "항목", "C": "금액"},
                    },
                },
                {
                    # V1-10: 앵커 0개인 year_blocks는 빈 성공이 아니라 실패.
                    "id": "t_years_no_anchor",
                    "title_template": "{year} 없는 시험표",
                    "sheet": "S1",
                    "unit": "천원",
                    "extract": {
                        "kind": "year_blocks",
                        "anchor_regex": "^[가-마]\\.\\d{4}년없는표",
                        "anchor_col": "B",
                        "header_offsets": [1],
                        "data_offset": 2,
                        "cols": ["B", "C"],
                        "value_cols": ["C"],
                        "col_formats": {"B": "text", "C": "int_comma"},
                        "header_map": {"B": "항목", "C": "금액"},
                    },
                },
                {
                    # year_grid 경로의 선택적 생략·확인 불가 계약.
                    "id": "t_grid",
                    "title": "노동력 시험표",
                    "sheet": "S1",
                    "unit": "일",
                    "extract": {
                        "kind": "year_grid",
                        "anchor": "노동력 계획",
                        "anchor_col": "B",
                        "year_row_offset": 2,
                        "sub_row_offset": 3,
                        "data_offset": 4,
                        "stop_before_regex": "^마\\.",
                        "label_cols": ["B", "C"],
                        "label_header_map": {"B": "구분", "C": "인력"},
                        "optional_when_blank": ["가족"],
                        "pick": {"sub": "일수", "format": "number"},
                    },
                },
                {
                    "id": "t_missing_sheet",
                    "title": "없는 시트 표",
                    "sheet": "없는시트",
                    "unit": "천원",
                    "extract": {
                        "kind": "block",
                        "anchor": "무엇이든",
                        "anchor_col": "B",
                        "header_offsets": [1],
                        "data_offset": 2,
                        "cols": ["B", "C"],
                        "value_cols": ["C"],
                        "col_formats": {},
                        "header_map": {"B": "항목", "C": "값"},
                    },
                },
            ],
        },
    ],
}

# run()에는 지도 주입 통로가 없으므로 _DEFAULT_MAP을 바꿔치기한다.
RUN_MAP = copy.deepcopy(MINI_MAP)
RUN_MAP["sections"][0]["tables"] = [
    t for t in RUN_MAP["sections"][0]["tables"]
    if t["id"] not in ("t_missing_sheet", "t_years_no_anchor", "t_empty")
]

# 미니 워크북과 맞는 레이아웃 지문 — V1-10 시험용.
LAYOUT_MAP = copy.deepcopy(MINI_MAP)
LAYOUT_MAP["layout"] = {
    "id": "mini_layout_v1",
    "signatures": [
        {"sheet": "S1", "col": "B", "max_row": 10,
         "pattern": "^가\\.투자내역"},
        {"sheet": "S1", "col": "B", "max_row": 80,
         "pattern": "노동력계획"},
    ],
}
LAYOUT_MAP_MISSING_SHEET = copy.deepcopy(LAYOUT_MAP)
LAYOUT_MAP_MISSING_SHEET["layout"]["signatures"].append(
    {"sheet": "없는시트", "col": "B", "max_row": 10, "pattern": "x"})


def _entry(sid, roles, *, base_year=2020, obs=None, aggregate=False):
    """check_source_entry를 통과하는 최소 수열 항목."""
    return {
        "id": sid,
        "agency": "국가데이터처(구 통계청)",
        "agency_kind": "national_statistics",
        "statistic_name": "시험통계",
        "item_name": "시험항목",
        "url": "https://kosis.kr/statHtml/statHtml.do?orgId=101&tblId=T",
        "evidence_locator": "시험 근거 위치",
        "base_year": base_year,
        "unit": "지수(2020=100)",
        "dimensions": {"region": "전국"},
        "observations": obs if obs is not None else [
            {"year": 2020, "value": 100.0},
            {"year": 2022, "value": 105.0},
            {"year": 2025, "value": 110.0},
        ],
        "unconfirmed_years": [],
        "allowed_roles": roles,
        "aggregate": aggregate,
    }


REGISTRY = {
    "test.general": _entry("test.general", ["general"]),
    "test.flat": _entry(
        "test.flat", ["general"], base_year=None,
        obs=[{"year": 2021, "value": 5000.0},
             {"year": 2024, "value": 5000.0}]),
    "test.wage": _entry("test.wage", ["wage"]),
    "test.sales": _entry(
        "test.sales", ["sales"], base_year=None,
        obs=[{"year": 2020, "value": 3000.0},
             {"year": 2024, "value": 3300.0}]),
    "test.aggregate": _entry(
        "test.aggregate", ["sales"], aggregate=True),
}

# test.general: (110/100)^(1/5)-1 = 0.0192377...
GENERAL_R = pa.cagr(100.0, 110.0, 5)


def _build_workbook(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "S1"
    # t_block: anchor 가. 투자내역 at B2, headers row 3, data rows 4-10.
    ws["B2"] = "가. 투자내역"
    ws["B3"] = "항목"
    ws["C3"] = "금액"
    ws["D3"] = "비고액"
    ws["B4"] = "토지"
    ws["C4"] = 330000
    ws["D4"] = 12345
    ws["B5"] = "하우스"
    ws["C5"] = None          # 빈 셀 -> 확인 불가
    ws["D5"] = 0
    ws["B6"] = "비율"
    ws["C6"] = "#DIV/0!"     # 오류 캐시 -> 확인 불가
    ws["D6"] = 0
    ws["B7"] = "수식"
    ws["C7"] = "=C4+D4"      # 캐시 없는 수식 -> 확인 불가
    ws["D7"] = 0
    ws["B8"] = "소계"        # keep_rows 구조 행 — 값 없어도 유지
    ws["B9"] = "미확인비목"   # 라벨만 있고 값 전부 공백 -> 확인 불가 행
    ws["B10"] = "미사용비목"  # optional_when_blank -> 생략
    # t_empty: anchor 나. 빈블록 at B12; 데이터 영역이 진짜로 비어 있다.
    ws["B12"] = "나. 빈블록"
    ws["B13"] = "항목"
    ws["C13"] = "값"
    ws["B15"] = "다. 비율표"   # stop_before for t_empty/t_block 계열
    # t_percent: headers row 16, data rows 17-20.
    ws["B16"] = "항목"
    for col, y in zip("CDEFG", range(2026, 2031)):
        ws["%s16" % col] = "%d년" % y
    vals = [0.5, 1, 1.5, 1.6, 2.5]
    ws["B17"] = "영농잉여율"   # percent_value: 저장값이 이미 백분율
    ws["B18"] = "소득율"       # ratio: 저장값이 비율(×100 표시)
    ws["B19"] = "제로율"       # ratio: 0은 정상값
    ws["B20"] = "역성율"       # ratio: 음수 허용
    for col, v in zip("CDEFG", vals):
        ws["%s17" % col] = v
        ws["%s18" % col] = v
        ws["%s19" % col] = 0
        ws["%s20" % col] = -0.05
    ws["B22"] = "라. 연도블록 시작"   # stop_before for t_percent
    # t_years: five year anchors 2026-2030.
    row = 24
    for i, marker in enumerate(("가", "나", "다", "라", "마")):
        ws.cell(row=row, column=2, value="%s. %d년 경비" % (marker, 2026 + i))
        ws.cell(row=row + 1, column=2, value="항목")
        ws.cell(row=row + 1, column=3, value="금액")
        ws.cell(row=row + 2, column=2, value="수선비")
        ws.cell(row=row + 2, column=3, value=800 + i)
        row += 5
    # t_grid: year_grid — anchor row 50, year row 52, sub row 53, data 54+.
    ws["B50"] = "노동력 계획"
    ws["F52"] = "2026년"
    ws["I52"] = "2027년"
    ws["F53"] = "인원"
    ws["G53"] = "일수"
    ws["H53"] = "금액"
    ws["I53"] = "인원"
    ws["J53"] = "일수"
    ws["K53"] = "금액"
    ws["B54"] = "자가노동비"
    ws["C54"] = "본인"
    ws["G54"] = 10
    ws["J54"] = 20
    ws["C55"] = "가족"        # optional_when_blank -> 생략
    ws["C56"] = "외주"        # 라벨만 있고 값 전부 공백 -> 확인 불가
    ws["B58"] = "마. 종료"     # stop_before_regex ^마\.
    wb.save(path)


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.xlsx = Path(cls._tmp.name) / "mini.xlsx"
        cls.map_path = Path(cls._tmp.name) / "map.json"
        _build_workbook(cls.xlsx)
        cls.map_path.write_text(
            json.dumps(MINI_MAP, ensure_ascii=False), encoding="utf-8")
        cls.before_hash = hashlib.sha256(
            cls.xlsx.read_bytes()).hexdigest()
        cls.result = ft.generate(
            cls.xlsx, major_id="specialty_crops",
            map_path=cls.map_path)
        cls.md = cls.result["markdown"]

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()


class TestExtract(_Base):
    def test_values_match_cells(self):
        # C4=330000 -> '330,000'; D4=12345 -> '12,345'
        self.assertIn("330,000", self.md)
        self.assertIn("12,345", self.md)
        self.assertIn("| 토지 | 330,000 | 12,345 |", self.md)

    def test_blank_error_uncached_become_missing(self):
        self.assertIn("| 하우스 | %s | 0 |" % MISSING, self.md)
        self.assertIn("| 비율 | %s | 0 |" % MISSING, self.md)
        self.assertIn("| 수식 | %s | 0 |" % MISSING, self.md)
        reasons = {w["cell"]: w["reason"] for w in self.result["warnings"]
                   if w.get("cell")}
        self.assertEqual(reasons.get("'S1'!C5"), "empty")
        self.assertEqual(reasons.get("'S1'!C6"), "error")
        self.assertEqual(reasons.get("'S1'!C7"), "uncached_formula")

    def test_named_blank_row_preserved_as_missing(self):
        # V1-08: 라벨은 있고 값이 전부 빈 행은 확인 불가로 보존한다.
        self.assertIn(
            "| 미확인비목 | %s | %s |" % (MISSING, MISSING), self.md)
        warns = [w for w in self.result["warnings"]
                 if w.get("row_label") == "미확인비목"]
        self.assertTrue(warns)

    def test_optional_blank_row_omitted(self):
        # V1-08: 지도가 명시한 미사용 행만 생략한다.
        self.assertNotIn("미사용비목", self.md)

    def test_keep_rows_structural_row_kept(self):
        # V1-08: keep_rows 구조 행은 값이 없어도 남는다.
        self.assertIn("| 소계 |", self.md)

    def test_empty_table_skipped(self):
        skipped = {s["table"]: s["reason"] for s in self.result["skipped"]}
        self.assertEqual(skipped.get("t_empty"), "empty")
        self.assertNotIn("빈 시험표", self.md)

    def test_missing_sheet_skipped(self):
        skipped = {s["table"]: s["reason"] for s in self.result["skipped"]}
        self.assertEqual(skipped.get("t_missing_sheet"), "sheet_missing")

    def test_year_blocks_count_five(self):
        year_tables = [e["table"] for e in self.result["emitted"]
                       if e["table"].startswith("t_years@")]
        self.assertEqual(len(year_tables), 5)
        self.assertIn("2026년", year_tables[0])
        self.assertIn("2030년", year_tables[4])

    def test_year_blocks_zero_anchors_fail(self):
        # V1-10: 앵커 0개는 빈 성공이 아니라 anchor_missing + 필수 표 누락.
        skipped = {s["table"]: s["reason"] for s in self.result["skipped"]}
        self.assertEqual(skipped.get("t_years_no_anchor"), "anchor_missing")
        missing = {m["table"]: m["reason"]
                   for m in self.result["missing_required"]}
        self.assertEqual(missing.get("t_years_no_anchor"), "anchor_missing")
        self.assertEqual(missing.get("t_missing_sheet"), "sheet_missing")

    def test_year_grid_optional_and_missing_rows(self):
        # V1-08 year_grid 경로: '가족'은 생략, '외주'는 확인 불가 보존.
        grid = self.md[self.md.find("노동력 시험표"):]
        self.assertIn("| 자가노동비 | 본인 | 10 | 20 |", grid)
        self.assertNotIn("가족", grid)
        self.assertIn("|  | 외주 | %s | %s |" % (MISSING, MISSING), grid)

    def test_source_credit_per_table(self):
        credits = [l for l in self.md.splitlines()
                   if l.strip().startswith("출처")]
        self.assertEqual(len(credits), len(self.result["emitted"]))
        self.assertTrue(all("통합문서" in c for c in credits))

    def test_lead_in_reference(self):
        self.assertIn("시험 투자내역은 표 1에 나타내었다.", self.md)

    def test_footnote_missing_warned(self):
        self.assertTrue(any(
            w["reason"] == "footnote_missing"
            and w["table"] == "t_block"
            for w in self.result["warnings"]))

    def test_no_forbidden_terms(self):
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        for term in policy["forbidden_body_terms"]:
            self.assertNotIn(term, self.md)

    def test_document_check_clean(self):
        issues = gd.check(self.md, str(self.xlsx.parent),
                          major_id="specialty_crops")
        errors = [i for i in issues
                  if i[0] not in gd.INFO_CHECKS | gd.WARNING_CHECKS]
        self.assertEqual(errors, [])

    def test_workbook_unchanged(self):
        self.assertEqual(
            self.before_hash,
            hashlib.sha256(self.xlsx.read_bytes()).hexdigest())


class TestRowUnits(_Base):
    """V1-07: 행 단위 계약 — 같은 값 0.5/1/1.5/1.6/2.5가 선언 단위대로
    다르게 표시되고 크기 추론은 없다."""

    def _row(self, label):
        for line in self.md.splitlines():
            if line.startswith("| %s " % label):
                return line
        self.fail("행 없음: " + label)

    def test_percent_value_row(self):
        # 저장값이 이미 백분율 — 그대로 표시(×100 금지, >150% 유지).
        self.assertEqual(
            self._row("영농잉여율"),
            "| 영농잉여율 | 0.5 | 1 | 1.5 | 1.6 | 2.5 |")

    def test_ratio_row(self):
        # 저장값이 비율 — ×100 해서 표시(1.5 경계도 일관 적용).
        self.assertEqual(
            self._row("소득율"),
            "| 소득율 | 50 | 100 | 150 | 160 | 250 |")

    def test_zero_and_negative_ratio(self):
        self.assertEqual(
            self._row("제로율"),
            "| 제로율 | 0 | 0 | 0 | 0 | 0 |")
        self.assertEqual(
            self._row("역성율"),
            "| 역성율 | -5 | -5 | -5 | -5 | -5 |")


class TestFootnotes(unittest.TestCase):
    """V1-09: 구조화 각주 — 레지스트리 해석·검증·CAGR 출력."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.xlsx = Path(cls._tmp.name) / "mini.xlsx"
        cls.map_path = Path(cls._tmp.name) / "map.json"
        _build_workbook(cls.xlsx)
        cls.map_path.write_text(
            json.dumps(MINI_MAP, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _gen(self, footnotes):
        return ft.generate(
            self.xlsx, major_id="specialty_crops",
            footnotes=footnotes, map_path=self.map_path,
            registry=REGISTRY)

    def _warn_reasons(self, result, kind=None):
        return [w for w in result["warnings"]
                if w["reason"] in ("footnote_invalid", "footnote_missing")
                and (kind is None or kind in (w.get("detail") or ""))]

    def test_valid_structured_footnote(self):
        r = self._gen({"general_price": {
            "source_id": "test.general", "base_year": 2020,
            "observation": {"start_year": 2020, "end_year": 2025}}})
        self.assertIn(
            "* 일반물가 상승률 %.4f%% 참고값" % (GENERAL_R * 100),
            r["markdown"])
        self.assertFalse(self._warn_reasons(r, "general_price"))

    def test_observation_optional_defaults_to_series(self):
        r = self._gen({"general_price": {
            "source_id": "test.general", "base_year": 2020}})
        self.assertIn("관측기간 2020~2025년", r["markdown"])

    def test_declared_rate_verified_not_used(self):
        # 검산용 rate가 CAGR과 일치하면 통과 — 출력은 계산값.
        r = self._gen({"general_price": {
            "source_id": "test.general", "base_year": 2020,
            "rate": round(GENERAL_R, 6)}})
        self.assertIn("%.4f%% 참고값" % (GENERAL_R * 100), r["markdown"])
        # 불일치 rate는 거부.
        r = self._gen({"general_price": {
            "source_id": "test.general", "base_year": 2020,
            "rate": 0.99}})
        self.assertTrue(self._warn_reasons(r, "general_price"))
        self.assertNotIn("99", r["markdown"])
        self.assertIn("* 일반물가 상승률 %s" % MISSING, r["markdown"])

    def test_zero_rate_is_legitimate(self):
        # 평탄 수열 → r=0도 정상값(결측과 혼동 금지).
        r = self._gen({"general_price": {
            "source_id": "test.flat", "base_year": None}})
        self.assertIn("* 일반물가 상승률 0.0000% 참고값", r["markdown"])
        self.assertFalse(self._warn_reasons(r, "general_price"))

    def test_rejects_unstructured_inputs(self):
        bad_inputs = [
            "임금 상승률 90% 적용 (출처: 개인 블로그)",      # 자유 문자열
            99,                                            # 숫자
            {"note": "임의 문구"},                          # 임의 문구 필드
            {"label": "일반물가", "rate": "99%",
             "source_name": "개인 블로그", "base_year": 2020},
            {"source_id": "test.general"},                # base_year 없음
            {"source_id": "test.general", "base_year": 2021},   # 불일치
            {"source_id": "test.general", "base_year": "2020"}, # 문자열
            {"source_id": "test.general", "base_year": True},   # bool
            {"source_id": " test.general ", "base_year": 2020},  # 공백 변형
            {"source_id": "TEST.GENERAL", "base_year": 2020},   # 대소문자
            {"source_id": "unknown.blog", "base_year": 2020},   # 미등록
            {"source_id": "test.wage", "base_year": 2020},      # 역할 불일치
            {"source_id": "test.general", "base_year": 2020,
             "observation": {"start_year": 2020, "end_year": 2026}},  # 무관측
            {"source_id": "test.general", "base_year": 2020,
             "observation": {"start_year": 2025, "end_year": 2020}},  # 역순
            {"source_id": "test.general", "base_year": 2020,
             "rate": float("nan")},
            {"source_id": "test.general", "base_year": 2020,
             "rate": True},
            {"source_id": "test.general", "base_year": 2020,
             "status": "APPLIED"},
            {"status": "not_applied"},                      # 사유 없음
        ]
        for info in bad_inputs:
            with self.subTest(info=info):
                r = self._gen({"general_price": info})
                self.assertTrue(
                    self._warn_reasons(r, "general_price"))
                self.assertIn(
                    "* 일반물가 상승률 %s" % MISSING, r["markdown"])
        self.assertNotIn("90%", self._gen(
            {"general_price": bad_inputs[0]})["markdown"])

    def test_not_applied_with_reason(self):
        r = self._gen({"general_price": {
            "status": "not_applied",
            "reason": "자재비 가격자료 없음"}})
        self.assertIn("* 일반물가 상승률 미적용(명시 선택): 자재비 가격자료 없음",
                      r["markdown"])
        self.assertFalse(self._warn_reasons(r, "general_price"))

    def test_sales_aggregate_rejected(self):
        # 판매가 역할에 총합 지수를 대입하면 거부(자동 대용 금지).
        r = self._gen({"selling_price": {
            "source_id": "test.aggregate", "base_year": 2020}})
        self.assertTrue(self._warn_reasons(r, "selling_price"))
        self.assertNotIn("시험통계", r["markdown"])
        self.assertIn("* 판매가 상승률 %s" % MISSING, r["markdown"])

    def test_target_dimensions_checked(self):
        r = self._gen({"general_price": {
            "source_id": "test.general", "base_year": 2020,
            "target_dimensions": {"crop": "봄감자"}}})
        self.assertTrue(self._warn_reasons(r, "general_price"))
        r = self._gen({"general_price": {
            "source_id": "test.general", "base_year": 2020,
            "target_dimensions": {"region": "전국"}}})
        self.assertFalse(self._warn_reasons(r, "general_price"))


class TestLayout(unittest.TestCase):
    """V1-10: 학과 템플릿 레이아웃 지문."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.xlsx = Path(self._tmp.name) / "mini.xlsx"
        _build_workbook(self.xlsx)
        self.map_path = Path(self._tmp.name) / "map.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _write_map(self, m):
        self.map_path.write_text(
            json.dumps(m, ensure_ascii=False), encoding="utf-8")

    def test_matching_signatures_pass(self):
        self._write_map(LAYOUT_MAP)
        r = ft.generate(self.xlsx, major_id="specialty_crops",
                        map_path=self.map_path)
        self.assertEqual(r.get("status"), "generated")
        self.assertEqual(r["layout"], "mini_layout_v1")

    def test_pattern_mismatch_holds(self):
        m = copy.deepcopy(LAYOUT_MAP)
        m["layout"]["signatures"][0]["pattern"] = "^절대없는앵커"
        self._write_map(m)
        r = ft.generate(self.xlsx, major_id="specialty_crops",
                        map_path=self.map_path)
        self.assertEqual(r["status"], "unsupported_layout")
        self.assertEqual(r["markdown"], None)
        self.assertEqual(r["emitted"], [])
        self.assertTrue(r["failed_signatures"])

    def test_missing_signature_sheet_holds(self):
        self._write_map(LAYOUT_MAP_MISSING_SHEET)
        r = ft.generate(self.xlsx, major_id="specialty_crops",
                        map_path=self.map_path)
        self.assertEqual(r["status"], "unsupported_layout")
        self.assertEqual(r["failed_signatures"][0]["sheet"], "없는시트")

    def test_failed_signature_reports_desc_and_detail(self):
        m = copy.deepcopy(LAYOUT_MAP)
        m["layout"]["signatures"][0]["pattern"] = "^절대없는앵커"
        m["layout"]["signatures"][0]["desc"] = "설명 라벨"
        self._write_map(m)
        r = ft.generate(self.xlsx, major_id="specialty_crops",
                        map_path=self.map_path)
        f = r["failed_signatures"][0]
        self.assertEqual(f["sheet"], "S1")
        self.assertEqual(f["pattern"], "^절대없는앵커")
        self.assertEqual(f["desc"], "설명 라벨")
        self.assertIn("S1", f["detail"])
        self.assertIn("설명 라벨", f["detail"])


REAL_MAP_PATH = REPO / "skills" / "knuaf-doc" / "references" / \
    "specialty-crops" / "finance-table-map.json"

# 학과 지문 9개 시트의 최소 앵커 — 시트 10의 연도 접두만 변형한다.
DEPT_ANCHORS = {
    "1. 기초재무상태조사": (2, "가. 자산"),
    "4. 원리금상환계획": (2, "가. 상환계획 종합"),
    "7. 영농자재소요계획": (2, "적용기준"),
    "8. 노무비계획": (2, "가. 노동력 소요 및 노무비 계획"),
    "11. 생산원가계획": (2, "과목"),
    "13. 추정대차대조표": (2, "과목"),
    "14. 현금흐름계획": (2, "구분"),
    "15.추정소득분석": (2, "비목별 소득"),
}


def _dept_shaped_workbook(path, year_label):
    """학과 템플릿 지문을 모두 만족하는 최소 워크북.

    year_label: 시트 '10. 감가상각비계획'의 연도 앵커 서식
    ('가. 2026년' 학과형 / '2027년' FX형 / None 레거시형).
    """
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for sheet, (row, label) in DEPT_ANCHORS.items():
        ws = wb.create_sheet(sheet)
        ws.cell(row=row, column=2, value=label)
    dep = wb.create_sheet("10. 감가상각비계획")
    if year_label is not None:
        dep.cell(row=4, column=2, value=year_label)
    wb.save(path)


class TestLayoutFxVariant(unittest.TestCase):
    """FIX-C2: 연도 앵커 서식 한 곳만 다른 학과 파생 워크북(FX형)은 허용하고,
    구조가 다른 레거시형은 거부한다."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _gen(self, wb_name, year_label):
        xlsx = self.dir / wb_name
        _dept_shaped_workbook(xlsx, year_label)
        return ft.generate(xlsx, major_id="specialty_crops",
                           map_path=REAL_MAP_PATH)

    def test_dept_prefixed_year_anchor_passes(self):
        r = self._gen("dept.xlsx", "가. 2026년")
        self.assertEqual(r["status"], "generated")
        self.assertEqual(r["layout"], "knuaf_dept_workbook_v1")

    def test_fx_unprefixed_year_anchor_passes(self):
        r = self._gen("fx.xlsx", "2027년")
        self.assertEqual(r["status"], "generated")
        self.assertEqual(r["layout"], "knuaf_dept_workbook_v1")

    def test_strict_pattern_would_reject_fx(self):
        # 수정 전 지문('가. NNNN년'만 허용)이 FX형을 거부했음을 직접 확인.
        import re
        old = re.compile(r"^[가-힣]\.\d{4}년")
        new = re.compile(r"^(?:[가-힣]\.)?\d{4}년")
        self.assertIsNone(old.search("2027년"))
        self.assertIsNotNone(new.search("2027년"))
        self.assertIsNotNone(old.search("가.2026년"))
        self.assertIsNotNone(new.search("가.2026년"))

    def test_legacy_flat_year_anchor_rejected(self):
        # 레거시형: 연도 블록 앵커가 아예 없다(평면 '연도' 표).
        r = self._gen("legacy.xlsx", None)
        self.assertEqual(r["status"], "unsupported_layout")
        self.assertIsNone(r["markdown"])
        failed = {f["sheet"]: f for f in r["failed_signatures"]}
        self.assertIn("10. 감가상각비계획", failed)
        f = failed["10. 감가상각비계획"]
        self.assertEqual(f["pattern"], r"^(?:[가-힣]\.)?\d{4}년")
        self.assertTrue(f["desc"])

    def test_bare_numeric_year_not_an_anchor(self):
        # '2027' 같은 숫자 연도는 'NNNN년' 앵커가 아니다 — 레거시와의 경계.
        r = self._gen("bare.xlsx", 2027)
        self.assertEqual(r["status"], "unsupported_layout")
        failed = {f["sheet"] for f in r["failed_signatures"]}
        self.assertIn("10. 감가상각비계획", failed)

    def test_other_signature_failure_still_fatal(self):
        # 한 지문이 관대해졌다고 전체가 느슨해지지 않는다 — 다른 시트
        # 지문이 실패하면 여전히 보류.
        xlsx = self.dir / "broken.xlsx"
        _dept_shaped_workbook(xlsx, "2027년")
        wb = openpyxl.load_workbook(xlsx)
        ws = wb["1. 기초재무상태조사"]
        ws.cell(row=2, column=2, value="엉뚱한머리")
        wb.save(xlsx)
        r = ft.generate(xlsx, major_id="specialty_crops",
                        map_path=REAL_MAP_PATH)
        self.assertEqual(r["status"], "unsupported_layout")
        failed = {f["sheet"] for f in r["failed_signatures"]}
        self.assertEqual(failed, {"1. 기초재무상태조사"})


class TestGuards(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.xlsx = Path(self._tmp.name) / "mini.xlsx"
        self.map_path = Path(self._tmp.name) / "map.json"
        _build_workbook(self.xlsx)
        self.map_path.write_text(
            json.dumps(MINI_MAP, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_wrong_major_rejected(self):
        for bad in ("industrial_insects", "hort_env_systems",
                    "fruit_trees", None):
            with self.assertRaises(ValueError):
                ft.generate(self.xlsx, major_id=bad,
                            map_path=self.map_path)

    def test_run_holds_on_unbound_project(self):
        value, code = ft.run(self.xlsx.parent, self.xlsx,
                             major_id="specialty_crops",
                             out="out/tables.md")
        self.assertEqual(code, 2)
        self.assertEqual(value["status"], "held")
        self.assertFalse((self.xlsx.parent / "out/tables.md").exists())


class TestRun(unittest.TestCase):
    """run() 경로: 바인딩·레이아웃·필수 표·정책 게이트·쓰기 가드."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "proj"
        self.root.mkdir()
        th.runtime("gg_core").init(self.root)
        th.bind_major(self.root, "specialty_crops")
        self.xlsx = Path(self._tmp.name) / "mini.xlsx"
        _build_workbook(self.xlsx)
        self.run_map = Path(self._tmp.name) / "run-map.json"
        self.run_map.write_text(
            json.dumps(RUN_MAP, ensure_ascii=False), encoding="utf-8")
        self._saved_map = ft._DEFAULT_MAP
        ft._DEFAULT_MAP = self.run_map

    def tearDown(self):
        ft._DEFAULT_MAP = self._saved_map
        self._tmp.cleanup()

    def _input(self, obj):
        p = self.root / "footnotes.json"
        p.write_text(json.dumps(
            {"footnotes": obj}, ensure_ascii=False), encoding="utf-8")
        return "footnotes.json"

    def test_run_generates(self):
        value, code = ft.run(self.root, self.xlsx,
                             major_id="specialty_crops",
                             out="build/tables.md")
        self.assertEqual(code, 0)
        self.assertEqual(value["status"], "generated")
        out = self.root / "build/tables.md"
        self.assertTrue(out.is_file())
        self.assertIn("| 토지 | 330,000 |", out.read_text(encoding="utf-8"))

    def test_run_no_overwrite(self):
        ft.run(self.root, self.xlsx, major_id="specialty_crops",
               out="build/tables.md")
        with self.assertRaises(ValueError):
            ft.run(self.root, self.xlsx, major_id="specialty_crops",
                   out="build/tables.md")

    def test_run_outside_output_rejected(self):
        with self.assertRaises(ValueError):
            ft.run(self.root, self.xlsx, major_id="specialty_crops",
                   out="../escape.md")

    def test_run_outside_input_rejected(self):
        with self.assertRaises(ValueError):
            ft.run(self.root, self.xlsx, major_id="specialty_crops",
                   input_path="../escape.json", out="build/t.md")

    def test_unsupported_layout_held_no_write(self):
        legacy = Path(self._tmp.name) / "legacy.xlsx"
        wb = openpyxl.Workbook()
        wb.active.title = "전혀다른시트"
        wb.save(legacy)
        ft._DEFAULT_MAP = self._saved_map  # 실제 지도 = 학과 지문
        try:
            value, code = ft.run(self.root, legacy,
                                 major_id="specialty_crops",
                                 out="build/legacy.md")
        finally:
            ft._DEFAULT_MAP = self.run_map
        self.assertEqual(code, 2)
        self.assertEqual(value["status"], "held")
        self.assertEqual(value["reason"], "unsupported_workbook_layout")
        self.assertFalse((self.root / "build/legacy.md").exists())
        # 사용자용 보류 사유가 어느 시트 지문이 실패했는지 구체적으로 담는다.
        self.assertIn("1. 기초재무상태조사", value["detail"])
        self.assertTrue(value["failed_signatures"])

    def test_required_tables_missing_fails_no_write(self):
        m = copy.deepcopy(RUN_MAP)
        m["sections"][0]["tables"].append({
            "id": "t_absent", "title": "없는 필수표", "sheet": "S9",
            "unit": "천원",
            "extract": {"kind": "block", "anchor": "x", "anchor_col": "B",
                        "header_offsets": [1], "data_offset": 2,
                        "cols": ["B", "C"], "value_cols": ["C"],
                        "col_formats": {}, "header_map": {}}})
        self.run_map.write_text(
            json.dumps(m, ensure_ascii=False), encoding="utf-8")
        value, code = ft.run(self.root, self.xlsx,
                             major_id="specialty_crops",
                             out="build/tables.md")
        self.assertEqual(code, 2)
        self.assertEqual(value["status"], "failed")
        self.assertEqual(value["reason"], "required_tables_missing")
        self.assertIn("t_absent", {x["table"] for x in value["missing"]})
        self.assertFalse((self.root / "build/tables.md").exists())

    def test_policy_gate_blocks_publish(self):
        # not_applied 사유는 학생 자유 텍스트 — 금칙어가 들어가면
        # 문서 정책 게이트가 발행을 막고 파일은 쓰지 않는다.
        value, code = ft.run(
            self.root, self.xlsx, major_id="specialty_crops",
            input_path=self._input({"general_price": {
                "status": "not_applied", "reason": "NPV 검증"}}),
            out="build/tables.md")
        self.assertEqual(code, 2)
        self.assertEqual(value["status"], "failed")
        self.assertEqual(value["reason"], "policy_gate_failed")
        self.assertTrue(any(i[0] == "forbidden_term"
                            for i in value["issues"]))
        self.assertFalse((self.root / "build/tables.md").exists())

    def test_receipt_path_loading_and_missing_receipt(self):
        src = pa.resolve_series("official.kosis.cpi.total")
        resolved = pa.resolve_series_rate(src)
        info = {"source_id": src["id"], "base_year": src["base_year"],
                "application_base_year": 2026}
        receipt = {"profile": ft.FINANCE_PROFILE,
                   "file_hash": hashlib.sha256(self.xlsx.read_bytes()).hexdigest(),
                   "price_assumptions": {"general": {**info, "status": "applied",
                       "rate": resolved["rate"], "observation": resolved["observation"]}}}
        (self.root / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
        for ref, applied in [("receipt.json", False), ("missing.json", False)]:
            (self.root / "input.json").write_text(json.dumps({
                "footnotes": {"general_price": info}, "application_receipt": ref}), encoding="utf-8")
            value, code = ft.run(self.root, self.xlsx, major_id="specialty_crops",
                                 input_path="input.json", out=ref+".md")
            self.assertEqual(code, 0)
            text = Path(value["path"]).read_text(encoding="utf-8")
            self.assertEqual("% 적용" in text, applied)
            if ref == 'missing.json':
                self.assertTrue(any(w["reason"] == "receipt_unavailable" for w in value["warnings"]))
        for ref in ("../escape.json", {"sha256": "inline"}):
            (self.root / "input.json").write_text(json.dumps({
                "footnotes": {"general_price": info}, "application_receipt": ref}), encoding="utf-8")
            with self.assertRaises(ValueError):
                ft.run(self.root, self.xlsx, major_id="specialty_crops", input_path="input.json",
                       out="escape.md")
            self.assertFalse((self.root / "escape.md").exists())

    def test_incomplete_year_group_not_published(self):
        m = copy.deepcopy(RUN_MAP)
        m["plan_years"] = {"sheet": "S1", "header_cells": [c+"16" for c in "CDEFG"]}
        self.run_map.write_text(json.dumps(m), encoding="utf-8")
        w = openpyxl.load_workbook(self.xlsx)
        for row in (29, 34, 39, 44):
            w["S1"].cell(row, 2).value = None
        w.save(self.xlsx)
        w.close()
        value, code = ft.run(self.root, self.xlsx, major_id="specialty_crops", out="incomplete.md")
        self.assertEqual((value["status"], code), ("failed", 2))
        self.assertTrue(any(x["table"] == "t_years" for x in value["missing"]))
        self.assertFalse((self.root / "incomplete.md").exists())

    def test_policy_gate_passes_clean_output(self):
        value, code = ft.run(
            self.root, self.xlsx, major_id="specialty_crops",
            input_path=self._input({"general_price": {
                "source_id": "official.kosis.cpi.total",
                "base_year": 2020}}),
            out="build/tables.md")
        self.assertEqual(code, 0)
        self.assertEqual(value["status"], "generated")
        out = self.root / "build/tables.md"
        text = out.read_text(encoding="utf-8")
        self.assertIn("* 일반물가 상승률", text)
        self.assertIn("official.kosis.cpi.total", text)


def _general_evidence(path, rate=GENERAL_R):
    """Synthetic annual producer formulas, separate from the extracted tables."""
    w = openpyxl.load_workbook(path)
    for name in ('7. 영농자재소요계획', '9. 경비계획'):
        s = w.create_sheet(name)
        for i in range(5):
            anchor = 2 + i * 10
            s['B%d' % anchor] = '%d년' % (2026+i)
            s['B%d' % (anchor+3)] = '합성 물가 적용 비목'
            s['C%d' % (anchor+3)] = 100 if not i else '=C%d*(1+%r)' % (anchor-7, rate)
    w.save(path)
    w.close()


def _workbook_view(path):
    w = openpyxl.load_workbook(path, data_only=True, read_only=True)
    files = ft._sheet_files(path)
    views = {s.title: ft._SheetView(s, *ft._sheet_meta(path, files[s.title])) for s in w}
    w.close()
    def view_for(name):
        for title, view in views.items():
            if ft._SHEET_KEY.sub('', title) == ft._SHEET_KEY.sub('', name):
                return view, title
        return None, name
    return view_for


class TestV2Receipt(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.xlsx = Path(self.tmp.name) / "mini.xlsx"
        _build_workbook(self.xlsx)
        _general_evidence(self.xlsx)
        self.map_path = self.xlsx.with_suffix(".map.json")
        self.map_path.write_text(json.dumps(MINI_MAP), encoding="utf-8")
        self.info = {"source_id": "test.general", "base_year": 2020,
                     "application_base_year": 2026}

    def gen(self, receipt=None, info=None):
        kw = {} if receipt is None else {"application_receipt": receipt}
        return ft.generate(self.xlsx, major_id="specialty_crops",
                           map_path=self.map_path, registry=REGISTRY,
                           footnotes={"general_price": info or self.info}, **kw)

    def receipt(self):
        return {"schema": "gg-xlsx-formula-patch-receipt/v1",
                "output": {"sha256": hashlib.sha256(self.xlsx.read_bytes()).hexdigest()},
                "d8": {"rates": {"general": {
                    "evidence_ref": {"source_id": "test.general"},
                    "base_year": 2020, "application_base_year": 2026,
                    "observation_years": [2020, 2025],
                    "observed_values": [100, 110], "r": GENERAL_R}}}}

    def test_no_receipt_cannot_claim_applied(self):
        r = self.gen()
        self.assertNotIn("% 적용", r["markdown"])
        self.assertTrue(any(w["reason"] == "footnote_unverified" for w in r["warnings"]))

    def test_sha_bound_patch_receipt(self):
        r = self.gen(self.receipt())
        self.assertIn("%.4f%% 적용" % (GENERAL_R * 100), r["markdown"])
        self.assertFalse(any(w["reason"] == "footnote_unverified" for w in r["warnings"]))

    def test_applied_formula_full_string(self):
        r = self.gen(self.receipt())
        line = next(l for l in r["markdown"].splitlines() if l.startswith("* 일반물가 상승률"))
        self.assertEqual(line,
            ("* 일반물가 상승률 %.4f%% 적용 (출처: 국가데이터처(구 통계청) "
             "시험통계 시험항목 [test.general], 기준연도 2020, 관측기간 2020~2025년, "
             "관측 연평균 변화율 %.4f%%, 산식 r=(끝값/시작값)^(1/5)-1, "
             "적용 기초연도 2026년)") % (GENERAL_R * 100, GENERAL_R * 100))

    def test_malformed_and_structural_only_receipts_downgrade(self):
        for receipt in ({}, [], {"schema": "unknown"},
                        {"schema": "gg-xlsx-formula-patch-receipt/v1", "output": []},
                        {"schema": "gg-xlsx-formula-patch-receipt/v1",
                         "output": {"sha256": hashlib.sha256(self.xlsx.read_bytes()).hexdigest()},
                         "d8": {"rates": {}}}):
            with self.subTest(receipt=receipt):
                self.assertNotIn("% 적용", self.gen(receipt)["markdown"])

    def test_receipt_mismatch_variants(self):
        for field, value in [("r", GENERAL_R + .0001), ("base_year", 2015),
                             ("evidence_ref", {"source_id": "test.flat"}),
                             ("application_base_year", 2025),
                             ("observed_values", [100, 111]),
                             ("observation_years", [2020, 2024]),
                             ("target_dimensions", {"region": "제주"}),
                             ("unit", "원/kg")]:
            with self.subTest(field=field):
                receipt = self.receipt()
                receipt["d8"]["rates"]["general"][field] = value
                self.assertNotIn("% 적용", self.gen(receipt)["markdown"])
        receipt = self.receipt()
        receipt["output"]["sha256"] = "0" * 64
        self.assertNotIn("% 적용", self.gen(receipt)["markdown"])

    def test_manifest_and_tampered_endpoints(self):
        manifest = {"profile": ft.FINANCE_PROFILE,
                    "file_hash": hashlib.sha256(self.xlsx.read_bytes()).hexdigest(),
                    "price_assumptions": {"general": {
                        **self.info, "status": "applied", "rate": GENERAL_R,
                        "observation": {"start_year": 2020, "end_year": 2025,
                                        "start_value": 100, "end_value": 110}}}}
        self.assertNotIn("% 적용", self.gen(manifest)["markdown"])
        # Parsing support is retained, but a legacy producer cannot attest a
        # department-layout output, regardless of the declared SHA.
        rates, _ = ft._receipt_rates(manifest, manifest['file_hash'], layout_id=ft.FINANCE_PROFILE)
        self.assertEqual(rates['general'][0]['rate'], GENERAL_R)
        line, err = ft._resolve_footnote('general_price', MINI_MAP['footnote_kinds']['general_price'],
            self.info, REGISTRY, applications=rates, view_for=_workbook_view(self.xlsx))
        self.assertIsNone(err)
        self.assertIn('% 적용', line)
        for key, value in [("start_value", 1), ("end_value", 111),
                           ("start_year", 2022), ("end_year", 2024)]:
            with self.subTest(key=key):
                bad = copy.deepcopy(manifest)
                bad["price_assumptions"]["general"]["observation"][key] = value
                self.assertNotIn("% 적용", self.gen(bad)["markdown"])
                rates, _ = ft._receipt_rates(bad, bad['file_hash'], layout_id=ft.FINANCE_PROFILE)
                line, err = ft._resolve_footnote('general_price', MINI_MAP['footnote_kinds']['general_price'],
                    self.info, REGISTRY, applications=rates, view_for=_workbook_view(self.xlsx))
                self.assertTrue(err)
                self.assertNotIn('% 적용', line)
        manifest["file_hash"] = "0" * 64
        self.assertNotIn("% 적용", self.gen(manifest)["markdown"])

    def test_wage_cache_and_both_sexes(self):
        w = openpyxl.load_workbook(self.xlsx)
        ws = w.create_sheet("8. 노무비계획")
        ws['F6'] = '2026년'
        ws["AC38"] = GENERAL_R
        ws["AD38"] = GENERAL_R
        w.save(self.xlsx)
        w.close()
        receipt = self.receipt()
        raw = receipt["d8"]["rates"].pop("general")
        raw["evidence_ref"]["source_id"] = "test.wage"
        receipt["d8"]["rates"] = {"wage_male": copy.deepcopy(raw),
                                      "wage_female": copy.deepcopy(raw)}
        info = {**self.info, "source_id": "test.wage"}
        m = copy.deepcopy(MINI_MAP)
        m["sections"][0]["tables"][0]["footnotes"] = ["wage"]
        self.map_path.write_text(json.dumps(m), encoding="utf-8")
        def generate():
            return ft.generate(self.xlsx, major_id="specialty_crops", map_path=self.map_path,
                               registry=REGISTRY, application_receipt=receipt,
                               footnotes={"wage": info})
        self.assertIn("% 적용", generate()["markdown"])
        receipt["d8"]["rates"]["wage_female"]["r"] = GENERAL_R + .001
        self.assertNotIn("% 적용", generate()["markdown"])
        receipt["d8"]["rates"]["wage_female"]["r"] = GENERAL_R
        w = openpyxl.load_workbook(self.xlsx)
        w["8. 노무비계획"]["AD38"] = GENERAL_R + .001
        w.save(self.xlsx)
        w.close()
        receipt["output"]["sha256"] = hashlib.sha256(self.xlsx.read_bytes()).hexdigest()
        self.assertNotIn("% 적용", generate()["markdown"])
        del receipt["d8"]["rates"]["wage_female"]
        self.assertNotIn("% 적용", generate()["markdown"])

    def test_real_patch_producer_receipt_and_sales_dimensions(self):
        from tests import test_template_wage_link as producer
        root = Path(self.tmp.name) / "producer"
        th.runtime("gg_core").init(root)
        th.bind_major(root, "specialty_crops")
        context = th.runtime("gg_major_contract").output_context(root, "specialty_crops")
        source = root / "source.xlsx"
        producer.fixture(source)
        config = producer.assumptions(.02)
        patch = th.runtime("gg_excel_formula_patch")
        mapping = patch.materialize_d8(source, config)
        mp = root / "patch.json"
        mp.write_text(json.dumps(mapping), encoding="utf-8")
        output = root / "patched.xlsx"
        receipt = patch.patch_copy(source, mp, output, context=context)
        applications, sources = ft._receipt_rates(receipt, hashlib.sha256(output.read_bytes()).hexdigest())
        raw = config["sales"]
        info = {"source_id": raw["evidence_ref"]["source_id"],
                "base_year": raw["base_year"], "application_base_year": 2026,
                "target_dimensions": raw["target_dimensions"]}
        cfg = MINI_MAP["footnote_kinds"]["selling_price"]
        view_for = _workbook_view(output)
        line, err = ft._resolve_footnote("selling_price", cfg, info, pa.load_registry(),
                                       applications=applications, student_sources=sources, view_for=view_for)
        self.assertIsNone(err)
        self.assertIn("2.0000% 적용", line)
        # A scalar and its one-element list denote the same confirmed crop.
        info["target_dimensions"] = {**raw["target_dimensions"], "crop": ["synthetic"]}
        line, err = ft._resolve_footnote("selling_price", cfg, info, pa.load_registry(),
                                       applications=applications, student_sources=sources, view_for=view_for)
        self.assertIsNone(err)
        for key in raw["target_dimensions"]:
            with self.subTest(missing=key):
                bad = copy.deepcopy(info)
                del bad["target_dimensions"][key]
                line, err = ft._resolve_footnote("selling_price", cfg, bad, pa.load_registry(),
                                               applications=applications, student_sources=sources)
                self.assertIsNone(line)
                self.assertTrue(err)

    def test_sales_requires_crop(self):
        line, err = ft._resolve_footnote("selling_price", MINI_MAP["footnote_kinds"]["selling_price"],
                                      {"source_id": "test.sales", "base_year": None}, REGISTRY)
        self.assertIsNone(line)
        self.assertIn("crop", err)

    def test_complete_formula_strings(self):
        cfg = MINI_MAP["footnote_kinds"]["general_price"]
        basis = ("* 일반물가 상승률 %.4f%% 참고값(워크북 적용 확인 불가) "
                 "(출처: 국가데이터처(구 통계청) 시험통계 시험항목 [test.general], "
                 "기준연도 2020, 관측기간 2020~2025년, 관측 연평균 변화율 %.4f%%, "
                 "산식 r=(끝값/시작값)^(1/5)-1") % (GENERAL_R * 100, GENERAL_R * 100)
        for aby in (None, 2026):
            info = {"source_id": "test.general", "base_year": 2020}
            if aby is not None:
                info["application_base_year"] = aby
            line, _ = ft._resolve_footnote("general_price", cfg, info, REGISTRY)
            self.assertEqual(line, basis + (", 적용 기초연도 2026년" if aby else "") + ")")


class TestV2Years(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.xlsx = Path(self.tmp.name) / "mini.xlsx"
        _build_workbook(self.xlsx)
        self.map_path = self.xlsx.with_suffix(".map.json")
        m = copy.deepcopy(MINI_MAP)
        m["plan_years"] = {"sheet": "S1", "header_cells": [c+"16" for c in "CDEFG"]}
        self.map_path.write_text(json.dumps(m), encoding="utf-8")

    def test_missing_duplicate_and_extra_years(self):
        for case in ("one", "duplicate", "wrong", "empty", "extra"):
            with self.subTest(case=case):
                _build_workbook(self.xlsx)
                w = openpyxl.load_workbook(self.xlsx)
                ws = w["S1"]
                if case == "one":
                    for r in (29, 34, 39, 44):
                        ws.cell(r, 2).value = None
                elif case == "duplicate":
                    for r in (29, 34, 39, 44):
                        ws.cell(r, 2).value = "가. 2026년 경비"
                elif case == "wrong":
                    ws["B44"] = "마. 2031년 경비"
                elif case == "empty":
                    ws["B46"] = None
                    ws["C46"] = None
                else:
                    ws["B49"] = "가. 2031년 경비"
                w.save(self.xlsx)
                w.close()
                r = ft.generate(self.xlsx, major_id="specialty_crops", map_path=self.map_path)
                self.assertTrue(any(x["table"] == "t_years" for x in r["missing_required"]))

    def test_invalid_plan_headers_fail(self):
        for value in (None, True, "2026년"):
            with self.subTest(header=value):
                _build_workbook(self.xlsx)
                w = openpyxl.load_workbook(self.xlsx)
                w["S1"]["D16"] = value
                w.save(self.xlsx)
                w.close()
                r = ft.generate(self.xlsx, major_id="specialty_crops", map_path=self.map_path)
                self.assertTrue(any(x["table"] == "t_years" for x in r["missing_required"]))

    def test_anchor_plan_source_is_not_shortened_or_duplicated(self):
        m = copy.deepcopy(MINI_MAP)
        m["plan_years"] = {"sheet": "S1", "anchor_col": "B",
                           "anchor_regex": r"^[가-마]\.\d{4}년경비",
                           "count_from": {"sheet": "S1", "header_cells": [c+"16" for c in "CDEFG"]}}
        self.map_path.write_text(json.dumps(m), encoding="utf-8")
        for label in (None, "마. 2026년 경비"):
            with self.subTest(label=label):
                _build_workbook(self.xlsx)
                w = openpyxl.load_workbook(self.xlsx)
                w["S1"]["B44"] = label
                w.save(self.xlsx)
                w.close()
                r = ft.generate(self.xlsx, major_id="specialty_crops", map_path=self.map_path)
                self.assertTrue(any(x["table"] == "t_years" for x in r["missing_required"]))

    def test_valid_years_still_five(self):
        r = ft.generate(self.xlsx, major_id="specialty_crops", map_path=self.map_path)
        self.assertEqual(len([x for x in r["emitted"] if x["table"].startswith("t_years@")]), 5)
        self.assertFalse(any(x["table"] == "t_years" for x in r["missing_required"]))


class TestV3ApplicationProof(unittest.TestCase):
    setUp = TestV2Receipt.setUp
    gen = TestV2Receipt.gen
    receipt = TestV2Receipt.receipt

    def test_matching_hash_without_workbook_rate_evidence_is_reference(self):
        w = openpyxl.load_workbook(self.xlsx)
        for name in ('7. 영농자재소요계획', '9. 경비계획'):
            del w[name]
        w.save(self.xlsx)
        w.close()
        self.assertNotIn('% 적용', self.gen(self.receipt())['markdown'])

    def test_legacy_manifest_cannot_attest_department_workbook(self):
        receipt = {'profile': ft.FINANCE_PROFILE,
                   'file_hash': hashlib.sha256(self.xlsx.read_bytes()).hexdigest(),
                   'price_assumptions': {'general': {**self.info, 'status': 'applied',
                       'rate': GENERAL_R, 'observation': {'start_year': 2020, 'end_year': 2025}}}}
        self.assertNotIn('% 적용', self.gen(receipt)['markdown'])

    def test_either_sex_not_applied_or_missing_cache_downgrades_whole_wage(self):
        w = openpyxl.load_workbook(self.xlsx)
        s = w.create_sheet('8. 노무비계획')
        s['F6'] = '2026년'
        s['AC38'] = GENERAL_R
        s['AD38'] = GENERAL_R
        w.save(self.xlsx)
        w.close()
        m = copy.deepcopy(MINI_MAP)
        m['sections'][0]['tables'][0]['footnotes'] = ['wage']
        self.map_path.write_text(json.dumps(m), encoding='utf-8')
        receipt = self.receipt()
        raw = receipt['d8']['rates'].pop('general')
        raw['evidence_ref']['source_id'] = 'test.wage'
        def generate():
            return ft.generate(self.xlsx, major_id='specialty_crops', map_path=self.map_path,
                registry=REGISTRY, application_receipt=receipt,
                footnotes={'wage': {**self.info, 'source_id': 'test.wage'}})['markdown']
        for sex in ('wage_male', 'wage_female'):
            with self.subTest(sex=sex):
                receipt['d8']['rates'] = {k: copy.deepcopy(raw) for k in ('wage_male', 'wage_female')}
                receipt['d8']['rates'][sex] = {'mode': 'not_applied', 'reason': '확인 불가'}
                self.assertNotIn('% 적용', generate())
        receipt['d8']['rates'] = {k: copy.deepcopy(raw) for k in ('wage_male', 'wage_female')}
        for cell in ('AC38', 'AD38'):
            w = openpyxl.load_workbook(self.xlsx)
            w['8. 노무비계획'][cell] = None
            w.save(self.xlsx)
            w.close()
            receipt['output']['sha256'] = hashlib.sha256(self.xlsx.read_bytes()).hexdigest()
            self.assertNotIn('% 적용', generate())

    def test_sales_metadata_without_workbook_view_is_reference(self):
        cfg = MINI_MAP['footnote_kinds']['selling_price']
        info = {'source_id': 'test.sales', 'base_year': None,
                'application_base_year': 2026, 'target_dimensions': {'crop': '시험작목', 'region': '전국', 'unit': '원/kg'}}
        source = copy.deepcopy(REGISTRY['test.sales'])
        source['dimensions']['crop'] = '시험작목'
        source['unit'] = '원/kg'
        source['dimensions']['unit'] = '원/kg'
        registry = {**REGISTRY, 'test.sales': source}
        resolved = pa.resolve_series_rate(source)
        line, err = ft._resolve_footnote('selling_price', cfg, info, registry,
            applications={'sales': [{**info, 'rate': resolved['rate'],
                                    'observation': resolved['observation']}]})
        self.assertTrue(err)
        self.assertIsNotNone(line)
        self.assertNotIn('% 적용', line)

    def test_custom_map_layout_name_cannot_relabel_receipt_producer(self):
        m = copy.deepcopy(MINI_MAP)
        m['layout'] = {'id': ft.FINANCE_PROFILE, 'signatures': []}
        self.map_path.write_text(json.dumps(m), encoding='utf-8')
        res = pa.resolve_series_rate(REGISTRY['test.general'])
        manifest = {'profile': ft.FINANCE_PROFILE,
                    'file_hash': hashlib.sha256(self.xlsx.read_bytes()).hexdigest(),
                    'price_assumptions': {'general': {**self.info, 'status': 'applied',
                        'rate': res['rate'], 'observation': res['observation']}}}
        self.assertNotIn('% 적용', self.gen(manifest)['markdown'])

    def test_general_rate_mismatch_missing_year_and_cache_contradiction(self):
        for cell, value in [('C15', '=C5*1.025'), ('C45', '=C35*(1+0.1)'),
                            ('C25', None), ('B32', '2028년')]:
            with self.subTest(cell=cell, value=value):
                original = self.xlsx.read_bytes()
                w = openpyxl.load_workbook(self.xlsx)
                w['7. 영농자재소요계획'][cell] = value
                w.save(self.xlsx)
                w.close()
                self.assertNotIn('% 적용', self.gen(self.receipt())['markdown'])
                self.xlsx.write_bytes(original)
        import xml.etree.ElementTree as ET
        import zipfile
        ns = ft._XLS_MAIN
        name = ft._sheet_files(self.xlsx)['7. 영농자재소요계획']
        with zipfile.ZipFile(self.xlsx) as z:
            entries = {n: z.read(n) for n in z.namelist()}
        root = ET.fromstring(entries[name])
        cell = next(c for c in root.iter(ns+'c') if c.get('r') == 'C15')
        cell.find(ns+'v').text = '999'  # Formula says ~101.9, cache contradicts it.
        entries[name] = ET.tostring(root)
        with zipfile.ZipFile(self.xlsx, 'w') as z:
            for n, data in entries.items():
                z.writestr(n, data)
        self.assertNotIn('% 적용', self.gen(self.receipt())['markdown'])

    def test_unreadable_general_chain_cannot_hide_behind_another_matching_item(self):
        w = openpyxl.load_workbook(self.xlsx)
        material = w['7. 영농자재소요계획']
        for i in range(5):
            row = 6+i*10
            material['B%d' % row] = '다른 물가 적용 비목'
            material['C%d' % row] = 100 if not i else '=C%d*(1+%r)' % (row-10, GENERAL_R)
        material['C16'] = '=(C6*1.025)'
        w.save(self.xlsx)
        w.close()
        self.assertNotIn('% 적용', self.gen(self.receipt())['markdown'])

    def test_general_zero_and_negative_rates_require_actual_matching_formula(self):
        for end in (100, 90):
            with self.subTest(end=end):
                r = pa.cagr(100, end, 5)
                source = copy.deepcopy(REGISTRY['test.general'])
                source['observations'] = [{'year': 2020, 'value': 100}, {'year': 2025, 'value': end}]
                w = openpyxl.load_workbook(self.xlsx)
                for name in ('7. 영농자재소요계획', '9. 경비계획'):
                    for row in (15, 25, 35, 45):
                        w[name]['C%d' % row] = '=C%d*(1+%r)' % (row-10, r)
                w.save(self.xlsx)
                w.close()
                receipt = self.receipt()
                raw = receipt['d8']['rates']['general']
                raw.update(r=r, observed_values=[100, end])
                result = ft.generate(self.xlsx, major_id='specialty_crops', map_path=self.map_path,
                    registry={**REGISTRY, 'test.general': source}, application_receipt=receipt,
                    footnotes={'general_price': self.info})
                self.assertIn('%.4f%% 적용' % (r*100), result['markdown'])

    def test_manifest_wage_uses_both_caches_even_when_parsed_as_legacy(self):
        w = openpyxl.load_workbook(self.xlsx)
        s = w.create_sheet('8. 노무비계획')
        s['F6'] = '2026년'
        s['AC38'] = GENERAL_R
        s['AD38'] = GENERAL_R + .001
        w.save(self.xlsx)
        w.close()
        info = {**self.info, 'source_id': 'test.wage'}
        res = pa.resolve_series_rate(REGISTRY['test.wage'])
        manifest = {'profile': ft.FINANCE_PROFILE,
                    'file_hash': hashlib.sha256(self.xlsx.read_bytes()).hexdigest(),
                    'price_assumptions': {'wage': {**info, 'status': 'applied',
                        'rate': res['rate'], 'observation': res['observation']}}}
        applications, sources = ft._receipt_rates(manifest, manifest['file_hash'], layout_id=ft.FINANCE_PROFILE)
        line, err = ft._resolve_footnote('wage', MINI_MAP['footnote_kinds']['wage'], info, REGISTRY,
            applications=applications, student_sources=sources, view_for=_workbook_view(self.xlsx))
        self.assertTrue(err)
        self.assertNotIn('% 적용', line)
        w = openpyxl.load_workbook(self.xlsx)
        w['8. 노무비계획']['AD38'] = GENERAL_R
        w.save(self.xlsx)
        w.close()
        manifest['file_hash'] = hashlib.sha256(self.xlsx.read_bytes()).hexdigest()
        applications, sources = ft._receipt_rates(manifest, manifest['file_hash'], layout_id=ft.FINANCE_PROFILE)
        line, err = ft._resolve_footnote('wage', MINI_MAP['footnote_kinds']['wage'], info, REGISTRY,
            applications=applications, student_sources=sources, view_for=_workbook_view(self.xlsx))
        self.assertIsNone(err)
        self.assertIn('% 적용', line)

    def test_sales_actual_formula_all_years_and_receipt_plan_crop(self):
        source = copy.deepcopy(REGISTRY['test.sales'])
        source['dimensions']['crop'] = '시험작목'
        source['unit'] = '원/kg'
        source['dimensions']['unit'] = '원/kg'
        r = pa.resolve_series_rate(source)['rate']
        info = {'source_id': source['id'], 'base_year': None, 'application_base_year': 2026,
                'target_dimensions': {'crop': '시험작목', 'region': '전국', 'unit': '원/kg'}}
        w = openpyxl.load_workbook(self.xlsx)
        s = w.create_sheet(' 5. 판매계획')
        s['C36'] = '2026년'
        for row, price in {38: 'O7', 39: 'O8', 40: 'O9', 41: 'O11', 42: 'O12', 43: 'O13'}.items():
            s[price] = 20
            for i, (q, rev) in enumerate(zip('CEGIK', 'DFHJL')):
                s[q+str(row)] = 100
                s[rev+str(row)] = '=%s%d*$%s$%s/1000*(1+%r)^%d' % (q, row, price[0], price[1:], r, i)
        w.save(self.xlsx)
        w.close()
        m = copy.deepcopy(MINI_MAP)
        m['sections'][0]['tables'][0]['footnotes'] = ['selling_price']
        self.map_path.write_text(json.dumps(m), encoding='utf-8')
        def receipt():
            return {'schema': 'gg-xlsx-formula-patch-receipt/v1',
                    'output': {'sha256': hashlib.sha256(self.xlsx.read_bytes()).hexdigest()},
                    'd8': {'assumptions': {'crops': ['시험작목']}, 'rates': {'sales': {
                        'evidence_ref': {'source_id': source['id']}, 'base_year': None,
                        'application_base_year': 2026, 'observation_years': [2020, 2024],
                        'observed_values': [3000, 3300], 'r': r, 'target_dimensions': info['target_dimensions']}}}}
        def generate(rcpt):
            return ft.generate(self.xlsx, major_id='specialty_crops', map_path=self.map_path,
                registry={**REGISTRY, source['id']: source}, application_receipt=rcpt,
                footnotes={'selling_price': info})['markdown']
        self.assertIn('% 적용', generate(receipt()))
        for field, value in [('crops', ['다른작목']), ('crop', '다른작목'), ('작목', '다른작목')]:
            bad = receipt()
            bad['d8']['assumptions'] = {field: value}
            self.assertNotIn('% 적용', generate(bad))
        for cell, value in [('F38', '=E38*$O$7/1000'),
                            ('J38', '=I38*$O$7/1000*(1+%r)^2' % r),
                            ('L43', '=K43*$O$13/1000*(1+0.1)^4'),
                            ('H39', None), ('C36', '2027년')]:
            with self.subTest(cell=cell, value=value):
                original = self.xlsx.read_bytes()
                w = openpyxl.load_workbook(self.xlsx)
                w[' 5. 판매계획'][cell] = value
                w.save(self.xlsx)
                w.close()
                self.assertNotIn('% 적용', generate(receipt()))
                self.xlsx.write_bytes(original)

    def test_shared_general_formulas_are_read_and_missing_base_is_unverified(self):
        import xml.etree.ElementTree as ET
        import zipfile
        ns = ft._XLS_MAIN
        name = ft._sheet_files(self.xlsx)['7. 영농자재소요계획']
        with zipfile.ZipFile(self.xlsx) as z:
            entries = {n: z.read(n) for n in z.namelist()}
        root = ET.fromstring(entries[name])
        for c in root.iter(ns+'c'):
            if c.get('r') in ('C15', 'C25', 'C35', 'C45'):
                f = c.find(ns+'f')
                f.set('t', 'shared')
                f.set('si', '0')
                if c.get('r') == 'C15':
                    f.set('ref', 'C15:C45')
                else:
                    f.text = None
        entries[name] = ET.tostring(root)
        def save():
            with zipfile.ZipFile(self.xlsx, 'w') as z:
                for n, data in entries.items():
                    z.writestr(n, data)
        save()
        self.assertIn('% 적용', self.gen(self.receipt())['markdown'])
        follower = next(c for c in root.iter(ns+'c') if c.get('r') == 'C25')
        follower.find(ns+'f').set('si', '99')
        entries[name] = ET.tostring(root)
        save()
        self.assertNotIn('% 적용', self.gen(self.receipt())['markdown'])


if __name__ == '__main__':
    unittest.main()
