# Jev capability transfer: gap matrix and Stage P1 offline gate

**Owner decision:** 2026-11-10. **Scope:** evaluation only; no new paid calls, plugin
install, gateway restart, routing change, production deployment or test against
private user, employer or school data. This follows [the tracked decision in
draft PR #1](https://github.com/mobilebob/hermes-switchyard/pull/1#issuecomment-6773499231).

## Architecture clarification

**Switchyard already uses TypeSafe Jev** for fast decisions. The Hermes Jev Skills
repository integrates the *same* decision model through another plugin. The goal
is **one Switchyard integration that reuses valuable methods**, not competing
automatic Jev handlers or a head-to-head general ranking of Sol and Luna.

Source snapshots reviewed:

- Switchyard evaluation source: `0c0551eb57bc53f23798f93220efaed201e9f63f`
- Jev Skills source: `8316358bf60e98a21477582af829ef11c9cf52fb`
- Existing Switchyard pilot: `evaluation/jev_transfer/README.md`
- Switchyard skill hook: `docs/AUTOMATIC-INTEGRATION.md`
- Switchyard screening: `docs/FEATURE-EXPANSION.md`
- Jev Skills source: [hermes-jev-skills](https://github.com/kerpopule/hermes-jev-skills/tree/8316358bf60e98a21477582af829ef11c9cf52fb)
- Different historical source snapshot, installed files and currently loaded
  runtime are *not* interchangeable provenance claims.

## Capability gap matrix

| Capability | Already in Switchyard | Candidate / gap | Decision & test |
|---|---|---|---|
| Jev hosted decisions | TypeSafe/OpenRouter decision client, acknowledgements, privacy rules, receipts | Jev Skills uses Jev too, not another model | **Reuse** Switchyard client; never install a second production hook |
| Automatic skill selection | Local/hosted Jev shortlisting and actual Hermes `skill_view` load, including no-skill and failure receipts | Jev Skills suggests skill names with different privacy/input rules | **Keep Switchyard loader**; test skill-load correctness and no duplicate paid requests |
| Adaptive reasoning effort | Local and hosted choice bounded by user cap | Another Jev small-decision path can duplicate network work | **P1**: share first-stage Jev request between skill and effort using existing `turn_consolidation` evaluation pilot |
| Active same-provider model switch | Approved-model tool is **advisory**; separate `model_routing` feasibility adapter | Jev Skills can alter model in `llm_request` without changing provider connection | **P2 shadow**; explicit manual pin and private/consequential abstention; not enabled |
| Memory passage selection | Session-search Jev reranking plus opt-in local retrieved-text screen | Jev Skills also scores relevance and flags planted instructions in retrieved passages | **P3 synthetic only**; needed-fact recall, poison misses, benign loss, unjudged passages, private-egress check |
| Web-result screening | Local opt-in screen of search cards/DOM observations; may withhold benign quotations | Jev Skills local+Jev screening for `web_search`/`web_extract` | **P4 isolated**; do not register competing `transform_tool_result` handlers; attack catches and false positives |
| GUI/browser actions | `jev_computer_use` with action allowlist and verification | Jev Skills exposes next-action and browser chooser | **Defer** until P1–P4 show gains; preserve human/API authorization boundaries |
| Compaction/memory retention | Hermes-native compressor, existing local preservation pilots | Jev Skills handoff/turn-selection ideas have mixed evidence | **Do not import blindly**; require demonstrated recall improvement and zero lost mandatory context |

## Stage A2 stop and accounting

The *original* 48 case/model-pair plan stays frozen, but further direct-model
requests are **not approved**. The first Sol/`extract-c` canary is permanently
uncertain; the revised collector obtained **six** correct responses and two
matched pairs, both models correct. These are not a model quality ranking or an
independently authenticated provider-served-model study. Both external ledgers,
logs and manifests remain on the Hermes host; no replay or reset.

## P1: share the existing skill + effort request, offline first

The existing `evaluation/turn_consolidation/pilot.py` contains a `Broker`
that stores the first-stage Jev result under `(session_id, task_id, turn_id)`.
It can consume a shared decision only once when consent, task excerpt, deadline,
response identity, requested effort and candidate ladder match. The prototype
does not ship as a plugin, and uses private adapter interfaces. We will **not**
register it in production or promise its synthetic latency gain generalizes.

The first regression layer is intentionally narrow. It tests the pilot's local
broker with injected synthetic decision receipts, no credentials or network:

- [ ] One validated result can be consumed **once**; a second consumer must
      fall back without presenting a duplicate Jev request receipt.
- [ ] A different session/turn key cannot consume another session's result.
- [ ] Missing consent, privacy scan failure, metadata-incompatible input,
      mismatched requested effort/candidate ladder, missing request ID and
      expired deadline all abstain.
- [ ] Cancellation clears unconsumed state; bounded cache evicts old decisions.
- [ ] Concurrent consumers never duplicate the same shared receipt.
- [ ] Preserve the *actual* number of provider requests and distinguish
      avoided extra call from inferred dollar savings. Never silently invent
      token usage or substitute an answer after failure.

This is **not** sufficient for promotion. The next layer must add a new, distinct
public synthetic holdout across skill/no-skill/effort and policy-denied turns,
large skill catalogs, later stage-two requests, provider outage, and overlapping
sessions. It must verify Hermes' actual `skill_view` load, user effort ceiling,
e2e latency, cancellation, per-turn receipt identity, no private egress and
all retry/fallback paths, with independent source/runtime provenance.
Shadow comparisons must not apply a model change.

## Promotion / fail-closed criteria

- Preserve the original single-owner Switchyard plugin/hook lifecycle.
- No second Jev call in the eligible combined path; no silently lost skill load.
- No private/school/employer text sent to hosted Jev.
- No scope crossing or reuse of stale decisions across tasks/sessions/turns.
- No override of manual `/model`, requested reasoning cap or native approvals.
- Uncertain usage remains unknown rather than zero; failure remains observable.
- No automatic merge or gateway/profile/config changes. Require an explicit
  later owner decision, independent held-out result and rollback plan.

The accompanying `tests/test_jev_skills_integration_gates.py` tests the narrow
broker invariants **offline**. It does not prove host hooks, network behavior,
provider billing or live performance.
