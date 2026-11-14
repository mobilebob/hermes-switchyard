# Offline web-security remediation

**Status: BLOCKED for protective deployment.** No configuration flag enables
this experimental handler. Existing `web_screen_mode` remains default-off and
supports advisory observation only, not blocking. Do not wire a security filter
through optional result transforms or middleware that falls back to raw results.

## Changes and boundaries

- Hosted privacy screening canonicalizes bounded Unicode and percent-encoded
  text before checking email/private markers. Original content and exact approval
  hashes remain unchanged. This is targeted hardening, not complete DLP.
- The shared local retrieved-text warning detector covers additional role,
  agent-directed action, page-policy, citation and structured-instruction forms.
  These are deterministic indicators, not a general semantic classifier. A
  negative warning result does not establish safety.
- `web_security_gate.SecureWebRetrieval` is an **unregistered** owned-handler
  implementation, with no default transport, no new observer, and no live calls
  in its tests. It owns fetch, strict projection, local screening, one typed
  classification attempt and the safe output. Callback failure, missing or
  malformed judgments, timeout, cancellation, stale scope and incomplete coverage
  quarantine rather than return the fetched body.

## Owned-handler contract

Only an operator-owned `StrictPolicy` approves an exact question, exact public
source hashes and session/task/turn/profile/provider-route/policy-epoch scope.
A page's claim to be public or safe never establishes that approval. The policy
contains no credentials. The trusted classifier adapter is responsible for
pinning and verifying the actual provider identity before returning its typed
reply; this prototype includes no qualified hosted adapter.

The native envelope is exactly `{"results": [...]}`. A source is exactly
`{"url": ..., "title": ..., "content": ...}`; all fields must be plain strings.
Unknown envelope fields quarantine the operation; unknown source fields
quarantine that source. The handler does not silently inherit body coverage for
citations, descriptions, tool JSON or metadata. Only these three source fields
can reach output, and all three occur in the classified chunks. Original source
objects are never mutated. Unknown fields are not silently promoted to trusted
citations. Public HTTPS URL restrictions and whole-source privacy checks apply
before classification. The existing destination policy is reused, but actual
fetch-time DNS, redirects and SSRF protection remain the fetch adapter's separate
responsibility and must be qualified before deployment.

The handler requires full coverage, at most 12 chunks total, in one classification
attempt. Chunks contain 2,400 body characters with 128-character overlap plus
the title and URL. Each whole source is capped at 24,000 characters, including
its title and URL, and the serialized request is capped at 96,000 UTF-8 bytes.
Content beyond these budgets is quarantined, not truncated and released. The
frozen buried-fact source fits this budget. Larger benign documents still need
separately reviewed paging; do not silently add calls. Exact duplicate approved
snapshots are deduplicated before planning. An owner is bounded to 128
invocations, without retry. These are not physical-request or monetary caps.

Only exact typed replies with the current opaque request identity, one boolean
risk and relevance value per expected chunk, and a closed evidence enum can
produce a locally issued ticket. A provider cannot overrule local warnings.
An operator may separately review an exact already-approved public source as a
benign security example using `StrictPolicy.reviewed_warning_sha256s`. This
default-empty set is distinct from public-source approval and is bound to the
exact snapshot and owner scope. Page-declared quotation/review claims do not
confer an exception. Revisions lose review; privacy restrictions and positive
classifier risk remain vetoes even for a reviewed source. This manual review
path is not proof of automatic semantic benign-example recognition.
Tickets never come from page JSON, global `last_receipt` or a previous invocation.
Local scope, generation, exact source/question/projection digests and expiry are
rechecked before release. Tickets stay inside one invocation and have no replay
or serialized-consumption API. Independently covered clean sources may survive;
a flagged mixed document is withheld whole. A fully risk-negative source with
any relevant chunk remains usable even when other chunks are irrelevant;
unknown judgments never count as negative. If material was removed, an aggregate
`sufficient` judgment is downgraded to `insufficient`. Clean `insufficient` and
`conflicting` evidence retains those states. No outcome authorizes an action.

