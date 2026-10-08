"""Behavior tests use synthetic repositories; never import analyzed modules."""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analysis.model import Index
from analysis.scan import build_report


class SourceAnalysisTests(unittest.TestCase):
    def analyze(self, files):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, source in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source, encoding='utf-8')
            return build_report(Index(root).load())

    def call(self, report, expression, path=None):
        return next(c for c in report['calls'] if c['expression'] == expression
                    and (path is None or c['path'] == path))

    def test_qualified_import_and_same_name(self):
        report = self.analyze({'eye.py': 'from pkg import backend\ndef build(): backend.compile({})\n',
            'lip.py': 'def compile(): pass\n', 'pkg/__init__.py': '',
            'pkg/backend.py': 'def compile(spec): return spec\n'})
        call = self.call(report, 'backend.compile')
        self.assertEqual(call['status'], 'resolved')
        self.assertEqual(call['targets'], ['pkg/backend.py:1:compile'])

    def test_local_relative_import_alias_and_reexport(self):
        report = self.analyze({'pkg/__init__.py': 'from .backend import solve as run\n',
            'pkg/backend.py': 'def solve(): pass\n',
            'pkg/ui.py': 'def click():\n from . import run as apply\n apply()\n'})
        self.assertEqual(self.call(report, 'apply')['targets'], ['pkg/backend.py:1:solve'])

    def test_inheritance_and_super_remain_candidates(self):
        report = self.analyze({'base.py': 'class Base:\n def build(self): pass\n',
            'eye.py': 'from base import Base\nclass Eye(Base):\n def rebuild(self):\n  self.build()\n  super(Eye, self).build()\n'})
        for expression in ['self.build', 'super(Eye, self).build']:
            self.assertEqual(self.call(report, expression)['status'], 'candidate')
            self.assertEqual(self.call(report, expression)['targets'], ['base.py:2:build'])

    def test_mro_diamond_uses_c3(self):
        report = self.analyze({'x.py': 'class A:\n def run(self): pass\nclass B(A): pass\n'
            'class C(A):\n def run(self): pass\nclass D(B,C):\n def go(self): self.run()\n'})
        self.assertEqual(self.call(report, 'self.run')['targets'], ['x.py:5:run'])

    def test_shadowed_import_never_claimed_resolved(self):
        report = self.analyze({'a.py': 'def run(): pass\n', 'b.py': 'from a import run\n'
            'def first(run): run()\ndef second():\n run = factory()\n run()\n'})
        calls = [c for c in report['calls'] if c['expression'] == 'run']
        self.assertTrue(all(c['status'] == 'unresolved' and not c['targets'] for c in calls))

    def test_external_and_builtin_separated(self):
        report = self.analyze({'x.py': 'import json\njson.loads("null")\nlen([])\n'})
        self.assertEqual(self.call(report, 'json.loads')['status'], 'external')
        self.assertEqual(self.call(report, 'len')['status'], 'builtin')

    def test_unknown_receiver_not_basename_matched(self):
        report = self.analyze({'x.py': 'def run(): pass\ndef act(obj): obj.run()\n'})
        self.assertEqual(self.call(report, 'obj.run')['status'], 'unresolved')

    def test_wildcard_collision_and_literal_all(self):
        report = self.analyze({'a.py': '__all__ = ["run"]\ndef run(): pass\n',
            'b.py': 'def run(): pass\n', 'x.py': 'from a import *\nfrom b import *\nrun()\n'})
        self.assertEqual(self.call(report, 'run')['status'], 'ambiguous')
        self.assertEqual(len(self.call(report, 'run')['targets']), 2)

    def test_import_cycle_terminates(self):
        report = self.analyze({'a.py': 'from b import run\nrun()\n',
                               'b.py': 'from a import run\n'})
        self.assertEqual(self.call(report, 'run')['status'], 'unresolved')

    def test_callbacks_are_references_not_calls(self):
        report = self.analyze({'x.py': 'def click(): pass\nwidget.connect(click)\n'})
        self.assertEqual(len(report['calls']), 1)
        self.assertEqual(report['callable_arguments'][0]['targets'], ['x.py:1:click'])

    def test_guard_snapshot_and_call_inventory(self):
        source = 'def a(): pass\ndef b(): pass\nif enabled:\n a()\nelse:\n b()\n'
        report = self.analyze({'x.py': source})
        self.assertEqual(self.call(report, 'a')['guards'][0]['branch'], 'then')
        self.assertEqual(self.call(report, 'b')['guards'][0]['branch'], 'else')
        self.assertEqual(len(report['calls']), sum(isinstance(n, ast.Call) for n in ast.walk(ast.parse(source))))

    def test_lambda_and_comprehension_shadowing(self):
        report = self.analyze({'x.py': 'def run(): pass\na = lambda run: run()\nb = [run() for run in funcs]\n'})
        self.assertTrue(all(c['status'] == 'unresolved' for c in report['calls']))

    def test_comprehension_free_receiver_keeps_lexical_candidate(self):
        report = self.analyze({'x.py': 'class Eye:\n def build(self):\n  return [self.rig(x) for x in data]\n def rig(self,x): pass\n'})
        self.assertEqual(self.call(report, 'self.rig')['status'], 'candidate')
        self.assertEqual(self.call(report, 'self.rig')['targets'], ['x.py:4:rig'])

    def test_parse_error_report_and_no_execution(self):
        report = self.analyze({'bad.py': 'def broken(\n', 'good.py':
            'raise RuntimeError("must not import")\ndef run(): pass\n'})
        self.assertEqual(report['coverage']['files_discovered'], 2)
        self.assertEqual(report['coverage']['files_parsed'], 1)
        self.assertEqual(report['parse_errors'][0]['path'], 'bad.py')

    def test_class_body_name_does_not_leak_into_method(self):
        report = self.analyze({'x.py': 'class A:\n def helper(): pass\n def method(self): helper()\n'})
        self.assertEqual(self.call(report, 'helper')['status'], 'unresolved')

    def test_unknown_external_base_not_guessed(self):
        report = self.analyze({'x.py': 'from outside import Base\nclass A(Base):\n def go(self): self.run()\n'})
        self.assertEqual(self.call(report, 'self.run')['status'], 'unresolved')

    def test_cli_reports_and_protects_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'main.py').write_text('print("hello")\n')
            output = root / 'analysis.json'
            args = [sys.executable, '-m', 'analysis', str(root), '--out', str(output),
                    '--markdown', str(root / 'coverage.md')]
            run = subprocess.run(args, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertFalse(json.loads(run.stdout)['runtime_verified'])
            data = output.read_bytes()
            repeat = subprocess.run(args, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
            self.assertEqual(repeat.returncode, 1)
            self.assertEqual(output.read_bytes(), data)

    def test_cli_invalid_root_and_empty_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            for root in [Path(directory) / 'missing', Path(directory)]:
                run = subprocess.run([sys.executable, '-m', 'analysis', str(root), '--out',
                    str(Path(directory) / 'report.json')], cwd=Path(__file__).resolve().parents[1],
                    capture_output=True, text=True)
                self.assertEqual(run.returncode, 1)
                self.assertEqual(json.loads(run.stdout)['status'], 'ERROR')

    def test_match_capture_shadows_import(self):
        report = self.analyze({'x.py': 'def run(): pass\ndef choose(value):\n match value:\n  case {"run": run}: run()\n'})
        self.assertEqual(self.call(report, 'run')['status'], 'unresolved')

    def test_nested_class_does_not_close_over_outer_class(self):
        report = self.analyze({'x.py': 'class Outer:\n def run(): pass\n class Inner:\n  run()\n'})
        self.assertEqual(self.call(report, 'run')['status'], 'unresolved')

    def test_later_star_cannot_be_hidden_by_explicit_import(self):
        report = self.analyze({'a.py': 'def run(): pass\n', 'b.py': 'def run(): pass\n',
            'x.py': 'from a import run\nfrom b import *\nrun()\n'})
        self.assertEqual(self.call(report, 'run')['status'], 'ambiguous')
        self.assertEqual(len(self.call(report, 'run')['targets']), 2)

    def test_definition_after_star_has_precise_binding(self):
        report = self.analyze({'a.py': 'def run(): pass\n',
            'x.py': 'from a import *\ndef run(): pass\nrun()\n'})
        self.assertEqual(self.call(report, 'run')['targets'], ['x.py:2:run'])

    def test_conditional_definition_does_not_hide_star_candidate(self):
        report = self.analyze({'a.py': 'def run(): pass\n',
            'x.py': 'from a import *\nif flag:\n def run(): pass\nrun()\n'})
        self.assertEqual(self.call(report, 'run')['status'], 'ambiguous')

    def test_module_collision_preserves_call_inventory_and_reports_gap(self):
        report = self.analyze({'pkg.py': 'print(1)\n', 'pkg/__init__.py': 'print(2)\n',
                               'x.py': 'from pkg import run\nrun()\n'})
        self.assertEqual(len(report['calls']), 3)
        self.assertEqual(len(report['parse_errors']), 1)
        self.assertEqual(self.call(report, 'run')['status'], 'unresolved')

    def test_reexport_beats_same_named_submodule_for_from_import(self):
        report = self.analyze({'pkg/__init__.py': 'from .backend import run\n',
            'pkg/backend.py': 'def run(): pass\n', 'pkg/run.py': '',
            'x.py': 'from pkg import run\nrun()\n'})
        self.assertEqual(self.call(report, 'run')['targets'], ['pkg/backend.py:1:run'])

    def test_class_comprehension_does_not_claim_class_namespace_binding(self):
        report = self.analyze({'x.py': 'def run(): pass\nclass A:\n def run(): pass\n values = [run() for _ in [1]]\n'})
        self.assertEqual(self.call(report, 'run')['status'], 'unresolved')

    def test_directory_link_is_not_traversed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'project'
            outside = Path(directory) / 'outside'
            root.mkdir()
            outside.mkdir()
            (root / 'main.py').write_text('print(1)\n')
            (outside / 'private.py').write_text('print(2)\n')
            link = root / 'linked'
            if os.name == 'nt':
                result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)],
                                        capture_output=True)
                if result.returncode:
                    self.skipTest('Directory junction creation unavailable')
            else:
                link.symlink_to(outside, target_is_directory=True)
            try:
                report = build_report(Index(root).load())
                self.assertEqual([f['path'] for f in report['files']], ['main.py'])
            finally:
                # Remove only this operation-owned link, never its target tree.
                os.rmdir(link) if os.name == 'nt' else link.unlink()
            self.assertTrue((outside / 'private.py').exists())


if __name__ == '__main__':
    unittest.main()
