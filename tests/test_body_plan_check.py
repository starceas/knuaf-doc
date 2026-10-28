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
        for row, values in enumerate(((0, 2400.4), (0.15, 2500), (0.3, 2600), (0.45, 2700), (0.6, 2800)), 2):
            sheet.cell(row, 2, values[0])
            sheet.cell(row, 3, values[1])
        book.save(self.xlsx)
        book.close()
        self.mapping = {'channels': [{'name': '직거래', 'aliases': ['직거래', '온라인'], 'years': {
            str(2040 + i): {'share': {'sheet': '판매계획', 'cell': f'B{i+2}'},
                           'price': {'sheet': '판매계획', 'cell': f'C{i+2}'}}
            for i in range(5)
        }}]}
        self.mod = runtime('gg_body_plan_check')

    def test_late_start_in_narrative_is_rejected_even_with_matching_ratio_elsewhere(self):
        result = self.mod.crosscheck('온라인·직거래 2042년 소량 시작\n2041년 직거래 15%', self.xlsx, self.mapping)
        self.assertEqual(result['status'], 'fail')
        self.assertEqual(result['issues'][0]['expected'], '2041')
        self.assertEqual(result['issues'][0]['metric'], 'start')
        self.assertEqual(result['issues'][0]['line'], 1)

    def test_metrics_before_year_cannot_hide_behind_a_matching_claim(self):
        for metric, claim in (
            ('start', '직거래 시작은 2042년이다.'),
            ('price', '직거래 단가 9,999원/kg은 2041년 계획이다.'),
            ('share', '직거래 비율 99%는 2041년 계획이다.'),
        ):
            with self.subTest(metric=metric):
                result = self.mod.crosscheck(claim + '\n2041년 직거래 15%', self.xlsx, self.mapping)
                self.assertEqual(result['status'], 'not_checked')
                self.assertEqual([r['status'] for r in result['comparisons']], ['pass'])
                self.assertEqual([(r['line'], r['metric'], r['status'], r['quote'])
                                  for r in result['issues']], [(1, metric, 'not_checked', claim)])

    def test_unparsed_prefix_and_matching_suffix_on_same_line_do_not_pass(self):
        claim = '직거래 단가 9,999원/kg은 2041년 계획이며 판매비율은 15%다.'
        result = self.mod.crosscheck(claim, self.xlsx, self.mapping)
        self.assertEqual(result['status'], 'not_checked')
        self.assertEqual([(r['metric'], r['status']) for r in result['comparisons']], [('share', 'pass')])
        self.assertEqual([(r['line'], r['metric']) for r in result['issues']], [(1, 'price')])

    def test_metrics_between_years_cannot_be_confirmed_against_the_earlier_plan(self):
        for metric, claim, unparsed in (
            ('price', '직거래 2041년 15%로 시작하며, 단가 2,500원/kg은 2042년 기준이다.', '2,500원/kg'),
            ('share', '직거래 2041년 시작했고, 비율 15%는 2042년 계획이다.', '15%'),
        ):
            with self.subTest(metric=metric):
                result = self.mod.crosscheck(claim, self.xlsx, self.mapping)
                self.assertEqual(result['status'], 'not_checked')
                issue = next(r for r in result['issues'] if r['metric'] == metric)
                self.assertEqual((issue['status'], issue['candidate_years'], issue['unparsed'], issue['quote']),
                                 ('not_checked', [2041, 2042], unparsed, claim))
                self.assertFalse(any(r['metric'] == metric for r in result['comparisons']))

    def test_year_before_metric_controls_still_report_the_later_year_mismatch(self):
        for metric, claim, actual, expected, cell in (
            ('price', '직거래 2041년 15%로 시작하며, 2042년 단가 2,500원/kg이다.', '2500', '2600', 'C4'),
            ('share', '직거래 2041년 시작했고, 2042년 비율 15%로 계획한다.', '15', '30', 'B4'),
        ):
            with self.subTest(metric=metric):
                result = self.mod.crosscheck(claim, self.xlsx, self.mapping)
                self.assertEqual(result['status'], 'fail')
                mismatch = next(r for r in result['issues'] if r['status'] == 'fail')
                self.assertEqual((mismatch['year'], mismatch['metric'], mismatch['actual'],
                                  mismatch['expected'], mismatch['locator']['cell']),
                                 (2042, metric, actual, expected, cell))

    def test_normal_claims_and_directly_year_bound_multiple_periods_pass(self):
        for claim in (
            '2041년 직거래 15%로 시작\n2042년 직거래 30%, 2,600원/kg',
            '직거래 2040년 0%, 2041년 비율 15%, 2042년 30%, 2043년 45%, 2044년 60%',
            '온라인 2041년 시작, 2042년 단가 2,600원/kg, 2043년 가격 2,700원/kg',
        ):
            with self.subTest(claim=claim):
                result = self.mod.crosscheck(claim, self.xlsx, self.mapping)
                self.assertEqual(result['status'], 'pass')
                self.assertEqual(result['issues'], [])
                self.assertTrue(all(r['actual'] == r['expected'] for r in result['comparisons']))

    def test_ambiguous_start_share_and_price_before_third_fourth_and_fifth_years(self):
        shares = {2040: 0, 2041: 15, 2042: 30, 2043: 45}
        for following in (2042, 2043, 2044):
            previous = following - 1
            prefix = '직거래 ' + ', '.join(f'{year}년 {shares[year]}%' for year in range(2040, following))
            for metric, text in (
                ('start', '시작'),
                ('share', f'비율 {shares[previous]}%'),
                ('price', f'단가 {2500 + (previous - 2041) * 100:,}원/kg'),
            ):
                for separator in (', ', '로 계획하며 '):
                    with self.subTest(following=following, metric=metric, separator=separator):
                        claim = f'{prefix}{separator}{text}은 {following}년 계획이다.'
                        result = self.mod.crosscheck(claim, self.xlsx, self.mapping)
                        self.assertEqual(result['status'], 'not_checked')
                        issue = next(r for r in result['issues']
                                     if r['metric'] == metric and r['unparsed'] in text)
                        self.assertEqual((issue['status'], issue['candidate_years'], issue['quote']),
                                         ('not_checked', [previous, following], claim))
                        self.assertFalse(any(r['metric'] == metric and r['year'] == previous
                                             for r in result['comparisons']))

    def test_explicit_later_years_retain_start_share_and_price_mismatches(self):
        shares = {2040: 0, 2041: 15, 2042: 30, 2043: 45}
        for year in (2042, 2043, 2044):
            prefix = '직거래 ' + ', '.join(f'{prior}년 {shares[prior]}%' for prior in range(2040, year))
            for metric, text, expected in (
                ('start', '시작', '2041'),
                ('share', f'비율 {shares[year - 1]}%', str(shares[year - 1] + 15)),
                ('price', f'단가 {2500 + (year - 2042) * 100:,}원/kg', str(2600 + (year - 2042) * 100)),
            ):
                with self.subTest(year=year, metric=metric):
                    result = self.mod.crosscheck(f'{prefix}, {year}년 {text}', self.xlsx, self.mapping)
                    self.assertEqual(result['status'], 'fail')
                    self.assertEqual([(r['year'], r['metric'], r['status'], r['expected'])
                                      for r in result['issues']], [(year, metric, 'fail', expected)])

    def test_even_an_adjacent_year_does_not_override_an_unseparated_following_year(self):
        for claim, metric in (
            ('직거래 2041년 단가 2,500원/kg은 2042년 기준이다.', 'price'),
            ('직거래 2041년 비율 15%는 2042년 계획이다.', 'share'),
            ('직거래 2041년 시작은 2042년이다.', 'start'),
        ):
            with self.subTest(metric=metric):
                result = self.mod.crosscheck(claim, self.xlsx, self.mapping)
                self.assertEqual(result['status'], 'not_checked')
                self.assertEqual(result['comparisons'], [])
                self.assertEqual([(r['metric'], r['candidate_years']) for r in result['issues']],
                                 [(metric, [2041, 2042])])

    def test_reviewed_shared_year_claims_compare_all_years(self):
        for claim, metrics in (
            ('직거래 2041년 및 2042년 비율은 모두 30%다.', {'share'}),
            ('직거래 2041년과 2042년 비율은 모두 30%다.', {'share'}),
            ('직거래 2041년–2042년 비율은 매년 30%다.', {'share'}),
            ('직거래 2041년 및 2042년 단가는 모두 2,600원/kg이다.', {'price'}),
            ('직거래 2041년과 2042년 단가는 모두 2,600원/kg이다.', {'price'}),
            ('직거래 (2041년 및 2042년) 비율은 모두 30%다.', {'share'}),
            ('직거래 2041년 및 2042년 단가는 2,600원/kg, 판매비율은 30%다.', {'price', 'share'}),
        ):
            with self.subTest(claim=claim):
                result = self.mod.crosscheck(claim, self.xlsx, self.mapping)
                self.assertEqual(result['status'], 'fail')
                self.assertEqual({(r['year'], r['metric'], r['status']) for r in result['comparisons']},
                                 {(year, metric, 'fail' if year == 2041 else 'pass')
                                  for year in (2041, 2042) for metric in metrics})

    def test_group_grammar_covers_lists_ranges_and_unmarked_endpoints(self):
        for expression, years in (
            ('2041년와 2042년', [2041, 2042]),
            ('2041년, 2042년', [2041, 2042]),
            ('2040, 2041 및 2042년', [2040, 2041, 2042]),
            ('2041–2043년', [2041, 2042, 2043]),
            ('2041~2043', [2041, 2042, 2043]),
            ('2041년부터 2043년까지', [2041, 2042, 2043]),
            ('2041년에서 2043년까지', [2041, 2042, 2043]),
            ('2041년, 2042–2044년', [2041, 2042, 2043, 2044]),
        ):
            with self.subTest(expression=expression):
                result = self.mod.crosscheck(f'직거래 {expression} 비율은 모두 30%다.', self.xlsx, self.mapping)
                self.assertEqual(result['status'], 'fail')
                self.assertEqual([r['year'] for r in result['comparisons']], years)
        mapping = copy.deepcopy(self.mapping)
        for year in ('2041', '2042'):
            mapping['channels'][0]['years'][year] = mapping['channels'][0]['years']['2042'].copy()
        result = self.mod.crosscheck('직거래 2041년 및 2042년 단가 2,600원/kg, 비율 30%', self.xlsx, mapping)
        self.assertEqual(result['status'], 'pass')
        self.assertEqual({(r['year'], r['metric']) for r in result['comparisons']},
                         {(year, metric) for year in (2041, 2042) for metric in ('price', 'share')})
        self.assertEqual(result['issues'], [])

    def test_range_interior_is_compared_even_when_both_endpoints_match(self):
        for metric, text, column in (('share', '비율 30%', 'B'), ('price', '단가 2,600원/kg', 'C')):
            with self.subTest(metric=metric):
                mapping = copy.deepcopy(self.mapping)
                for year, row in (('2041', 4), ('2042', 3), ('2043', 4)):
                    mapping['channels'][0]['years'][year][metric]['cell'] = f'{column}{row}'
                result = self.mod.crosscheck(f'직거래 2041~2043년 {text}', self.xlsx, mapping)
                self.assertEqual(result['status'], 'fail')
                self.assertEqual([(r['year'], r['status']) for r in result['comparisons']],
                                 [(2041, 'pass'), (2042, 'fail'), (2043, 'pass')])
                self.assertEqual(result['issues'][0]['locator']['cell'], f'{column}3')

    def test_uncertain_groups_preserve_every_member_instead_of_comparing_a_subset(self):
        for expression, candidates in (
            ('2041년 또는 2042년', [2041, 2042]),
            ('2041년 대비 2042년', [2041, 2042]),
            ('2041/2042년', [2041, 2042]),
            ('2042년~2041년', [2041, 2042]),
            ('(2041년 및 2042년 중 2043년)', [2041, 2042, 2043]),
        ):
            with self.subTest(expression=expression):
                claim = f'직거래 {expression} 단가 2,600원/kg, 비율 30%'
                result = self.mod.crosscheck(claim + '\n2044년 직거래 60%', self.xlsx, self.mapping)
                self.assertEqual(result['status'], 'not_checked')
                self.assertEqual([(r['line'], r['year'], r['status']) for r in result['comparisons']],
                                 [(2, 2044, 'pass')])
                self.assertEqual({(r['metric'], tuple(r['candidate_years']), r['status'], r['quote'])
                                  for r in result['issues']},
                                 {(metric, tuple(candidates), 'not_checked', claim) for metric in ('price', 'share')})

    def test_later_group_does_not_hide_behind_an_earlier_matching_period(self):
        result = self.mod.crosscheck('직거래 2040년 0%, 2041년 및 2042년 비율은 모두 30%', self.xlsx, self.mapping)
        self.assertEqual(result['status'], 'fail')
        self.assertEqual([(r['year'], r['status']) for r in result['comparisons']],
                         [(2040, 'pass'), (2041, 'fail'), (2042, 'pass')])

    def test_unmapped_range_year_blocks_even_when_all_known_years_match(self):
        mapping = copy.deepcopy(self.mapping)
        mapping['channels'][0]['years']['2043']['share']['cell'] = 'B6'
        result = self.mod.crosscheck('직거래 2043년~2045년 비율은 모두 60%', self.xlsx, mapping)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual([(r['year'], r['status']) for r in result['comparisons']],
                         [(2043, 'pass'), (2044, 'pass'), (2045, 'blocked')])

    def test_cli_summary_counts_comparisons_and_unchecked_claims_for_every_status(self):
        for claim, mapping, status, code, comparisons, unchecked in (
            ('직거래 2041년 비율 15%', self.mapping, 'pass', 0, 1, 0),
            ('직거래 2041년 대비 2042년 비율 30%', self.mapping, 'not_checked', 2, 0, 1),
            ('직거래 2041년 대비 2042년 비율 30%\n2044년 직거래 60%', self.mapping, 'not_checked', 2, 1, 1),
            ('직거래 2041년 및 2042년 비율 30%', self.mapping, 'fail', 1, 2, 0),
            ('직거래 2041년 비율 15%', {}, 'blocked', 2, 0, 0),
        ):
            with self.subTest(status=status, comparisons=comparisons):
                body, locator = self.root / 'body.md', self.root / 'map.json'
                body.write_text(claim, encoding='utf-8')
                locator.write_text(json.dumps(mapping, ensure_ascii=False), encoding='utf-8')
                result = subprocess.run([sys.executable, str(SCRIPTS / 'gg_body_plan_check.py'), '--body', str(body),
                                         '--xlsx', str(self.xlsx), '--map', str(locator)], capture_output=True, text=True)
                self.assertEqual(result.returncode, code, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload['status'], status)
                self.assertEqual((payload['summary']['comparison_count'], payload['summary']['not_checked_count']),
                                 (comparisons, unchecked))

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

    def test_cli_with_matching_and_unparsed_claims_returns_not_checked(self):
        locator = self.root / 'map.json'
        locator.write_text(json.dumps(self.mapping, ensure_ascii=False), encoding='utf-8')
        for text in ('직거래 시작은 2042년이다.', '직거래 단가 9,999원/kg은 2041년 계획이다.'):
            with self.subTest(text=text):
                body = self.root / 'body.md'
                body.write_text(text + '\n2041년 직거래 15%', encoding='utf-8')
                result = subprocess.run([sys.executable, str(SCRIPTS / 'gg_body_plan_check.py'),
                                         '--body', str(body), '--xlsx', str(self.xlsx),
                                         '--map', str(locator)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 2, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload['status'], 'not_checked')
                self.assertEqual(payload['issues'][0]['quote'], text)
