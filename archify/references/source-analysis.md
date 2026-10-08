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
but does not implement automatic incremental invalidation or runtime tracing.

## Tests

```sh
/absolute/python3 -m unittest discover -s archify/test -p source_analysis_test.py
ARCHIFY_PYTHON=/absolute/python3 node --test archify/test/source-analysis.test.mjs
```

Run the Node suite with `ARCHIFY_PYTHON` set; unset means the end-to-end cases are
skipped, not passed. Synthetic fixtures contain no production repository source.
Private analysis reports stay local; do not commit them to this public fork.
