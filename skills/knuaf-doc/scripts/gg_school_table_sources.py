"""D3 §3–4 adapter for core.paper's frozen generator return value only.

No default credits, file writes or replacement of existing citations. Input
table_sources maps the six IDs (or exact captions) to {sources: [objects]}.
Each source requires kind/source_type/source/title/year/locator, with an
optional plain HTTP(S) url and grade 1/2. These are the D3 proposed adapter
enums, not a claim about the global registry: stat/official_stat,
public_data/official_public, academic/academic_paper,
research_report/institution_report, school_material/official_school,
textbook/formal_textbook, interview/author_survey. The survey exception
requires source="작성자 직접 조사", survey_date, subject and
verified="user-stated"; it does not certify truth or finance eligibility.
Optional id/revision connect an existing project.sources record; the
required locator is the per-table location in that source. No new registry
or task/receipt is created by this pure transformation.
"""
import json
import re
import unicodedata
from urllib.parse import urlsplit


SOURCE_TYPES = {
    'stat': 'official_stat', 'public_data': 'official_public',
    'academic': 'academic_paper', 'research_report': 'institution_report',
    'school_material': 'official_school', 'textbook': 'formal_textbook',
    'interview': 'author_survey',
}
# Exact generator captions and heading paths; only whitespace is compacted.
TABLES = {
    'farm_overview': ('표 1. 농장 종합 개요',
                      ('Ⅱ. 외부환경분석', '1. 농업환경분석', '가. 농장 개요')),
    'climate': ('표 2. 기상환경',
                ('Ⅱ. 외부환경분석', '1. 농업환경분석', '나. 지역적 조건', '1) 기상환경')),
    'disasters': ('표 3. 재해',
                  ('Ⅱ. 외부환경분석', '1. 농업환경분석', '나. 지역적 조건', '6) 재해')),
    'shipping_markets': ('표 4. 출하시장 거리',
                         ('Ⅱ. 외부환경분석', '1. 농업환경분석',
                          '다. 지리, 사회, 경제적 조건', '1) 지리적 조건')),
    'growth_targets': ('표 5. 가족구성 및 성장목표',
                       ('Ⅲ. 영농계획수립', '2. 영농목표 및 전략', '가. 농장의 가족구성 및 성장목표')),
    'swot': ('표 6. SWOT 분석',
             ('Ⅲ. 영농계획수립', '2. 영농목표 및 전략', '마. SWOT 분석')),
}
_REQUIRED = {'kind', 'source_type', 'source', 'title', 'year', 'locator'}
_OPTIONAL = {'url', 'grade', 'survey_date', 'subject', 'verified', 'id', 'revision'}
_MISSING = {'n/a', 'na', 'none', 'null', 'unknown', '-', '—', '미정', '미확인',
            '확인필요', '자료없음', '없음', '해당없음', '미제공'}


