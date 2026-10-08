"""CLI boundary for source analysis; errors and partial analysis are explicit."""
import argparse
import json
import sys
from pathlib import Path
from .model import Index
from .scan import build_report
from .evidence import attach_runtime


def markdown(report):
    coverage = report['coverage']
    lines = ['# Python source coverage', '',
             'Static inventory only. Behavior review and runtime tests are not completed.', '',
             f"Files parsed: {coverage['files_parsed']}/{coverage['files_discovered']}",
             f"Symbols: {coverage['symbols']}; calls: {coverage['calls']}", '',
             '| Call status | Count |', '| --- | ---: |']
    lines += [f'| {key} | {value} |' for key, value in coverage['call_statuses'].items()]
    lines += ['', '## Review checklist', '',
              '- [ ] Confirm public/UI entry points and callbacks.',
              '- [ ] Check unresolved and ambiguous calls against source.',
              '- [ ] Confirm candidate virtual/super dispatch using actual receiver types.',
              '- [ ] Trace each required behavior through branches to state/output writes.',
              '- [ ] Verify output contracts and error/undo lifecycle.',
              '- [ ] Run relevant behavior and performance tests separately.', '',
              '## Analysis gaps', '']
    for row in report['parse_errors'] + report['skipped_files']:
        lines.append(f"- {row['path']}: {row['reason']}")
    gaps = [row for row in report['calls'] if row['status'] in ('unresolved', 'ambiguous')]
    lines.append(f'{len(gaps)} call sites need review. First 100 below; all sites are in analysis.json.')
    for row in gaps[:100]:
        label = row['expression'].replace('`', "'").replace('\n', ' ')
        lines.append(f"- {row['path']}:{row['line']} `{label}` — {row['reason']}")
    lines += ['', '## Limits', ''] + ['- ' + line for line in report['limits']]
    if 'runtime' in report:
        runtime = report['runtime']
        lines += ['', '## Runtime observations', '',
            f"Runs: {len(runtime['runs'])}; observed symbols: {runtime['observed_symbols']}",
            f"Observed call sites: {runtime['observed_call_sites']}; unobserved: {runtime['unobserved_call_sites']}",
            'Observed execution is not a verified behavior contract or complete branch coverage.', '',
            '| Run | Status | Target outcome | Gaps |', '| --- | --- | --- | --- |']
        lines += [f"| {r['id']} | {r['status']} | {r['outcome']['status']} | {', '.join(r['reasons'])} |"
                  for r in runtime['runs']]
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root')
    parser.add_argument('--out', required=True)
    parser.add_argument('--markdown')
    parser.add_argument('--trace', action='append', default=[])
    args = parser.parse_args(argv)
    try:
        root = Path(args.root).resolve()
        output = Path(args.out).resolve()
        summary = Path(args.markdown).resolve() if args.markdown else None
        if output.suffix != '.json' or (summary and summary.suffix != '.md'):
            raise ValueError('Outputs must use .json and .md extensions')
        if summary == output:
            raise ValueError('JSON and Markdown outputs must be different')
        # Reports never overwrite existing files: choose a new run destination.
        if output.exists() or (summary and summary.exists()):
            raise ValueError('Output already exists; choose a new run destination')
        report = build_report(Index(root).load())
        if args.trace:
            attach_runtime(report, args.trace)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        if summary:
            summary.parent.mkdir(parents=True, exist_ok=True)
            with summary.open('x', encoding='utf-8') as stream:
                stream.write(markdown(report))
        partial = bool(report['parse_errors'] or report['skipped_files'] or
                       any(r['status'] != 'PASS' for r in report.get('runtime', {}).get('runs', [])))
        print(json.dumps({'status': 'PARTIAL' if partial else 'PASS',
            'analysis': str(output), 'markdown': str(summary) if summary else None,
            'coverage': report['coverage'], 'runtime_verified': False,
            'runtime_observed': bool(args.trace)}))
        return 2 if partial else 0
    except (OSError, ValueError, RecursionError) as error:
        print(json.dumps({'status': 'ERROR', 'diagnostics': [{
            'code': 'analysis/input-or-output', 'message': str(error),
            'supportedFixes': ['Check the repository, source encoding and new output paths.']}]}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
