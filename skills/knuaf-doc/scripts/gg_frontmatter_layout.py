"""Measured school form bands. Keeps long titles inside fixed page regions."""
import re
from docx.shared import Pt, Mm, RGBColor
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ROW_HEIGHT_RULE, WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from gg_major_format import (ENABLED_FORMAT_MAJORS, format_enabled, profile_for,
                            front_title)


def _font(run, font, size, bold=False):
    run.font.name, run.font.size, run.bold = font, Pt(size), bold
    run.font.color.rgb = RGBColor(0, 0, 0)
    rf = run._element.get_or_add_rPr().get_or_add_rFonts()
    for key in ('ascii', 'hAnsi', 'eastAsia', 'cs'): rf.set(qn('w:'+key), font)


def _p(cell, text, font, size=18, bold=False, align=WD_ALIGN_PARAGRAPH.CENTER, first=True):
    p = cell.paragraphs[0] if first else cell.add_paragraph()
    f = p.paragraph_format
    f.space_before = f.space_after = Pt(0)
    f.line_spacing = Pt(size * 1.35)
    f.keep_with_next = False
    f.widow_control = False
    p.alignment = align
    _font(p.add_run(text), font, size, bold)
    return p


def _cell_props(cell):
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
    pr = cell._tc.get_or_add_tcPr()
    margins = OxmlElement('w:tcMar')
    for side in ('top','bottom','left','right'):
        e=OxmlElement('w:'+side);e.set(qn('w:w'),'0');e.set(qn('w:type'),'dxa');margins.append(e)
    pr.append(margins)
    borders=OxmlElement('w:tcBorders')
    for side in ('top','bottom','left','right','insideH','insideV'):
        e=OxmlElement('w:'+side);e.set(qn('w:val'),'nil');borders.append(e)
    pr.append(borders)


def _bands(doc, specs, font):
    # Each start coordinate is millimetres from the physical A4 page top.
    # Section top margin is 20 mm; exact row heights prevent cumulative drift.
    table=doc.add_table(rows=len(specs), cols=1)
    table.autofit=False;table.alignment=WD_TABLE_ALIGNMENT.CENTER
    table.columns[0].width=Mm(150)
    for i,(top,bottom,text,size,bold) in enumerate(specs):
        row=table.rows[i];row.height=Mm(bottom-top);row.height_rule=WD_ROW_HEIGHT_RULE.EXACTLY
        pr=row._tr.get_or_add_trPr();pr.append(OxmlElement('w:cantSplit'))
        cell=row.cells[0];cell.width=Mm(150);_cell_props(cell)
        _p(cell,text,font,size,bold)
    return table


def _break(doc):
    p=doc.add_paragraph();p.paragraph_format.space_before=Pt(0);p.paragraph_format.space_after=Pt(0);p.paragraph_format.line_spacing=Pt(1)
    r=p.add_run();r.font.size=Pt(1)
    br=OxmlElement('w:br');br.set(qn('w:type'),'page');r._r.append(br)


def _texts(nodes,start,end):
    return [n['text'].strip() for n in nodes[start:end] if n.get('text','').strip()]


