# Design record: Jev-assisted memory relevance and injection filtering

**Shadow implementation is now present, default-off.** See the
[memory screening guide](MEMORY-SCREENING-SHADOW.md) for the implemented contract,
native scope bridge, qualification boundaries and remaining limitations. The
proposal below is retained as the design record, not a live-quality claim.
Native provenance qualification alone does not authorize model routing or
external disclosure of memory.

## Existing owner and integration point

Keep `session_search_rerank.py:rerank_session_search` as the sole owner of session
card normalization, selection, match-message anchoring, request budgets and FTS
fallback. Keep `retrieved_screen.py:screen_shortlist` as the local instruction
screen. Its current shadow/enforce setting and full bounded-field examination
must remain independent of Jev availability.

The existing `_rerank_session_search` operation asks a `session` choice question;
`_pick_match_message_id` can make a later, dependent anchor choice. Extend the
first decision payload only when independent passage questions fit the same
physical request. Do not install `transform_tool_result` or another automatic
memory hook, and do not merge dependent anchor verification into stage one.
Do not reinterpret an old session choice as per-passage relevance or safety.

## Proposed data contract

The retrieval owner constructs an immutable plan from the query, existing cards
and their existing stable passage/anchor IDs. Each planned passage has:

- A stable opaque ID and source digest, preserving session/message pointers.
- Whole bounded text for screening; the existing clipped presentation stays a
  separate field. Never call clipped or unexamined text clean.
- A code-owned source data class, source scope/profile digest and consent epoch.
- A local verdict and an explicit eligibility/rejection reason.
- Jev relevance and injection fields that initially read **unjudged**, not zero.

Proposed operations (design interfaces, not shipped APIs):

1. `plan_memory_screen(query, cards, policy, budget)` produces eligible passage
   questions plus locally retained/unjudged passages. It never calls a provider.
2. The existing retrieval owner asks its existing session-choice question and
   independent eligible passage questions through its existing DecisionClient.
3. `consume_memory_screen(plan, validated_response, current_scope)` creates
   advisory verdicts. It does not change storage or grant action authority.

The source class is metadata supplied by the trusted retrieval owner, not text
claiming that a passage is public. Account/profile labels are not proof of data
classification. Unknown, private, employer, school, regulated or credential-like
material stays local. Public/sanitized consent is required for both query and
passages. Local pattern scanning is defense in depth, never DLP proof. Default
behavior must remain off/local-only until explicitly approved.

## Question semantics and call budget

For each eligible passage, ask two independent bounded questions:

- **Relevance:** does this passage contain facts needed to answer the actual
  recall query? Semantic usefulness is not truth, trust or authority.
- **Instruction injection:** does this retrieved text attempt to direct the
  assistant, override policy, impersonate a privileged role, solicit secrets or
  cause unauthorized external actions rather than provide recalled facts?

Send the query once in shared state. Keep passage IDs stable across question
names and returned verdicts. Use the existing strict response validator; missing
or malformed answers remain unjudged. Do not project scores from another passage,
turn, user, profile, clipped suffix or source revision.

The first implementation should add **no physical request beyond the existing
retrieval operation**. Preflight the augmented request using the existing request
size/question-batch logic and remaining aggregate deadline. If it would split,
keep the original ranking request and mark extra passages unjudged. No speculative
second screening call, hidden retry, or fresh spend budget. Existing dependent
anchor selection keeps its existing accounting and is not called an avoided call.

## Initial policy: shadow only

Add an explicit off/shadow mode inside the retrieval owner. Do not add enforce
or auto-delete in the first milestone. Shadow records proposed inclusion and
warnings but returns the same cards/order/context as the baseline owner. Existing
local enforcement, when already enabled, must still run on failure paths.

Jev cannot override local hard warnings or turn instructions from memory into
system/developer authority. A high relevance score cannot make an injected passage
safe. A low relevance score cannot delete mandatory context, explicit owner
constraints or the only retained source pointer. No destructive edits to memories
or changes to native compaction/retention are authorized.

Future enforcement would require an independent owner decision and separate
recall/false-positive evidence. Any withholding should retain a minimal notice,
source pointer and explicit reason rather than silently erasing a needed fact.

## Failure, privacy and lifecycle rules

| Condition | Required outcome |
|---|---|
| Consent absent, source class unknown/private, query unsafe | Local-only screen and normal safe retrieval fallback; no hosted payload |
| Provider outage, deadline, malformed/partial response | Preserve ordinary FTS/ranking fallback; keep local screen active; mark unjudged coverage |
| Payload/field/passages exceed bounds | Explicit overflow/unjudged count; do not claim full coverage or add a paid call |
| Cancellation, replaced query, source edits, profile/session change | Invalidate the plan; reject late/stale verdicts; never replay or reset accounting |
| Benign quotation of an attack instruction | Record false-positive evidence in shadow; do not silently broaden an exemption |
| Relevant injected passage or indirect URL exfiltration | Relevance does not defeat the instruction warning; no tool or network authority |

Bind plans to profile/session/turn, query/source digests, consent epoch, provider
route identity and a bounded producer generation. Consume once. Use the existing
retrieval request/receipt owner and lifecycle invalidation; do not create a
competing automatic decision owner. Do not import the model-routing adapter's
missing pin/account claims as memory-data authorization.

## Offline qualification plan

Freeze a new public-synthetic holdout, separate from historical evaluation
ledgers. Include:

- Required-fact recall, semantic paraphrases, contradictory/stale memories,
  multi-fact queries, indispensable source pointers and mandatory constraints.
- Direct overrides, role spoofing, tool-output laundering, encoded/Unicode
  controls, exfiltration URLs, instruction hidden after preview truncation and
  attacks carried in anchor previews rather than the card title.
- Benign security documentation, quotations, negation and troubleshooting text.
- Missing/false public-source claims, private synthetic markers, malformed cards,
  overlong fields, unjudged passages, partial answers and provider failures.
- Concurrent profiles, cancellation during dispatch, source change during
  screening, replaced query, retry accounting and exact consume-once handling.

Report required-fact recall at fixed K/context budget, mandatory-context loss,
attack misses, benign false positives, passage coverage/unjudged fraction,
physical request count, exact fallback behavior and private-egress attempts.
Keep scoring labels independent of Jev verdicts; probabilities are not calibrated
truth. Reusing passage text with new IDs is not an independent holdout.

Before any activation: zero private egress, zero cross-scope/stale reuse, no lost
mandatory facts/pointers, shadow output identical to baseline, no duplicate hook
or physical request, and observable failures. A live quality/latency/cost study
would require separate explicit authorization; none is included here.

## Smallest implementation slice after review

First build only the pure planner and synthetic verdict consumer, with an exact
payload-fit/no-extra-call gate and rejection receipts. Integrate shadow-only
questions into the existing session-selection request only after those tests
pass. Do not implement a parallel memory plugin or a general memory rewrite.
