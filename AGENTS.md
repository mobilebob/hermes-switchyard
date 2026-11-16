# Hermes Switchyard — agent guide

Short rules for coding agents working in this public repository.
Read `CONTRIBUTING.md` and `SECURITY.md` before changing behavior.
This file does not describe Hermes core.

## What this repo is

Hermes Switchyard is a standalone native plugin for
[Hermes Agent](https://github.com/NousResearch/hermes-agent).
It routes small decisions (skills, reasoning effort, typed checks)
through Jev, then acts only inside the limits already in this tree,
and writes a local receipt.

Keep every change inside this repository. Use the documented Hermes
plugin context. Do not patch Hermes core to make the plugin work.

Stable names: manifest `hermes-switchyard`, root `__init__.py`,
package `hermes_switchyard`.

## Local commits and publication

Owner policy: Git commit signing is not required for this project. Unsigned
local checkpoints are allowed. If local Git configuration enforces signing,
override it for that commit only; do not change global configuration or create
signing credentials. Do not push or update a remote PR until the owner has
reviewed the implementation and test results.

## Pull requests

Open a ready-for-review pull request against `main`. Do not open a draft
unless Brian asks for one.

On every hermes-switchyard pull request, after the PR is open, comment
exactly `@coderabbitai review` so CodeRabbit runs. Do this even when the
repo is under 10 stars. Do not request GitHub Copilot review until
2026-11-01.

`@coderabbitai review` does not rerun a commit CodeRabbit has already
reviewed. CodeRabbit is incremental: it reviews new commits and leaves
already reviewed ones alone. To review the whole changeset again, comment
`@coderabbitai full review`. That full-review command applies only when
automatic reviews are paused.

Watch the checks that start on that pull request and fix failures this
change caused. Do not shrink or disable existing workflows to get green.
The required check is Ubuntu / Python 3.11 / native Hermes. Workflow
lint, CodeQL, and Sourcery may also run. Do not treat a bot review as
the merge decision.

Do not tag, release, or bump the Hermes catalog unless Brian asks.
Do not merge your own pull request without an approving review from
someone else when branch protection requires one.

## Prove value before a release candidate

New behavior stays opt-in, and out of the release claim, until a frozen
check shows it earns its place. Say when a result is synthetic, when a
live check is still missing, and when no comparison has been measured.
Do not call a feature a release candidate on a green unit run alone.

## Public text only

Source, tests, docs, commits, and pull request text stay public-safe:

- no personal paths, private hostnames, or lab notes
- no API keys, tokens, or raw captures
- no claim the code does not establish

Security reports go through `SECURITY.md`, not a public issue.

## Checks

From the repository root, the offline checks in `CONTRIBUTING.md` are
the default: unit tests, `evaluation/evaluate.py --validate`, and
`scripts/check_portability.py`. Tests use synthetic fixtures and must
not call a live provider or a real desktop. For workflow edits, the
pinned actionlint / ShellCheck job is the check that must pass.
