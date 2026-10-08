import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const cli = fileURLToPath(new URL('../bin/archify.mjs', import.meta.url));
const python = process.env.ARCHIFY_PYTHON;
const run = (args, cwd) => spawnSync(process.execPath, [cli, ...args], { cwd, encoding: 'utf8' });

test('analyze rejects missing runtime, unknown options and unsupported language', () => {
  for (const args of [
    ['analyze', 'python', '.', '--out', 'x.json'],
    ['analyze', 'python', '.', '--unknown', 'yes'],
    ['analyze', 'javascript', '.', '--out', 'x.json'],
    ['analyze', 'python', '.', '--python', 'python', '--out', 'x.json'],
  ]) {
    const result = run(args);
    assert.notEqual(result.status, 0);
    assert.equal(JSON.parse(result.stdout).status, 'ERROR');
  }
});

test('analyze real CLI creates source inventory from a different cwd', { skip: !python && 'Set ARCHIFY_PYTHON to an absolute Python 3.10+ executable' }, () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'archify-analysis-'));
  try {
    fs.writeFileSync(path.join(dir, 'backend.py'), 'def solve(): return 1\n');
    fs.writeFileSync(path.join(dir, 'entry.py'), 'from backend import solve as run\nrun()\n');
    const args = ['analyze', 'python', dir, '--python', python, '--out', path.join(dir, 'report.json'),
      '--markdown', path.join(dir, 'coverage.md'), '--json'];
    const result = run(args, os.tmpdir());
    assert.equal(result.status, 0, result.stderr + result.stdout);
    const receipt = JSON.parse(result.stdout);
    assert.equal(receipt.runtime_verified, false);
    const report = JSON.parse(fs.readFileSync(receipt.analysis));
    assert.equal(report.calls[0].status, 'resolved');
    assert.deepEqual(report.calls[0].targets, ['backend.py:1:solve']);
    assert.match(fs.readFileSync(receipt.markdown, 'utf8'), /Review checklist/);
    assert.notEqual(run(args).status, 0, 'must not replace previous reports');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('analyze propagates partial parse failures without losing valid-file evidence', { skip: !python && 'Set ARCHIFY_PYTHON' }, () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'archify-analysis-'));
  try {
    fs.writeFileSync(path.join(dir, 'good.py'), 'print(1)\n');
    fs.writeFileSync(path.join(dir, 'broken.py'), 'def broken(\n');
    const result = run(['analyze', 'python', dir, '--python', python, '--out', path.join(dir, 'report.json')]);
    assert.equal(result.status, 2);
    assert.equal(JSON.parse(result.stdout).status, 'PARTIAL');
    const report = JSON.parse(fs.readFileSync(path.join(dir, 'report.json')));
    assert.equal(report.parse_errors.length, 1);
    assert.equal(report.coverage.files_parsed, 1);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('trace real CLI executes explicit entry then attaches observed dynamic calls', { skip: !python && 'Set ARCHIFY_PYTHON' }, () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'archify-trace-'));
  try {
    const script = path.join(dir, 'entry.py');
    const trace = path.join(dir, 'trace.json');
    fs.writeFileSync(script, 'import sys\ndef work(): return 7\nregistry={"run":work}\nassert registry["run"]()==7\nassert sys.argv[1:]==["--input", "a b"]\nprint("target-log 运行证据")\n');
    const traced = run(['trace', 'python', dir, '--python', python, '--entry', script,
      '--out', trace, '--', '--input', 'a b'], os.tmpdir());
    assert.equal(traced.status, 0, traced.stderr + traced.stdout);
    assert.equal(JSON.parse(traced.stdout).status, 'PASS');
    assert.match(traced.stderr, /target-log 运行证据/);
    const result = run(['analyze', 'python', dir, '--python', python, '--trace', trace,
      '--out', path.join(dir, 'analysis.json'), '--markdown', path.join(dir, 'coverage.md')]);
    assert.equal(result.status, 0, result.stderr + result.stdout);
    assert.equal(JSON.parse(result.stdout).runtime_verified, false);
    assert.equal(JSON.parse(result.stdout).runtime_observed, true);
    const report = JSON.parse(fs.readFileSync(path.join(dir, 'analysis.json')));
    assert.ok(report.runtime.observed_symbols > 0);
    assert.match(fs.readFileSync(path.join(dir, 'coverage.md'), 'utf8'), /Runtime observations/);
    fs.appendFileSync(script, '# changed\n');
    const stale = run(['analyze', 'python', dir, '--python', python, '--trace', trace,
      '--out', path.join(dir, 'stale.json')]);
    assert.equal(stale.status, 1);
    assert.match(stale.stdout, /SHA-256/);
    assert.equal(fs.existsSync(path.join(dir, 'stale.json')), false);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('trace and analysis retain failure evidence as PARTIAL', { skip: !python && 'Set ARCHIFY_PYTHON' }, () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'archify-trace-failure-'));
  try {
    const script = path.join(dir, 'entry.py');
    const trace = path.join(dir, 'trace.json');
    fs.writeFileSync(script, 'def fail(): raise ValueError("expected")\nfail()\n');
    const result = run(['trace', 'python', dir, '--python', python, '--entry', script, '--out', trace]);
    assert.equal(result.status, 2);
    assert.equal(JSON.parse(result.stdout).outcome.status, 'failed');
    const merged = run(['analyze', 'python', dir, '--python', python, '--trace', trace,
      '--out', path.join(dir, 'analysis.json')]);
    assert.equal(merged.status, 2);
    assert.equal(JSON.parse(merged.stdout).status, 'PARTIAL');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('trace requires explicit target and bounds execution time', { skip: !python && 'Set ARCHIFY_PYTHON' }, () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'archify-trace-timeout-'));
  try {
    const script = path.join(dir, 'entry.py');
    fs.writeFileSync(script, 'while True: pass\n');
    const base = ['trace', 'python', dir, '--python', python, '--out', path.join(dir, 'trace.json')];
    assert.equal(run(base).status, 1);
    const timed = run([...base, '--entry', script, '--timeout-ms', '500']);
    assert.equal(timed.status, 1);
    assert.equal(JSON.parse(timed.stdout).status, 'ERROR');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
