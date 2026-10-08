import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const entry = fileURLToPath(new URL('../analysis/trace_run.py', import.meta.url));

export function commandTrace(args) {
  try {
    const [language, root, ...rest] = args;
    if (language !== 'python' || !root || root.startsWith('--')) {
      throw new Error('Usage: trace python <root> --python <absolute-executable> --entry <script.py> --out <new.json> [--timeout-ms 120000] [--max-events 200000] [-- script-args]');
    }
    const options = new Map();
    let scriptArgs = [];
    for (let i = 0; i < rest.length; i += 1) {
      const key = rest[i];
      if (key === '--') { scriptArgs = rest.slice(i + 1); break; }
      if (key === '--json') continue;
      if (!['--python', '--entry', '--out', '--timeout-ms', '--max-events'].includes(key) || options.has(key)) {
        throw new Error(`Unknown or repeated option: ${key}`);
      }
      const value = rest[++i];
      if (!value || value.startsWith('--')) throw new Error(`Missing value for ${key}`);
      options.set(key, value);
    }
    for (const required of ['--python', '--entry', '--out']) {
      if (!options.has(required)) throw new Error(`${required} is required; target execution is explicit.`);
    }
    const python = options.get('--python');
    if (!path.isAbsolute(python)) throw new Error('--python must be an absolute executable path.');
    const timeout = Number(options.get('--timeout-ms') ?? '120000');
    if (!Number.isSafeInteger(timeout) || timeout < 1 || timeout > 2147483647) {
      throw new Error('--timeout-ms must be a positive integer up to 2147483647.');
    }
    const argv = ['-I', '-X', 'utf8', entry, path.resolve(root), '--entry', path.resolve(options.get('--entry')),
      '--out', path.resolve(options.get('--out'))];
    if (options.has('--max-events')) argv.push('--max-events', options.get('--max-events'));
    argv.push('--', ...scriptArgs);
    const result = spawnSync(python, argv, { encoding: 'utf8', windowsHide: true,
      cwd: path.resolve(root), timeout, maxBuffer: 4 * 1024 * 1024,
      env: { ...process.env, PYTHONUTF8: '1' } });
    if (result.stdout) process.stdout.write(result.stdout);
    if (result.stderr) process.stderr.write(result.stderr);
    if (result.error) throw result.error;
    if (result.signal) throw new Error(`Trace interpreter ended with ${result.signal}; evidence may be incomplete.`);
    process.exitCode = result.status ?? 1;
  } catch (error) {
    console.log(JSON.stringify({ status: 'ERROR', diagnostics: [{ code: 'trace/arguments-or-runtime',
      message: error.message, supportedFixes: ['Select an explicit runnable script, absolute Python runtime and new output path.'] }] }));
    process.exitCode = 1;
  }
}
