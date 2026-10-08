# Python source analysis (GGboom-er fork)

This fork adds an opt-in `analyze` command before repository authoring. It builds
a local source inventory for the agent to query and review. It neither imports
the target code nor calls a language model, installs dependencies, uploads source,
or claims that a generated graph proves program behavior.

## Run

Requirements: the existing Archify Node runtime and an explicitly selected
Python 3.10+ executable. Only the Python standard library is used.

```sh
node /absolute/archify/bin/archify.mjs analyze python /absolute/repository \
  --python /absolute/python3 \
  --out /absolute/new-run/analysis.json \
  --markdown /absolute/new-run/coverage.md --json
```

Use a new output path each run. Existing files are rejected, including source
files. No target modules are imported. The interpreter runs in isolated mode;
its extra import path points only to the installed analyzer.

`PASS`/exit 0 means discovered Python files were parsed and an inventory was
written, **not** that every call was resolved. `PARTIAL`/exit 2 includes file parse
errors or skipped file symlinks while preserving evidence from valid files.
`ERROR`/exit 1 means an input/output/runtime failure. Argument-parser errors may
also exit 2; consume the receipt and diagnostic text, not the number alone.

## What the report contains

- Source-relative symbol IDs and call-site IDs, file SHA-256, source lines and
  columns. A working-tree report is not a Git revision claim.
- Functions, classes, modules, syntactic calls, callable arguments, return sites
  and control sites. No known incoming call does not mean dead code.
- Lexical import aliases, relative imports, explicit reexports, bounded wildcard
  exports and known local class hierarchies using C3 order.
- Counts by resolution status; parse failures, skipped files, and review gaps.
- A behavior checklist with `not_reviewed` and `not_run` states. These are not
  test coverage percentages, output equivalence results, or runtime profiles.

| Call status | Meaning |
| --- | --- |
| resolved | A source binding identifies an internal definition. Execution, import timing, decorators and monkey patches remain unverified. |
| candidate | A declared method target exists, but runtime receiver/dispatch is not established. |
| ambiguous | More than one binding/export is possible; inspect every candidate. |
| external | The import refers outside the scanned modules; implementation not analyzed. |
| builtin | The lexical name is a Python builtin. |
| unresolved | No safe target is available; the report gives the reason. |

Python is dynamic. Assignments, factories, dynamic registries, `getattr`, binary
extensions, metaclasses, runtime instance attributes and unknown bases are not
evaluated. Lambda calls and comprehension-bound callables remain unresolved.
The scanner inventories conditional code rather than solving path feasibility.
Function annotations/defaults/decorators are source sites, not proof they execute
in a specific runtime configuration. Python 2-only syntax appears as parse errors.

Directory exclusions: `.git`, `.hg`, `.svn`, `.venv`, `venv`, `node_modules`,
`__pycache__`, `.mypy_cache`, `.pytest_cache`, `.archify`. Directory symlinks are
not traversed; Windows reparse points (including junctions) are excluded too.
Choose the smallest connected code root; other languages are not
supported in this phase.

## Use with repository diagrams

1. Select required behaviors and real UI/API/CLI entries; do not draw every symbol.
2. Use `symbols`, `calls` and `callable_arguments` as navigation candidates. Find
   each selected caller's records, then read the referenced source and conditions.
3. Inspect unresolved/ambiguous sites and candidate receiver types. A callback
   passed as an argument is a reference edge, not a proven invocation.
4. Trace state writes, outputs and lifecycle from source. Complete the behavior
   checklist using explicit evidence; do not equate parsed files with understanding.
5. Author the existing typed diagram JSON with verified source references. Use
   the existing `finalize ... --repo-root ...` workflow; diagram schemas and
   repository evidence checks are unchanged.

Re-run in a new directory after code changes. This phase records source hashes
but does not implement automatic incremental invalidation.

## Optional runtime evidence

`trace` is a separate, explicit execution command. Select an entry whose execution
is authorized for the task. It runs with the selected interpreter's normal
permissions and dependencies, with the repository root as its working directory.
It is not a sandbox. Static `analyze` never launches the target, including when
attaching an existing trace.

```sh
node /absolute/archify/bin/archify.mjs trace python /absolute/repository \
  --python /absolute/python3 --entry /absolute/repository/tests/probe.py \
  --out /absolute/new-run/runtime.json --timeout-ms 120000 --max-events 200000 \
  -- --scenario example

node /absolute/archify/bin/archify.mjs analyze python /absolute/repository \
  --python /absolute/python3 --trace /absolute/new-run/runtime.json \
  --out /absolute/new-run/observed.json --markdown /absolute/new-run/observed.md
```

Repeat `--trace` with distinct reports for multiple scenarios. The merger requires
exactly the current Python file inventory and SHA-256 values, rejecting changed,
added or removed source. It validates consistency, not authenticity of edited data.

The recorder checks observed code objects against a compiled source snapshot and
records Python frame relationships and event counts. It captures dynamic dispatch,
registry/factory calls and callbacks that actually run, including newly started
Python threads. Existing threads, subprocesses and native internals are outside
the scope. `observed_python_stack` links visible Python frames; native callback
intermediaries can be hidden, so it does not necessarily mean a direct source call.
Generator/coroutine resumptions can emit additional call events.

Reports contain identities, positions and counts, not argument values, locals,
command-line arguments or return values. Normal target stdout goes to stderr,
leaving stdout for the CLI receipt.

Python 3.11+ column metadata can match an exact AST call range. Python 3.10
line-only observations remain `line_candidates`, even with one explicit call:
an implicit descriptor/operator could occupy the same line. Unmapped frames stay
visible without fabricated IDs. Static `status`/`targets` never change; observations
use separate `runtime_observed` and `runtime_observations` fields. `runtime_test`
and `behavior_review` do not automatically become passed.

`PASS` means the selected entry finished without a detected recording gap.
`PARTIAL`/exit 2 retains evidence after target/thread failures, profiler changes,
event limits or unfinished threads. Source changes also prevent merging.
`outcome` describes the entry script; `thread_failed` separately records worker
failure even if the entry itself returned normally. `ERROR`/exit 1 covers invalid
paths, existing outputs and launch/time-limit failures. Timeout or abrupt exit may
leave an incomplete report, which is invalid evidence; use a new path on retry.
The default timeout is 120000 ms. The event limit bounds target events recorded,
not target work.

This is cooperative instrumentation, not tamper-proof monitoring. It cannot prove
all paths ran, outputs are correct, or timings match an uninstrumented run. Use
assertion-bearing scenarios and inspect unobserved relationships; behavior and
performance still need their own acceptance tests.

## Tests

```sh
/absolute/python3 -m unittest discover -s archify/test -p source_analysis_test.py
/absolute/python3 -m unittest discover -s archify/test -p source_runtime_test.py
ARCHIFY_PYTHON=/absolute/python3 node --test archify/test/source-analysis.test.mjs
```

Run the Node suite with `ARCHIFY_PYTHON` set; unset means the end-to-end cases are
skipped, not passed. Synthetic fixtures contain no production repository source.
Private analysis reports stay local; do not commit them to this public fork.
