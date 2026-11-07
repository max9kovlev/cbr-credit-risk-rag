import argparse
import json
import re
from collections import Counter
from pathlib import Path

DOC = 'N845-P'
SECTION = re.compile(r'^\s*(?:#{1,6}\s+)?\u0420\u0430\u0437\u0434\u0435\u043b\s+([IVXLCDM]+)\.\s*(.*)$', re.IGNORECASE)
CHAPTER = re.compile(r'^\s*(?:#{1,6}\s+)?\u0413\u043b\u0430\u0432\u0430\s+(\d+)\.\s*(.*)$', re.IGNORECASE)
APPENDIX = re.compile(r'^\s*(?:#{1,6}\s+)?\*{0,2}\u041f\u0440\u0438\u043b\u043e\u0436\u0435\u043d\u0438\u0435\s+(\d+)\b', re.IGNORECASE)
HEADING = re.compile(r'^\s*#{1,6}\s+(.+)$')
NUMBER = re.compile(r'^\s*(\d+(?:\.\d+)*)\.\s+(.+)$')


def parse(lines):
    records, issues = [], []
    ids = set()
    app = section = chapter = active = None
    in_code = False

    def add(kind, number=None, title=None, text='', parent=None, line_no=None):
        nonlocal active
        scope = f'appendix:{app}' if app else 'main'
        if kind == 'appendix': uid = f'{DOC}:appendix:{number}'
        elif kind in ('section', 'chapter'): uid = f'{DOC}:{scope}:{kind}:{number}'
        elif kind == 'heading': uid = f'{DOC}:{scope}:heading:{len(records)+1}'
        elif kind == 'preamble': uid = f'{DOC}:preamble'
        else: uid = f'{DOC}:{scope}:{number}'
        if uid in ids:
            issues.append({'type': 'duplicate_number', 'line': line_no, 'id': uid})
            uid += f':duplicate:{len(records)+1}'
        ids.add(uid)
        
        record_section = uid if kind == 'section' else section
        record_chapter = None if kind == 'section' else (uid if kind == 'chapter' else chapter)
        rec = {'provision_id': uid, 'document_id': DOC, 'type': kind,
               'number': number, 'title': title, 'text': text, 'parent_id': parent,
               'section_id': record_section, 'chapter_id': record_chapter,
               'appendix_id': f'{DOC}:appendix:{app}' if app else None,
               'order': len(records)+1, 'source_lines': [line_no] if line_no else []}
        records.append(rec)
        active = rec
        return uid

    for line_no, raw in enumerate(lines, 1):
        stripped = raw.strip()
        if stripped.startswith(('```', '~~~')):
            in_code = not in_code
        if in_code or not stripped:
            if active and stripped:
                active['text'] += ('\n' if active['text'] else '') + raw
                active['source_lines'].append(line_no)
            continue
        match = APPENDIX.match(stripped)
        if match:
            app = match.group(1)
            section = chapter = None
            add('appendix', app, title=re.sub(r'^#{1,6}\s+', '', stripped).strip('* '), line_no=line_no)
            continue
        match = SECTION.match(stripped)
        if match:
            section = add('section', match.group(1).upper(), title=match.group(2),
                          parent=f'{DOC}:appendix:{app}' if app else None, line_no=line_no)
            chapter = None
            continue
        match = CHAPTER.match(stripped)
        if match:
            chapter = add('chapter', match.group(1), title=match.group(2),
                          parent=section or (f'{DOC}:appendix:{app}' if app else None), line_no=line_no)
            continue
        match = HEADING.match(stripped)
        if match:
            add('heading', title=match.group(1),
                parent=chapter or section or (f'{DOC}:appendix:{app}' if app else None), line_no=line_no)
            continue
        match = NUMBER.match(stripped)
        if match and (app or len(match.group(1).split('.')) >= 2):
            num, body = match.groups()
            depth = len(num.split('.'))
            if app:
                parent = (f'{DOC}:appendix:{app}:' + num.rsplit('.', 1)[0]) if depth > 1 else (chapter or section or f'{DOC}:appendix:{app}')
            else:
                parent = f'{DOC}:main:{num.rsplit(".", 1)[0]}' if depth > 2 else (chapter or section)
            kind = 'point' if (app and depth == 1) or (not app and depth == 2) else 'subpoint'
            add(kind, num, text=body, parent=parent, line_no=line_no)
            continue
        if active is None:
            add('preamble', text=raw, line_no=line_no)
        else:
            active['text'] += ('\n' if active['text'] else '') + raw
            active['source_lines'].append(line_no)
    existing = {r['provision_id'] for r in records}
    missing = [{'id': r['provision_id'], 'parent': r['parent_id']} for r in records
               if r['parent_id'] and r['parent_id'] not in existing]
    return records, {'records': len(records), 'by_type': dict(Counter(r['type'] for r in records)),
                     'missing_parents': missing, 'issues': issues,
                     'hierarchy_valid': not missing and not issues}


def main():
    base = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, default=base / 'output/docling/N845-docling.md')
    parser.add_argument('--output-dir', type=Path, default=base / 'output/docling/structure')
    args = parser.parse_args()
    if not args.input.is_file():
        parser.error(f'File not found: {args.input}')
    records, report = parse(args.input.read_text(encoding='utf-8').splitlines())
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_stem = f"{args.input.stem.removesuffix('-docling')}-md-docling"
    dest = args.output_dir / f'{output_stem}-provisions.jsonl'
    with dest.open('w', encoding='utf-8') as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    (args.output_dir / f'{output_stem}-report.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Saved {len(records)} records: {dest}')
    print(f'Unresolved parents: {len(report["missing_parents"])}; issues: {len(report["issues"])}')


if __name__ == '__main__':
    main()
