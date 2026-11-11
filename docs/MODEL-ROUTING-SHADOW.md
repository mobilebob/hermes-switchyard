# Passive same-provider model-routing shadow

In short: Switchyard can observe an **already requested, approved-registry model
recommendation** and record what it would propose for the same task. It makes
**no new Jev requests**, registers no second decision/request-mutation owner,
and has **no model-application authority**. The default is off.

## Configuration and authority

`model_routing_mode` supports:

- `off` — default; no shadow consumer or shadow capture.
- `shadow` — consume qualifying completed decisions and record proposals only.
- `apply` — unavailable in this milestone; record `apply_unavailable`, never
  change a model, provider, account, request body, or fallback policy.

Unknown values fail closed to off. Shadow requires the existing adaptive-effort
request owner, public/sanitized-data consent and lifecycle cleanup support.
`shared_jev_skill_effort` remains independently default-off; enabling shadow does
not enable shared skill/effort decisions. Existing adaptive effort retains its
own behavior; the shadow observer itself never mutates requests.

Use only a disposable evaluation profile for experiments. This is not production
activation guidance or a release-candidate claim.

## Which existing decisions qualify

Only successful output from the existing `jev_model_route_approved` owner can
populate the consumer. That path already uses the profile-approved registry,
Jev capability-fit questions, response validation and code-owned selection.
The profile policy now preserves its provider request ID in the typed receipt.
Ad-hoc candidate routing, skill picks, effort answers, arbitrary tool output,
and model/user prose are not model-selection evidence.

Do **not** issue a speculative tool call just to fill the shadow cache. If an
approved-model operation was not independently needed, shadow abstains with
`decision_unavailable`; it does not ask Jev to manufacture a result.

A qualifying decision requires:

- An exact native session/task/turn scope and clean foreground CLI capture.
- An exact public/sanitized task match, at most 1,200 characters.
- A successful, unapplied approved-registry receipt with a nonempty request ID
  and exactly one physical decision request.
- A still-approved candidate, unexpired registry/version, unchanged registry
  contents and unchanged Jev route/profile/credential-generation identity.
- A completed dispatch within 400 ms and consumption within 30 seconds.
- No cancellation, superseding capture or already-consumed result.

Pending state and receipts are process-local and bounded to 32 scopes. Task text
is not retained in shadow state or receipts; only its digest is retained.
Stop/session reset/end and unload invalidate pending work, including responses
that arrive after cancellation. Unsupported cleanup registration disables reuse.

## Trusted runtime gates

The native request context must explicitly provide:

- `manual_model_pinned: false` — a genuine host pin-state assertion, **not** an
  inference from whether the current model equals the configured default.
- `provider` and `model_account` matching the selected registry entry exactly.
- `model` matching the outgoing request's current model.

An explicit pin, including a pin to the default model, always wins. Unknown pin
state, unknown account, another provider/account or an inconsistent active model
abstains. Tool arguments and text claiming that a model is unpinned do not count
as runtime provenance.

This milestone supports only a single text user `input` item matching the
captured task, with no tools or previous-response state. Other request shapes
abstain rather than assuming that a model-fit answer covers changed context.
The observer intentionally does not project a cross-provider choice into a new
same-provider choice: that would invent an answer the approved router did not
make.

## Evidence and observability

The existing runtime-status payload exposes `model_routing_shadow`, containing
mode, `apply_available: false`, and the most recent bounded receipt. A proposal
is always `applied: false`, `can_apply: false`, `new_jev_requests: 0`. Receipt
state does not authorize any later component to apply that proposal.

Offline tests use the real policy and DecisionClient validator with synthetic
fit responses. Packaged tests build a verified release archive in a fixture
repository, load the installed copy through native Hermes PluginManager, invoke
the existing approved-model handler, and exercise the existing request middleware.
They block sockets and use only synthetic credentials/data. Off and shadow each
make one fixture request for the same explicit approved-model operation; observing
and consuming it adds none. Outgoing requests are structurally identical.

```text
python -m unittest discover -s tests -p 'test_model_routing_shadow.py' -v
python -m unittest discover -s tests -p 'test_shared_jev_native.py' -k shadow -v
```

## Known limitations and next step

Native Hermes hosts that do not supply trusted manual-pin/account provenance will
abstain. Tests inject these fields as synthetic **host** context; they do not
prove that a running gateway supplies them. There is no live gateway or provider
qualification, latency/billing measurement, automatic model-fit call, multi-step
request qualification, cross-provider routing, or apply implementation.

Next: qualify a trusted host pin/account provenance adapter, without modifying
Hermes core or adding another decision hook. Keep apply unavailable and shadow
explicitly off until an owner-approved isolated protocol establishes those
boundaries. Memory relevance/injection filtering remains a later transfer,
not a reason to introduce a competing retrieval hook.