def render_forms(doc,nodes,plan,font):
    if plan.get('spec') and plan['spec'].major_id != 'specialty_crops':
        return _render_peer_forms(doc, nodes, plan, font)
    ix=plan['indices']
    cover=_texts(nodes,ix['겉표지']+1,ix['표제면'])
    submission=_texts(nodes,ix['표제면']+1,ix['제출서'])
    approval=_texts(nodes,ix['인준서']+1,ix['목차'])
    if len(cover)<7 or len(submission)<9 or len(approval)<6:
        raise ValueError('학교 양식 필수 메타데이터 순서/항목 누락')
    degree,plan_label=cover[:2]
    graduation,school,department,author=cover[-4:]
    title_parts=cover[2:-4]
    if not 1<=len(title_parts)<=2: raise ValueError('학교 제목과 부제는 각 한 문단으로 입력')
    title=title_parts[0];subtitle=title_parts[1] if len(title_parts)>1 else ''
    import math, unicodedata
    def units(text): return sum(1 if unicodedata.east_asian_width(ch) in {'W','F'} else .55 for ch in text)
    required_height=math.ceil(units(title)/22)*8.573+(math.ceil(units(subtitle)/29)*6.668 if subtitle else 0)
    if required_height>55: raise ValueError('표지 제목/부제가 허용 영역을 초과함: 제목을 줄이거나 별도 양식 프로필 필요')
    for value,limit in ((degree,23),(plan_label,10),(graduation,20),(school,20),(department,22),(author,22)):
        if units(value)>limit: raise ValueError('학교 양식의 한 줄 필드가 너무 김: 이름·학교·학과·날짜 또는 계획명 확인')
    advisor=submission[0]
    if units(advisor)>23: raise ValueError('지도교수 표시가 학교 양식의 한 줄 영역을 초과함')
    submission_date=submission[-4]
    statement=next((s for s in submission if s.startswith('이 논문을')), '이 논문을 농업전문학사 학위논문으로 제출함')
    # 양식 슬롯에 배정되지 않은 저작 행(검토 메모·제출서 문구 등)은 각 양식의
    # 빈 상단 밴드에 그대로 표시해 내용을 조용히 버리지 않는다.
    consumed={advisor,submission_date,statement,degree,plan_label,title,subtitle,graduation,school,department,author}
    extras_sub=[x for x in submission if x not in consumed]
    extras_sub+=_texts(nodes,ix['제출서']+1,ix['인준서'])
    extras_sub=' '.join(extras_sub).strip()
    if extras_sub and units(extras_sub)>85: raise ValueError('표제면/제출서 추가 문구가 양식 상단 영역을 초과함')
    # 인준서 블록은 내용으로 판별한다: (인)으로 끝나는 행은 서명란,
    # '인준함'이 있는 행은 인준 문구, 날짜 행은 연도 패턴, 나머지는 검토 메모.
    signatures=[x for x in approval if x.rstrip().endswith('(인)')]
    appr_core=[x for x in approval if not x.rstrip().endswith('(인)')]
    stmt_i=next((i for i,x in enumerate(appr_core) if '인준함' in x), None)
    if stmt_i is None:
        if len(appr_core)<2: raise ValueError('인준 문구 누락')
        stmt_i=len(appr_core)-2
    appr_statement=appr_core[stmt_i]
    appr_rest=appr_core[:stmt_i]+appr_core[stmt_i+1:]
    date_i=next((i for i,x in enumerate(appr_rest) if re.search(r'\d{4}\s*년',x)), None)
    appr_date=appr_rest.pop(date_i) if date_i is not None else (appr_rest.pop() if appr_rest else '')
    if len(signatures)>4: raise ValueError('인준 서명란은 최대 4명')
    appr_extras=' '.join(appr_rest).strip()
    if appr_extras and units(appr_extras)>140: raise ValueError('인준서 추가 문구가 양식 상단 영역을 초과함')
    table=_bands(doc,[
        (20,52,'',1,False),(52,88,degree,18,False),(88,116,' '.join(plan_label) if len(plan_label)<=9 else plan_label,30,False),
        (116,176,title,18,False),(176,218,graduation,20,False),(218,233,school,20,False),
        (233,248,department,18,False),(248,265,author,18,False)],font)
    if subtitle: _p(table.cell(3,0),subtitle,font,14,first=False)
    _break(doc)
    table=_bands(doc,[
        (20,40,extras_sub,12,False),(40,51,advisor,18,False),(51,88,degree,18,False),
        (88,116,' '.join(plan_label) if len(plan_label)<=9 else plan_label,30,False),(116,175,title,18,False),
        (175,188,statement,16,False),(188,224,submission_date,20,False),(224,238,school,20,False),
        (238,252,department,18,False),(252,268,author,18,False)],font)
    if subtitle: _p(table.cell(4,0),subtitle,font,14,first=False)
    _break(doc)
    table=_bands(doc,[(20,52,appr_extras,12,False),(52,95,appr_statement,18,False),(95,159,appr_date,20,False),
                      (159,185,'',1,False),(185,211,'',1,False),(211,237,'',1,False),(237,266,'',1,False)],font)
    for i,line in enumerate(signatures):
        cell=table.cell(i+3,0);p=cell.paragraphs[0]
        p.paragraph_format.line_spacing=Pt(1);p.runs[0].font.size=Pt(1)
        inner=cell.add_table(rows=1,cols=3);inner.autofit=False;inner.alignment=WD_TABLE_ALIGNMENT.RIGHT
        widths=[24,65,19]
        label='위원장' if i==0 else '위  원'
        name=line.removeprefix('위원장').removeprefix('위  원').removesuffix('(인)').strip()
        for j,(w,text) in enumerate(zip(widths,[label,name,'(인)'])):
            inner.columns[j].width=Mm(w);c=inner.cell(0,j);c.width=Mm(w);_cell_props(c)
            _p(c,text,font,18,align=WD_ALIGN_PARAGRAPH.LEFT if j==0 else WD_ALIGN_PARAGRAPH.CENTER)
            borders=c._tc.get_or_add_tcPr().find(qn('w:tcBorders'));e=borders.find(qn('w:bottom'));e.set(qn('w:val'),'single');e.set(qn('w:sz'),'4');e.set(qn('w:color'),'000000')
        inner.rows[0].height=Mm(9);inner.rows[0].height_rule=WD_ROW_HEIGHT_RULE.EXACTLY
        tail=cell.paragraphs[-1];tail.paragraph_format.line_spacing=Pt(1);tail.paragraph_format.space_after=Pt(0)
    _break(doc)


