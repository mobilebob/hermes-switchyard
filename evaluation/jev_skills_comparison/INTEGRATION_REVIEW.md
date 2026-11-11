# Jev Skills integration review and shared-decision readiness

Status: development only. The profile-aware maintenance workstream is closed.
Broad Stage A2 Sol/Luna benchmarking remains stopped. No paid request, production
routing activation, plugin installation, gateway change, ledger reset or replay
is part of this work.

## Offline qualification checkpoint

The final regression run completed with **1,876 tests, zero failures, zero
errors, 16 documented skips, exit 0**. The shared-decision family contains
48 focused tests, including 22 fresh-process native artifact scenarios. The
native tests build and verify a release ZIP from the working-tree allowlist in
a synthetic fixture repository, install it in disposable homes, and exercise
actual Hermes PluginManager discovery, hooks, skill_view and request middleware.
They do not contact Jev, invoke a main model, or run the production gateway.

Native qualification exposed a real cancellation gap: a response arriving after
stop could become reusable. Bounded producer-generation tokens now reject late
and superseded stores and cancellation during replay. Stop, session end/reset,
and unload invalidate pending generations. If cleanup registration is unavailable,
sharing stays disabled. A stop with a gateway session_key conservatively clears
all pending sharing state; unrelated sessions use independent fallback.

The native lifecycle covers dispatch cancellation/unload, overlapping and
concurrent turns, failure/retry, stale results, route changes before/during/after
dispatch, a 403-entry catalog, deadlines, no-skill/private turns, consume-once,
and default-off after installation and a fresh-process restart. Native automatic
multi-stage selection correctly remains independent. Separate installed-facade
bridge tests verify a stage-two success/failure after a shared result without
reissuing its effort request; these bridge tests are not a claim that automatic
multi-stage selection has been consolidated. The native dispatcher can skip or
serialize a still-running hook; tests distinguish that from an accepted second
producer instead of manufacturing parallel Jev calls.

Jev 1.14 compatibility and existing native lifecycle regressions remain green.
The 16 skips are existing opt-in/browser-platform and Windows-only checks; none
of the 22 new native scenarios skipped. The portability scanner still reports
seven pre-existing full-tree findings; no scanner exemption was broadened.
The namespace regression now checks exact retired namespace boundaries, with
positive import/path/quoted-name tests, while preserving historical receipt labels.

This is an offline development checkpoint, not activation approval. The native
host under test is pinned Hermes 0.21.3; production gateway delivery, hosted Jev
quality, live latency/billing, other hosts, desktop turns and broader model/provider
coverage are not established. No source revision or release was pushed.

## Reviewed implementations

