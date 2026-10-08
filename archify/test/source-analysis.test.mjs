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
