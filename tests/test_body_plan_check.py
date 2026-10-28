"""Explicit channel claims must not contradict the recalculated workbook."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from openpyxl import Workbook
from tests._harness import SCRIPTS, runtime


class BodyPlanCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.xlsx = self.root / 'plan.xlsx'
        book = Workbook()
        sheet = book.active
        sheet.title = '판매계획'
        for row, values in enumerate(((0, 2400.4), (0.15, 2500), (0.3, 2600)), 2):
            sheet.cell(row, 2, values[0])
            sheet.cell(row, 3, values[1])
        book.save(self.xlsx)
        book.close()
        self.mapping = {'channels': [{'name': '직거래', 'aliases': ['직거래', '온라인'], 'years': {
            str(2040 + i): {'share': {'sheet': '판매계획', 'cell': f'B{i+2}'},
                           'price': {'sheet': '판매계획', 'cell': f'C{i+2}'}}
            for i in range(3)
        }}]}
        self.mod = runtime('gg_body_plan_check')

    def test_late_start_in_narrative_is_rejected_even_with_matching_ratio_elsewhere(self):
        result = self.mod.crosscheck('온라인·직거래 2042년 소량 시작\n2041년 직거래 15%', self.xlsx, self.mapping)
        self.assertEqual(result['status'], 'fail')
        self.assertEqual(result['issues'][0]['expected'], '2041')
        self.assertEqual(result['issues'][0]['metric'], 'start')
        self.assertEqual(result['issues'][0]['line'], 1)

    def test_start_ratio_and_rounded_price_match(self):
        result = self.mod.crosscheck('2041년 직거래 15%로 시작\n2042년 온라인 30%\n2040년 직거래 단가 2,400원/kg', self.xlsx, self.mapping)
        self.assertEqual(result['status'], 'pass')
        self.assertEqual([(r['metric'], r['actual'], r['expected']) for r in result['comparisons']],
                         [('start', '2041', '2041'), ('share', '15', '15'), ('share', '30', '30'), ('price', '2400', '2400')])

    def test_ratio_and_price_mismatch_name_their_cells(self):
        result = self.mod.crosscheck('2041년 직거래 20%, 단가 2,600원/kg', self.xlsx, self.mapping)
        self.assertEqual(result['status'], 'fail')
        self.assertEqual([r['locator']['cell'] for r in result['issues']], ['B3', 'C3'])

    def test_zero_claims_does_not_pass(self):
        result = self.mod.crosscheck('판로는 학생에게 확인한다.', self.xlsx, self.mapping)
        self.assertEqual(result['status'], 'not_checked')
        self.assertEqual(result['comparisons'], [])

    def test_missing_cache_is_blocked_not_zero(self):
        book = Workbook()
        book.active.title = '판매계획'
        book.active['B2'] = '=0.1'
        book.save(self.xlsx)
        book.close()
        with self.assertRaisesRegex(ValueError, 'cached value'):
            self.mod.crosscheck('2040년 직거래 10%', self.xlsx, self.mapping)

    def test_gapped_plan_period_is_rejected(self):
        mapping = copy.deepcopy(self.mapping)
        del mapping['channels'][0]['years']['2041']
        with self.assertRaisesRegex(ValueError, 'contiguous'):
            self.mod.crosscheck('2042년 직거래 시작', self.xlsx, mapping)

    def test_cli_reports_mismatch_and_nonzero_exit(self):
        body = self.root / 'body.md'
        body.write_text('온라인 2042년 소량 시작', encoding='utf-8')
        locator = self.root / 'map.json'
        locator.write_text(json.dumps(self.mapping, ensure_ascii=False), encoding='utf-8')
        result = subprocess.run([sys.executable, str(SCRIPTS / 'gg_body_plan_check.py'), '--body', str(body),
                                 '--xlsx', str(self.xlsx), '--map', str(locator)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'fail')
