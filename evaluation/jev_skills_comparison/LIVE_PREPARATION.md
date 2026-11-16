# Stage A live transport preparation

This is a separate evaluation driver, **not production model routing**. The
production plugin, gateway hooks, profiles and model-selection configuration are
unchanged. The existing offline collector remains offline-only. `live_driver.py`
can prepare a fully verified plan without dispatch, then execute that exact plan
through the isolated broker and one durable ledger.

## Verified first-party billing and guarded execution

The [official billing article](https://help.typesafe.ai/articles/5187202169-how-much-does-jev-cost)
states that USD 0.042/M input tokens applies to every account, output is free,
and there are no per-request, failed-request or minimum fees. The
[model documentation](https://docs.typesafe.ai/models) states that state is
ingested once and that the default 64k request limit covers state plus all
questions. Long context is off by default and requires its special header.
The broker never sends that header, media, fallback or additional service calls.

`billing_evidence.py` verifies the saved first-party URLs, resolved origins,
source hashes and significant tariff/context clauses in both the original
source and extracted text. The driver does not accept a boolean-only pricing
approval. The conservative 262,144-token ceiling exceeds the default context;
at the documented tariff its upward-rounded input cost is 11,011 micro-USD,
below the permanently consumed 20,000-micro-USD reservation. Twenty-four such
reservations total USD 0.48, below the USD 0.50 authorization.

```text
python evaluation/jev_skills_comparison/live_driver.py --prepare \
  --output /owner-controlled/durable-trial \
  --reviewed-plan /reviewed/native-outbound-review.json \
  --billing-evidence /verified/first-party-sources \
  --switchyard-source /pinned/switchyard --jev-skills-source /pinned/jev-skills

python evaluation/jev_skills_comparison/live_driver.py --execute \
  --output /owner-controlled/durable-trial --initialize-ledger
```

Use `--initialize-ledger` exactly once, and only when no trial ledger exists.
The existing `TYPESAFE_API_KEY` is read by the parent broker only; no secret goes
into child arguments, payloads or logs. Resume without the initialization flag.
Previously attempted or failed cases are never replayed, even if a response was
lost before observation recording. The driver stops new dispatch after a
provider/transport failure or changed prerequisite. Code, frozen policy,
billing evidence and approval-file identities are rechecked before dispatch.
Each observation is flushed/fsynced; unresolved ledger reservations survive
interruption. Billing confirmation remains distinct from provider-usage-based
estimates. No main-model request or route application exists in this driver.

## Limits and prerequisites

The trial authorization is at most 24 external Jev requests and USD 0.50 total,
zero retries, zero main-model evaluation requests, no model switching, and
public synthetic data only. The broker fixes the classifier to direct TypeSafe
`jev-1.14.0` at `https://api.typesafe.ai/v1/systemone`. Both pinned native clients
have compatible custom-transport seams; do not use their normal network paths.

`live_preflight.py --inventory <snapshot.json> --output <new-report.json>` is
read-only prerequisite reporting. It never loads a credential or sends traffic.
An inventory or its boolean fields are **not authorization**. Proof references
and the complete payload approval must come from trusted owner/operator review,
not from model output or arbitrary incoming JSON.

An existing TypeSafe credential may be reused without a separate console login.
With explicit credential-reuse authorization and first-reserved-request
verification selected, account authentication can remain pending until that
request. Every other gate must already pass. The credential fingerprint and
account reference still bind the approved plan, transport and persistent ledger;
a different credential cannot reuse the approval. Authentication errors consume
the reservation and do not authorize another attempt for that case/arm.

Before any dispatch, establish:

1. An explicit, real same-provider model allowlist and current account/model,
   including suitable capability/context metadata and the intended price order.
   Cached model IDs establish discovery only, not availability or authorization.
2. Exact native request bodies generated from the unchanged frozen 12 cases and
   that same allowlist. Review the entire state, instructions and criteria, not
   just the task field. Record canonical whole-body hashes and pinned sources.
3. An account-applicable upper price and billable-input-token bound. Public
   pricing or a context-window size alone must not be promoted to this proof.
4. A durable owner-controlled ledger outside disposable worker homes, shared by
   both arms and all invocations. Never create a new ledger to reset this trial.

## Outbound and retry controls

The broker accepts only the fixed model and `model/state/questions` body shape,
at most three questions and 16,000 canonical JSON bytes. It rejects media and
credential fields recursively, task substitutions, altered/unreviewed full
bodies, and the active credential anywhere in the outbound body. Private-message
and restricted-content cases cannot send their task text, even if a supplied
approval artifact says otherwise. Text allowed by other cases is still subject
to the complete reviewed-body allowlist. These checks are not general-purpose
DLP or an authentication system for untrusted approval artifacts.

The only network operation is one fresh standard-library `HTTPSConnection`
and one POST. It has no pool replay, SDK network path, redirect following,
provider fallback or automatic retry loop. `Connection: close` is fixed and
caller headers are discarded. Switchyard's injected transport bypasses its
normal connection/rate-retry code. Jev Skills must use its injected transport
with `retries=0`; broker failures become its typed, nonretryable
`shadow_no_retry` error. A repeated callback cannot send again because of the
persistent unique case/arm reservation. Tests cover constructor/connect-path,
request-write, response-header, body-read and close failures, plus 302/429/529,
crashes, failed accounting writes and concurrent consumers.

## Credential-free native request capture

`live_worker.py` accepts only the unchanged frozen public task text and the
owner-approved `openai-codex:gpt-6.1-sol` / `openai-codex:gpt-6-luna` pair. It
verifies the exact clean source pin before loading the decision library, then
denies network/process operations and uses parent stdin/stdout IPC for native
transport requests. It has no real API credential or direct HTTP path. Private
and no-text cases are refused before source loading. Candidate descriptions and
reference price ordering are code-owned public metadata; the 4096-token context
floor is an evaluation budget, not a full advertised model limit, and reference
prices are not claimed to be Codex account billing.

Offline capture exercised all 12 cases in both arms: 20 native request bodies
and four local privacy exclusions, with no external requests. Every captured
body passed the fixed model/shape/size/field guards and exact frozen task check;
both arms received the same candidate IDs and task text. Questions and policy
APIs remain implementation-specific. Captured bodies are preparation evidence,
not observed model decisions or an active live trial driver.

The active authorization retains the USD 0.50 ceiling. Stale spending-waiver
flags are explicitly rejected, and price verification cannot be bypassed by an
approval JSON flag. The interrupted draft was preserved outside the repository
before that correction. Authentication can still be established on the first
reserved request once every remaining gate passes.

## Persistent accounting and monetary assumptions

SQLite uses `BEGIN IMMEDIATE` and `synchronous=FULL`; the request intent commits
**before connection construction**. A reservation permanently consumes one
request and 20,000 micro-USD (USD 0.02). Both arms share the same atomic count and
spending checks. Reopening never silently creates an empty ledger; exclusive
creation, matching authorization binding and duplicate-case refusal prevent
ordinary resume/retry budget resets. Deleting/copying the ledger is an owner
operation outside these process-level controls; this is not an OS sandbox.

At most 24 reservations total USD 0.48, leaving USD 0.02 below the authorized
ceiling. This is a reservation policy, **not a claim that billing is already
bounded**. The price proof must separately support the per-request upper bound.
The current policy requires at most USD 0.042 per million billable input tokens,
free output, no other fees and a verified billable-token ceiling of 262,144. At
that ceiling, upward-rounded input cost is 11,011 micro-USD, below the 20,000
micro-USD reservation. The request does not enable long context or images.
Do not infer the billing-token bound solely from those omitted features.

Connection errors, uncertain sends, HTTP failures, invalid responses and crashes
retain both reservations. Nothing refunds them, even after successful usage is
observed. A failed post-response accounting write leaves the original durable
intent unresolved. Provider `usage.input_tokens` is recorded when valid; the
calculated amount is a **usage estimate**, not a billed cost. Unknown billing is
reported as unknown, never zero for an attempted request.

## Revisions and current readiness

- Switchyard decision source: `cc45394c6c04627f6e62736f987fceb84117a6d9`.
- Jev Skills decision source: `8316358bf60e98a21477582af829ef11c9cf52fb`.
- The shared frozen case set is unchanged. Private/no-text cases must be recorded
  as local exclusions, not sent or replaced with invented provider decisions.

The live driver does not install Jev Skills or activate production routing. It
uses the approved real pair, exact reviewed native payloads, saved first-party
billing proof and existing TypeSafe credential. Authentication occurs on the
first reserved request. It preserves proposals, abstentions, local exclusions,
policy rejections, measured latency and ledger references; generated fixtures
are never fed into the live comparison as measured evidence.

Validation results and current blockers are recorded in draft PR #1. Generated
inventories, budget databases, raw observations, logs and package archives stay
outside the repository. No downstream task correctness or realized savings has
been measured by these preparation tests.
