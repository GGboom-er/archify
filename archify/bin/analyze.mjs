import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const entry = fileURLToPath(new URL('../analysis/run.py', import.meta.url));

export function commandAnalyze(args) {
  try {
    const [language, root, ...rest] = args;
    if (language !== 'python' || !root || root.startsWith('--')) {
      throw new Error('Usage: analyze python <repo-root> --python <executable> --out <new.json> [--markdown <new.md>] [--json]');
    }
    const options = new Map();
    for (let i = 0; i < rest.length; i += 1) {
      const key = rest[i];
      if (key === '--json') continue;
      if (!['--python', '--out', '--markdown'].includes(key) || options.has(key)) {
        throw new Error(`Unknown or repeated option: ${key}`);
      }
      const value = rest[++i];
      if (!value || value.startsWith('--')) throw new Error(`Missing value for ${key}`);
      options.set(key, value);
    }
    if (!options.has('--python') || !options.has('--out')) {
      throw new Error('--python and --out are required; the Python runtime is never guessed.');
    }
    const python = options.get('--python');
    if (!path.isAbsolute(python)) throw new Error('--python must name an absolute executable path.');
    const argv = ['-I', entry, path.resolve(root), '--out', path.resolve(options.get('--out'))];
    if (options.has('--markdown')) argv.push('--markdown', path.resolve(options.get('--markdown')));
    const result = spawnSync(python, argv, { encoding: 'utf8', windowsHide: true,
      maxBuffer: 4 * 1024 * 1024, env: { ...process.env, PYTHONUTF8: '1' } });
    if (result.error) throw result.error;
    if (result.stdout) process.stdout.write(result.stdout);
    if (result.stderr) process.stderr.write(result.stderr);
    process.exitCode = result.status ?? 1;
  } catch (error) {
    console.log(JSON.stringify({ status: 'ERROR', diagnostics: [{ code: 'analysis/arguments-or-runtime',
      message: error.message, supportedFixes: ['Use analyze python with an absolute Python 3.10+ executable and new output paths.'] }] }));
    process.exitCode = 1;
  }
}
