# Contributing

Thanks for helping out. Hermes Switchyard is a standalone native Hermes plugin. Keep every change inside this repository, and use only the documented Hermes plugin context. Don't modify Hermes core to support this plugin.

New to the project? The [Concepts primer](docs/CONCEPTS.md) and [documentation map](docs/README.md) are the quickest way in.

## Keep these stable

- the manifest name `hermes-switchyard`
- the root `__init__.py` entrypoint
- the `hermes_switchyard` package path

Preserve `bgrablin` attribution and any genuine third-party attribution.

Never add any of the following to source, tests, docs, or issue reports:

- personal paths or private hostnames
- lab details or operational handoffs
- credentials
- raw UI captures

## Run the local checks

From the repository root:

```text
python -m unittest discover -s tests -v
python evaluation/evaluate.py --validate
python scripts/check_portability.py
python scripts/check_portability.py --history
python scripts/build_release.py --source-sha "$(git rev-parse --verify HEAD)" --output-dir dist
```

What these do:

- **Unit tests** run offline with synthetic fixtures. Some tests skip, with a stated reason, when no Hermes runtime can be imported.
- **The evaluator** uses public synthetic fixtures. It never calls the live Decisions service or drives a real GUI.
- **The portability check** scans for private paths, credentials, and stale branding. `--history` scans the Git history as well.
- **The release builder** reads its fixed file allowlist from Git at the given commit and writes a deterministic ZIP. It then verifies extraction, hashes, the manifest, the entrypoint, and the source reference. If you add a doc that the README links to, add it to `RELEASE_FILES` in `scripts/build_release.py`.

Don't commit `dist/`, test results, caches, logs, or local evidence. Keep behavior tests under `tests/`, and focus them on observable contracts rather than source-text snapshots.

For GitHub Actions changes, run `actionlint -color` from the repository root with
[actionlint 1.7.12](https://github.com/rhysd/actionlint/blob/v1.7.12/docs/install.md).
Install ShellCheck as well to include checks of embedded shell scripts. The
`Workflow lint` workflow checks every workflow on pull requests and main pushes
using a version-pinned, SHA-256-verified actionlint download and the Ubuntu
runner's ShellCheck. It complements Ruff, CodeQL, and the compatibility suite;
it needs no model calls, service credentials, or additional GitHub App.

CodeRabbit's path instructions in `.coderabbit.yaml` focus reviews on native
plugin compatibility, authorization and redaction boundaries, offline regression
tests, CI coverage, and honest evaluation claims. Keep this guidance aligned with
the contracts above and `SECURITY.md`. Sourcery's pull-request review controls
remain in its dashboard; its legacy YAML does not configure those controls.

## Offline memory-screen qualification

Keep the new memory corpus separate from historical evaluation ledgers. Do not
reset or replay those ledgers. The frozen labels and SHA-256 manifest are under
`evaluation/memory_screening/`; the lexical transport is blind to labels/IDs and
is deliberately not a Jev-quality measurement. With a scratch output path:

```text
python -m unittest discover -s tests -p 'test_memory*.py' -v
python evaluation/memory_screening/holdout.py --output /path/to/scratch/memory-holdout.json
python -m unittest discover -s tests -p 'test_shared_jev_native.py' -v
```

Native lifecycle checks require a native Hermes test environment. Run with a
disposable `HOME`/`HERMES_HOME`, `HERMES_PROFILE=default`, no production auth files
and blocked external sockets; the native workers also refuse socket connections.
They install a release archive into disposable state and use a deterministic
transport, never live Jev. An actual native tool-dispatch case qualifies the
public tool-execution scope bridge; synthetic provider responses are not live
quality/latency/billing evidence. Finish with the full regression suite and an
archive built from the exact committed revision. Keep all new features default-off.

## Pull requests

In the description:

- describe the behavior you changed and the checks you ran;
- state any test boundary that remains.

Be explicit when:

- a result is synthetic;
- a real GUI check is still pending;
- comparative benchmark results aren't available.

Don't claim calibration, task completion, or security properties the code doesn't establish.

## Writing style

All project-facing text and examples are in English.

- **Lead with what the reader can do or will see,** then the details. For a new feature guide, open with a short "In short" paragraph, and put exhaustive rules in a clearly marked reference section.
- **Use concrete limits and real configuration names.**
- **Never include tokens** in commands or URLs.
