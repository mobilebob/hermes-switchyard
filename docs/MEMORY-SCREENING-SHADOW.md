# Memory screening: shadow only

**In short:** an opt-in extension of `jev_session_search_rerank` can ask separate
memory-relevance and instruction-injection questions inside its existing primary
Jev request. It records observations, never applies them. It defaults **off**;
this milestone does not authorize production activation or demonstrate served-Jev
accuracy, latency, or cost savings.

## One owner, no additional screening request

`session_search_rerank.py` still owns selection, ordering, match-message anchoring,
request accounting, deadlines and FTS fallback. `retrieved_screen.py` still owns
local screening, with its existing independent shadow/enforce option. The new
`memory_screening.py` contains a bounded planner, per-operation client facade and
advisory consumer, not another provider client or memory store.

For approved inputs the facade augments only the existing `session` question.
It preflights the same client's strict question validator, serialized request
size and physical batching logic. It adds only whole passages that fit one
physical request, with a 1,024-byte size reserve. The query is reused from shared
state. Dependent `match_message` selection remains a later, unchanged operation;
it is neither merged nor counted as an avoided call. A full batch, unsupported
preflight or insufficient deadline cannot trigger a dedicated screening call,
new retry or separate spend budget. Unexamined passages remain unjudged.

In shadow mode the original validated ranking answers and metadata return to the
retrieval owner. Advisory relevance/injection scores cannot withhold, reorder,
delete or rewrite memories, override local warnings, or grant tool authority.
Adding questions is not proof that a real model would return identical ranking
answers: output equality here is an offline protocol/control measurement.

## Authorization and native scope

Settings under the plugin's existing settings namespace:

```yaml
memory_screen_mode: "off"   # off by default; only other implemented mode: shadow
memory_screen_policy: {}   # no source authorization by default
```

An operator-owned policy has exactly `epoch`, `profile`, `queries` and `sources`.
`queries` maps exact query digests to `public` or `sanitized`; `sources` does the
same for each whole raw card, including its anchors. The policy must match the
effective native profile. Digests are SHA-256 of
`json.dumps(value, sort_keys=True, allow_nan=False).encode()`; `digest()` implements
that contract. Epoch/profile/policy/source/route changes invalidate observations.
Do not copy classification from memory prose, card claims or tool arguments.
These attestations are authorization inputs, not an automatic DLP classifier.

Both the normal acknowledgement and exact operator approvals are required.
Missing, unknown, private, employer/school, regulated or credential-like sources
stay local. The full bounded input is scanned before display clipping; a changed
redaction result is not permission to export the original. This includes the
existing reranker email/phone/token redaction, applied to full fields and combined
passages: inputs that would change under it stay local, even if attested public.
Existing local
screening still runs even when hosting is refused or unavailable.

**The privacy gate covers the original ranking payload too.** If any source or
query is ineligible, the operation uses the existing local FTS fallback rather
than exporting the legacy ranking state and withholding only the extra scores.
This safe fallback can differ from a formerly unrestricted hosted selection.
Holdout privacy comparisons therefore use the existing local fallback as their
reference, not a privacy-violating hosted request.

Plans require native profile, session, task and turn identity. The pinned native
registry handler supplies only task/session, while the supported
`tool_execution` continuation supplies the turn. One pass-through, context-local
scope binder bridges that omission for this retrieval tool only; it calls the
continuation once with the identical arguments and restores its binding in
`finally`. It adds no retrieval transform, pre-LLM hook, request routing owner or
Jev call. Native missing/conflicting scope abstains; tool-argument fields cannot
supply it. This does not add the manual-pin/account provenance still missing for
model-routing activation. Hosts without this middleware safely stay local when
their handler lacks complete scope. Missing lifecycle invalidation disables
hosting rather than silently restoring unrestricted legacy hosting.

## Reference: limits and observations

- At most 32 cards, eight anchors per card, 1,200 characters per field and combined
  passage, and 96,000 examined field characters. Oversize/unsupported input stays
  local with an explicit bounds/schema/overflow reason and unknown coverage;
  it is not silently truncated and labelled clean.
- Query length must fit both the existing retrieval bound and 1,200 characters.
- At most 16 passages considered for augmentation; actual admission may be lower
  because of the existing client's byte/question budget.
- Passage identity includes the whole raw card digest and its card/anchor slot;
  duplicate session IDs cannot project scores across different source revisions.
- The original aggregate request/deadline budget remains authoritative. Screening
  additionally expires at the requested deadline or 30 seconds, whichever is less.
- Pending generations and ephemeral observation receipts are each capped at 32.
  Every invocation has its own opaque generation, including parallel calls with
  identical session/task/turn IDs. Generations are never shared across searches.
  Receipts contain digests, counts and advisory scores, not query/passage text;
  there is no new receipt file, ledger replay or persistent memory rewrite.
- Only a validated one-physical-request response with a nonempty request ID can
  supply observations. Missing/partial/malformed scores do not become zero.
  The original strict validator and FTS failure path remain in charge.
- Cancellation/unload, source/epoch/profile/route changes and deadline
  expiry discard late observations, not validated primary ranking responses or
  their physical request accounting. Pre-dispatch authorization is still checked.
  Consume/finish are idempotent. Observer
  failures invalidate observations without changing the retrieval owner's answer.
- `memory_screen` runtime status reports mode, native scope-bridge availability
  and the last in-memory receipt. `applied` is always false; there is no apply mode.

## Qualification and limitations

The new frozen public synthetic corpus has 32 independently labelled cases.
Each is compared with local enforcement off and on, for 64 protocol comparisons.
The response fixture receives neither case IDs nor gold labels and intentionally
uses limited lexical logic. It is not Jev and is not a claim of detector quality.
The initial run had zero output mismatches, source mutations, extra fixture
requests or private/unknown-source fixture dispatches. The diagnostic fixture
still had two attack misses and two benign false positives; those failures are
retained, not relabelled into successes.

Packaged-plugin checks use fresh disposable native Hermes processes, synthetic
credentials, blocked sockets and explicit default profile selection. They cover
installed defaults/restart, the actual native tool dispatcher and scope bridge,
missing approvals/scope, private queries/sources, forged card claims, local
withholding, outages, cancellation, changed source/credential identity, deadlines,
bounds, batching, anchor accounting, named-profile isolation, native concurrent
searches both across sessions and within one turn, PII payload refusal and
observer failure. Positive cases are native execution with a synthetic provider
transport, not a live Jev or production-profile qualification.

Before considering activation: review the code-owned classification provisioning
and retention policy; extend independent recall/benign-quotation/attack evidence;
qualify the intended native host/profile. Any served-model quality, latency or
cost study requires separate explicit authorization. Do not activate enforcement
from these fixture probabilities. Shared skill/effort decisions and model routing
remain separate default-off features; their prior activation blockers are unchanged.

Developer commands and evidence boundaries are in [CONTRIBUTING](../CONTRIBUTING.md).
The original [design](MEMORY-RELEVANCE-INJECTION-DESIGN.md) is retained as the design
record, not an assertion that live quality has been established.