- Switchyard starting revision: `77a6d3a62a9d41d6e8dac140fdcce924253342be`.
- Jev Skills reviewed revision: [`12a7a703ed8069f0c4791844bbb807f2b2d5299c`](https://github.com/kerpopule/hermes-jev-skills/tree/12a7a703ed8069f0c4791844bbb807f2b2d5299c).
  This is a new source review, not the historical snapshot or installed runtime.
- Historical comparison/accounting remains in the existing evaluation documents
  and external receipts. Do not substitute this offline work for those results.

## What Switchyard already has

`hermes_switchyard/combined_decisions.py` is shipped implementation, distinct from
`evaluation/turn_consolidation/pilot.py`. `register()` creates one broker only
when `shared_jev_skill_effort` is explicitly enabled, automatic skill loading and
adaptive effort are enabled, hosted-sanitized loading is selected, and both data
acknowledgements allow it. The manifest default remains **false**.

The facade combines original skill questions with medium/high effort ladders
and stakes in one validated physical request. The original skill consumer sees
only its original answers. Effort consumes the saved answer once, using the
existing effort policy and ceiling. The skill path owns physical-call usage;
the effort receipt does not claim a second provider request. Missing identity,
multiple physical requests, mismatched task/turn/route, sensitive input, expiry,
metadata, consent or incompatible ladders do not qualify for reuse.

This narrow implementation is limited to short clean foreground CLI turns with
`gpt-6-sol`; the effort middleware additionally requires `openai-codex` and the
supported request shape. It does not switch models. Desktop, subagent,
multi-stage and other-provider coverage must not be inferred from these tests.
The production broker is bounded to 32 pending records and 30 seconds; a clear
operation and lifecycle invalidation now protect in-flight producers, as covered
by the offline qualification checkpoint above.

Existing components to keep as owners:

- `client.py`: fixed provider endpoints, strict Jev response validation, budgets,
  redaction and Jev version handling, including 1.14.
- `automatic.py` and `two_stage_routing.py`: discovery, no-skill handling,
  catalog reduction, local fallback and actual Hermes skill loading.
- `reasoning_effort_adapter.py`: supported wire formats, effort ceilings,
  task/privacy gates and request mutation. No second effort middleware.
- `model_registry.py`, `model_policy.py`, `model_route_adapter.py`: approved
  model filtering and advisory receipts. The experimental model-routing pilot
  is not the production automatic router.
- `session_search_rerank.py`, `retrieved_screen.py`, source/browser selectors:
  existing relevance selection and optional local instruction screening. Keep
  their privacy gates, URL checks, receipts and fail-open behavior.
- Existing browser/desktop execution, native approvals, output preservation and
  compaction boundaries. These do not need a second Jev Skills plugin.

## Transfer capabilities, not duplicate modules

| Priority | Specific source capability | Integration decision |
|---|---|---|
| P1 | `jevkit/turn.py`: ask independent questions over one copy of turn text; return answers to their policy owners; stage two remains separate | Already embodied in Switchyard's shared skill/effort facade. Harden and qualify it rather than importing a second broker/client/hook. Never merge when the combined payload splits into multiple physical requests. |
| P2 | `jevkit/route.py` and the Jev Skills request hook: constrain candidates to the current provider, separate proposed/applied model, shadow mode, invalidation and user-pin handling | Reuse Switchyard's registry and **existing** effort request owner. First build a pure shadow consumer with explicit off/shadow/apply controls; shadow must never change model or effort or claim applied savings. Use trusted host pin provenance, not just default-model string comparison: an explicit pin to the default model must still win. No live router activation in this change. |
| P3 | `jevkit/rerank.py`: per-passage relevance plus injection judgment, bounded batches, local screening on every failure path, explicit unjudged/local-only states and exfiltration-URL checks | Extend the existing retrieval selector only after a new synthetic needed-fact/poison/benign holdout. Preserve required facts and original storage; report omitted/unjudged passages rather than silently dropping them. Do not copy broad regex sets without false-positive measurement. |
| P4 | `jevkit/webscreen.py`: structure-preserving full-content chunk screening; `jevkit/search.py`: result selection, evidence sufficiency and bounded next-query choice | Extend Switchyard's existing screening/selection owner, not a competing `transform_tool_result` handler. Keep injection safety separate from evidence quality. Measure primary-source coverage, factual support, benign loss and false sufficiency. The source's web screen deliberately does **not** use relevance scoring; do not claim generic relevance gains from its injection tests. |

Do not transfer the source's independent plugin lifecycle, provider/client
configuration, automatic skill hook, effort hook, GUI executor or compaction
pipeline. The source is MIT licensed (Steve Darlow); retain its notice if a
future change copies substantial source. This change adds original Switchyard
integration guards/tests rather than vendoring Jev Skills modules.

## Small improvement implemented

1. Capture the live route identity **before** the combined request and require
   the same identity when storing and consuming its result. Previously, an
   identity change during dispatch could label the old response with the new
   identity. The regression reproduced reuse before the fix and now observes
   independent effort fallback, with no shared receipt.
2. If route identity cannot be established before dispatch, send the original
   skill request without the three unusable effort/stakes questions.
3. Include `combined_decisions.py` in the release allowlist. The baseline archive
   could not import the plugin even with sharing disabled because `__init__.py`
   imports that module unconditionally. Existing package-coverage and archive
   import regressions now pass; no release was installed or published.
4. Add production-broker tests rather than relying only on the evaluation pilot:
   consume-once/concurrency, scope isolation, consent/privacy/metadata, both
   ladders, request identity/count, expiry/deadline, bounded state, clear,
   response-copy isolation, invalid identities and registration/default-off.
   Runtime fixtures also cover no-skill, provider failure and route changes.
   Hosted failure retains the existing local skill fallback and original effort.

## Evidence and boundaries

All new decision evidence is offline, using deterministic in-process transport
fixtures and production Switchyard policies, not served-model measurements.
For an eligible synthetic turn, sharing retains the same selected skill and
outgoing low effort while reducing physical fixture calls from **two to one**.
The unknown-identity path avoids **three** unusable questions (five to two in
the skill request). These establish request-count/payload-work savings, **not**
measured live latency, token savings, dollars saved or improved Jev accuracy.

Reproduction commands (test environment with native Hermes available):

```text
python -m unittest discover -s tests -p 'test_shared_jev*' -v
python -m unittest discover -s tests -p 'test_jev_combined*' -v
python -m unittest discover -s tests -p 'test_typesafe_model_resolution.py' -v
python -m unittest discover -s tests -p 'test_release.py' -v
python -m unittest discover -s tests -v
python evaluation/evaluate.py --validate
python scripts/check_portability.py
```

Use one compatible interpreter and dependency environment for the full suite;
inherited `PYTHONPATH` entries from a different Python ABI can contaminate native
subprocess tests. Run Ruff with the repository-pinned version, 0.11.13. Archive
tests use isolated fixture repositories; an uncommitted development tree is not
a published source revision.

## What remains before controlled activation

- A distinct public-synthetic holdout using the **shipped** facade in a disposable
  native Hermes lifecycle, not only emulated hook calls or the evaluation pilot.
- Large catalogs, combined-payload budget/split fallbacks, later stage-two calls,
  overlapping producers, cancellation while the first request is in flight,
  retries, cache hits and late/outage paths. Explicitly verify no stale
  post-cancellation result becomes consumable and no retry duplicates accounting.
- Demonstrate actual skill context injection, effort caps, privacy/no-egress
  behavior and single-owner hook registration in that lifecycle. Keep fail-open
  and abstention receipts distinguishable from successful sharing.
- An explicit isolated-profile activation/rollback decision, source/runtime
  provenance and useful live latency/quality evidence under a separately
  authorized bounded protocol. No such calls are authorized by this review.
- Resolve remaining full-tree hygiene findings rather than broadening scanner
  exemptions; preserve historical evidence and receipts unchanged.

Next development step: add a pure same-provider shadow routing consumer that can
reuse shared answers without adding another automatic hook or paid decision
request. Explicit manual pins must win, and shadow must leave requests unchanged.
The feature stays default-off until those gates and owner approval are complete.