def page_number_field(paragraph, profile=None, font='신명조'):
    """바닥글 쪽번호 문단: 가운데 정렬 + `PAGE` 필드.

    프로필 `page_label`이 없으면 지금까지와 똑같이 `PAGE` 필드 하나만 넣는다
    (식량작물과 프로필 밖 전공의 바이트 고정). 있으면 `prefix` 글자 →
    `PAGE` → `suffix` 글자를 넣고 셋 다 같은 크기(`pt`)로 맞춘다. 낙농이
    쓰는 `- 1 -` 꼴이다. 필드 안 run은 빈 껍데기이되 `rPr`을 들고 있어
    Word가 필드를 갱신할 때 숫자도 그 크기로 그린다.
    """
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    label = getattr(profile, 'page_label', None)
    if not label:
        field = OxmlElement('w:fldSimple')
        field.set(qn('w:instr'), 'PAGE')
        paragraph._p.append(field)
        return paragraph
    size = label.get('pt', 9)
    prefix = label.get('prefix') or ''
    if prefix:
        _font(paragraph.add_run(prefix), font, size)
    field = OxmlElement('w:fldSimple')
    field.set(qn('w:instr'), 'PAGE')
    holder = paragraph.add_run('')
    _font(holder, font, size)
    field.append(holder._r)
    paragraph._p.append(field)
    suffix = label.get('suffix') or ''
    if suffix:
        _font(paragraph.add_run(suffix), font, size)
    return paragraph


def _roman_front_section(doc, margin_mm=None, profile=None, font='신명조'):
    """Open the profile front-matter section: upper-Roman page numbers.

    The school form numbers 요약 through 목차/표 목차/그림 목차 with upper-Roman
    page numbers in the 아래 가운데 footer, then restarts the body at Arabic 1.
    바닥글 꾸밈(`- Ⅰ -`)은 프로필 `page_label`이 정한다 — 없으면 `PAGE`
    필드 하나만(지금까지 바이트 그대로).
    """
    from docx.enum.section import WD_SECTION
    sec = doc.add_section(WD_SECTION.NEW_PAGE)
    if margin_mm is not None:
        sec.top_margin, sec.bottom_margin = Mm(margin_mm['top']), Mm(margin_mm['bottom'])
        sec.left_margin, sec.right_margin = Mm(margin_mm['left']), Mm(margin_mm['right'])
        sec.header_distance = Mm(margin_mm['header'])
        sec.footer_distance = Mm(margin_mm['footer'])
        sec.gutter = Mm(margin_mm['gutter'])
    inherited = sec._sectPr.find(qn('w:pgNumType'))
    if inherited is not None:
        sec._sectPr.remove(inherited)
    pg = OxmlElement('w:pgNumType')
    pg.set(qn('w:start'), '1')
    pg.set(qn('w:fmt'), 'upperRoman')
    sec._sectPr.append(pg)
    sec.footer.is_linked_to_previous = False
    page_number_field(sec.footer.paragraphs[0], profile, font)
    return sec


