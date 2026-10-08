"""Real execution probes; fixtures are synthetic and isolated from production code."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))
from analysis.evidence import attach_runtime
from analysis.model import Index
from analysis.scan import build_report


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / 'trace.json'

    def write(self, name, source):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding='utf-8')
        return path

    def run_trace(self, source, *args):
        entry = self.write('entry.py', source)
        result = subprocess.run([sys.executable, '-I', str(PACKAGE / 'analysis/trace_run.py'),
            str(self.root), '--entry', str(entry), '--out', str(self.output), *args],
            capture_output=True, text=True, timeout=15)
        self.assertTrue(result.stdout, result.stderr)
        self.receipt = json.loads(result.stdout)
        return result, json.loads(self.output.read_text(encoding='utf-8')) if self.output.exists() else None

    def merge(self):
        report = build_report(Index(self.root).load())
        attach_runtime(report, [self.output])
        return report

    def test_dynamic_dispatch_factory_and_unobserved_branch(self):
        result, trace = self.run_trace('''
class Eye:
    def solve(self): return 1
def factory(): return Eye().solve
def never(): raise AssertionError('must stay unexecuted')
registry = {'run': factory()}
registry['run']()
getattr(Eye(), 'solve')()
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(trace['status'], 'PASS')
        report = self.merge()
        calls = {c['expression']: c for c in report['calls']}
        self.assertEqual(calls["registry['run']"]['status'], 'unresolved')
        self.assertEqual(bool(calls["registry['run']"]['runtime_observations']), sys.version_info >= (3, 11))
        self.assertTrue(any(e['target']['name'] == 'solve' for e in report['runtime']['edges']))
        never = next(s for s in report['symbols'] if s['name'] == 'never')
        self.assertFalse(never['runtime_observed'])
        self.assertEqual(never['runtime_test'], 'not_run')
        self.assertFalse(report['runtime']['behavior_verified'])

    def test_same_line_calls_do_not_cross_assign_targets(self):
        result, trace = self.run_trace('def a(): pass\ndef b(): pass\na(); b()\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        report = self.merge()
        calls = {c['expression']: c for c in report['calls']}
        for expression in ('a', 'b'):
            observations = calls[expression]['runtime_observations']
            if sys.version_info >= (3, 11):
                self.assertEqual(len(observations), 1)
                self.assertEqual(report['runtime']['edges'][observations[0]]['target']['name'], expression)
            else:
                self.assertEqual(observations, [])
        # Explicitly exercise the Python 3.10 line-only evidence contract.
        for edge in trace['edges']:
            if edge['site']:
                edge['site'].update(column=None, end_line=None, end_column=None)
        self.output.write_text(json.dumps(trace), encoding='utf-8')
        self.assertEqual(self.merge()['runtime']['ambiguous_edges'], 2)

    def test_thread_callbacks_are_observed(self):
        result, trace = self.run_trace('import threading\ndef callback(): pass\nt=threading.Thread(target=callback)\nt.start()\nt.join()\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        edge = next(e for e in trace['edges'] if e['target']['name'] == 'callback')
        self.assertIsNone(edge['caller'])
        report = self.merge()
        self.assertTrue(next(s for s in report['symbols'] if s['name'] == 'callback')['runtime_observed'])

    def test_decorated_function_and_multiline_call(self):
        result, trace = self.run_trace('def deco(f): return f\n@deco\ndef actual(): pass\nactual(\n)\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        report = self.merge()
        self.assertTrue(next(s for s in report['symbols'] if s['name'] == 'actual')['runtime_observed'])
        self.assertEqual(bool(next(c for c in report['calls'] if c['expression'] == 'actual')['runtime_observations']),
                         sys.version_info >= (3, 11))

    def test_exception_keeps_partial_evidence_without_false_success(self):
        result, trace = self.run_trace('def fail(): raise RuntimeError("test")\nfail()\n')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(trace['outcome']['status'], 'failed')
        self.assertIn('target_failed', trace['reasons'])
        report = self.merge()
        self.assertTrue(report['runtime']['observed_symbols'])
        self.assertEqual(report['runtime']['runs'][0]['status'], 'PARTIAL')

    def test_system_exit_zero_and_script_arguments(self):
        result, trace = self.run_trace('import sys\nassert sys.argv[1:]==["--switch", "hello world"]\nprint("target output")\nsys.exit(0)\n', '--', '--switch', 'hello world')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('target output', result.stderr)
        self.assertEqual(trace['outcome']['exit_code'], 0)
        self.assertNotIn('hello world', self.output.read_text(encoding='utf-8'))

    def test_source_changed_after_trace_rejected(self):
        self.run_trace('def f(): pass\nf()\n')
        self.write('entry.py', 'def f(): return 2\nf()\n')
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            self.merge()

    def test_source_changed_during_trace_rejected(self):
        result, trace = self.run_trace('from pathlib import Path\nPath(__file__).write_text("pass\\n")\n')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(trace['integrity'], 'changed')
        with self.assertRaisesRegex(ValueError, 'changed during'):
            self.merge()

    def test_new_source_during_trace_marks_partial(self):
        result, trace = self.run_trace('from pathlib import Path\nPath(__file__).with_name("new.py").write_text("pass\\n")\n')
        self.assertEqual(result.returncode, 2)
        self.assertIn('new.py', trace['changed_files'])

    def test_malformed_evidence_is_a_diagnostic(self):
        self.run_trace('pass\n')
        self.output.write_text('{"schema_version":1,"kind":"archify-python-runtime"}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Runtime evidence'):
            self.merge()

    def test_runtime_generated_code_cannot_impersonate_source(self):
        result, trace = self.run_trace('exec(compile("def fake(): pass\\nfake()", __file__, "exec"))\n')
        self.assertEqual(result.returncode, 2)
        self.assertIn('runtime_code_differs_from_source', trace['reasons'])
        self.assertFalse(any(e['target']['name'] == 'fake' for e in trace['edges']))

    def test_limits_and_profiler_replacement_are_partial(self):
        result, trace = self.run_trace('def f(): pass\nfor _ in range(20): f()\n', '--max-events', '3')
        self.assertEqual(result.returncode, 2)
        self.assertIn('event_limit', trace['reasons'])
        self.assertEqual(trace['events'], 3)

    def test_replaced_profiler_is_not_hidden(self):
        result, trace = self.run_trace('import sys\nsys.setprofile(None)\ndef f(): pass\nf()\n')
        self.assertEqual(result.returncode, 2)
        self.assertIn('profiler_replaced', trace['reasons'])

    def test_worker_exception_is_not_success(self):
        result, trace = self.run_trace('import threading\ndef work(): raise RuntimeError("bad")\nt=threading.Thread(target=work)\nt.start()\nt.join()\n')
        self.assertEqual(result.returncode, 2)
        self.assertIn('thread_failed', trace['reasons'])
        self.assertEqual(trace['thread_failures'], [{'exception_type': 'RuntimeError'}])

    def test_worker_profiler_disable_is_detected(self):
        result, trace = self.run_trace('import threading,sys\ndef hidden(): pass\ndef work():\n sys.setprofile(None)\n hidden()\nt=threading.Thread(target=work)\nt.start()\nt.join()\n')
        self.assertEqual(result.returncode, 2)
        self.assertIn('profiler_changed_during_run', trace['reasons'])

    def test_implicit_call_cannot_be_assigned_to_explicit_call_on_same_line(self):
        result, trace = self.run_trace('class X:\n def __getitem__(self, n): return 1\ndef f(): pass\nx=X()\nx[0]; f()\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        for edge in trace['edges']:
            if edge['site']:
                edge['site'].update(column=None, end_line=None, end_column=None)
        self.output.write_text(json.dumps(trace), encoding='utf-8')
        report = self.merge()
        self.assertEqual(next(c for c in report['calls'] if c['expression'] == 'f')['runtime_observations'], [])

    def test_existing_output_refuses_execution(self):
        self.output.write_text('keep', encoding='utf-8')
        entry = self.write('entry.py', 'raise RuntimeError("MUST NOT RUN")\n')
        result = subprocess.run([sys.executable, '-I', str(PACKAGE / 'analysis/trace_run.py'),
            str(self.root), '--entry', str(entry), '--out', str(self.output)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('MUST NOT RUN', result.stderr)
        self.assertEqual(self.output.read_text(), 'keep')

    def test_adding_file_invalidates_inventory(self):
        self.run_trace('pass\n')
        self.write('new.py', 'pass\n')
        with self.assertRaisesRegex(ValueError, 'inventory'):
            self.merge()

    def test_identical_code_in_different_files_keeps_identity(self):
        self.write('eye.py', 'def solve(): pass\n')
        self.write('lip.py', 'def solve(): pass\n')
        result, trace = self.run_trace('import eye,lip\neye.solve(); lip.solve()\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        paths = {e['target']['path'] for e in trace['edges'] if e['target']['name'] == 'solve'}
        self.assertEqual(paths, {'eye.py', 'lip.py'})


if __name__ == '__main__':
    unittest.main()
