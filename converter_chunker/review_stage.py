"""Review real files produced by a pipeline stage; exit nonzero on failure."""
import argparse
from datetime import datetime
import json

from pyimport_pipeline_config import BASE_DIR
from pyimport_stage_validation import validate_conversion, validate_latex, validate_chunks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=('conversion', 'latex', 'chunks'))
    stage = parser.parse_args().stage
    errors = []
    details = {}
    try:
        details = {'conversion': validate_conversion, 'latex': validate_latex,
                   'chunks': validate_chunks}[stage](errors)
    except Exception as error:
        errors.append(f'{type(error).__name__}: {error}')
    report = {'stage': stage, 'passed': not errors, 'details': details, 'errors': errors}
    directory = BASE_DIR / 'logs'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"review-{stage}_{datetime.now():%Y-%m-%d_%H-%M-%S_%f}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f'Review report: {path}')
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