def _black_runs(paragraph, font, size):
    """식량작물 앞부분 제목의 글자색만 검정으로 고정한다.

    문서 스타일(Heading 1)은 다른 전공·본문이 함께 쓰므로 건드리지 않고,
    이 문단에 실제로 만들어진 run의 색만 바꾼다. run이 없으면 하나 만든다.
    """
    if not paragraph.runs:
        paragraph.add_run(paragraph.text or '')
    for run in paragraph.runs:
        run.font.name = font
        run.font.size = Pt(size)
        run.font.color.rgb = RGBColor(0, 0, 0)


def _cover_text_key(text, spec):
    """앞부분 한 줄이 `cover_text`의 어느 종류인가.

    판정 순서는 식량작물 시절 함수의 `if` 순서 그대로다. 줄 글자로만
    가른다: 학위논문 이름(degree), 계획명(plan), 제출 문구(statement),
    인준 문구(approval), 서명 줄(advisor), 그 밖(default).
    """
    if text == spec.degree:
        return 'degree'
    if spec.plan_label and text == spec.plan_label:
        return 'plan'
    if text.startswith('이 논문을'):
        return 'statement'
    if text.endswith('인준함'):
        return 'approval'
    if (text.startswith('위원장') or text.startswith('위  원')
            or text.startswith('지도교수 ')):
        return 'advisor'
    return 'default'


def _profile_pt(profile, name, legacy):
    """프로필의 (pt, 굵기) 한 칸. 프로필이 없으면 식량작물 현행값."""
    if profile is None:
        return legacy
    size, bold = getattr(profile, name, legacy)
    return size, bool(bold)


def _peer_form_text_size(text, spec, profile=None):
    """양식 실측 크기(§2.3). 굵기와 함께 (size, bold)를 돌려준다.

    프로필이 있으면 `cover_text`가 종류별 값을 정한다. 없으면 식량작물
    실측값 그대로다(바이트 불변 기준선).
    """
    key = _cover_text_key(text, spec)
    if profile is not None:
        size, bold = profile.cover_text.get(key, _LEGACY_COVER_TEXT[key])
        return size, bool(bold)
    return _LEGACY_COVER_TEXT[key]


# 프로필 표에 값이 없을 때 쓰는 식량작물 현행 제목 크기. 프로필을 읽되 값이
# 비면 여기로 돌아간다(바이트 불변 기준선).
_LEGACY_TITLE_PT = {'요약': 22, '감사의 글': 20, '학부모 동의서': 20}

# 같은 이유의 앞부분 줄 크기·요약 번호형 소제목. 식량작물 프로필 값과 같다.
_LEGACY_COVER_TEXT = {
    'degree': (18, True), 'plan': (32, True), 'statement': (18, True),
    'approval': (18, True), 'advisor': (18, True), 'default': (20, False),
}
_LEGACY_SUMMARY_ITEM = (16, True)

# 이 파일이 프로필 표를 읽는 전공. 값은 `gg_major_format.ENABLED_FORMAT_MAJORS`
# 한 곳이 정한다(별칭이라 세 빌더가 같은 튜플 객체를 본다).
_FRONTMATTER_PROFILE_MAJORS = ENABLED_FORMAT_MAJORS


