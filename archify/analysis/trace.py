"""Explicit script execution boundary. Static analyze never calls this module."""
import argparse
from contextlib import redirect_stdout
import json
from pathlib import Path
import runpy
import sys
import threading
import traceback

from .runtime import Recorder


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root')
    parser.add_argument('--entry', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--max-events', type=int, default=200000)
    # Script arguments are separated before parsing, so filenames are unambiguous.
    values = list(sys.argv[1:] if argv is None else argv)
    split = values.index('--') if '--' in values else len(values)
    script_args = values[split + 1:]
    args = parser.parse_args(values[:split])
    try:
        root, entry, output = (Path(p).resolve() for p in (args.root, args.entry, args.out))
        if output.suffix != '.json' or output.exists():
            raise ValueError('Choose a new .json output path')
        if not entry.is_file() or entry.suffix != '.py' or not entry.is_relative_to(root):
            raise ValueError('--entry must be an existing Python script inside the selected root')
        recorder = Recorder(root, args.max_events)
        relative = entry.relative_to(root).as_posix()
        if relative not in recorder.expected:
            raise ValueError('Entry is excluded, linked, or cannot be parsed')
        output.parent.mkdir(parents=True, exist_ok=True)
        # Reserve before execution; an unusable output must never launch target code.
        with output.open('x', encoding='utf-8') as stream:
            old_argv, old_path = sys.argv, list(sys.path)
            sys.argv = [str(entry)] + script_args
            sys.path[:0] = [str(entry.parent), str(root)]
            outcome = {'status': 'success', 'exit_code': 0}
            recorder.start()
            try:
                with redirect_stdout(sys.stderr):
                    try:
                        runpy.run_path(str(entry), run_name='__main__')
                    except SystemExit as error:
                        code = error.code if isinstance(error.code, int) else (0 if error.code is None else 1)
                        outcome = {'status': 'success' if code == 0 else 'failed', 'exit_code': code}
                    except BaseException as error:
                        outcome = {'status': 'failed', 'exit_code': 1, 'exception_type': type(error).__name__}
                        traceback.print_exc()
                    # Match normal interpreter shutdown for new non-daemon threads.
                    for thread in threading.enumerate():
                        if thread.ident not in recorder.initial_threads and not thread.daemon:
                            thread.join()
            finally:
                report = recorder.stop()
                sys.argv, sys.path[:] = old_argv, old_path
            report.update(entry=relative, outcome=outcome)
            if outcome['status'] != 'success':
                report['status'] = 'PARTIAL'
                report['reasons'].append('target_failed')
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        print(json.dumps({'status': report['status'], 'trace': str(output),
                          'outcome': outcome, 'events': report['events'], 'reasons': report['reasons']}))
        return 0 if report['status'] == 'PASS' else 2
    except (OSError, ValueError, RecursionError) as error:
        print(json.dumps({'status': 'ERROR', 'diagnostics': [{'code': 'trace/input-or-output',
            'message': str(error), 'supportedFixes': ['Select a runnable script and a new report destination.']}]}))
        return 1
