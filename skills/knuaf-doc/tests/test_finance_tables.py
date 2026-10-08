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
from contextlib import redirect_stdout
import hashlib
import io
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
    "workbook_rate_note_pattern": "상승률|성장률|연평균변화율",
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
            "* 일반물가 상승률 r=%.4f%%는 참고값이다" % (GENERAL_R * 100),
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
        self.assertIn("r=%.4f%%는 참고값이다" % (GENERAL_R * 100), r["markdown"])
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
        self.assertIn("* 일반물가 상승률 r=0.0000%는 참고값이다", r["markdown"])
        self.assertFalse(self._warn_reasons(r, "general_price"))

    def test_rejects_unstructured_inputs(self):
        bad_inputs = [
            "임금 상승률 90% (출처: 개인 블로그)",      # 자유 문자열
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

    def test_reference_footnote_full_string(self):
        # Q-DESIGN-1 A: 각주는 항상 참고값 — 산식 문자열의 마지막 1과
        # "통합문서에서 따로 확인" 안내가 온전히 남아야 한다.
        cfg = MINI_MAP["footnote_kinds"]["general_price"]
        for aby in (None, 2026):
            with self.subTest(application_base_year=aby):
                info = {"source_id": "test.general", "base_year": 2020}
                if aby is not None:
                    info["application_base_year"] = aby
                line, err = ft._resolve_footnote(
                    "general_price", cfg, info, REGISTRY)
                self.assertIsNone(err)
                self.assertEqual(line, (
                    "* 일반물가 상승률 r=%.4f%%는 참고값이다 "
                    "(출처: 국가데이터처(구 통계청) 시험통계 시험항목 "
                    "[test.general], 지수 기준연도 2020, 관측기간 2020~2025년, "
                    "산식 r=(끝값/시작값)^(1/5)-1). 이 값이 위 표의 계산에 "
                    "쓰였는지는 통합문서에서 따로 확인해야 한다."
                    % (GENERAL_R * 100)))

    def test_sales_crop_and_dimensions(self):
        # 판매가 각주는 crop 차원 필수이며 수열의 모든 차원이 확정돼야 한다.
        cfg = MINI_MAP["footnote_kinds"]["selling_price"]
        source = copy.deepcopy(REGISTRY["test.sales"])
        source["dimensions"]["crop"] = "시험작목"
        registry = {**REGISTRY, "test.sales": source}
        line, err = ft._resolve_footnote(
            "selling_price", cfg,
            {"source_id": "test.sales", "base_year": None}, registry)
        self.assertIsNone(line)
        self.assertIn("crop", err)
        for dims in ({"crop": "다른작목", "region": "전국"},
                     {"crop": "시험작목"}):
            with self.subTest(dims=dims):
                line, err = ft._resolve_footnote(
                    "selling_price", cfg,
                    {"source_id": "test.sales", "base_year": None,
                     "target_dimensions": dims}, registry)
                self.assertIsNone(line)
                self.assertTrue(err)
        line, err = ft._resolve_footnote(
            "selling_price", cfg,
            {"source_id": "test.sales", "base_year": None,
             "target_dimensions": {"crop": "시험작목", "region": "전국",
                                   "unit": source["unit"]}},
            registry)
        self.assertIsNone(err)
        self.assertIn("참고값이다", line)

    def test_sales_requires_crop(self):
        line, err = ft._resolve_footnote(
            "selling_price", MINI_MAP["footnote_kinds"]["selling_price"],
            {"source_id": "test.sales", "base_year": None}, REGISTRY)
        self.assertIsNone(line)
        self.assertIn("crop", err)

    def test_complete_formula_strings(self):
        registry = copy.deepcopy(REGISTRY)
        registry["test.sales"]["dimensions"]["crop"] = "시험작목"
        for kind, sid in (("general_price", "test.general"),
                          ("wage", "test.wage"),
                          ("selling_price", "test.sales")):
            with self.subTest(kind=kind):
                src = registry[sid]
                cfg = MINI_MAP["footnote_kinds"][kind]
                resolved = pa.resolve_series_rate(src)
                base = src["base_year"]
                line, err = ft._resolve_footnote(kind, cfg, {
                    "source_id": sid, "base_year": base,
                    "target_dimensions": {**src["dimensions"], "unit": src["unit"]}}, registry)
                self.assertIsNone(err)
                basis = "지수 기준연도 %d" % base if base else "지수 기준연도 해당 없음"
                self.assertEqual(line, (
                    "* %s r=%.4f%%는 참고값이다 (출처: 국가데이터처(구 통계청) "
                    "시험통계 시험항목 [%s], %s, 관측기간 2020~%d년, "
                    "산식 r=(끝값/시작값)^(1/%d)-1). 이 값이 위 표의 계산에 "
                    "쓰였는지는 통합문서에서 따로 확인해야 한다."
                    % (cfg["label"], resolved["rate"] * 100, sid, basis,
                       resolved["observation"]["end_year"], resolved["interval_years"])))

    def test_negative_observed_rate_is_reference(self):
        src = copy.deepcopy(REGISTRY["test.general"])
        src["observations"] = [{"year": 2020, "value": 100.0},
                               {"year": 2025, "value": 90.0}]
        line, err = ft._resolve_footnote(
            "general_price", MINI_MAP["footnote_kinds"]["general_price"],
            {"source_id": src["id"], "base_year": 2020},
            {src["id"]: src})
        self.assertIsNone(err)
        self.assertIn("r=%.4f%%는 참고값이다" % (pa.cagr(100, 90, 5) * 100), line)

    def test_unit_and_unconfirmed_observations_rejected(self):
        cfg = MINI_MAP["footnote_kinds"]["general_price"]
        info = {"source_id": "test.general", "base_year": 2020}
        for unit in ("원/kg", "원/일"):
            with self.subTest(unit=unit):
                line, err = ft._resolve_footnote(
                    "general_price", cfg, {**info, "unit": unit}, REGISTRY)
                self.assertIsNone(line)
                self.assertTrue(err)
        registry = copy.deepcopy(REGISTRY)
        registry["test.general"]["observations"].pop()
        registry["test.general"]["unconfirmed_years"] = [2025]
        line, err = ft._resolve_footnote("general_price", cfg,
            {**info, "observation": {"start_year": 2020, "end_year": 2025}}, registry)
        self.assertIsNone(line)
        self.assertTrue(err)

    def test_workbook_rate_notes_use_validated_reference(self):
        # 검증 없는 원문 비율을 그대로 각주로 복사하지 않는다.
        with tempfile.TemporaryDirectory() as tmp:
            xlsx = Path(tmp) / "notes.xlsx"
            _build_workbook(xlsx)
            wb = openpyxl.load_workbook(xlsx)
            wb["S1"]["B5"] = "* 원문 연평균 성장률 90%"
            wb["S1"]["C5"] = None
            wb["S1"]["D5"] = None
            wb["S1"]["B6"] = "* 수선비는 구입금액의 0.5%"
            wb["S1"]["C6"] = None
            wb["S1"]["D6"] = None
            wb.save(xlsx)
            wb.close()
            result = ft.generate(xlsx, major_id="specialty_crops",
                footnotes={"general_price": {"source_id": "test.general", "base_year": 2020}},
                map_path=self.map_path, registry=REGISTRY)
            self.assertNotIn("원문 연평균 성장률", result["markdown"])
            self.assertIn("* 수선비는 구입금액의 0.5%", result["markdown"])
            self.assertIn("r=%.4f%%는 참고값이다" % (GENERAL_R * 100), result["markdown"])
            missing = ft.generate(xlsx, major_id="specialty_crops", map_path=self.map_path)
            self.assertNotIn("원문 연평균 성장률", missing["markdown"])
            self.assertTrue(any(w["reason"] == "footnote_missing" for w in missing["warnings"]))


class TestWorkbookRateNotes(unittest.TestCase):
    """V6-01: 모든 원문 출력 경로와 좌표 경고, 일반 계산 설명 보존."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.xlsx = Path(self._tmp.name) / "notes.xlsx"
        self.map_path = Path(self._tmp.name) / "map.json"

    def _generate(self, cells, table_map=None, footnotes=None):
        _build_workbook(self.xlsx)
        wb = openpyxl.load_workbook(self.xlsx)
        for cell, value in cells.items():
            wb["S1"][cell] = value
        wb.save(self.xlsx)
        wb.close()
        before = self.xlsx.read_bytes()
        self.map_path.write_text(json.dumps(table_map or MINI_MAP,
                                            ensure_ascii=False), encoding="utf-8")
        result = ft.generate(self.xlsx, major_id="specialty_crops",
                             map_path=self.map_path, footnotes=footnotes,
                             registry=REGISTRY)
        self.assertEqual(self.xlsx.read_bytes(), before)
        return result

    def _dropped_cells(self, result):
        warnings = [w for w in result["warnings"]
                    if w["reason"] == "workbook_rate_note_dropped"]
        self.assertTrue(all(w["sheet"] == "S1" for w in warnings))
        return {w["cell"] for w in warnings}

    def test_inline_note_keywords_and_reference_footnotes(self):
        m = copy.deepcopy(MINI_MAP)
        extract = m["sections"][0]["tables"][0]["extract"]
        extract["value_cols"] = ["C"]
        extract["note_cols"] = ["D"]
        for note in ("* 일반물가 상승률 90% 적용", "연평균 성장률 90% 적용",
                     "연평균 변화율 90% 적용", "물가 상 승 률 90% 적용"):
            for footnotes in (None, {"general_price": {
                    "source_id": "test.general", "base_year": 2020}}):
                with self.subTest(note=note, footnotes=footnotes):
                    result = self._generate({"D4": note}, m, footnotes)
                    self.assertIn("| 토지 | 330,000 |  |", result["markdown"])
                    self.assertNotIn("90%", result["markdown"])
                    self.assertEqual(self._dropped_cells(result), {"'S1'!D4"})
                    if footnotes:
                        self.assertIn("r=%.4f%%는 참고값이다" % (GENERAL_R * 100),
                                      result["markdown"])
                    else:
                        self.assertTrue(any(w["reason"] == "footnote_missing"
                                            for w in result["warnings"]))

    def test_every_cell_render_path_drops_with_source_coordinate(self):
        # 값·구조 행·빈 행 라벨·연도 열 라벨/값·머리글 경로.
        for cell in ("D4", "D8", "B9", "C54", "G54", "C56", "B3", "F52"):
            with self.subTest(cell=cell):
                m = copy.deepcopy(MINI_MAP)
                m["sections"][0]["tables"][0]["extract"]["header_map"] = {}
                text = "* 원문 성장률 90% 적용" if cell == "D8" else "원문 성장률 90% 적용"
                if cell == "F52":
                    text = "2026년 " + text
                result = self._generate({cell: text}, m)
                self.assertNotIn("90%", result["markdown"])
                self.assertIn("'S1'!" + cell, self._dropped_cells(result))
                self.assertIn("표 1.", result["markdown"])

    def test_year_block_inline_note_keeps_all_five_tables(self):
        m = copy.deepcopy(MINI_MAP)
        extract = m["sections"][0]["tables"][3]["extract"]
        extract["cols"].append("D")
        extract["note_cols"] = ["D"]
        baseline = self._generate({}, m)
        result = self._generate({"D26": "* 일반물가 상승률 90% 적용"}, m)
        self.assertEqual(result["markdown"], baseline["markdown"])
        self.assertEqual(result["emitted"], baseline["emitted"])
        self.assertEqual(len([e for e in result["emitted"]
                              if e["table"].startswith("t_years@")]), 5)
        self.assertEqual(self._dropped_cells(result), {"'S1'!D26"})

    def test_merged_note_warns_at_original_cell(self):
        _build_workbook(self.xlsx)
        wb = openpyxl.load_workbook(self.xlsx)
        wb["S1"]["D4"] = "* 일반물가 상승률 90% 적용"
        wb["S1"].merge_cells("D4:D5")
        wb.save(self.xlsx)
        wb.close()
        self.map_path.write_text(json.dumps(MINI_MAP), encoding="utf-8")
        result = ft.generate(self.xlsx, major_id="specialty_crops",
                             map_path=self.map_path)
        self.assertNotIn("90%", result["markdown"])
        self.assertEqual(self._dropped_cells(result), {"'S1'!D4"})

    def test_independent_continuation_and_trailing_notes(self):
        cells = {"B5": "* 수선비는 구입금액의 0.5% 적용",
                 "B6": "연평균 성장률 90% 적용",
                 "B7": "감가상각은 내용년수 적용",
                 "B13": "* 일반물가 상승률 91% 적용", "C13": None}
        for row in (5, 6, 7):
            cells["C%d" % row] = None
            cells["D%d" % row] = None
        m = copy.deepcopy(MINI_MAP)
        m["sections"][0]["tables"][0]["extract"]["trailing_notes"] = True
        result = self._generate(cells, m)
        self.assertNotIn("90%", result["markdown"])
        self.assertNotIn("91%", result["markdown"])
        self.assertIn(cells["B5"], result["markdown"])
        self.assertIn(cells["B7"], result["markdown"])
        self.assertTrue({"'S1'!B6", "'S1'!B13"} <= self._dropped_cells(result))
        # '*' 선두인 독립 주석도 같은 좌표 경고를 낸다.
        result = self._generate({"B5": "* 연평균 성장률 90% 적용",
                                 "C5": None, "D5": None})
        self.assertNotIn("90%", result["markdown"])
        self.assertIn("'S1'!B5", self._dropped_cells(result))

    def test_non_rate_application_text_is_preserved(self):
        for note in ("수선비 구입금액의 0.5% 적용", "수선비 6% 적용",
                     "감가상각 내용년수 적용", "일반물가 참고 설명"):
            with self.subTest(note=note):
                result = self._generate({"D4": note})
                self.assertIn("| 토지 | 330,000 | %s |" % note, result["markdown"])
                self.assertEqual(self._dropped_cells(result), set())


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

    def test_public_and_direct_cli_drop_inline_note_identically(self):
        import gg

        wb = openpyxl.load_workbook(self.xlsx)
        wb["S1"]["D4"] = "* 일반물가 상승률 90% 적용"
        wb["S1"]["D5"] = "수선비 0.5% 적용"
        wb.save(self.xlsx)
        wb.close()
        for has_footnote in (False, True):
            with self.subTest(footnote=has_footnote):
                args = [str(self.root), "--xlsx", str(self.xlsx),
                        "--major", "specialty_crops"]
                if has_footnote:
                    args += ["--input", self._input({"general_price": {
                        "source_id": "official.kosis.cpi.total", "base_year": 2020}})]
                outputs = []
                reports = []
                for name, main, prefix in (("public", gg.main, ["finance-tables"]),
                                           ("direct", ft.main, [])):
                    out = "%s-%s.md" % (name, has_footnote)
                    stream = io.StringIO()
                    with redirect_stdout(stream):
                        code = main(prefix + args + ["--out", out])
                    self.assertEqual(code, 0, stream.getvalue())
                    reports.append(json.loads(stream.getvalue()))
                    outputs.append((self.root / out).read_bytes())
                self.assertEqual(outputs[0], outputs[1])
                self.assertEqual(reports[0]["warnings"], reports[1]["warnings"])
                self.assertNotIn("90%", outputs[0].decode("utf-8"))
                self.assertIn("수선비 0.5% 적용", outputs[0].decode("utf-8"))
                self.assertTrue(any(w["reason"] == "workbook_rate_note_dropped"
                                    and w["cell"] == "'S1'!D4"
                                    for w in reports[0]["warnings"]))

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


if __name__ == '__main__':
    unittest.main()
