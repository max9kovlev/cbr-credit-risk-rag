import argparse
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
STRUCTURE_DIR = BASE_DIR / 'output/docling/structure'


def read(path):
    records = {}
    with path.open(encoding='utf-8') as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            record = json.loads(line)
            uid = record['provision_id']
            if uid in records:
                raise ValueError(f'{path}:{line_no}: duplicate provision_id: {uid}')
            records[uid] = record
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--md', type=Path,
                        default=STRUCTURE_DIR / 'N845-md-docling-provisions.jsonl')
    parser.add_argument('--json', dest='json_input', type=Path,
                        default=STRUCTURE_DIR / 'N845-json-docling-provisions.jsonl')
    parser.add_argument('--output-dir', type=Path, default=STRUCTURE_DIR)
    args = parser.parse_args()
    for path in (args.md, args.json_input):
        if not path.is_file():
            parser.error(f'File not found: {path}. Run the corresponding normative parser first.')

    md_records = read(args.md)
    json_records = read(args.json_input)
    shared = md_records.keys() & json_records.keys()
    changed = [uid for uid in shared
               if ' '.join(md_records[uid]['text'].split())
               != ' '.join(json_records[uid]['text'].split())]
    report = {
        'md_count': len(md_records),
        'json_count': len(json_records),
        'shared_ids': len(shared),
        'only_md': sorted(md_records.keys() - json_records.keys()),
        'only_json': sorted(json_records.keys() - md_records.keys()),
        'different_text_ids': sorted(changed),
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / 'N845-docling-parser-comparison.json'
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print({key: len(value) if isinstance(value, list) else value
           for key, value in report.items()})
    print(f'Report: {output}')


if __name__ == '__main__':
    main()