The entire fetch/classify path has a deadline no later than the inherited host
operation deadline. Host cancellation ContextVars are copied into the worker
and rechecked before fetch, classification and publication. Cancellation-probe
failures are denial, not a clean state. Cooperative clients see the effective
local deadline and stopping event through the existing client context API;
ordinary advisory clients retain their previous behavior. Envelope/request
byte budgets count actual UTF-8, not inflated ASCII escapes. Initialization
failures return quarantine and release any worker reservation. Cancellation and invalidation
suppress late publication; invalidation permanently revokes the owner. A
non-cooperative callback cannot be killed in Python. One daemon worker can remain
alive until it returns, keeps the owner busy and never publishes a late result.
Normal output contains fixed notices and no quarantined text, URLs, digests or
provider exception bodies. No raw-quarantine store is created; a future opaque
review-handle store would need its own access and retention controls.

## Native enforcement remains unavailable

`require_native_enforcement` refuses missing, advisory and merely claimed host
capabilities. Generic `transform_tool_result` errors/timeouts and execution
middleware errors can release original content; competing earlier string
transforms defeat quarantine precedence. This implementation does not patch
Hermes core or claim to repair those semantics.

The unregistered `pre_tool_call` method illustrates a deny-by-default tool policy
for a dedicated public-information session: only the exact secure-retrieval tool
in the approved session/task/turn can pass. All alternative retrieval, browser,
connector/MCP, terminal, file-read and upload tools are blocked regardless of
classifier confidence. Native authorization fixtures exercise this callback,
including failure/timeout block behavior. This is not a registered production
session or proof that every alternate route is disabled in an actual deployment.
Do not install the callback into an ordinary multi-tool session.

`web_security_native.register_restricted_authorization` is an explicit offline
integration entrypoint, never called by normal plugin registration. It requires
behavioral fresh-manager crash/SystemExit/timeout/veto-parser canaries before
registering a restricted hook or optional secure tool. It issues process-local,
single-use eligibility bound to the real context, manager, dispatcher code and
registration generation. Strings, page claims, stale/cross-context tokens,
unbounded-hook configurations and hook-disabled workers cannot grant eligibility.
Subsequent runtime/manager changes revoke the registration lease. Authorizer
exceptions become fixed native `action: block`/`message` directives; reason-only
objects are not assumed to block the actual host dispatcher.

The cached CI runtime genuinely falls through after an unwrapped authorization
callback exception. It is **ineligible**, and restricted registration is refused
before exposing the hook/tool. The installed runtime passes the canaries and
offline dispatch tests. This rejects the incompatible host; it does not repair
Hermes core or certify unrestricted sessions. No runtime is deployment-authorized
by this offline compatibility result alone.

A supported host-owned, required, fail-closed result-security gate **or** a fully
qualified restricted secure-retrieval session is mandatory. Missing registration
must refuse startup, not downgrade strict mode to shadow. Until then, the new
handler is offline review material and existing Hermes web answers are unchanged.

## Offline regression checks

Use disposable HOME/HERMES_HOME/shared-auth and an OS outbound-network guard.
No production credentials or authentication state are needed. Run:

```text
python -m unittest discover -s tests -p 'test_web_security*.py' -v
python -m unittest discover -s tests -p 'test_web_screen*.py' -v
python -m unittest discover -s tests -v
python evaluation/evaluate.py --validate
python scripts/check_portability.py
```

Native tests additionally require the supported Hermes test runtime. Forced
positive/negative replies establish control-flow behavior, not Jev accuracy.
Frozen historical labels are never changed to improve results. Before any live
evaluation, qualify independently labeled fresh semantic cases, benign retention,
provider identity/privacy, restricted routes, request/token/spend caps and
fetch-time destination controls. Use new authorization, never consumed or
uncertain historical approvals. No rollout authorization is requested here.
