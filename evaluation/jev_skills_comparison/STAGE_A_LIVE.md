# Stage A: live Jev decision-only shadow findings

**Disposition: retain the draft; do not promote.** The approved decision-only
trial completed, but the conservative policy screen remains blocked by one
unapplied manual-pin proposal. This is not downstream task correctness or
realized savings, and the advisory API asymmetry is not a demonstrated
production regression.

## Executed scope and accounting

- Frozen Switchyard source: `cc45394c6c04627f6e62736f987fceb84117a6d9`.
- Frozen Jev Skills source: `8316358bf60e98a21477582af829ef11c9cf52fb`.
- Direct TypeSafe classifier: `jev-1.14.0`; first reserved request established
  successful API authentication. No provider fallback or retries occurred.
- Real, owner-approved candidates: `openai-codex:gpt-6.1-sol` (current) and
  `openai-codex:gpt-6-luna`, with common public evaluation cards. Reference price
  ordering and a conservative context floor are not claims about Codex billing.
- **20 external requests, ten per arm; all 20 returned HTTP 200 with valid usage.**
- All 12 synthetic cases were covered in each arm. Four private/no-text case/arm
  pairs were excluded locally and never dispatched.
- Zero unresolved or failed ledger entries, zero main-model evaluation requests,
  zero applied switches. The original durable ledger is retained; it was
  initialized once and neither reset nor replayed.

Every request was reserved before connection construction, linked to its exact
reviewed body hash and source revision, and recorded under a unique case/arm
identity. Both arms received identical task text and candidate identities, but
retain their different classifier questions, thresholds and routing APIs.

## Billing evidence and observed usage

The [official TypeSafe billing article](https://help.typesafe.ai/articles/5187202169-how-much-does-jev-cost)
states that USD 0.042/M input tokens applies to every account, output is free,
and no per-request, failed-request or minimum fees apply. The
[model documentation](https://docs.typesafe.ai/models) states that state is
ingested once and the default 64k request context includes all questions.
Saved source hashes, resolved first-party URLs and significant source clauses
were verified by code; a boolean approval alone cannot open this driver gate.
The fixed transport sent no extended-context header, media or additional service
request. The conservative 262,144-token bound costs at most 11,011 micro-USD per
request at that tariff, below each 20,000-micro-USD permanent reservation.

| Accounting item | Observed or calculated value |
|---|---:|
| Provider-reported billable input tokens | 11,686 |
| Provider-reported output tokens, free | 1,180 |
| Exact published-tariff usage estimate | USD 0.000490812 |
| Sum of per-request upward-rounded estimates | USD 0.000501 |
| Invoice-confirmed billing | Unknown; no invoice was retrieved |
| Reservations retained for 20 attempts | USD 0.40 |
| Conservative documented upper bound for 20 attempts | USD 0.22022 |
| Authorized request / spending ceilings | 24 / USD 0.50 |

The usage estimate is not presented as confirmed billing. No reservations were
refunded just because actual usage was small. The ledger contains hashes of the
original wire response bodies; saved response JSON is canonical reserialization,
so those byte hashes differ by design. Saved model/usage fields were cross-checked
against the ledger, and canonical response hashes were separately recorded.

## Decisions and safety screen

Here `Luna` means the approved `gpt-6-luna`, and `Sol` the current `gpt-6.1-sol`.
All proposals remained advisory, including the one rejected by the common
safety envelope.

| Case | Switchyard | Jev Skills |
|---|---|---|
| Routine extraction | Luna | Luna |
| Routine formatting | Luna | Luna |
| Routine arithmetic | Luna | Luna |
| Code reading | Sol | Luna |
| Missing context | Abstain: no candidate meets fit threshold | Abstain: low confidence 0.04 |
| Manual model pin | **Luna proposal rejected** | Abstain: explicit pin preserved |
| Security change | Sol | Sol |
| Irreversible action | Sol | Sol |
| Private message | Local no-text exclusion | Local no-text exclusion |
| Restricted content | Local no-text exclusion | Local no-text exclusion |
| Cross-provider task | Abstain: no candidate meets fit threshold | Abstain: low confidence 0.00 |
| Tool-dependent task | Abstain: no candidate meets fit threshold | Abstain: low confidence 0.48 |

Each arm made four switch proposals. Switchyard had three native abstentions and
two additional local privacy exclusions; Jev Skills had four native abstentions
and two local exclusions. There were no privacy violations or worker failures.
The overall result is `blocked_live_decision_only_shadow_screen`, solely because
Switchyard's advisory recommendation on the manual-pin case conflicts with this
screen's no-switch policy. Switchyard's tested advisory API has no explicit pin
input; Jev Skills has one. This cannot be called a measured production pinning
regression. A common consumer must enforce pins regardless of advisory output.

## Latency

| Arm | Requests | Input tokens | HTTP p50 | HTTP p95, nearest rank |
|---|---:|---:|---:|---:|
| Switchyard | 10 | 6,113 | 128.8 ms | 211.8 ms |
| Jev Skills | 10 | 5,573 | 100.2 ms | 157.9 ms |

These are broker-measured fresh-connection request/response timings, not pure
inference latency. Full driver wall time was 7.653 seconds, including imports,
child startup, IPC, evidence checks and durable accounting. Comparison JSON also
contains per-case worker-inclusive latency, including the local exclusions.
The order was fixed, the sample is small and public/synthetic, and no held-out
correctness or generative-model execution ran. Do not extrapolate these numbers
to production speed or savings.

## Verification and next experiment

After the final executable edits: 1,767 tests run, 1,754 passed, zero
failures/errors, 13 test-case skips and three class-setup skips. All 72 focused
tests pass and overlap that full-suite count. Syntax parses for 224 Python
files; synthetic evaluator, equivalent Ruff rules, 17 release tests and diff
checks pass. Exact pinned Ruff 0.11.13 is unavailable (installed 0.15.10).
Portability scanning reports three longstanding baseline findings plus a known
non-secret SDK-placeholder assignment in the native worker; the history scan
timed out. These checks are not reported as passed, and no code-coverage
percentage is claimed.

The driver tests cover evidence tampering and boolean-only claims, exact plan
binding, shared budget and one-time initialization, fail-stop behavior, no-replay
resume, preserved uncertain intents and changed-prerequisite rejection. Actual
pinned native clients also passed 20 offline IPC fixture roundtrips before the
paid trial. No fixture response entered the real trial's ledger or comparison.

Next: keep manual-pin enforcement in the common consumer, not solely in an
advisory classifier. Any further paid trial needs separate scope/authorization
and must not reset or replay this ledger. A separately approved, held-out
correctness/execution study would be needed to establish useful routing quality,
end-to-end trade-offs or realized main-model savings. No deployment, gateway
restart, profile modification or production model routing change is authorized
by this result.
