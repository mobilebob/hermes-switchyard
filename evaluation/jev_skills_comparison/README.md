# Jev Skills comparison — isolated evaluation

**Evaluation only:** no production plugin installation, new hook, profile change, paid call, or gateway restart is part of this branch. This is an offline policy screen, **not** a measured Jev model-routing result.

## Revision and implementation boundaries

- Switchyard snapshot: `cc45394c6c04627f6e62736f987fceb84117a6d9`.
- [Jev Skills](https://github.com/kerpopule/hermes-jev-skills) snapshot: `8316358bf60e98a21477582af829ef11c9cf52fb`.
- Installed files, source HEAD, and code loaded by a running gateway are separate facts. Record exact **observed runtime/source identity** on each eventual test.
- Switchyard currently automatically **loads** matched skills with Hermes' `skill_view`; Jev Skills' Hermes plugin **suggests** skill names to load. Running both automatic skill handlers can duplicate calls.
- Switchyard can change **effort**, but its approved-model routing is advisory (`applied: false`). Jev Skills routes the model within its already-established provider connection via `llm_request`, and supports explicit routing shadow mode.
- Switchyard already has a native, non-production same-provider model routing pilot under [`evaluation/jev_transfer/`](../jev_transfer/README.md). Its synthetic results are feasibility evidence, not production qualification or a head-to-head Jev Skills comparison.

## Stage A: model routing shadow comparison

1. Pin both repository source SHAs and capture installed/runtime provenance separately.
2. Prepare two independent temporary Hermes homes. No shared default/Coble gateway, messaging adapters, scheduler, or actual production tools.
3. Keep all decisions **non-applying**. On Jev Skills use `/jev routing shadow`; on Switchyard capture advisory routing-tool responses. Both can be represented in the comparison as `mode: shadow` / `applied: false`, but they are *not* the same configuration switch.
4. Use approved, same-provider model candidates only. Exclude manual pins, consequential/security changes, private content, unsupported provider boundaries, tool-dependent work, or missing context from the *switchable* case set.
5. Record each attempted case, including abstentions, failures, budget/latency, provenance, privacy egress, and number of observed external requests. Do not infer that a call was free just because a request failed.
6. To establish value, replay an independent held-out set with an objective downstream correctness check; include provider usage/cost, classifier latency, execution, fallback, and verification. Shadow-mode predictions cannot establish realized savings.

The 12 public, synthetic `cases.json` entries are a *conservative policy screen*. They are **not** labels saying which cheaper model can correctly perform a task.

### Offline gate

`compare.py` is stdlib-only. It accepts operator-collected JSONL evidence; it makes **no** Jev/model calls, performs no action, and never reads private Hermes state. No example observation file is committed to prevent fixtures from being mistaken for actual measurements.

Each observation has this structure (all values below are **illustrative** and not a measurement):

```json
{"case_id":"routine-extract","arm":"jev-skills","source_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","mode":"shadow","applied":false,"would_switch":true,"proposed_model":"same-provider-approved-small","egress":"redacted_text","latency_ms":410,"external_calls":1}
```

`arm`: `switchyard` or `jev-skills`; `egress`: `none`, `coarse_features`, `redacted_text`; `source_sha`: actual observed 40-hex source revision. `external_calls` counts observed requests, **not** billing. Egress classification must come from trusted observation, not a guessed label.

```text
python3 evaluation/jev_skills_comparison/compare.py --observations /path/to/shadow-observations.jsonl
python3 -m unittest discover -s tests -p test_jev_skills_comparison.py -v
```

Incomplete case/arm coverage, unsafe switch proposals, privacy excesses and malformed evidence fail the gate. Passing is *only* a complete synthetic shadow policy screen; output always says `promotion_authorized: false`.

## Collector: prerequisites and offline execution

`collect.py` has **no live transport or automatic switching mode**. Its default
is a read-only preflight: verify the two exact clean checkouts, the 12 frozen
cases and the explicit candidate catalog, then list the missing live prerequisites.
The included `catalog.json` approves only `fixture:small` and `fixture:large` for
**offline simulation**, not any real provider, account or model.

```text
python evaluation/jev_skills_comparison/collect.py \
  --switchyard-source /path/to/pinned-switchyard \
  --jev-skills-source /path/to/pinned-jev-skills
```

A separate owner-approved experiment needs explicit Jev request **and monetary**
caps, timeouts/retries/uncertain-dispatch accounting, approved provider account
and real model IDs, and approved synthetic outbound payloads. A reviewed live
transport must be added separately. There is intentionally no flag that enables
paid calls in this collector. No main-model request is needed to prepare it.

### Reproduce the offline wiring check

Use an existing test interpreter. Check out each revision above into separate
clean disposable repositories; do not point at an installed plugin or import a
Hermes plugin hook. No dependency installation is required by the collector.

```text
python evaluation/jev_skills_comparison/collect.py \
  --switchyard-source /path/to/pinned-switchyard \
  --jev-skills-source /path/to/pinned-jev-skills \
  --offline no-response --output /path/to/new-no-response-run

python evaluation/jev_skills_comparison/collect.py \
  --switchyard-source /path/to/pinned-switchyard \
  --jev-skills-source /path/to/pinned-jev-skills \
  --offline synthetic-simple --output /path/to/new-fixture-run

python evaluation/jev_skills_comparison/compare.py \
  --observations /path/to/new-fixture-run/observations.jsonl
```

Each run is 12 cases × 2 arms in 24 fresh subprocesses with separate temporary
homes, allowlisted environments (no inherited credentials or `PYTHONPATH`),
bytecode disabled, no production tools, and Python network/process guards. This
is defense in depth for these pinned trusted sources, **not an OS sandbox for
arbitrary code**. Workers are decision-only library adapters, not complete
Hermes `llm_request` hook or gateway integration tests.

Both arms receive byte-identical tasks and the same provider, approved candidate
identities, context limits and relative price ordering. The common adapter
records one current model. Switchyard is called through
`recommend_approved_model`; Jev Skills through `jevkit.route.decide`. Candidate
metadata is translated to their different APIs; this does **not** make their
classifier questions, thresholds, or safety policies identical. The `may_switch`
policy labels are used only by the independent safety screen, not as classifier
answers. Jev's API has explicit current-model/manual-pin inputs; Switchyard's
advisory API does not. The same manual-pin task text reaches both, and the
independent safety envelope rejects any forbidden proposal. This API asymmetry
must not be presented as a measured production regression.

- `no-response`: injected classifier unavailability; measures fallback wiring.
- `synthetic-simple`: explicit generated classifier answers, not inference,
  quality labels, an observed Jev response, or evidence a cheap model succeeds.
- Every row records proposal, abstention/reason, source SHA, input/catalog hashes,
  safety decision, attempted stub calls, external provider requests and latency.
  Offline provider requests are empty, external calls are zero, and egress is
  `none`; simulated requests are counted separately, not treated as paid calls.
- Latency includes process start, imports and offline decision logic. It is not
  Jev inference latency or production end-to-end timing.
- Raw unsafe switch proposals remain visible to the comparator even when the
  independent safety envelope rejects them. Nothing is applied in either arm.
- The manifest records exact source identity, catalog, interpreter, worker hash
  and prerequisites. Source identity is checked before and after execution;
  partial output and worker failures never count as successful coverage.

The collector automatically reparses persisted JSONL and writes
`comparison.json`. Schema-v2 comparison validates pinned sources and frozen
input/catalog identity, rejects mixed evidence modes, unapproved candidates,
non-shadow application and impossible offline egress/accounting. Fixture runs
are labeled `offline_fixture_only` or `offline_no_response_only` when otherwise
clean; unsafe proposals, missing coverage and worker failures remain blocked.
The collector and comparator return zero for valid, complete offline-only
results, **without** claiming measured inference or promotion; blocked/incomplete
results return one, malformed evidence returns two. The forced-simple scenario
can therefore return one even though collection itself completed. Legacy
schema-v1 operator evidence is retained for compatibility and has weaker provenance validation; it is not a
collector receipt. `promotion_authorized` is always false.

Generated manifests, observations and logs belong outside the repository. See
[Stage A findings](STAGE_A.md) for actual execution results and remaining limits.

## Isolated live transport preparation

The separate [live preparation guide](LIVE_PREPARATION.md) describes the fixed
TypeSafe broker, exact public-payload gate, durable shared request/spend ledger,
no-retry adapters and first-reserved-request authentication. These modules do
not add a live flag to `collect.py` or activate a trial. An approved budget and
an available credential are not substitutes for an approved real candidate
allowlist, verified outbound bodies and an enforceable monetary bound.

## Stage B: memory filtering

Compare Jev Skills memory filtering to Switchyard's session-search rerank as distinct features. Use fake passages containing known facts and planted instructions. Measure fact recall, unsafe instruction promotion, benign loss, coverage, latency, egress and fallback. Do not send customer memory to Jev.

## Stage C: web-result screening

Compare Switchyard's optional local pattern screen against Jev Skills' local-plus-Jev screening using frozen synthetic public web results. Measure attack catches, benign false positives, clean evidence withheld, fallback and end-to-end latency. Never add a competing production `transform_tool_result` hook without isolation tests.

## Promotion requirements

A separate owner-approved change would require: safe controlled tests, exact loaded source identity, comparable evaluation on genuinely held-out tasks, no security or privacy regressions, clear provider and model account authorization, measured overall benefit, approved API cost limits, rollback, full offline regression, and independent verification. **Do not activate Jev Skills alongside Switchyard on the existing shared gateway as part of this evaluation.**
