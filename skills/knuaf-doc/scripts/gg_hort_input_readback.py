#!/usr/bin/env python3
"""원예: 학생이 손으로 채운 학교 엑셀을 읽어 251칸을 상태 하나씩 분류한다.

읽기 전용이다. 정본(``project.json``)을 고치지 않고, 학교 원본 ``H-X1``은
읽기만 하며, 쓰기는 ``--out`` JSON 한 파일뿐이다(이미 있으면 거절).

판정 순서는 ⓪ → ① → ②다.

⓪ 원본 사본 대조 — ``gg_office.inspect_preserved_template``을 그대로 부른다.
   두 파일을 임시 폴더에 zip 재작성으로 복사하면서, 입력 좌표 251을 포함하는
   병합(20개)만 양쪽에서 먼저 지운다. 나머지 zip 파트는 바이트 그대로 옮겨
   ``styles.xml``의 cellXfs 인덱스가 흔들리지 않게 한다. 병합은 시트 단위
   정렬 목록으로 비교되므로 이 사전 제거가 없으면 입력 칸 병합 때문에 모든
   정상 파일이 거절된다.

   대조 범위에서 뺄 항목 두 가지:
   (a) 주소가 입력 좌표 251에 드는 항목(학생이 고쳐도 되는 칸),
   (b) 원본 시트에 그 주소의 ``<c>``가 없고 그 주소가 원본 시트의 사용 범위
       (``<c>`` 주소의 최대 열·행) 밖인 항목. 원본에 없는 확장 열에 학생이
       값을 적은 경우(시험 12)가 여기 걸린다. 사용 범위 안쪽에 값을 새로
       적으면 (b)로 빠지지 않고 ⓪가 ``template_cell_changed``로 거절한다 —
       규칙을 넓히면 시험 2가 깨지므로 범위 밖으로만 좁힌다.

   캐시는 ⓪에서 비교되지 않는다(``snapshot``이 수식 칸의 값을 (수식 글자,
   속성)으로 덮어쓰고 ``old[:2] != new[:2]``로만 본다). 그래서 다시 계산한
   정상 파일은 거절되지 않는다. 캐시 차이는 ②에서만 본다.

① 읽지 않는 30칸(아닌 자리 26 + 못 가림 4)은 원본 분류표의 상태를 그대로
   쓰고 답으로 삼지 않는다.

② 적는 자리 221칸은 원본 종류 → 학생 종류 → 값(또는 수식 글자·캐시) 순서로
   11상태 중 하나가 된다. 수식 글자는 앞의 ``=`` 하나만 정규화하고 나머지는
   바이트 그대로 비교한다. 캐시는 ``gg_hort_workbook._native_caches``로만
   읽고, 단위가 TEXT 계열이라 ``compare_raw_cache``가 단위 미분류로 거절하면
   ``_example_equal``(문자열/Decimal 동등)로 비교한다.

출력 계약 밖의 판단 두 가지(체크포인트 "기준서 vs 코드"에 기록):
  * ``gg_finance_tables.run``이 쓰는 ``gg_major_contract`` 출력 게이트를 달지
    않는다. 기준서의 종료 코드는 0/2/3뿐이고 2는 ⓪ 거절이며, 이 명령은
    정본을 바꾸지 않는 읽기다.
  * ``--out``이 정본 폴더 자체이거나 폴더 밖이면 이유 코드는 기준서가 이름
    붙인 ``output_path_in_canonical``(폴더 자체)·``output_outside_project``
    (폴더 밖)를 쓰되, 종료 코드는 입력 오류인 3으로 낸다. 2는 ⓪ 거절 전용
    이라는 코드표를 따른다.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import json
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.parsers import expat

import gg_core
import gg_hort_plan
import gg_hort_workbook as hwb
import gg_office
from gg_excel_template import (CELL_RE, NS_MAIN, col_num, expand_ref, local,
                               load_shared, text_value, workbook_sheets)

HERE = Path(__file__).resolve().parent
PARAMS_PATH = HERE.parent / "references" / "hort-env-systems" / "finance-params.json"
DEFAULT_OUT = "build/hort-input-readback.json"

READ = "read"

STATE_ORDER = (
    "not_read_template",
    "not_read_textbook",
    "not_read_footnote",
    "not_read_unclear",
    "constant_overwritten",
    "same_value",
    "constant_deleted",
    "blank_filled",
    "blank_kept",
    "formula_kept",
    "formula_cache_only",
    "formula_replaced",
    "formula_to_constant",
    "formula_deleted",
    "formula_added",
)

MESSAGES = {
    "not_read_template": "원본이 비워 둔 연도 칸이라 읽지 않았어요.",
    "not_read_textbook": "교재 예시 칸이라 읽지 않았어요.",
    "not_read_footnote": "안내문·출처 칸이라 읽지 않았어요.",
    "not_read_unclear": "무엇을 적는 자리인지 확인이 필요해요.",
    "same_value": "원본과 같은 값이에요.",
    "constant_deleted": "적혀 있던 칸을 지우셨어요.",
    "blank_kept": "아직 비어 있어요.",
    "formula_kept": "그대로 둔 계산 칸이에요.",
    "formula_cache_only": "엑셀이 다시 계산한 칸이에요.",
    "formula_replaced": "계산식을 바꾸셨어요.",
    "formula_to_constant": "계산식을 값으로 바꾸셨어요.",
    "formula_deleted": "계산식을 지우셨어요.",
    "formula_added": "수식을 넣으셨어요. 계산된 숫자는 읽지 않았어요.",
}
READ_STATES = STATE_ORDER[4:]
VALUE_STATES = ("constant_overwritten", "blank_filled")
VALUE_MESSAGE = "엑셀에 적으신 값 {v}예요."

REJECT_REASONS = {
    "template sheet order changed": "template_sheet_order_changed",
    "template sheet missing": "template_sheet_missing",
    "template state changed": "template_state_changed",
    "template merges changed": "template_merges_changed",
    "template print_area changed": "template_print_area_changed",
    "template formula/value changed": "template_cell_changed",
}
REASON_OUT_OF_RANGE = "ratio_out_of_range"
REASON_RATIO_UNIT = "ratio_unit_unconfirmed"
REASON_RATIO_FINITE = "ratio_value_not_finite"
REASON_UNMAPPED = "unmapped_cell"
REASON_CACHE_UNCOMPARED = "cache_not_compared"
REASON_SNAPSHOT = "template_snapshot_invalid"


def _snapshot_invalid(detail):
    return hwb.Held(REASON_SNAPSHOT, detail)


# (시트, ((주소, param_key, unit_code, 판정), ...)) — 좌표·상태·param_key·단위만
# 저장소에 둔다. 교재 값은 넣지 않는다.
RECORDED = {
    '1. 자산조사': (
        ('B8', 'opening.land_kind', 'TEXT', 'read'),
        ('C8', 'opening.land_address', 'TEXT', 'read'),
        ('D8', 'opening.land_area_m2', 'M2', 'read'),
        ('E8', 'opening.land_value_krw1000', 'KRW_1000', 'read'),
        ('F8', 'opening.land_distance_km', 'KM', 'read'),
        ('G8', 'opening.land_purchase_year', 'YEAR', 'read'),
        ('B36', 'opening.equipment_slot1.kind', 'TEXT', 'read'),
        ('C36', 'opening.equipment_slot1.spec', 'TEXT', 'read'),
        ('D36', 'opening.equipment_slot1.quantity', 'COUNT', 'read'),
        ('E36', 'opening.equipment_slot1_cost_krw1000', 'KRW_1000', 'not_read_unclear'),
        ('F36', 'opening.equipment_slot1.life_years', 'YEARS', 'read'),
        ('G36', 'opening.equipment_slot1.purchase_year', 'YEAR', 'read'),
        ('H36', 'opening.equipment_slot1.note', 'TEXT', 'read'),
        ('B37', 'opening.equipment_slot2.kind', 'TEXT', 'read'),
        ('C37', 'opening.equipment_slot2.spec', 'TEXT', 'read'),
        ('D37', 'opening.equipment_slot2.quantity', 'COUNT', 'read'),
        ('E37', 'opening.equipment_slot2_cost_krw1000', 'KRW_1000', 'read'),
        ('F37', 'opening.equipment_slot2.life_years', 'YEARS', 'read'),
        ('G37', 'opening.equipment_slot2.purchase_year', 'YEAR', 'read'),
        ('H37', 'opening.equipment_slot2.note', 'TEXT', 'read'),
        ('B43', 'opening.cash_kind', 'TEXT', 'read'),
        ('C43', 'opening.cash_detail', 'TEXT', 'read'),
        ('E43', 'opening.cash_krw1000', 'KRW_1000', 'read'),
        ('B44', 'opening.cash_deposit_name', 'TEXT', 'read'),
        ('C44', 'opening.cash_deposit_detail', 'TEXT', 'read'),
        ('E44', 'opening.cash_deposit_krw1000', 'KRW_1000', 'read'),
    ),
    '2. 중장기영농목표': (
        ('C5', 'goal.note.y1', 'TEXT', 'not_read_unclear'),
        ('D5', 'goal.note.y2', 'TEXT', 'not_read_unclear'),
        ('C12', 'goal.sales_basis', 'TEXT', 'read'),
        ('C13', 'goal.source', 'TEXT', 'not_read_footnote'),
    ),
    '3.모델농장분석': (
        ('C3', 'model_farm.name', 'TEXT', 'not_read_textbook'),
        ('C4', 'model_farm.owner', 'TEXT', 'not_read_textbook'),
        ('C5', 'model_farm.address', 'TEXT', 'not_read_textbook'),
        ('C6', 'model_farm.history', 'TEXT', 'not_read_textbook'),
        ('C7', 'model_farm.items', 'TEXT', 'not_read_textbook'),
        ('C8', 'model_farm.scale', 'TEXT', 'not_read_textbook'),
        ('C9', 'model_farm.facilities', 'TEXT', 'not_read_textbook'),
        ('C10', 'model_farm.production', 'TEXT', 'not_read_textbook'),
        ('C11', 'model_farm.sales', 'TEXT', 'not_read_textbook'),
        ('C12', 'model_farm.cost', 'TEXT', 'not_read_textbook'),
        ('C13', 'model_farm.reason', 'TEXT', 'not_read_textbook'),
    ),
    '4.투자계획': (
        ('C19', 'invest.slot1.item', 'TEXT', 'read'),
        ('D19', 'invest.slot1.spec', 'TEXT', 'read'),
        ('F19', 'invest.slot1.self_funding_krw1000', 'KRW_1000', 'read'),
        ('G19', 'invest.slot1.asset_grant_krw1000', 'KRW_1000', 'read'),
        ('H19', 'invest.slot1.loan_funding_krw1000', 'KRW_1000', 'read'),
        ('C20', 'invest.slot2.item', 'TEXT', 'read'),
        ('D20', 'invest.slot2.spec', 'TEXT', 'read'),
        ('F20', 'invest.slot2.self_funding_krw1000', 'KRW_1000', 'read'),
        ('G20', 'invest.slot2.asset_grant_krw1000', 'KRW_1000', 'read'),
        ('H20', 'invest.slot2.loan_funding_krw1000', 'KRW_1000', 'read'),
        ('C21', 'invest.slot3.item', 'TEXT', 'read'),
        ('D21', 'invest.slot3.spec', 'TEXT', 'read'),
        ('F21', 'invest.slot3.self_funding_krw1000', 'KRW_1000', 'read'),
        ('G21', 'invest.slot3.asset_grant_krw1000', 'KRW_1000', 'read'),
        ('H21', 'invest.slot3.loan_funding_krw1000', 'KRW_1000', 'read'),
        ('C22', 'invest.slot4.item', 'TEXT', 'read'),
        ('D22', 'invest.slot4.spec', 'TEXT', 'read'),
        ('F22', 'invest.slot4.self_funding_krw1000', 'KRW_1000', 'read'),
        ('G22', 'invest.slot4.asset_grant_krw1000', 'KRW_1000', 'read'),
        ('H22', 'invest.slot4.loan_funding_krw1000', 'KRW_1000', 'read'),
        ('C23', 'invest.slot5.item', 'TEXT', 'read'),
        ('D23', 'invest.slot5.spec', 'TEXT', 'read'),
        ('F23', 'invest.slot5.self_funding_krw1000', 'KRW_1000', 'read'),
        ('G23', 'invest.slot5.asset_grant_krw1000', 'KRW_1000', 'read'),
        ('H23', 'invest.slot5.loan_funding_krw1000', 'KRW_1000', 'read'),
        ('C25', 'invest.slot6.item', 'TEXT', 'not_read_template'),
        ('D25', 'invest.slot6.spec', 'TEXT', 'not_read_template'),
        ('F25', 'invest.slot6.self_funding_krw1000', 'KRW_1000', 'read'),
        ('G25', 'invest.slot6.asset_grant_krw1000', 'KRW_1000', 'read'),
        ('H25', 'invest.slot6.loan_funding_krw1000', 'KRW_1000', 'read'),
        ('C26', 'invest.slot7.item', 'TEXT', 'not_read_template'),
        ('D26', 'invest.slot7.spec', 'TEXT', 'not_read_template'),
        ('F26', 'invest.slot7.self_funding_krw1000', 'KRW_1000', 'read'),
        ('G26', 'invest.slot7.asset_grant_krw1000', 'KRW_1000', 'read'),
        ('H26', 'invest.slot7.loan_funding_krw1000', 'KRW_1000', 'read'),
        ('C27', 'invest.slot8.item', 'TEXT', 'not_read_template'),
        ('D27', 'invest.slot8.spec', 'TEXT', 'not_read_template'),
        ('F27', 'invest.slot8.self_funding_krw1000', 'KRW_1000', 'read'),
        ('G27', 'invest.slot8.asset_grant_krw1000', 'KRW_1000', 'read'),
        ('H27', 'invest.slot8.loan_funding_krw1000', 'KRW_1000', 'read'),
        ('C28', 'invest.slot9.item', 'TEXT', 'not_read_template'),
        ('D28', 'invest.slot9.spec', 'TEXT', 'not_read_template'),
        ('F28', 'invest.slot9.self_funding_krw1000', 'KRW_1000', 'read'),
        ('G28', 'invest.slot9.asset_grant_krw1000', 'KRW_1000', 'read'),
        ('H28', 'invest.slot9.loan_funding_krw1000', 'KRW_1000', 'read'),
        ('B30', 'invest.land_price_note', 'TEXT', 'not_read_footnote'),
        ('C36', 'land.paddy.owned.y1', 'M2', 'read'),
        ('D36', 'land.paddy.owned.y2', 'M2', 'read'),
        ('E36', 'land.paddy.owned.y3', 'M2', 'read'),
        ('F36', 'land.paddy.owned.y4', 'M2', 'read'),
        ('G36', 'land.paddy.owned.y5', 'M2', 'read'),
        ('C37', 'land.paddy.rented_in.y1', 'M2', 'read'),
        ('D37', 'land.paddy.rented_in.y2', 'M2', 'read'),
        ('E37', 'land.paddy.rented_in.y3', 'M2', 'read'),
        ('F37', 'land.paddy.rented_in.y4', 'M2', 'read'),
        ('G37', 'land.paddy.rented_in.y5', 'M2', 'read'),
        ('C38', 'land.paddy.rented_out.y1', 'M2', 'read'),
        ('D38', 'land.paddy.rented_out.y2', 'M2', 'read'),
        ('E38', 'land.paddy.rented_out.y3', 'M2', 'read'),
        ('F38', 'land.paddy.rented_out.y4', 'M2', 'read'),
        ('G38', 'land.paddy.rented_out.y5', 'M2', 'read'),
        ('C44', 'land.field.owned.y1', 'M2', 'read'),
        ('D44', 'land.field.owned.y2', 'M2', 'read'),
        ('E44', 'land.field.owned.y3', 'M2', 'read'),
        ('F44', 'land.field.owned.y4', 'M2', 'read'),
        ('G44', 'land.field.owned.y5', 'M2', 'read'),
        ('C45', 'land.field.rented_in.y1', 'M2', 'read'),
        ('D45', 'land.field.rented_in.y2', 'M2', 'read'),
        ('E45', 'land.field.rented_in.y3', 'M2', 'read'),
        ('F45', 'land.field.rented_in.y4', 'M2', 'read'),
        ('G45', 'land.field.rented_in.y5', 'M2', 'read'),
        ('C46', 'land.field.rented_out.y1', 'M2', 'read'),
        ('D46', 'land.field.rented_out.y2', 'M2', 'read'),
        ('E46', 'land.field.rented_out.y3', 'M2', 'read'),
        ('F46', 'land.field.rented_out.y4', 'M2', 'read'),
        ('G46', 'land.field.rented_out.y5', 'M2', 'read'),
        ('C52', 'machinery.held.y1', 'COUNT', 'read'),
        ('D52', 'machinery.held.y2', 'COUNT', 'read'),
        ('E52', 'machinery.held.y3', 'COUNT', 'read'),
        ('F52', 'machinery.held.y4', 'COUNT', 'read'),
        ('G52', 'machinery.held.y5', 'COUNT', 'read'),
        ('H52', 'machinery.note', 'TEXT', 'read'),
        ('C53', 'machinery.purchased.y1', 'COUNT', 'read'),
        ('D53', 'machinery.purchased.y2', 'COUNT', 'read'),
        ('E53', 'machinery.purchased.y3', 'COUNT', 'read'),
        ('F53', 'machinery.purchased.y4', 'COUNT', 'read'),
        ('G53', 'machinery.purchased.y5', 'COUNT', 'read'),
        ('C54', 'machinery.replaced.y1', 'COUNT', 'read'),
        ('D54', 'machinery.replaced.y2', 'COUNT', 'read'),
        ('E54', 'machinery.replaced.y3', 'COUNT', 'read'),
        ('F54', 'machinery.replaced.y4', 'COUNT', 'read'),
        ('G54', 'machinery.replaced.y5', 'COUNT', 'read'),
        ('B59', 'facility.slot1.name', 'TEXT', 'read'),
        ('C59', 'facility.slot1.area_m2.y1', 'M2', 'read'),
        ('D59', 'facility.slot1.area_m2.y2', 'M2', 'read'),
        ('E59', 'facility.slot1.area_m2.y3', 'M2', 'read'),
        ('F59', 'facility.slot1.area_m2.y4', 'M2', 'read'),
        ('G59', 'facility.slot1.area_m2.y5', 'M2', 'read'),
    ),
    '5. 원리금상환계획': (
        ('C39', 'loan.slot1.execution_year', 'YEAR', 'read'),
        ('D39', 'loan.slot1.principal_krw1000', 'KRW_1000', 'read'),
        ('F39', 'loan.slot1.annual_rate', 'RATIO', 'read'),
        ('C73', 'loan.slot2.execution_year', 'YEAR', 'read'),
        ('D73', 'loan.slot2.principal_krw1000', 'KRW_1000', 'read'),
        ('F73', 'loan.slot2.annual_rate', 'RATIO', 'read'),
        ('C96', 'loan.slot3.execution_year', 'YEAR', 'read'),
        ('D96', 'loan.slot3.principal_krw1000', 'KRW_1000', 'read'),
        ('F96', 'loan.slot3.annual_rate', 'RATIO', 'read'),
        ('C119', 'loan.slot4.execution_year', 'YEAR', 'read'),
        ('D119', 'loan.slot4.principal_krw1000', 'KRW_1000', 'read'),
        ('F119', 'loan.slot4.annual_rate', 'RATIO', 'read'),
        ('C142', 'loan.slot5.execution_year', 'YEAR', 'read'),
        ('D142', 'loan.slot5.principal_krw1000', 'KRW_1000', 'read'),
        ('F142', 'loan.slot5.annual_rate', 'RATIO', 'read'),
    ),
    '6.시설계획': (
        ('B11', 'facility.model.width_m', 'M', 'read'),
        ('C11', 'facility.model.height_side_m', 'M', 'read'),
        ('D11', 'facility.model.height_top_m', 'M', 'read'),
        ('E11', 'facility.model.length_m', 'M', 'read'),
        ('F11', 'facility.model.area_m2', 'M2', 'read'),
        ('B15', 'facility.site.wind_ms', 'M_PER_S', 'read'),
        ('C15', 'facility.site.snow_depth', 'TEXT', 'read'),
        ('B42', 'facility.spec_reference', 'TEXT', 'not_read_footnote'),
        ('B73', 'facility.cost_reference', 'TEXT', 'not_read_footnote'),
        ('B74', 'facility.cost_growth_note', 'TEXT', 'not_read_footnote'),
        ('C76', 'facility.construction.total_krw1000', 'KRW_1000', 'read'),
        ('C77', 'facility.construction.per_pyeong_krw1000', 'KRW_1000', 'read'),
        ('C78', 'facility.construction.expected_krw1000', 'KRW_1000', 'read'),
    ),
    ' 7. 판매계획': (
        ('B8', 'price.observed.y1.year', 'YEAR', 'read'),
        ('C8', 'price.observed.y1.m1.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('D8', 'price.observed.y1.m2.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('E8', 'price.observed.y1.m3.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('F8', 'price.observed.y1.m4.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('G8', 'price.observed.y1.m5.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('H8', 'price.observed.y1.m6.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('I8', 'price.observed.y1.m7.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('J8', 'price.observed.y1.m8.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('K8', 'price.observed.y1.m9.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('L8', 'price.observed.y1.m10.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('M8', 'price.observed.y1.m11.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('N8', 'price.observed.y1.m12.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('B9', 'price.observed.y2.year', 'YEAR', 'read'),
        ('C9', 'price.observed.y2.m1.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('D9', 'price.observed.y2.m2.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('E9', 'price.observed.y2.m3.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('F9', 'price.observed.y2.m4.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('G9', 'price.observed.y2.m5.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('H9', 'price.observed.y2.m6.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('I9', 'price.observed.y2.m7.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('J9', 'price.observed.y2.m8.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('K9', 'price.observed.y2.m9.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('L9', 'price.observed.y2.m10.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('M9', 'price.observed.y2.m11.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('N9', 'price.observed.y2.m12.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('B10', 'price.observed.y3.year', 'YEAR', 'read'),
        ('C10', 'price.observed.y3.m1.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('D10', 'price.observed.y3.m2.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('E10', 'price.observed.y3.m3.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('F10', 'price.observed.y3.m4.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('G10', 'price.observed.y3.m5.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('H10', 'price.observed.y3.m6.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('I10', 'price.observed.y3.m7.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('J10', 'price.observed.y3.m8.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('K10', 'price.observed.y3.m9.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('L10', 'price.observed.y3.m10.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('M10', 'price.observed.y3.m11.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('N10', 'price.observed.y3.m12.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('B11', 'price.observed.y4.year', 'YEAR', 'read'),
        ('C11', 'price.observed.y4.m1.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('D11', 'price.observed.y4.m2.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('E11', 'price.observed.y4.m3.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('F11', 'price.observed.y4.m4.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('G11', 'price.observed.y4.m5.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('H11', 'price.observed.y4.m6.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('I11', 'price.observed.y4.m7.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('J11', 'price.observed.y4.m8.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('K11', 'price.observed.y4.m9.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('L11', 'price.observed.y4.m10.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('M11', 'price.observed.y4.m11.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('N11', 'price.observed.y4.m12.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('B12', 'price.observed.y5.year', 'YEAR', 'read'),
        ('C12', 'price.observed.y5.m1.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('D12', 'price.observed.y5.m2.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('E12', 'price.observed.y5.m3.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('F12', 'price.observed.y5.m4.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('G12', 'price.observed.y5.m5.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('H12', 'price.observed.y5.m6.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('I12', 'price.observed.y5.m7.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('J12', 'price.observed.y5.m8.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('K12', 'price.observed.y5.m9.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('L12', 'price.observed.y5.m10.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('M12', 'price.observed.y5.m11.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
        ('N12', 'price.observed.y5.m12.krw_per_unit', 'KRW_PER_SALES_UNIT', 'read'),
    ),
    '8. 생산계획': (
        ('Y26', 'area.cultivation.y5.m2', 'M2', 'not_read_unclear'),
    ),
    '11. 인건비계획': (
        ('D7', 'labor.base_year_label', 'TEXT', 'read'),
    ),
    '13. 감가상각비계획 ': (
        ('B3', 'depreciation.life_years_source', 'TEXT', 'not_read_footnote'),
    ),
    '15. 손익계획': (
        ('C33', 'tax.y1.paid_krw1000', 'KRW_1000', 'read'),
        ('D33', 'tax.y2.paid_krw1000', 'KRW_1000', 'read'),
        ('E33', 'tax.y3.paid_krw1000', 'KRW_1000', 'read'),
        ('F33', 'tax.y4.paid_krw1000', 'KRW_1000', 'read'),
        ('G33', 'tax.y5.paid_krw1000', 'KRW_1000', 'read'),
        ('B47', 'loss.packing_source_note', 'TEXT', 'not_read_footnote'),
    ),
    '16. 수익성분석': (
        ('G25', 'income.nonfarm_krw1000.y1', 'KRW_1000', 'read'),
        ('H25', 'income.nonfarm_krw1000.y2', 'KRW_1000', 'read'),
        ('I25', 'income.nonfarm_krw1000.y3', 'KRW_1000', 'read'),
        ('J25', 'income.nonfarm_krw1000.y4', 'KRW_1000', 'read'),
        ('K25', 'income.nonfarm_krw1000.y5', 'KRW_1000', 'read'),
        ('G26', 'income.transfer_krw1000.y1', 'KRW_1000', 'read'),
        ('H26', 'income.transfer_krw1000.y2', 'KRW_1000', 'read'),
        ('I26', 'income.transfer_krw1000.y3', 'KRW_1000', 'read'),
        ('J26', 'income.transfer_krw1000.y4', 'KRW_1000', 'read'),
        ('K26', 'income.transfer_krw1000.y5', 'KRW_1000', 'read'),
    ),
}


def _sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _numformats(archive):
    """cellXfs 순서대로의 표시 형식 — snapshot()과 같은 해석."""
    from openpyxl.styles.numbers import BUILTIN_FORMATS
    tag = "{" + NS_MAIN + "}"
    styles = ET.fromstring(archive.read("xl/styles.xml"))
    formats = dict(BUILTIN_FORMATS)
    for item in styles.findall(tag + "numFmts/" + tag + "numFmt"):
        formats[int(item.get("numFmtId"))] = item.get("formatCode")
    mc = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
    xfs = []
    for item in styles.find(tag + "cellXfs"):
        xf = item if item.tag == tag + "xf" else item.find(mc + "Fallback/" + tag + "xf")
        if xf is None:
            raise _snapshot_invalid("unsupported style alternative without an xf fallback")
        xfs.append(xf)
    return formats, xfs


def _formula_text(cell, shared_formulas):
    """수식 하나의 정규형(앞의 ``=`` 하나) 또는 None."""
    formula = cell.find(local("f"))
    if formula is None:
        return None
    if formula.get("t") == "shared":
        si = formula.get("si")
        if formula.text:
            return "=" + formula.text
        try:
            anchor, expression = shared_formulas[si]
        except KeyError:
            raise _snapshot_invalid("shared formula member without anchor: " + si)
        from openpyxl.formula.translate import Translator
        return Translator("=" + expression, origin=anchor).translate_formula(cell.get("r"))
    if formula.get("t") == "array":
        return "=" + (formula.text or "")
    return "=" + (formula.text or "")


def _read_workbook(path):
    """{sheet: {"used": (maxcol, maxrow), "cells": {ref: cell}}} 원본 1회 해석.

    cell = {"kind", "value", "numfmt", "has_formula", "cache"}.
    종류는 원본 파일 기준 3가지뿐이다 — constant / blank / formula.
    """
    caches = hwb._native_caches(path)
    out = {}
    with zipfile.ZipFile(path) as archive:
        shared = load_shared(archive)
        formats, xfs = _numformats(archive)
        for name, part in workbook_sheets(archive):
            raw = archive.read(part)
            root = ET.fromstring(raw)
            _, nodes, _, arrays = hwb._sheet_structure(raw, part)
            shared_formulas = {}
            for cell in root.iter(local("c")):
                formula = cell.find(local("f"))
                if formula is not None and formula.get("t") == "shared" and formula.text:
                    shared_formulas[formula.get("si")] = (cell.get("r"), formula.text)
            cells, maxcol, maxrow = {}, 0, 0
            for cell in root.iter(local("c")):
                ref = cell.get("r")
                match = CELL_RE.match(ref or "")
                if not match:
                    raise _snapshot_invalid("invalid source address: %s!%s" % (name, ref))
                maxcol = max(maxcol, col_num(match.group(1)))
                maxrow = max(maxrow, int(match.group(2)))
                # 배열 수식의 나머지 칸(E10)은 자기 <f>가 없다. snapshot()도
                # 그 칸을 상수로 담으므로 여기서도 <f> 유무로만 가른다.
                state = hwb._cell_state(nodes.get(ref), array=ref in arrays)
                if state == "unsupported":
                    raise _snapshot_invalid("unsupported source cell type: %s!%s" % (name, ref))
                if cell.find(local("f")) is not None:
                    kind = "formula"
                    value = _formula_text(cell, shared_formulas)
                elif state in ("blank_node", "absent"):
                    kind, value = "blank", None
                else:
                    kind, value = "constant", text_value(cell, shared)
                style = int(cell.get("s", "0"))
                if style >= len(xfs):
                    raise _snapshot_invalid("invalid source style index: %s!%s" % (name, ref))
                fmt_id = int(xfs[style].get("numFmtId", "0"))
                cells[ref] = {
                    "kind": kind,
                    "value": value,
                    "numfmt": formats.get(fmt_id, "builtin:%d" % fmt_id),
                    "has_formula": cell.find(local("f")) is not None,
                    "cache": caches.get(name, {}).get(ref),
                }
            out[name] = {"used": (maxcol, maxrow), "cells": cells}
    return out


_PARAMS = None


def _params():
    global _PARAMS
    if _PARAMS is None:
        _PARAMS = json.loads(PARAMS_PATH.read_text(encoding="utf-8"))["parameters"]
    return _PARAMS


def _allowed_range(param_key):
    """그 칸 자신의 허용 범위. 파라미터 표에 없으면 빈 dict."""
    spec = _params().get(param_key)
    if not isinstance(spec, dict):
        return {}
    rng = spec.get("allowed_range")
    return rng if isinstance(rng, dict) else {}


def _same_value(left, right):
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, str) or isinstance(right, str):
        return isinstance(left, str) and isinstance(right, str) and left == right
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, ValueError, TypeError):
        return False


def _display(value):
    """학생 값 표시 — 숫자는 Decimal 정규화(``1.50`` → ``1.5``)."""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float, Decimal)):
        try:
            number = Decimal(str(value)).normalize()
        except (InvalidOperation, ValueError):
            return str(value)
        if number == number.to_integral_value():
            return "%d" % int(number)
        return format(number, "f")
    return str(value)


def _cache_same(original, student, unit_code):
    """원본 캐시 vs 학생 캐시. 한쪽이 없으면 '다르다고 말할 수 없음'은 같음."""
    if original is None or student is None:
        return True
    expected, actual = original[1], student[1]
    if expected is None or actual is None:
        return True
    try:
        hwb.compare_raw_cache(expected, actual, unit_code)
        return True
    except hwb.Held:
        return hwb._example_equal(expected, actual)


def _ratio_message(value):
    """범위 밖 비율 값의 학생 문구 — 적은 소수가 몇 %로 읽히는지 짚어 준다."""
    percent = value * 100
    return ("비율 칸에는 0과 1 사이 소수로 적어 주세요. 적으신 값 %s는 "
            "%s%%로 읽혀요." % (_display(value), _display(percent)))


def _cache_uncompared(source, student, state):
    """수식은 그대로인데 한쪽에 계산값이 없어 캐시를 나란히 못 본 경우.

    11상태 계약은 그대로 두고(``formula_kept``) 대신 issue 한 줄로 알린다 —
    캐시가 한쪽에 없다고 '계산값도 같다'고 말할 수는 없다.
    """
    if state != "formula_kept":
        return False
    for cell in (source, student):
        cache = None if cell is None else cell.get("cache")
        if cache is None or cache[1] is None:
            return True
    return False


def _ratio_issues(sheet, ref, param_key, unit_code, source, student, state):
    """비율 칸의 범위 이탈·단위 미확인. 값이 바뀐 칸에만 붙는다."""
    issues = []
    if unit_code != "RATIO" or state not in VALUE_STATES:
        return issues
    if student["kind"] != "constant":
        return issues
    if student["numfmt"] != source["numfmt"]:
        issues.append({"status": "needs_visual_review", "reason": REASON_RATIO_UNIT,
                       "sheet": sheet, "cell": ref})
    try:
        value = Decimal(str(student["value"]))
    except (InvalidOperation, ValueError, TypeError):
        return issues
    if not value.is_finite():
        # NaN·Infinity는 Decimal로는 만들어지지만 범위 비교에서 예외가 난다.
        issues.append({"status": "needs_visual_review", "reason": REASON_RATIO_FINITE,
                       "sheet": sheet, "cell": ref})
        return issues
    rng = _allowed_range(param_key)
    low, high = rng.get("min"), rng.get("max")
    outside = False
    if isinstance(low, (int, float)) and value < Decimal(str(low)):
        outside = True
    if isinstance(high, (int, float)):
        limit = Decimal(str(high))
        outside = outside or (value >= limit if rng.get("max_exclusive") else value > limit)
    if outside:
        issues.append({"status": "needs_visual_review", "reason": REASON_OUT_OF_RANGE,
                       "sheet": sheet, "cell": ref,
                       "message": _ratio_message(value)})
    return issues


def _read_state(source, student, unit_code):
    """② 11상태 — 원본 종류 → 학생 종류 → 값(또는 수식 글자·캐시)."""
    if student is None:
        student = {"kind": "absent", "value": None, "numfmt": None, "cache": None}
    kind = student["kind"]
    if source["kind"] == "formula":
        if kind == "formula":
            if student["value"] != source["value"]:
                return "formula_replaced"
            return "formula_kept" if _cache_same(source["cache"], student["cache"], unit_code) \
                else "formula_cache_only"
        return "formula_to_constant" if kind == "constant" else "formula_deleted"
    if source["kind"] == "constant":
        if kind == "formula":
            return "formula_added"
        if kind == "constant":
            return "same_value" if _same_value(source["value"], student["value"]) \
                else "constant_overwritten"
        return "constant_deleted"
    if kind == "formula":
        return "formula_added"
    return "blank_filled" if kind == "constant" else "blank_kept"


def _differs(source, student):
    if source["kind"] == "formula" or student is None or student["kind"] == "formula":
        return None
    if source["kind"] == "constant" and student["kind"] == "constant":
        return not _same_value(source["value"], student["value"])
    if source["kind"] == "constant":
        return True
    return student["kind"] == "constant"


def _row(sheet, ref, state, source, student):
    student_kind = "absent" if student is None else student["kind"]
    if student is None or student["kind"] == "blank":
        student_value = None
    else:
        student_value = student["value"]
    if state in VALUE_STATES:
        message = VALUE_MESSAGE.format(v=_display(student_value))
    else:
        message = MESSAGES[state]
    return {
        "sheet": sheet,
        "cell": ref,
        "state": state,
        "source_kind": source["kind"],
        "source_value": source["value"],
        "student_kind": student_kind,
        "student_value": student_value,
        "differs": _differs(source, student),
        "message": message,
    }


INPUT_CELLS = {
    (sheet, ref): (param_key, unit, state)
    for sheet, rows in RECORDED.items()
    for ref, param_key, unit, state in rows
}
INPUT_BY_SHEET = {}
for (_sheet, _ref) in INPUT_CELLS:
    INPUT_BY_SHEET.setdefault(_sheet, set()).add(_ref)
del _sheet, _ref

# 시트 안에서 입력 병합을 포함하는 것만 지운다(20개). 나머지 412개는 그대로.
def _tag_end(data, index):
    """``index``에서 시작하는 태그 하나의 ``>`` 다음 위치(따옴표 안 ``>``는 건너뛴다)."""
    quote = None
    while index < len(data):
        char = data[index]
        if quote is not None:
            if char == quote:
                quote = None
        elif char in (ord('"'), ord("'")):
            quote = char
        elif char == ord(">"):
            return index + 1
        index += 1
    return index


def _merge_spans(data, refs):
    """입력 좌표와 겹치는 ``mergeCell`` 요소의 ``(시작, 끝)`` 바이트 구간.

    표기(공백·작은따옴표·네임스페이스 접두사·닫는 태그)에 기대지 않도록 XML로
    읽는다. expat의 바이트 오프셋이라 나머지 바이트는 손대지 않는다.
    """
    parser = expat.ParserCreate()
    spans, stack = [], []

    def start(name, attrs):
        start_index = parser.CurrentByteIndex
        opening_end = _tag_end(data, start_index)
        ref = next((value for key, value in attrs.items()
                    if key.rsplit(":", 1)[-1] == "ref"), None)
        closed = data[opening_end - 2:opening_end] == b"/>"
        stack.append((name.rsplit(":", 1)[-1], start_index, ref,
                      opening_end if closed else None))

    def end(name):
        local_name, start_index, ref, span_end = stack.pop()
        if local_name != "mergeCell":
            return
        if span_end is None:
            span_end = _tag_end(data, parser.CurrentByteIndex)
        try:
            cells = expand_ref(ref or "")
        except ValueError:
            return
        if refs.intersection(cells):
            spans.append((start_index, span_end))

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.Parse(data, True)
    return spans


def _drop_input_merges(data, refs):
    """``<mergeCell>`` 중 입력 좌표와 겹치는 것만 지운 사본 바이트."""
    out = data
    for start_index, end_index in reversed(_merge_spans(data, refs)):
        out = out[:start_index] + out[end_index:]
    return out


def _strip_input_merges(src, dst):
    """zip 재작성 — 병합 제거 말고 다른 파트는 바이트 그대로 옮긴다."""
    with zipfile.ZipFile(src) as archive:
        sheets = dict(workbook_sheets(archive))
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as out:
            for info in archive.infolist():
                data = archive.read(info.filename)
                name = next((n for n, part in sheets.items()
                             if part == info.filename and n in INPUT_BY_SHEET), None)
                if name is not None:
                    data = _drop_input_merges(data, INPUT_BY_SHEET[name])
                out.writestr(info, data)


def _drop_item(item, source_cells, extents):
    """⓪ 결과에서 입력 칸·원본 사용 범위 밖 항목을 뺀다.

    칸이 원본에 있고 그 주소가 사용 범위 밖이 아니면 절대 빼지 않는다 —
    범위 안쪽에 값을 새로 적으면 ``template_cell_changed``로 거절된다.
    """
    sheet = item.get("sheet")
    ref = item.get("cell")
    if sheet is None or ref is None:
        return False  # 시트 순서·시트 단위 항목은 절대 빼지 않는다
    if (sheet, ref) in INPUT_CELLS:
        return True
    known = source_cells.get(sheet)
    extent = extents.get(sheet)
    match = CELL_RE.match(ref)
    if known is None or extent is None or match is None:
        return False
    if ref in known["cells"]:
        return False
    if col_num(match.group(1)) <= extent[0] and int(match.group(2)) <= extent[1]:
        return False
    return True


def _snapshot(student_path, source_path, source_cells, extents):
    """⓪ 원본 사본 대조 → (rejections, issues). 캐시는 비교되지 않는다."""
    rejections, issues = [], []
    try:
        with tempfile.TemporaryDirectory(prefix="hort-readback-") as work:
            before, after = Path(work) / "before.xlsx", Path(work) / "after.xlsx"
            _strip_input_merges(source_path, before)
            _strip_input_merges(student_path, after)
            compared = gg_office.inspect_preserved_template(before, after)
    except (zipfile.BadZipFile, ET.ParseError, expat.ExpatError) as error:
        # 병합을 지우려면 ZIP·시트 XML을 다시 읽어야 한다. 못 읽는 파일은
        # 대조 거절(2)이 아니라 입력 오류(3)다 — run이 이 Held를 그렇게 낸다.
        raise _snapshot_invalid("unreadable workbook: %s" % error) from error
    except InvalidOperation as error:
        # 비율 칸에 숫자가 아닌 글자가 적히면 ⓪의 Decimal 해석이 여기서 멈춘다.
        # 근원은 gg_office(p3C 소유)지만 고칠 자리는 우리 읽기 경계다 —
        # 이것도 못 읽는 사본이므로 같은 Held·입력 오류(3)로 닫는다.
        raise _snapshot_invalid("unreadable workbook: value is not a number") from error
    except ValueError as error:
        return [{"status": "fail", "reason": REASON_SNAPSHOT, "detail": str(error)}], issues
    for item in compared:
        if _drop_item(item, source_cells, extents):
            continue
        if item.get("status") == "fail":
            code = REJECT_REASONS.get(item.get("reason"), "template_changed")
            rejection = {"status": "fail", "reason": code}
            if item.get("sheet") is not None:
                rejection["sheet"] = item["sheet"]
            if item.get("cell") is not None:
                rejection["cell"] = item["cell"]
            if item.get("detail") is not None:
                rejection["detail"] = item["detail"]
            rejections.append(rejection)
        else:
            issues.append(item)
    return rejections, issues


def _student_cell(cells, ref):
    cell = cells.get(ref)
    if cell is None or cell["kind"] == "blank":
        if cell is None:
            return None
        return {"kind": "blank", "value": None, "numfmt": cell["numfmt"], "cache": None}
    return cell


def _classify(source_cells, student_cells):
    """① 30칸 + ② 221칸 → (rows, issues). 원본에 있는 251칸만 다룬다."""
    rows, issues = [], []
    for sheet, refs in INPUT_BY_SHEET.items():
        student_sheet = student_cells.get(sheet)
        for ref in sorted(refs, key=lambda r: (col_num(CELL_RE.match(r).group(1)),
                                               int(CELL_RE.match(r).group(2)))):
            param_key, unit, recorded = INPUT_CELLS[(sheet, ref)]
            source = source_cells[sheet]["cells"][ref]
            student = None if student_sheet is None else _student_cell(
                student_sheet["cells"], ref)
            if recorded != READ:
                state = recorded
            else:
                state = _read_state(source, student, unit)
                issues.extend(_ratio_issues(sheet, ref, param_key, unit,
                                            source, student, state))
                if _cache_uncompared(source, student, state):
                    issues.append({"status": "needs_visual_review",
                                   "reason": REASON_CACHE_UNCOMPARED,
                                   "sheet": sheet, "cell": ref})
            rows.append(_row(sheet, ref, state, source, student))
    return rows, issues


def _unmapped(source_cells, student_cells):
    """원본 251 밖 칸에 학생이 값을 넣었거나 수식을 넣은 경우 한 줄씩."""
    out = []
    for sheet in sorted(student_cells):
        source = source_cells.get(sheet)
        if source is None:
            continue
        known = source["cells"]
        for ref in sorted(student_cells[sheet]["cells"],
                          key=lambda r: (col_num(CELL_RE.match(r).group(1)),
                                         int(CELL_RE.match(r).group(2)))):
            if (sheet, ref) in INPUT_CELLS:
                continue
            student = student_cells[sheet]["cells"][ref]
            if student["kind"] == "blank":
                continue
            if ref not in known:
                out.append({"status": "needs_visual_review", "reason": REASON_UNMAPPED,
                            "sheet": sheet, "cell": ref})
                continue
            old = known[ref]
            if old["kind"] != student["kind"] or old["value"] != student["value"]:
                out.append({"status": "needs_visual_review", "reason": REASON_UNMAPPED,
                            "sheet": sheet, "cell": ref})
    return out


def _held(reason, detail=None):
    value = {"status": "held", "reason": reason, "cells": []}
    if detail:
        value["detail"] = detail
    return value


def _source_workbook(root, profile):
    """profile의 H-X1 해시와 같은 등록 원천 → 프로젝트 안 실제 경로."""
    reference = profile["workbook_reference"]
    project = gg_core.load(root)
    info = gg_hort_plan._workbook_references(project, reference, root)
    held = []
    for item in info["items"]:
        if item["file_check"] != "match":
            held.append("%s: %s" % (item["source_id"], item["held_reason"]))
            continue
        src = (project.get("sources") or {})[item["source_id"]]
        return gg_core.local(root, src["path"])
    raise ValueError("; ".join(held) or info["status"])


def run(project_root, xlsx_path, out_path=None):
    """251칸 분류표를 만들어 JSON 한 파일로 쓴다. ``(value, exit_code)``."""
    root = Path(project_root).resolve()
    out_rel = DEFAULT_OUT if out_path is None else out_path
    out = (root / out_rel).resolve()
    if out == root:
        return _held("output_path_in_canonical"), 3
    if not out.is_relative_to(root):
        return _held("output_outside_project"), 3
    if out.exists():
        return _held("output_exists", str(out_rel)), 3
    student = Path(xlsx_path)
    if not student.is_absolute():
        student = (Path.cwd() / student)
    student = student.resolve()
    if not student.is_file():
        return _held("student_file_missing", str(xlsx_path)), 3
    try:
        profile = gg_hort_plan.load_profile()
        source_path = _source_workbook(root, profile)
    except gg_hort_plan.ProfileInvalidError as error:
        return _held("profile_invalid", str(error)), 3
    except (ValueError, KeyError, TypeError, OSError) as error:
        return _held("project_invalid", str(error)), 3
    try:
        source_cells = _read_workbook(source_path)
        student_cells = _read_workbook(student)
    except hwb.Held as error:
        return _held(error.code, error.detail), 3
    except (ValueError, KeyError, OSError, zipfile.BadZipFile, ET.ParseError,
            expat.ExpatError, InvalidOperation) as error:
        # BadZipFile은 Exception 직속, ParseError는 SyntaxError 직속이라
        # (ValueError, OSError)로는 안 잡힌다. 깨진 파일은 입력 오류 3이다.
        # 비율 칸의 비수치 글자(InvalidOperation)도 같은 입력 오류로 닫는다.
        return _held(REASON_SNAPSHOT, str(error)), 3

    extents = {name: item["used"] for name, item in source_cells.items()}
    try:
        rejections, issues = _snapshot(student, source_path, source_cells, extents)
    except hwb.Held as error:
        return _held(error.code, error.detail), 3
    if rejections:
        value = _held(rejections[0]["reason"])
        value["status"] = "held"
        value["rejections"] = rejections
        return value, 2

    rows, ratio_issues = _classify(source_cells, student_cells)
    issues.extend(ratio_issues)
    issues.extend(_unmapped(source_cells, student_cells))

    by_state = {state: 0 for state in STATE_ORDER}
    for row in rows:
        by_state[row["state"]] += 1
    read = sum(by_state[state] for state in READ_STATES)
    value = {
        "status": "read",
        "source_sha256": _sha256(source_path),
        "student_sha256": _sha256(student),
        "counts": {
            "recorded": len(rows),
            "read": read,
            "not_read": len(rows) - read,
            "by_state": by_state,
        },
        "issues": issues,
        "cells": rows,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    return value, 0


def summary_lines(value):
    if value.get("status") != "read":
        return [
            "총 0칸",
            "기록 있음 0 · 적는 자리 0 · 읽지 않음 0",
            "거절: " + str(value.get("reason")),
        ]
    counts = value["counts"]
    return [
        "총 %d칸" % counts["recorded"],
        "기록 있음 %d · 적는 자리 %d · 읽지 않음 %d"
        % (counts["recorded"], counts["read"], counts["not_read"]),
        "거절: 0",
    ]


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="gg_hort_input_readback.py",
        description="원예: 채운 학교 엑셀의 251칸을 상태 하나씩 분류",
    )
    parser.add_argument("project", metavar="DIR")
    parser.add_argument("--xlsx", required=True, metavar="FILE")
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    value, code = run(args.project, args.xlsx, args.out)
    for line in summary_lines(value):
        print(line)
    return code


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        getattr(_stream, "reconfigure", lambda **_: None)(encoding="utf-8")
    raise SystemExit(main())