def load_spec(spec_bytes):
    """Read once, refusing duplicate keys before json.loads can erase them."""
    def object_from_pairs(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('중복 JSON 키: ' + key)
            value[key] = item
        return value
    return json.loads(spec_bytes, object_pairs_hook=object_from_pairs)


def _compact(value):
    return re.sub(r'\s+', '', value)


def _plain(value, field, *, url=False):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('출처 필드는 비어 있지 않은 문자열이어야 함: ' + field)
    normalized = unicodedata.normalize('NFKC', value)
    if any(unicodedata.category(c).startswith('C') or c in '\r\n\t\u2028\u2029'
           for c in value + normalized):
        raise ValueError('출처 제어문자/줄바꿈 금지: ' + field)
    forbidden = '|#[]<>\\`*{}~' + ('()"' if url else '')
    if any(c in normalized for c in forbidden) or re.search(r'(?<!\w)_[^_]+_', normalized):
        raise ValueError('출처 Markdown/자리표시자 금지: ' + field)
    folded = _compact(normalized).casefold()
    if folded in _MISSING or any(x in folded for x in ('확인필요', '미정', '미확인', '자료없음')):
        raise ValueError('미확인 출처 값: ' + field)
    if not url and re.search(r'(?:https?://|!\s*\[)', normalized, re.I):
        raise ValueError('URL은 별도 평문 url 필드에만 허용: ' + field)
    if url:
        try:
            parsed = urlsplit(value)
            valid = (parsed.scheme in {'http', 'https'} and parsed.hostname
                     and not parsed.username and not parsed.password
                     and not any(c.isspace() for c in value))
        except ValueError:
            valid = False
        if not valid:
            raise ValueError('출처 URL은 평문 HTTP(S) 주소여야 함')
    return value.strip()


def _check_grade(grade):
    if isinstance(grade, str):
        grade = unicodedata.normalize('NFKC', _plain(grade, 'grade')).strip()
    if isinstance(grade, bool) or str(grade) not in {'1', '2'}:
        raise ValueError('출처 등급은 1/2만 허용; 3/4등급 금지')


def _source(value, project):
    if not isinstance(value, dict) or not _REQUIRED <= value.keys() \
            or value.keys() - (_REQUIRED | _OPTIONAL):
        raise ValueError('출처 객체 필드 누락/미지 필드')
    fields = {k: _plain(v, k, url=(k == 'url')) for k, v in value.items()
              if k not in {'grade', 'revision'}}
    kind = fields['kind']
    if kind not in SOURCE_TYPES or fields['source_type'] != SOURCE_TYPES[kind]:
        raise ValueError('허용되지 않은 출처 kind/source_type')
    if 'grade' in value:
        _check_grade(value['grade'])
    if kind == 'interview':
        if fields['source'] != '작성자 직접 조사' or fields.get('verified') != 'user-stated' \
                or not {'survey_date', 'subject'} <= fields.keys():
            raise ValueError('작성자 직접 조사 계약 필드 누락/충돌')
    elif fields['source'] == '작성자 직접 조사' or {'survey_date', 'subject'} & fields.keys():
        raise ValueError('직접 조사 표시는 interview/author_survey에서만 허용')
    if 'id' in value or 'revision' in value:
        revision = value.get('revision')
        if ('id' not in fields or isinstance(revision, bool)
                or not isinstance(revision, int) or revision < 1):
            raise ValueError('출처 id/revision을 함께 명시해야 함')
        record = (project or {}).get('sources', {}).get(fields['id'])
        if not isinstance(record, dict) or record.get('revision') != revision:
            raise ValueError('정본 출처 없음/개정 충돌')
        if 'grade' in record:
            _check_grade(record['grade'])
        for key in _REQUIRED | {'grade', 'verified', 'survey_date', 'subject', 'url'}:
            if key != 'locator' and key in record and key in value and record[key] != value[key]:
                raise ValueError('정본 출처 종류/서지 충돌: ' + key)
    parts = [fields['source'], fields['title'], fields['year'], fields['locator']]
    if kind == 'interview':
        parts.extend([fields['survey_date'], fields['subject'], fields['verified']])
    if 'id' in fields:
        parts.append('%s revision %s' % (fields['id'], value['revision']))
    if 'url' in fields:
        parts.append(fields['url'])
    return ', '.join(parts)


def validate_table_sources(spec, *, project=None):
    """Validate every entry before parsing/editing. Missing and {} are no-op."""
    if 'table_sources' not in spec:
        return {}
    entries = spec['table_sources']
    if not isinstance(entries, dict):
        raise ValueError('table_sources는 JSON 객체여야 함')
    aliases = {caption: key for key, (caption, _) in TABLES.items()}
    result = {}
    for key, entry in entries.items():
        target = key if key in TABLES else aliases.get(key)
        if target is None or target in result:
            raise ValueError('미지/중복 표 출처 키: ' + str(key))
        if not isinstance(entry, dict) or set(entry) != {'sources'} \
                or not isinstance(entry['sources'], list) or not entry['sources']:
            raise ValueError('표 출처에는 비어 있지 않은 sources 배열만 허용')
        result[target] = '출처: ' + '; '.join(_source(s, project) for s in entry['sources'])
    return result


def _targets(nodes, keys):
    headings, candidates = {}, {key: [] for key in keys}
    for index, node in enumerate(nodes):
        if node['kind'] == 'heading':
            level = node['level']
            headings = {k: v for k, v in headings.items() if k < level}
            headings[level] = _compact(node['text'])
        if node['kind'] != 'caption' or node['label'] != '표':
            continue
        for key in keys:
            caption, context = TABLES[key]
            if _compact(node['text']) == _compact(caption):
                candidates[key].append(index)
                if tuple(headings[k] for k in sorted(headings)) != tuple(map(_compact, context)):
                    raise ValueError('표 제목/문맥 불일치: ' + key)
    result = {}
    for key, matches in candidates.items():
        if len(matches) != 1:
            raise ValueError('표는 정확히 1개 매칭해야 함: ' + key)
        index = matches[0] + 1
        if index >= len(nodes) or nodes[index]['kind'] != 'table':
            raise ValueError('캡션 바로 뒤 표 없음: ' + key)
        rows = nodes[index]['rows']
        header = rows[0] if rows else []
        expected = {'farm_overview': ['항목', '내용'],
                    'disasters': ['구분', '가장 자주 발생', '자주 발생', '가끔 발생'],
                    'shipping_markets': ['주요출하시장', '거리', '소요시간', '비고'],
                    'swot': ['구분', '내용']}.get(key)
        if expected is not None and list(map(_compact, header)) != list(map(_compact, expected)):
            raise ValueError('표 열 계약 불일치: ' + key)
        if key == 'growth_targets' and (len(header) != 3 or header[0] != '구분'):
            raise ValueError('성장목표 열 계약 불일치')
        if key == 'climate' and not any('12월' in cell for row in rows[:3] for cell in row):
            raise ValueError('기상환경 월/기간 열 계약 불일치')
        result[key] = index
    return result


def _credits(nodes, policy):
    """Match the current policy's paragraph-window/nearest-object ownership.

    parse is the public parser. Never interpret citations inside table cells.
    Equal distances follow the policy's preceding-object tie break, while
    preserving competing candidates for ambiguity checks on old citations.
    """
    groups, i = [], 0
    while i < len(nodes):
        node = nodes[i]
        if node['kind'] in {'caption', 'table', 'image'}:
            end = i
            if i + 1 < len(nodes) and (
                (node['kind'] == 'caption' and node['label'] == '표' and nodes[i+1]['kind'] == 'table')
                or (node['kind'] == 'image' and nodes[i+1]['kind'] == 'caption' and nodes[i+1]['label'] == '그림')
            ):
                end += 1
            groups.append((i, end))
            i = end + 1
        else:
            i += 1
    labels = '|'.join(re.escape(v) for v in policy['objects']['credit_labels'])
    pattern = re.compile(r'^(?:[\\*＊·•]\s*)*(?:' + labels + r')\s*[:：]\s*(.*?)\s*$')
    candidates = {}
    for group, (start, end) in enumerate(groups):
        for origin, step in ((start - 1, -1), (end + 1, 1)):
            pos = origin
            for distance in range(1, policy['objects']['scan_lines'] + 1):
                if not 0 <= pos < len(nodes) or nodes[pos]['kind'] != 'paragraph':
                    break
                if pattern.fullmatch(nodes[pos]['text'].strip().strip('*')):
                    candidates.setdefault(pos, []).append((distance, group))
                pos += step
    return groups, candidates


def attach_table_sources(text, spec, *, project=None):
    rendered = validate_table_sources(spec, project=project)
    if not rendered:
        return text
    import gg_document as document
    # draft strips surrounding whitespace. Accept only that loss, with an
    # exact line-offset mapping; reject any marker/body extraction instead.
    body = document.draft(text)
    if body != text.strip():
        raise ValueError('표 출처 입력은 generator 본문만 허용; DRAFT 추출 금지')
    line_offset = text[:text.index(body)].count('\n')
    nodes = document.parse(text, with_spans=True)
    targets = _targets(nodes, rendered)
    policy = document.policy_for_major(document.load_plain_policy(), 'specialty_crops')
    groups, candidates = _credits(nodes, policy)
    owners = {pos: min(options)[1] for pos, options in candidates.items()}
    edits = []
    lines = text.splitlines(keepends=True)
    for key, table_index in targets.items():
        group = next(i for i, (_, end) in enumerate(groups) if end == table_index)
        own = [pos for pos, owner in owners.items() if owner == group]
        if own:
            if (len(own) != 1 or own[0] != table_index + 1
                    or nodes[own[0]]['text'] != rendered[key]):
                raise ValueError('기존 출처 충돌/소유 불명확: ' + key)
            continue
        if any(any(owner == group for _, owner in options) for options in candidates.values()):
            raise ValueError('주변 기존 출처의 소유 불명확: ' + key)
        end_line = nodes[table_index]['end_line'] + line_offset
        offset = sum(map(len, lines[:end_line]))
        ending = '\r\n' if lines[end_line - 1].endswith('\r\n') else '\n'
        prefix = '' if text[:offset].endswith('\n') else ending
        edits.append((offset, prefix + ending + rendered[key] + ending + ending))
    result = text
    for offset, insertion in sorted(edits, reverse=True):
        result = result[:offset] + insertion + result[offset:]
    # Reparse actual transformed bytes and verify each credit's real owner.
    after = document.parse(result, with_spans=True)
    after_targets = _targets(after, rendered)
    after_groups, after_candidates = _credits(after, policy)
    for key, index in after_targets.items():
        pos = index + 1
        group = next(i for i, (_, end) in enumerate(after_groups) if end == index)
        if (pos >= len(after) or after[pos]['kind'] != 'paragraph'
                or after[pos]['text'] != rendered[key]
                or min(after_candidates.get(pos, [(float('inf'), -1)]))[1] != group):
            raise ValueError('변환 후 출처 귀속 오류: ' + key)
    return result
