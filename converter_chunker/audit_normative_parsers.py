import argparse
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from zipfile import ZipFile

BASE = Path(__file__).resolve().parent
NS = {'office': 'urn:oasis:names:tc:opendocument:xmlns:office:1.0',
      'text': 'urn:oasis:names:tc:opendocument:xmlns:text:1.0'}
NUMBER = re.compile(r'^(\d+(?:\.\d+)*)\.\s+(.+)', re.S)


def visible(node):
    out = node.text or ''
    for child in node:
        tag = child.tag.split('}')[-1]
        if tag == 's':
            out += ' ' * int(child.get('{%s}c' % NS['text'], '1'))
        elif tag in ('tab', 'line-break'):
            out += ' '
        elif tag not in ('note', 'annotation'):
            out += visible(child)
        out += child.tail or ''
    return out


def normalize(text):
    text = re.sub(r'!\[[^\]]*\]\([^)]*\)', ' ', text)
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)
    return ' '.join(re.findall(r'\w+', text.casefold()))


def reference(path):
    with ZipFile(path) as archive:
        body = ET.fromstring(archive.read('content.xml')).find('.//office:text', NS)
    records, active = [], None
    scope, chapter = 'main', None
    for block, node in enumerate(body, 1):
        tag = node.tag.split('}')[-1]
        text = visible(node).strip()
        if tag not in ('p', 'h'):
            if active is not None:
                active['manual_review'].append(f'{tag} at ODT block {block}')
            continue
        app = re.match(r'^\u041f\u0440\u0438\u043b\u043e\u0436\u0435\u043d\u0438\u0435\s+(\d+)\b', text, re.I)
        chap = re.match(r'^\u0413\u043b\u0430\u0432\u0430\s+(\d+)\.', text, re.I)
        if app:
            scope, chapter, active = 'appendix:' + app[1], None, None
            continue
        if chap:
            chapter, active = chap[1], None
            continue
        if tag == 'h' or re.match(r'^\u0420\u0430\u0437\u0434\u0435\u043b\s+[IVXLCDM]+\.', text, re.I):
            active = None
            continue
        match = NUMBER.match(text)
        if match and (scope != 'main' or '.' in match[1]):
            number = match[1]
            depth = len(number.split('.'))
            parent = (f'N845-P:{scope}:{number.rsplit(".", 1)[0]}'
                      if depth > (2 if scope == 'main' else 1)
                      else f'N845-P:{scope}:chapter:{chapter}' if chapter
                      else f'N845-P:{scope}' if scope != 'main' else None)
            active = {'id': f'N845-P:{scope}:{number}', 'number': number,
                      'parent_id': parent, 'odt_block': block,
                      'text': match[2], 'manual_review': []}
            records.append(active)
        elif active is not None and text:
            active['text'] += '\n' + text
        if active is not None and any(x.tag.split('}')[-1] in ('image', 'object', 'object-ole') for x in node.iter()):
            active['manual_review'].append(f'image/formula at ODT block {block}')
    counts = Counter(r['id'] for r in records)
    for r in records:
        if counts[r['id']] > 1:
            r['manual_review'].append('ambiguous repeated number in ODT')
    return records


def evaluate(refs, records):
    by_id = {r['provision_id']: r for r in records}
    normalized = {r['provision_id']: normalize(r['text']) for r in records}
    results = []
    for ref in refs:
        expected = normalize(ref['text'])
        exact = by_id.get(ref['id'])
        
        candidates = [r for r in records if r.get('number') == ref['number']]
        best = max(candidates, key=lambda r: SequenceMatcher(None, expected, normalized[r['provision_id']], autojunk=False).ratio(), default=None)
        chosen = exact or best
        similarity = SequenceMatcher(None, expected, normalized[chosen['provision_id']], autojunk=False).ratio() if chosen else 0
        results.append({'reference_id': ref['id'], 'id_found': exact is not None,
                        'candidate_id': chosen['provision_id'] if chosen else None,
                        'parent_matches': chosen is not None and chosen.get('parent_id') == ref['parent_id'],
                        'text_equal_normalized': chosen is not None and expected == normalized[chosen['provision_id']],
                        'text_similarity': round(similarity, 4),
                        'candidate_text': chosen['text'] if chosen else None})
    reference_ids = {r['id'] for r in refs}
    return {'summary': {'reference_candidates': len(refs),
                        'ids_found': sum(r['id_found'] for r in results),
                        'parents_matching': sum(r['parent_matches'] for r in results),
                        'texts_equal_normalized': sum(r['text_equal_normalized'] for r in results),
                        'mean_text_similarity': round(sum(r['text_similarity'] for r in results) / len(results), 4) if results else None},
            'numbered_ids_not_in_reference': [r['provision_id'] for r in records if r['type'] in ('point', 'subpoint') and r['provision_id'] not in reference_ids],
            'results': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--odt', type=Path, default=BASE / 'input/N845.odt')
    parser.add_argument('--structure-dir', type=Path, default=BASE / 'output/docling/structure')
    args = parser.parse_args()
    refs = reference(args.odt)
    report = {'source': str(args.odt), 'limitations': [
        'ODT reference is automatically extracted, not manually verified ground truth.',
        'Only numbered provisions are scored; headings and preamble are not scored.',
        'Tables, images, formulas, footnotes and text formatting are not scored.',
        'Normalization ignores punctuation and case; similarity does not prove legal equivalence.',
        'Missing IDs do not prove lost text; text may be attached to another record.'],
        'reference': refs, 'methods': {}}
    for method in ('md', 'json'):
        path = args.structure_dir / f'N845-{method}-docling-provisions.jsonl'
        records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        report['methods'][method] = evaluate(refs, records)
        print(method, report['methods'][method]['summary'])
    output = args.structure_dir / 'N845-docling-odt-audit.json'
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Report:', output)


if __name__ == '__main__':
    main()
