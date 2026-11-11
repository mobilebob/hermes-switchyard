# Native request provenance: qualified fields and deliberate unknowns

**Scope:** a read-only Switchyard adapter inside the existing shadow observer.
No Hermes-core changes, profile/provider management, credential lookup, Jev
request, request mutation, or extra hook is introduced. Shadow stays default-off;
apply remains unavailable.

## Discovery matrix

Pinned executable test host: Hermes 0.21.3, source revision
`dad0057271f14da3278a91afd438c901b2fcc5f3`. Read-only inspection of the installed
Hermes workspace found the same missing pin/account export. That inspection is
not execution or qualification of the running gateway.

| Information | Authoritative origin | Plugin-visible authority and decision |
|---|---|---|
| Current model/provider/endpoint/API mode | `agent/turn_api_request.py:build_api_request`, from the live agent after per-attempt preparation | Read its documented middleware fields exactly; compare outgoing model. Never substitute configured defaults. |
| Session/task/turn/API attempt | The same request builder; `agent/turn_context.py:_bind_turn_identity` owns turn/task binding and the conversation retry loop supplies attempt IDs | Require native nonempty scope and attempt fields. A reused attempt with conflicting route/scope is unknown. A fresh retry ID does not inherit an old identity. |
| Explicit CLI selection | CLI construction owns `_explicit_model_override`; session reset clears it. CLI model switching persists route/model through `cli_model_switch_mixin.py`. | These are not exported pin-state fields. Do not inspect frames, locate a CLI singleton or infer intent from model equality. |
| Gateway `/model` selection/removal | `gateway/session.py:SessionStore.set_model_override/get_model_override`; the slash-command owner changes the persisted override | There is no supported request-scoped SessionStore accessor in PluginContext. The adapter does not open its own DB/store. Missing override is not proof of unpinned CLI state. |
| Account/credential | The live agent's selected credential-pool entry and provider/auth owner; rotation can change them per attempt | No account/pool-entry identity is exported to request middleware. Do not re-resolve auth, read raw keys, inspect private auxiliary globals or equate a registry account alias with a provider name. Remains unknown. |
| Effective profile | Public `hermes_constants.get_hermes_home`, `hermes_home_key`, `profile_name_for_home` | Use the context-local home/profile owner. An ambient `HERMES_PROFILE` label cannot override it. Keep paths out of receipts; use a home digest. API absence/failure is unknown, not an environment-based guess. |
| Cancellation | Existing native stop/session-end/reset/unload notifications | Reuse Switchyard's existing invalidation owner. Retire observed attempts and reject their late reuse. There is no exported authoritative current cancellation boolean; caller claims cannot prove an attempt is live. |

`agent.auxiliary_client` has context-local runtime state but its private reader
can fall back to legacy process mirrors and includes raw authentication material.
It is not used as a pin/account authority. `gateway.session_context.get_session_env`
can fall back to process environment; matching text from it is not proof of a
request binding. Neither fallback is silently promoted into native provenance.

## Adapter behavior

`native_provenance.py` whitelists only fields actually emitted by the native
request builder. It discards extra `manual_model_pinned`, `model_account`, profile,
cancellation and nested provenance claims. Pin/account values remain `None`.
A true or false claim from another plugin, tool argument, user message or a test
fixture is not evidence that the host exported those fields.

This is an in-process contract, not a sandbox or cryptographic attestation
against arbitrary Python plugins. Trusted code can execute arbitrary code;
model-generated data is not trusted code and must never populate the adapter.

The adapter checks model/body consistency and binds an API attempt to its
profile, session/task/turn and literal model/provider/endpoint/mode fingerprint.
Known attempts and revoked attempts are each bounded to 128 entries. Cancellation
uses the existing conservative global invalidation behavior; fresh attempt IDs
can be observed after cancellation, but pin/account still remain unknown.
Revocation is bounded process-local evidence, not a durable replay database.

The shadow cache is profile-keyed and includes the native profile in its route
identity. Concurrent named profiles do not share pending decisions merely because
their session/task/turn strings happen to match. Nothing here changes the shared
skill/effort broker or redefines the active Hermes route.

## Qualification and actual eligibility

Tests use disposable homes, real context-local profile APIs, native SessionStore
selection/clear operations and the real `build_api_request` middleware call site.
Payload preparation/cache decoration are synthetic fixture seams; there is no
transport call. Native pin/account kwargs are not injected into that call site.

The existing consumer-policy tests retain an explicitly named
`SyntheticContractProvenance` dependency so semantic proposal/consume-once tests
remain possible. Those fixtures are **not native qualification**. The former
packaged positive case that supplied flat pin/account kwargs is now an explicit
unqualified/abstention test, rather than a production compatibility claim.

**No increase in demonstrated safe native shadow eligibility.** Current hosts
lack the two required authorities. Improvements are stricter rejection of
unsupported claims, effective-profile isolation, attempt conflict/cancellation
checks and accurate explanation of unknown state. All native proposals remain
blocked. Neither selection of the current default model nor clearing a stored
override opens an eligibility window.

Tests also demonstrate the account limitation directly: changing an agent's
credential-entry fixture does not change exported middleware metadata. The
adapter therefore does not claim to detect that account change; it keeps account
identity unknown throughout. Provider changes are visible and get distinct route
fingerprints. Different-session reuse of an old attempt is a conflict, not an
invitation to guess session aliases.

Full CLI/gateway UI-to-agent pin propagation and live credential rotation are
not qualified by these tests. Positive native eligibility requires a future
supported, request-scoped host API for explicit pin and opaque selected-account
identity. Do not patch Hermes core or use configuration inference to fake it.

## Next capability

[Memory relevance and injection filtering](MEMORY-RELEVANCE-INJECTION-DESIGN.md)
is a design only. No memory-filter implementation is part of this checkpoint.
