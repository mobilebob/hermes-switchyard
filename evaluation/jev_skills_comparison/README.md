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

## Stage B: memory filtering

Compare Jev Skills memory filtering to Switchyard's session-search rerank as distinct features. Use fake passages containing known facts and planted instructions. Measure fact recall, unsafe instruction promotion, benign loss, coverage, latency, egress and fallback. Do not send customer memory to Jev.

## Stage C: web-result screening

Compare Switchyard's optional local pattern screen against Jev Skills' local-plus-Jev screening using frozen synthetic public web results. Measure attack catches, benign false positives, clean evidence withheld, fallback and end-to-end latency. Never add a competing production `transform_tool_result` hook without isolation tests.

## Promotion requirements

A separate owner-approved change would require: safe controlled tests, exact loaded source identity, comparable evaluation on genuinely held-out tasks, no security or privacy regressions, clear provider and model account authorization, measured overall benefit, approved API cost limits, rollback, full offline regression, and independent verification. **Do not activate Jev Skills alongside Switchyard on the existing shared gateway as part of this evaluation.**