def _render_peer_forms(doc, nodes, plan, font):
    """Source page grouping, common flowing layout; never claim measured bands."""
    spec = plan['spec']
    indices = plan['indices']
    ordered = plan['order']
    forms = spec.physical_forms
    profile = (profile_for(spec.major_id)
               if format_enabled(spec.major_id, _FRONTMATTER_PROFILE_MAJORS)
               else None)
    roman_front = bool(profile is not None and profile.roman_front)
    front_margin_mm = None
    if roman_front:
        front_margin_mm = profile.section_formats[1]['margin_mm']
    title_sizes = {role: front_title(profile, role, _LEGACY_TITLE_PT[role])[0]
                   for role in _LEGACY_TITLE_PT}
    # Preserve authored preamble rather than silently dropping it.
    if any(n.get('text', '').strip() for n in nodes[:indices[ordered[0]]]):
        raise ValueError('학교 앞머리 앞에 배정되지 않은 내용이 있습니다')
    for position, roles in enumerate(forms):
        # 식량작물: 요약 묶음 앞에서 로마 대문자 쪽번호 구역을 연다.
        if roman_front and position and '요약' in roles:
            _roman_front_section(doc, front_margin_mm, profile, font)
        for role in roles:
            start = indices[role]
            next_role = ordered[ordered.index(role) + 1]
            end = indices[next_role]
            if role in {'학부모 동의서', '요약', '감사의 글'}:
                title = doc.add_paragraph(role, style='Heading 1')
                if roman_front:
                    # 양식 검정 제목. 문서 스타일은 건드리지 않고 이 두 문단만.
                    _black_runs(title, font, title_sizes[role])
            if roman_front and role == '요약':
                bookmark(title, 'gg_summary', 9001)
            if roman_front and role == '감사의 글':
                bookmark(title, 'gg_thanks', 9004)
            for node in nodes[start + 1:end]:
                if node.get('kind') not in {'paragraph', 'heading'}:
                    raise ValueError('학교 앞머리에는 문단만 지원: ' + role)
                text = node.get('text', '').strip()
                p = doc.add_paragraph(node.get('text', ''))
                # Common defaults are not measurements of a peer's school form.
                # 식량작물 요약·감사의 글은 줄글이라 양쪽 맞춤을 쓴다
                # (앞부분 표지·제출서·인준서는 학교 양식대로 가운데).
                if roman_front and role in {'요약', '감사의 글'}:
                    p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                elif spec.layout_confidence != 'unverified_common_defaults':
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                size, bold = 12, False
                if roman_front:
                    if role == '요약' and re.match(r'^\d+\s*\.', text):
                        # 요약의 번호형 소제목(`1. 농장 개요`, `2. …`)은 전부
                        # `summary_item_pt`(식량작물 16pt 굵게), 나머지 요약
                        # 본문은 12pt. HWPX `_front_charpr_targets`와 같은 규칙.
                        size, bold = _profile_pt(
                            profile, 'summary_item_pt', _LEGACY_SUMMARY_ITEM)
                    elif role not in {'요약', '감사의 글'}:
                        size, bold = _peer_form_text_size(text, spec, profile)
                for run in p.runs:
                    _font(run, font, size, bold)

        if roles == forms[0]:
            present = {n.get('text', '').strip() for n in nodes[:plan['toc']]}
            for notice in spec.notices:
                if notice not in present:
                    doc.add_paragraph(notice)
        # The 요약 section break already starts a new page; a page break here
        # would leave an empty page between 인준서 and 요약.
        if roman_front and position + 1 < len(forms) and '요약' in forms[position + 1]:
            continue
        _break(doc)


def bookmark(p,name,num):
    start=OxmlElement('w:bookmarkStart');start.set(qn('w:id'),str(num));start.set(qn('w:name'),name)
    end=OxmlElement('w:bookmarkEnd');end.set(qn('w:id'),str(num))
    p._p.append(start);p._p.append(end)


def page_reference(p,name,font,size=12):
    field=OxmlElement('w:fldSimple');field.set(qn('w:instr'),'PAGEREF '+name+' \\h');field.set(qn('w:dirty'),'true')
    run=OxmlElement('w:r');pr=OxmlElement('w:rPr');sz=OxmlElement('w:sz');sz.set(qn('w:val'),str(size*2));pr.append(sz);run.append(pr)
    txt=OxmlElement('w:t');txt.text='?';run.append(txt);field.append(run);p._p.append(field)


def contents(doc,entries,font,title='목    차',anchor=None,num=9000,*,title_size=20):
    from docx.enum.text import WD_TAB_ALIGNMENT, WD_TAB_LEADER
    p=doc.add_paragraph();p.alignment=WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before=Mm(18);p.paragraph_format.space_after=Mm(12)
    _font(p.add_run(title),font,title_size)
    if anchor: bookmark(p,anchor,num)
    for label,name,level in entries:
        p=doc.add_paragraph();f=p.paragraph_format
        f.space_before=f.space_after=Pt(0);f.line_spacing=Pt(21)
        f.left_indent=Mm(5 if level==2 else 0)
        f.first_line_indent=Mm(0)
        f.tab_stops.add_tab_stop(Mm(150), WD_TAB_ALIGNMENT.RIGHT, WD_TAB_LEADER.DOTS)
        _font(p.add_run(label),font,12,level==1)
        p.add_run('\t');page_reference(p,name,font)
