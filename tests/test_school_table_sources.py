"""D3/X1-02: explicit credits, byte preservation and managed publication.

All citations here are synthetic test data, never usable references.
"""
import copy
import hashlib
import itertools
import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

from tests._harness import SCRIPTS, ContractCase, bind_major, runtime, write_text
from tests.test_major_guard_paper import _spec


IDS = ('farm_overview', 'climate', 'disasters', 'shipping_markets',
       'growth_targets', 'swot')
CAPTIONS = ('표 1. 농장 종합 개요', '표 2. 기상환경', '표 3. 재해',
            '표 4. 출하시장 거리', '표 5. 가족구성 및 성장목표', '표 6. SWOT 분석')


def source(**extra):
    value = dict(kind='stat', source_type='official_stat',
                 source='합성 검증 기관', title='합성 검증 자료 실제 인용 금지',
                 year='2026', locator='합성 표 1 행 2')
    value.update(extra)
    return value


def credits(ids=IDS):
    return {key: {'sources': [source(locator='합성 위치 ' + key)]} for key in ids}


class SchoolTableSourcesTests(ContractCase):
    def setUp(self):
        self.core = runtime('gg_core')
        self.doc = runtime('gg_document')
        self.sp = runtime('gg_school_paper')
        self.root = self.make_project()
        bind_major(self.root)
        self.spec = _spec(major_id='specialty_crops')
        self.context = runtime('gg_major_contract').output_context(self.root, None)
        self.original = self.sp.paper(self.spec, context=self.context)

    def attach(self, text=None, entries=None, **kw):
        spec = dict(self.spec)
        if entries is not None:
            spec['table_sources'] = entries
        return runtime('gg_school_table_sources').attach_table_sources(
            self.original if text is None else text, spec, **kw)

    def publish(self, spec=None, raw=None, out='build/credits.md'):
        raw = raw if raw is not None else json.dumps(
            spec or dict(self.spec, table_sources=credits()),
            ensure_ascii=False).encode()
        write_text(self.root, 'spec.json', raw.decode())
        return self.core.paper(self.root, 'spec.json', raw, out)

    def issues(self, text):
        return self.doc.check(text, self.root, major_id='specialty_crops')

    def test_core_inserts_before_publication_hash(self):
        value = self.publish()
        body = Path(value['path']).read_bytes()
        self.assertEqual(body.count('출처: '.encode()), 6)
        managed = self.root / ('build/%s/paper' % self.core.load(self.root)['revision'])
        self.assertEqual((managed / 'paper.md').read_bytes(), body)
        receipts = self.core._find_request(self.root, value['request_id'], operation='paper')
        row = next(f for f in receipts[0]['files'] if f['path'] == 'paper.md')
        self.assertEqual(row['sha256'], hashlib.sha256(body).hexdigest())
        self.assertEqual(row['size'], len(body))
        self.assertFalse(any(k == 'object_credit' for k, _ in self.issues(body.decode())))

    def test_core_forbidden_source_is_not_committed(self):
        bad = dict(self.spec, table_sources={'farm_overview': {'sources': [source(kind='blog')]}})
        write_text(self.root, 'spec.json', json.dumps(bad, ensure_ascii=False))
        before = self._tree_bytes(self.root)
        with self.assertRaises(self.core.OperationError) as caught:
            self.core.paper(self.root, 'spec.json', (self.root / 'spec.json').read_bytes(), 'build/bad.md')
        self.assertEqual(caught.exception.result['reason'], 'invalid_spec')
        self.assertEqual(self._tree_bytes(self.root), before)

    def test_core_duplicate_json_key_is_rejected(self):
        raw = json.dumps(self.spec, ensure_ascii=False)[:-1] + ',"table_sources":{},"table_sources":{}}'
        with self.assertRaises(self.core.OperationError):
            self.publish(raw=raw.encode())

    def test_all_64_subsets_preserve_bytes_and_other_issues(self):
        baseline = [(k, v) for k, v in self.issues(self.original)
                    if k not in {'object_credit', 'sourced_objects'}]
        for bits in itertools.product((False, True), repeat=6):
            selected = [key for key, yes in zip(IDS, bits) if yes]
            with self.subTest(bits=bits):
                value = self.attach(entries=credits(selected))
                errors = self.issues(value)
                expected = {cap for cap, yes in zip(CAPTIONS, bits) if not yes}
                self.assertEqual({v.split('없음: ', 1)[1] for k, v in errors if k == 'object_credit'}, expected)
                self.assertEqual([(k, v) for k, v in errors if k not in {'object_credit', 'sourced_objects'}], baseline)
                # Delete exactly the inserted regions, including blank lines.
                restored = value
                for key in selected:
                    rendered = runtime('gg_school_table_sources').validate_table_sources(
                        {'table_sources': credits([key])})[key]
                    restored = restored.replace('\n' + rendered + '\n\n', '', 1)
                self.assertEqual(restored.encode(), self.original.encode())
                self.assertEqual(self.attach(text=value, entries=credits(selected)), value)

    def test_missing_empty_and_legacy_core_noop(self):
        self.assertIs(self.attach(), self.original)
        self.assertIs(self.attach(entries={}), self.original)
        self.assertEqual(self.attach(text='<!-- DRAFT -->\nanything', entries={}), '<!-- DRAFT -->\nanything')
        for spec in (self.spec, dict(self.spec, table_sources={})):
            root = self.make_project()
            bind_major(root)
            raw = json.dumps(spec).encode()
            value = self.core.paper(root, 'spec.json', raw, 'out.md')
            self.assertEqual(Path(value['path']).read_bytes(), self.original.encode())

    def test_schema_invalid_explicit_values_and_alias_duplicates(self):
        bad_values = [None, [], 3, True, '', {'unknown': {'sources': [source()]}},
                      {'swot': None}, {'swot': {'sources': []}},
                      {'swot': {'sources': source()}}, {'swot': {'sources': [None]}},
                      {'swot': {'sources': [source()], 'extra': True}},
                      {'swot': {'sources': [source(extra='unclassified')]}},
                      {'swot': {'sources': [source()]}, CAPTIONS[-1]: {'sources': [source()]}}]
        mod = runtime('gg_school_table_sources')
        for bad in bad_values:
            with self.subTest(value=bad), self.assertRaises(ValueError):
                mod.attach_table_sources(self.original, dict(self.spec, table_sources=bad))
        for field in ('kind', 'source_type', 'source', 'title', 'year', 'locator'):
            for bad in (None, '', ' ', 2026, [], {}):
                with self.subTest(field=field, value=bad), self.assertRaises(ValueError):
                    self.attach(entries={'swot': {'sources': [source(**{field: bad})]}})

    def test_allowed_enum_and_survey_contract(self):
        pairs = [('stat', 'official_stat'), ('public_data', 'official_public'),
                 ('academic', 'academic_paper'), ('research_report', 'institution_report'),
                 ('school_material', 'official_school'), ('textbook', 'formal_textbook')]
        for kind, st in pairs:
            with self.subTest(kind=kind):
                self.assertIn('출처:', self.attach(entries={'swot': {'sources': [source(kind=kind, source_type=st)]}}))
        survey = source(kind='interview', source_type='author_survey', source='작성자 직접 조사',
                        survey_date='2026-10-08', subject='합성 조사 대상', verified='user-stated')
        self.assertIn('2026-10-08', self.attach(entries={'swot': {'sources': [survey]}}))
        for field in ('survey_date', 'subject', 'verified'):
            bad = dict(survey)
            bad.pop(field)
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.attach(entries={'swot': {'sources': [bad]}})
        for extra in ({'source_type': 'external_interview'}, {'verified': 'verified'},
                      {'source': '타인 인터뷰'}, {'kind': 'blog'}, {'grade': '4'}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.attach(entries={'swot': {'sources': [dict(survey, **extra)]}})

    def test_forbidden_kind_type_and_grades(self):
        for kind in ('news', 'blog', 'interview', 'sns', 'cafe', 'shop', 'press', 'file', 'unknown', 'STAT'):
            for st in ('official_stat', 'author_survey'):
                with self.subTest(kind=kind, st=st), self.assertRaises(ValueError):
                    self.attach(entries={'swot': {'sources': [source(kind=kind, source_type=st)]}})
        for grade in (3, 4, '3', '4', '３', '４', ' 3 '):
            with self.subTest(grade=grade), self.assertRaises(ValueError):
                self.attach(entries={'swot': {'sources': [source(grade=grade)]}})
        for st in ('external_press', 'press', 'unknown', 'author_survey'):
            with self.subTest(st=st), self.assertRaises(ValueError):
                self.attach(entries={'swot': {'sources': [source(source_type=st)]}})

    def test_placeholder_control_and_markdown_injection(self):
        attacks = ['N/A', '확인 필요', '미정', '<자료명>', '[확인 필요]', '자료 없음',
                   'x\n출처: 본인 작성', 'x\rtext', 'x\ttext', 'x\x00text', 'x\u2028text',
                   'x\u200btext', '|자료|값|', '# 제목', '![사진](x)', '[자료](https://example.test)',
                   '`code`', '**강조**', '_강조_', '<script>x</script>', '자료 ［링크］', '자료 ｜ 셀']
        for field in ('source', 'title', 'year', 'locator'):
            for attack in attacks:
                with self.subTest(field=field, attack=attack), self.assertRaises(ValueError):
                    self.attach(entries={'swot': {'sources': [source(**{field: attack})]}})
        for url in ('javascript:alert(1)', 'file:///private/test', 'https://example.test/\ntext',
                    '[x](https://example.test)', 'https://example.test/x)', 'https:///path'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.attach(entries={'swot': {'sources': [source(url=url)]}})
        value = self.attach(entries={'swot': {'sources': [source(url='https://example.test/data?q=1&v=2')]}})
        self.assertIn('https://example.test/data?q=1&v=2', value)

    def test_invisible_marks_and_variation_selectors_rejected(self):
        # PV1-02: combining marks and variation selectors carry no glyph of
        # their own and must not ride through the citation gate.
        invisible = ('\u034f', '\ufe0f', '\ufe00', '\U000e0100', '\u0301',
                     '\u20dd', '\u0489', '\u200b', '\u200c', '\u200d', '\u2060',
                     '\ufeff', '\u00ad', '\u180e', '\U000e0001', '\U0001d173')
        for field in ('source', 'title', 'year', 'locator'):
            for mark in invisible:
                for attack in (mark, '가' + mark, mark + '합성', '가' + mark + '나'):
                    with self.subTest(field=field, attack=repr(attack)), \
                            self.assertRaises(ValueError):
                        self.attach(entries={'swot': {'sources': [source(**{field: attack})]}})
        # Optional fields go through the same contract.
        with self.assertRaises(ValueError):
            self.attach(entries={'swot': {'sources': [source(kind='st\u034fat')]}})
        with self.assertRaises(ValueError):
            self.attach(entries={'swot': {'sources': [source(id='syn\u034fthetic', revision=1)]}})
        with self.assertRaises(ValueError):
            self.attach(entries={'swot': {'sources': [source(url='https://example.test/\u034f')]}})
        survey = source(kind='interview', source_type='author_survey',
                        source='작성자 직접 조사', survey_date='2026-10-08',
                        subject='합성 조사 대상', verified='user-stated')
        for field in ('source', 'survey_date', 'subject', 'verified'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.attach(entries={'swot': {'sources': [dict(survey, **{field: survey[field] + '\u034f'})]}})

    def test_placeholder_judged_on_display_string(self):
        # Marks hidden inside a placeholder are judged by what prints.
        hidden = ('미\u034f정', '미\ufe0f정', '미\U000e0100정', '미\u20dd정',
                  '미\u034f확인', '확인\u034f필요', '자료\ufe0f없음', '없\u0489음',
                  'n\u034f/a', '\u034f-\u034f', '미정\u034f')
        for field in ('source', 'title', 'year', 'locator'):
            for attack in hidden:
                with self.subTest(field=field, attack=repr(attack)), \
                        self.assertRaises(ValueError):
                    self.attach(entries={'swot': {'sources': [source(**{field: attack})]}})

    def test_display_string_requires_letter_or_digit(self):
        for attack in ('.', '·', '§', '()', '——', '···', '?!', '°°', '∼∼', '※※', ' , ;'):
            for field in ('source', 'title', 'year', 'locator'):
                with self.subTest(field=field, attack=repr(attack)), \
                        self.assertRaises(ValueError):
                    self.attach(entries={'swot': {'sources': [source(**{field: attack})]}})

    def test_compat_and_needed_symbol_chars_still_accepted(self):
        good = [source(source='농촌진흥청\u3000(전북)', title='「농업기상」 — ２０２４년'),
                source(title='±0.5°C 범위 ∼ 상세', locator='§3 표 4-1'),
                source(title='café 보고서'),
                source(locator='제３판 p. 12')]
        value = self.attach(entries={'swot': {'sources': good}})
        self.assertIn('출처:', value)

    def test_all_six_invisible_sources_publish_nothing(self):
        # PV1-02 regression: a spec whose six table citations are all
        # invalid must produce no publication file and no receipt.
        bad = dict(self.spec, table_sources={
            key: {'sources': [source(source='\u034f', title='\ufe0f',
                                     year='\U000e0100', locator='\u034f')]}
            for key in IDS})
        write_text(self.root, 'spec.json', json.dumps(bad, ensure_ascii=False))
        before = self._tree_bytes(self.root)
        with self.assertRaises(self.core.OperationError) as caught:
            self.core.paper(self.root, 'spec.json',
                            (self.root / 'spec.json').read_bytes(), 'build/invisible.md')
        self.assertEqual(caught.exception.result['reason'], 'invalid_spec')
        self.assertEqual(caught.exception.result['commit_state'], 'not_committed')
        self.assertFalse((self.root / 'build/invisible.md').exists())
        self.assertEqual(self._tree_bytes(self.root), before)

    def test_caption_alias_compound_source_and_unrelated_text(self):
        entries = {CAPTIONS[-1]: {'sources': [source(title='합성 첫 자료'), source(title='합성 둘째 자료')]}}
        value = self.attach(entries=entries)
        self.assertEqual(value.count('출처:'), 1)
        self.assertIn('; ', value)
        text = self.original.replace('표 1은 농장 종합 개요다.',
                                     '표 1은 농장 종합 개요다. 목차에 표 1. 농장 종합 개요가 있다.')
        self.assertEqual(self.attach(text=text, entries=credits(['farm_overview'])).count('출처:'), 1)
        # A caption-looking cell cannot introduce a second table candidate.
        text = self.original.replace('|농장명|', '|표 1. 농장 종합 개요|')
        self.assertEqual(self.attach(text=text, entries=credits(['farm_overview'])).count('출처:'), 1)

    def test_missing_duplicate_title_context_and_draft_spans(self):
        texts = [self.original.replace(CAPTIONS[0], '표 1. 다른 제목'),
                 self.original.replace('가. 농장 개요', '가. 다른 절'),
                 self.original.replace('Ⅱ. 외부환경분석', 'Ⅳ. 외부환경분석'),
                 self.original.replace('|항목|내용|', '|다른열|내용|', 1),
                 self.original.replace(CAPTIONS[0], CAPTIONS[0] + '\n설명 문장'),
                 self.original.replace(CAPTIONS[0], CAPTIONS[0] + '\n|항목|내용|\n|---|---|\n|a|b|\n' + CAPTIONS[0]),
                 self.core.START + '\n' + self.original + '\n' + self.core.END,
                 '## DRAFT\n' + self.original + '\n## STATUS\n메타데이터']
        for text in texts:
            with self.subTest(text=text[:120]), self.assertRaises(ValueError):
                self.attach(text=text, entries=credits(['farm_overview']))

    def test_all_validation_precedes_transformation(self):
        mod = runtime('gg_school_table_sources')
        entries = credits()
        entries['swot']['sources'][0]['kind'] = 'blog'
        before = copy.deepcopy(entries)
        with self.assertRaises(ValueError):
            self.attach(entries=entries)
        self.assertEqual(entries, before)
        self.assertEqual(self.sp.paper(self.spec, context=self.context), self.original)
        with mock.patch.object(self.doc, 'parse', wraps=self.doc.parse) as parse:
            with self.assertRaises(ValueError):
                mod.attach_table_sources(self.original, dict(self.spec, table_sources=entries))
            parse.assert_not_called()

    def test_exact_line_offsets_crlf_and_no_final_newline(self):
        for text in (self.original.replace('\n', '\r\n'), '\n\n' + self.original + '\n',
                     self.original.rstrip('\n'), self.original.replace('|농장명|', '|출처: 셀 자료|')):
            with self.subTest(text=text[:50]):
                value = self.attach(text=text, entries=credits())
                ending = '\r\n' if '\r\n' in text else '\n'
                restored = value
                for line in runtime('gg_school_table_sources').validate_table_sources(
                        {'table_sources': credits()}).values():
                    restored = restored.replace(ending + line + ending + ending, '', 1)
                self.assertEqual(restored.encode(), text.encode())
                self.assertEqual(self.attach(text=value, entries=credits()), value)

    def test_valid_adjacent_tables_and_final_table_without_line_ending(self):
        nodes = self.doc.parse(self.original, with_spans=True)
        lines = self.original.splitlines(keepends=True)
        c1 = next(i for i, n in enumerate(nodes) if n.get('text') == CAPTIONS[0])
        c2 = next(i for i, n in enumerate(nodes) if n.get('text') == CAPTIONS[1])
        start, end = nodes[c1+1]['end_line'], nodes[c2]['start_line']
        # Preserve the second table's declared heading context in the gap.
        text = ''.join(lines[:start]) + '나. 지역적 조건\n1) 기상환경\n' + ''.join(lines[end:])
        value = self.attach(text=text, entries=credits(['farm_overview', 'climate']))
        self.assertEqual(sum(k == 'object_credit' for k, _ in self.issues(value)), 4)
        # An EOF table gets a new paragraph without dropping any original byte.
        swot = next(i for i, n in enumerate(nodes) if n.get('text') == CAPTIONS[-1])
        text = ''.join(lines[:nodes[swot+1]['end_line']]).rstrip('\n')
        value = self.attach(text=text, entries=credits(['swot']))
        rendered = runtime('gg_school_table_sources').validate_table_sources(
            {'table_sources': credits(['swot'])})['swot']
        self.assertEqual(value.replace('\n\n' + rendered + '\n\n', '', 1), text)
        self.assertEqual(self.attach(text=value, entries=credits(['swot'])), value)

    def test_duplicate_json_keys_at_every_nested_level(self):
        mod = runtime('gg_school_table_sources')
        for raw in ('{"table_sources":{},"table_sources":{}}',
                    '{"table_sources":{"swot":{},"swot":{}}}',
                    '{"table_sources":{"swot":{"sources":[],"sources":[]}}}',
                    '{"table_sources":{"swot":{"sources":[{"kind":"blog","kind":"stat"}]}}}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                mod.load_spec(raw.encode())

    def test_existing_credit_conflict_and_shared_credit(self):
        credited = self.attach(entries=credits(['farm_overview']))
        self.assertEqual(self.attach(text=credited, entries=credits(['farm_overview'])), credited)
        entries = credits(['farm_overview'])
        entries['farm_overview']['sources'][0]['title'] = '합성 다른 자료'
        with self.assertRaises(ValueError):
            self.attach(text=credited, entries=entries)
        text = self.original.replace(CAPTIONS[0], '출처: 다른 출처\n\n' + CAPTIONS[0])
        with self.assertRaises(ValueError):
            self.attach(text=text, entries=credits(['farm_overview']))
        # Two adjacent caption+table objects share one old credit: refuse.
        nodes = self.doc.parse(self.original, with_spans=True)
        lines = self.original.splitlines(keepends=True)
        c1 = next(i for i, n in enumerate(nodes) if n.get('text') == CAPTIONS[0])
        c2 = next(i for i, n in enumerate(nodes) if n.get('text') == CAPTIONS[1])
        start, end = nodes[c1+1]['end_line'], nodes[c2]['start_line']
        text = ''.join(lines[:start]) + '\n출처: 공유 합성 자료\n\n' + ''.join(lines[end:])
        with self.assertRaises(ValueError):
            self.attach(text=text, entries=credits(['farm_overview', 'climate']))

    def test_registry_ref_conflicts_and_revision(self):
        record = dict(source(), id='synthetic-source', revision=2)
        project = {'sources': {'synthetic-source': record}}
        entries = {'swot': {'sources': [record]}}
        self.assertIn('출처:', self.attach(entries=entries, project=project))
        for changes in ({'revision': 1}, {'kind': 'academic', 'source_type': 'academic_paper'},
                        {'title': '다른 서지'}, {'source': '다른 기관'}):
            bad = {'swot': {'sources': [dict(record, **changes)]}}
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.attach(entries=bad, project=project)
        with self.assertRaises(ValueError):
            self.attach(entries=entries, project={'sources': {}})
        with self.assertRaises(ValueError):
            self.attach(entries=entries)

    def test_registry_optional_metadata_omission_is_not_a_conflict(self):
        record = dict(source(), id='synthetic-source', revision=2, grade=1,
                      url='https://example.test/data')
        provided = {k: v for k, v in record.items() if k not in {'grade', 'url'}}
        entries = {'swot': {'sources': [provided]}}
        self.assertIn('출처:', self.attach(entries=entries, project={'sources': {'synthetic-source': record}}))
        for grade in (3, 4, '3', '4'):
            with self.subTest(grade=grade), self.assertRaises(ValueError):
                self.attach(entries=entries, project={'sources': {'synthetic-source': dict(record, grade=grade)}})

    def test_recovery_never_rerenders_and_new_spec_conflicts(self):
        value = self.publish()
        original = Path(value['path']).read_bytes()
        Path(value['path']).unlink()
        with mock.patch.object(self.sp, 'paper', side_effect=AssertionError('re-render')), \
                mock.patch.object(runtime('gg_school_table_sources'), 'attach_table_sources',
                                  side_effect=AssertionError('re-attach')):
            retry = self.publish()
        self.assertEqual(Path(retry['path']).read_bytes(), original)
        changed = dict(self.spec, table_sources=credits(['farm_overview']))
        with self.assertRaises(self.core.OperationError):
            self.publish(spec=changed)
        self.assertEqual(Path(value['path']).read_bytes(), original)

    def test_cli_accepts_and_rejects_duplicate_nested_keys(self):
        raw = json.dumps(dict(self.spec, table_sources=credits()), ensure_ascii=False)
        write_text(self.root, 'cli.json', raw)
        args = [sys.executable, '-B', str(SCRIPTS / 'gg.py'), 'paper', str(self.root),
                '--input', 'cli.json', '--out', 'build/cli.md']
        result = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.root / 'build/cli.md').read_text(encoding='utf-8').count('출처:'), 6)
        raw = raw.replace('"kind": "stat"', '"kind": "blog", "kind": "stat"', 1)
        write_text(self.root, 'cli.json', raw)
        before = self._tree_bytes(self.root)
        result = subprocess.run(args, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)['reason'], 'invalid_spec')
        self.assertEqual(self._tree_bytes(self.root), before)

    def test_frozen_sha_and_direct_generator_unchanged(self):
        self.assertEqual(hashlib.sha256((SCRIPTS / 'gg_school_paper.py').read_bytes()).hexdigest(),
                         'b70d36698e07fe3f9cb5a9869c866da49fbd7692fbb675f282013aeec76f81e1')
        self.assertEqual(self.sp.paper(dict(self.spec, table_sources=credits()), context=self.context), self.original)
        self.assertEqual(sum(k == 'object_credit' for k, _ in self.issues(self.original)), 6)

    def test_docx_conversion_preserves_tables_and_credit_placement(self):
        convert = runtime('build_docx').convert
        context = runtime('gg_major_contract').output_context(self.root, 'specialty_crops')
        before = convert(self.original, '신명조', self.root, context=context)
        after = convert(self.attach(entries=credits()), '신명조', self.root, context=context)
        self.assertEqual(len(before.tables), 9)  # three form tables and six body tables
        self.assertEqual([t._tbl.xml for t in before.tables], [t._tbl.xml for t in after.tables])
        for table in after.tables[-6:]:
            following = table._tbl.getnext()
            self.assertTrue(following.tag.endswith('}p'))
            self.assertTrue(''.join(following.xpath('.//w:t/text()')).startswith('출처:'))
