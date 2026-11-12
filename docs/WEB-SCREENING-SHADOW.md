# Web screening: shadow only

**In short:** Switchyard can opt in to bounded, advisory screening of public web
search snippets and extracted pages. It records relevance, evidence sufficiency
and instruction-injection judgments separately. It never changes a tool result,
withholds a source, rewrites a citation, follows page instructions or grants tool
authority. Installation defaults **off**. Automatic filtering is unavailable.

## Ownership and request accounting

The existing `transform_tool_result` callback owns the observation point. The web
observer returns no replacement, even on an error. There is no additional result
hook, pre-LLM callback, request middleware or plugin lifecycle. Native retrieval,
security guidance and existing local screening keep their original authority.
No Jev answer can clear a local rejection. Terminal output compaction and
cross-tool failure advice retain their existing, separate scopes.

Shared skill/effort and memory ranking answers do not have web evidence question
contracts. They cannot be reused as web relevance or safety answers. This
milestone makes at most **one additional physical Jev request per eligible web
tool invocation**, never one request per chunk. No retries, hidden second-stage
calls or unrelated-contract reuse are allowed. Duplicate callback delivery must
not spend again. Ineligible/default-off inputs add zero requests. Shadow mode
cannot reduce actual result processing: any potential saving is observational,
not a deployed token, latency or billing benefit. Within one unchanged approved
scope, exact question/chunk contracts may reuse a validated observation (including
singleflight waiters). Cache keys include the complete planned chunk set, question
schema, policy/profile/route identity and invalidation generation. Failures are
not cached. A duplicate native tool-call ID never spends again, even after failure
or cancellation. Bounded tombstones are not evicted to permit replay; after 128
invocations the observer abstains until a fresh plugin instance. These conservative
limits deliberately constrain any controlled shadow qualification.

## Operator authorization

Settings live in the existing plugin settings namespace:

```yaml
web_screen_mode: "off"  # only other implemented mode: shadow
web_screen_policy: {}   # no approved question or source by default
```

A standing data acknowledgement is necessary but not sufficient. Exact question
and source approvals must come from operator-owned configuration, never from a
page, search snippet, source label or tool argument claiming public access.
Missing, stale, private, authenticated or sensitive classifications stay local.
Neither this checkpoint nor its tests authorize production activation or billed
provider requests.

The policy contract is deliberately exact and operator-owned:

```python
# Compute locally; do not send the approval table to a provider.
from hermes_switchyard.web_screening import digest
approval = {"data_class": "public", "access": "public_unauthenticated"}
policy = {
    "epoch": "operator-review-v1",
    "profile": "default",
    "scope": {"session_id": approved_session, "task_id": approved_task,
              "turn_id": approved_turn},
    "question": approved_question,
    "queries": {digest(approved_question): "public"},
    "sources": {digest(whole_source_object): approval},
}
```

`scope` binds the actual native session/task/turn. The question is approved
separately by its exact digest; a search query must exactly match that question.
Extraction arguments must contain exactly the URLs represented by the approved
source objects. The existing hook does not provide a trusted user-question field,
so the question is supplied only by this operator-owned contract, not by pages.
`sources` binds each complete raw search/page object, including its source URL
and metadata. The only permitted data classes are `public` and `sanitized`,
paired with `public_unauthenticated` access. The approved question must be `public`;
a `sanitized` source is still subject to full privacy checks. `epoch`, profile and all approval
maps are checked again around dispatch/publication. Do not automate approvals
from retrieved content. Changed content requires a fresh operator review.
This strict provisioning is intentional and remains an operational deployment
blocker, not an invitation to auto-classify every result as public.

The observer checks whole bounded inputs before creating chunks. Existing secret
redaction is a prerequisite, not permission to export unredacted originals.
Inputs that fail privacy checks remain local; an unavailable redactor is not a
reason to bypass the check. Unknown/malformed envelopes and incomplete source
identities abstain rather than being labelled safe. Bounds limit work and do not
assert that unexamined text is clean. Limits are 96,000 serialized input bytes,
16 sources and 256 planned chunks. Text windows contain at most 1,000 content
characters with 128-character overlap; titles are separately bounded to 1,200
characters. At most 12 chunks and 50,000 request bytes are admitted into one
request. `planned`, `judged` and `unjudged` counters expose partial coverage.
Evidence sufficiency applies only to admitted chunks, never to the whole web.
Only four workers may be outstanding; the callback deadline defaults to 0.8 s
and cannot exceed 2 s. A noncooperative transport may finish later, but its result
is discarded, its client closes on completion, and it cannot trigger a retry.

## Interpretation and fallback

- Retrieved content is untrusted evidence, not instructions to the assistant.
- Relevance is not safety; a relevant page can contain an attack.
- A legitimate quoted instruction can resemble an attack; neither a local
  pattern nor an advisory model probability establishes intent.
- Evidence sufficiency is about the approved question and all admitted sources,
  not whether an isolated chunk sounds plausible. Conflicts and missing evidence
  must remain distinct from sufficient support.
- A malformed/partial response, provider outage, timeout, missing scope,
  cancellation, source/policy/route change or observer exception preserves the
  original result and falls back to no observation. Unknown is never clean.
- Receipts are sanitized, in-memory observations. Raw queries, page text, source
  URLs, credentials and user/session identifiers do not belong in receipts.
- Cancellation and unload invalidate outstanding observations through the
  existing shared lifecycle owner. They cannot authorize another request.

## Independent offline qualification

The frozen corpus, integrity manifest, evaluator and provenance statement are in
`evaluation/web_screening/`. Its response transport is a deterministic lexical
fixture, blind to gold labels and case identifiers. It is **not Jev**. Fixture
confusion counts are diagnostics of this synthetic setup, not live detector
accuracy, real latency or cost measurements. Labels and failures must not be
edited to improve a score.

Qualification measures separate injection/relevance/sufficiency judgments,
false positives, exact output and source retention, fallback and physical
fixture-request counts. A hypothetical whole-source filtering comparison shows
why removing suspicious text can destroy required evidence; actual shadow
outputs are never filtered. Packaged native lifecycle tests install the archive
in disposable homes with synthetic credentials and blocked sockets.

See [capability review](JEV-WEB-CAPABILITY-REVIEW.md) for the upstream comparison.
Before any limited activation, independently qualify provider quality and
latency under separately approved spend, review classification provisioning and
privacy, and verify the intended native host/profile. Automatic filtering needs
its own evidence-retention and adversarial qualification; it remains unavailable.
Shared decisions, memory screening and model routing keep their own activation
boundaries. A green offline suite is not an activation authorization.
