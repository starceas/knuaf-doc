"""Body object boundaries and explicit, non-rendered layout review requests."""
import re

from gg_document import photo_guide

NOTE = re.compile(r"^(?:주\s*\)|(?:주|자료|출처|단위)\s*[:：])")


def object_blocks(nodes):
    """Yield caption/object/note ranges; never put spacers inside a block."""
    i = 0
    while i < len(nodes):
        node = nodes[i]
        kind = node['kind']
        if kind not in {'caption', 'table', 'image'} and not photo_guide(node.get('text', '')):
            i += 1
            continue
        start = i
        if i + 1 < len(nodes):
            after = nodes[i + 1]
            if (kind == 'caption' and node['label'] == '표' and after['kind'] == 'table'
                    or (kind == 'image' or photo_guide(node.get('text', '')))
                    and after['kind'] == 'caption' and after['label'] == '그림'):
                i += 1
        while i + 1 < len(nodes):
            after = nodes[i + 1]
            if after['kind'] != 'paragraph' or not NOTE.match(after['text']):
                break
            i += 1
        yield start, i
        i += 1


def spacer_positions(nodes):
    """One gap at object/prose or object/object boundaries, not at headings."""
    positions = set()
    for start, end in object_blocks(nodes):
        if start and nodes[start - 1]['kind'] != 'heading':
            positions.add(start)
        if end + 1 < len(nodes) and nodes[end + 1]['kind'] != 'heading':
            positions.add(end + 1)
    return positions


def review_requests(nodes):
    """A review hook, not a predicted line-wrap or pagination verdict."""
    return {
        'status': 'not_run',
        'authority': 'actual Word PDF render; HWPX requires native Hancom render',
        'tables': [i + 1 for i, node in enumerate(nodes) if node['kind'] == 'table'],
        'figures': [i + 1 for i, node in enumerate(nodes) if node['kind'] == 'image'],
        'checks': [
            'Widen long-text columns before reducing table text one point at a time.',
            'Inspect every table page for broken words, isolated punctuation and stranded header characters.',
            'Keep table notes attached; inspect the preceding page for new large gaps.',
            'Shrink a displaced figure only while its legend and embedded text remain readable.',
        ],
    }
