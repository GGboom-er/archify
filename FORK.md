# Archify — GGboom-er source-analysis fork

This is a source-maintained fork of [tt-a1i/archify](https://github.com/tt-a1i/archify).
The first enhancement is based on the installed, verified upstream commit
`73aaa0696e8f72c232ea710e6fa94fd953f3e773` (3.0.1). It deliberately preserves that
deployment baseline instead of combining the feature with an upstream upgrade.
Upstream copyrights and the MIT license are retained.

## Added capability

`archify analyze python` inventories Python functions, classes, imports, calls,
candidate method dispatch, callable arguments and unresolved relationships.
It records source hashes and a coverage checklist for the agent to verify before
authoring a diagram. It uses Python's standard-library AST without importing the
target repository. Existing diagram schemas and rendering behavior are unchanged.

See [source analysis usage and limitations](archify/references/source-analysis.md).
The analyzer currently supports Python 3.10+ source syntax, not arbitrary languages,
runtime tracing or automatic proof of complete behavior. Parse failures produce a
partial report; unknown calls are retained rather than guessed.

## Use this fork

Use a durable checkout of this repository and call `archify/bin/archify.mjs`.
Select an absolute Python 3.10+ executable for the analysis subcommand. Existing
rendering commands continue to require only the normal Archify runtime.

```sh
node archify/bin/archify.mjs analyze python /path/to/project \
  --python /absolute/python3 --out /path/to/new-run/analysis.json \
  --markdown /path/to/new-run/coverage.md --json
```

This change is delivered as source commits. It does not publish a new upstream
release, overwrite version 3.0.1, or regenerate the upstream `archify.zip`. That
archive remains the upstream artifact and does **not** contain this enhancement.
Use the source checkout, not the old archive, for the added command.

## Maintenance and validation

- Keep analysis implementation in `archify/analysis/` and CLI adaptation in
  `archify/bin/analyze.mjs`; the existing CLI only adds dispatch and help.
- Keep public fixtures synthetic. Private repository analysis reports stay local.
- Run Python behavioral tests and Node CLI tests as documented in source-analysis.md.
- The dedicated CI workflow runs those tests with Python 3.10/3.12 and Node 22.
- Reuse upstream diagram/finalize checks after changing the shared entry point.
- Track upstream separately; review compatibility before updating the pinned base.

Static call resolution supports source navigation. The agent remains responsible
for tracing actual data, checking dynamic dispatch and validating runtime behavior.
