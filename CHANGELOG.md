# Changelog

## Unreleased — optional Jev accounting and runtime evidence

- Add read-only `runtime-verify` provenance reporting: checkout/receipt source identity,
  user-systemd gateway PID/start timestamp where available, and revision-grouped
  post-start receipts. A match is observational, never a loaded-module attestation.
- Add opt-in `SWITCHYARD_JEV_USAGE_LEDGER=on` request attempt accounting to
  the shared Jev client. Real network decisions write bounded begin/finish records
  before/after requests; crashes and validation failures remain unknown usage,
  not zero cost. Synthetic transports never write; default remains off.
- Add read-only `jev-usage` reporting and offline tests. No model/skill routing
  behavior changes, no new provider requests, and no live default switch.
- Design inspired by the evidence-accounting/provenance practices of
  `kerpopule/hermes-jev-skills` (MIT); implementation is original to Switchyard.

## 0.6.0 (2026-10-04)

This release adds local reports and offline skill checks, does less hosted work on easy turns, and adds opt-in tool review features that stay off by default.

### Added

**Receipts and local reports**

- `hermes switchyard wow` and `/switchyard wow`: read-only offline aggregates from retained routing and effort receipts. Default window is 7 days; `--days N` and versioned `--json` are available. Each metric shows its denominator and window. No savings, outcome, or complete-coverage claim. (#152)
- `hermes switchyard receipt --human`: selected skill, decision source, verified skill-load status, Jev call or skip, and unverified task outcome. JSON stays the default. History queries only. (#198)
- Effort receipts use `always` | `auto` | `off` (default `auto`). Plain language, for example `Reasoning: high→low · 180 ms`. Status leads with the cap, the last level sent, and why. Summary labels say `cloud decisions` / `local decisions`. (#147)
- Offline outcome labels over retained routing receipts: four evidence-censored boolean/unknown fields, per-field coverage, and a stable 20% hash split. No routing change or benefit claim. (#167)
- Synthetic routing-value fixtures: deterministic 25-, 150-, and 600-skill catalogs for paired plugin-off checks. No comparative result yet. (#130)

**Skills, status, and opt-in tools**

- `hermes switchyard lint-skills [--json]`: offline skill-description routability hints and name-only near-duplicate reports. It does not edit skills or replace Hermes lint. The synthetic evaluation does not establish real-catalog accuracy.
- `hermes switchyard status --check`: opt-in. Exits 0 only when local status is `ready` (and the browser probe has started, if `--browser` is set). The default command still exits 0. It does not call a provider or prove a model works. (#203)
- Opt-in tool review, retrieval screening, and conservative output handling. Runtime switches default **off**. Includes catalog admission review, a consequential tool gate, a smart approval provider, repeated terminal-line compaction, cross-tool stuck advice, and a browser plan cache. No measured latency, cost, or task-quality claim. (#193)

**Source finder**

- Opt-in multi-file evidence bundles: exact per-file citations, keyed relevance questions, duplicate citations, and whole-lookup fallback. The 80-conversation synthetic pilot passes its gates. Still opt-in.
- Opt-in prefetch for a simple request that names a file. Exact evidence can arrive before the first main-model call. Unsupported requests use ordinary tools. (#183)

### Changed

**Less work on easy turns**

- Light turns skip hosted skill selection by default (`automatic_skill_light_turn_bypass`). Greetings and closed acknowledgements qualify; domain tasks and path-scoped listings do not. Optional early bypass before catalog discover stays **off** (#160). Optional no-skill gate stays **off**.
- Validated skill candidates are cached in-process. Root, policy, config, and plugin-metadata changes invalidate the cache; otherwise a 30-second TTL applies. Callers receive independent dictionaries.
- Explicit-skill patterns run only when the task text contains that skill's name. Selections stay the same. On a 600-candidate synthetic case with no skill named, local time fell from about 84 ms to about 0.1 ms. Not an end-to-end latency claim. (#205)
- Skill partition planning counts bytes instead of serializing JSON repeatedly. Boundaries and payloads stay the same. 600-entry cases use about 94–97% less local planning time. No whole-conversation speedup is claimed.
- Optional deferral of six Switchyard decision-tool schemas, default **off** (#158). Automatic routing, explicit tools, and `jev_computer_use` stay. No measured latency win. The cheap-shortlist experiment (#157) is excluded.

**Lint and triage**

- `lint-skills` leads with errors and warnings, shows each overlap once, and caps text at 20 findings. Wording preferences need `--style`. JSON is `switchyard.lint_skills.v2`. Empty descriptions no longer cascade into collisions. Non-ASCII descriptions are omitted from lexical comparison. (#199)
- Record-triage severity uses explicit impact text whenever that library workflow runs. In a frozen synthetic comparison, correct priorities rose from 18/48 to 42/48 and wrong accepted priorities fell from 2 to 0. Mean latency rose 18.74%. Indices and budgets are unchanged.

**Checks**

- Weekly and manual runs check current Hermes HEAD on Python 3.14 and keep report-only receipts. Pinned 3.11–3.13 checks are unchanged.
- The single-skill and multi-skill rows in `docs/BENCHMARKS.md` are re-measured on pre-release source `dd4477a` (`04786f3` with the 0.6.0 version bump): 12/12 single-skill picks and 5/5 multi-skill sets, as before. After that measurement, #215 changed the approval gate code and the bundled operations skill text changed. These rows were not re-measured after #215. The other rows remain 0.5.0-only.

### Fixed

- The consequential tool gate requests approval for a closed set of destructive Python calls in `execute_code` and `python -c` (`os.remove`, `os.unlink`, `os.rmdir`, `os.removedirs`, `shutil.rmtree`, `shutil.move`, `pathlib.Path.unlink`, `pathlib.Path.rmdir`, and literal `open` modes containing `w`, `a`, `x`, or `+` on credential paths; read-only `r` and `rb` do not), including aliased imports. `execute_code` also flags an unresolved dotted call in that set because the session kernel keeps earlier imports; a fresh `python -c` process does not. Literal `python`, `python3`, and `py -c` prints are exempt on every platform, including Windows. `echo` and `printf` stay gated on Windows. Code that does not parse stays gated. `getattr`, `exec`, `eval`, star imports, and other dynamic dispatch are not detected. Parsed shell commands named `del`, `erase`, or `rd` request approval on every host. A Python `del` statement, or text that only mentions those words, does not. (#215, fixes #204)
- Approval prompts name the tool, the trigger, the matched field, redacted input and target, and what each choice covers. If masking would hide a command or the command cannot be inspected, the prompt withholds input and names the trigger. Input text that copies the prompt's truncation marker cannot reveal a masked value. (#201, #214)
- The bundled `hermes-switchyard-operations` skill registers with the description from its `SKILL.md` when the host accepts that argument. Older hosts keep the previous call. (#206)
- Enum-strict Chat Completions relays map `none` / `minimal` to `low` and `ultra` to `max` before the trivial-turn floor, so a greeting does not send an invalid effort and force a provider 400. (#181)
- One `not adapted` receipt per foreground turn when the host sent no effort or the route cannot adapt. `off` still hides it. (#182, fixes #180)
- Opt-in repeated-output compaction keeps full or verbatim output for the captured session, task, and turn. Uncertain captures pass through. (#196)
- Light-turn detection rejects substantive follow-on work, keeps a single-word skill override, and does not let callers mutate cached candidates.
- The wow report window follows receipt timestamps.
- Windows tests use a native source-finder root, check the 400 ms decision budget without charging later work to it, and keep archive bytes for provenance checks. Production timeout and archive headers are unchanged.

### Not included

- Same-turn Jev consolidation and same-provider model routing stay evaluation-only. Neither is registered or release-qualified. (#188)
- No claim that the Hermes TUI chip matches wire effort. The receipt shows what was sent; the status bar and `/reasoning` remain the cap.
- The Jev-feature redesign (#139) is still exploration, not a release commitment.

## 0.5.6 (2026-09-28)

This release changes content only. It has no new features and no behavior change for ordinary tasks.

### Changed

- The local marking scan now keeps a message local only for `proprietary` text, a standalone `Confidential` banner, and `company`, `employer`, or `client confidential` markings. Other marking words are ordinary text: Hermes egress redaction still masks secret values before any hosted Jev request.
- The fail-closed routing reason codes have new names: `local_scan_no_envelope` (was the clean-scan, no-envelope code) and `local_scan_unreadable` (was the non-text code). The history view skips older history records that contain the old codes.
- Tests, documentation, and repository history use only civilian example text. Tags from v0.4.2 to v0.5.5 point to rewritten, signed commits, and their release archives were rebuilt from those commits.

## 0.5.5 (2026-09-27)

This release fixes adaptive effort and the public DOM browser, changes egress to redact-not-block, and adds a visible effort receipt. It ships no new #98 feature: both candidates failed their frozen evaluations (see **Not included**).

### Included

- [#117](https://github.com/bgrablin/hermes-switchyard/issues/117): public DOM browser fills now use stable element identity, confirm field values, and handle same-URL document replacement. This does not add authenticated browsing.
- [#118](https://github.com/bgrablin/hermes-switchyard/issues/118): adaptive effort isolates delegated task state, re-caps after `/reasoning` changes, and reconsiders routine read-only tool rounds. Foreground replies can show a default-on effort receipt line; `/switchyard effort summary` shows recent choices and local/Jev counts. A saved-token value appears only when usage supports an estimate.
- Receipt line and history: Hermes stores the `transform_llm_output` result, so the reply you see (with the receipt line) is what `/resume` shows. Before each request, the `llm_request` middleware removes that exact trailing line from earlier assistant replies, so the model never gets it back. This also runs when adaptive effort is off or pinned. Tested through `AIAgent.run_conversation` on the Anthropic and Codex/Responses wire shapes.
- [#121](https://github.com/bgrablin/hermes-switchyard/issues/121): default-on adaptive effort uses up to 1,200 characters of the current message after Hermes egress redaction. It does not send history or raw tool output. Without that redactor, eligible nontrivial turns can use a closed-set metadata-only Jev decision. Marked confidential data stays local. Set `adaptive_reasoning_effort=false` to stop this path. The stakes threshold remains provisional.
- [#122](https://github.com/bgrablin/hermes-switchyard/issues/122) was superseded by redact-not-block; [#123](https://github.com/bgrablin/hermes-switchyard/issues/123) closed a gap in its former secret-assignment blocklist; [#124](https://github.com/bgrablin/hermes-switchyard/issues/124) was reworked to use the current redactor. Ordinary topic words and email addresses do not count as secrets. A clean scan is not data loss prevention.
- [#133](https://github.com/bgrablin/hermes-switchyard/pull/133): automatic skill routing redacts eligible task text before a bounded hosted excerpt, skips hosted work without Hermes redaction, and defaults to one parallel stage-one round. Trivial skill turns can stop locally.
- [#136](https://github.com/bgrablin/hermes-switchyard/pull/136): adaptive effort now sends a greeting or thanks at the lowest allowed level with **no Jev call**. A request with an action still uses Jev when allowed. The receipt says `local (no Jev call)` and history records `jev_called: false`. The installed-candidate [recorded trace](docs/FIRST-RUN.md) shows the boundary; it does not establish a warm latency target.
- The normal Git installer now passes its community-source scan on this candidate. The source is still unreviewed by the Hermes catalog; the scan is not a quality guarantee. [First-run measurements](docs/FIRST-RUN.md) separate local steps from unmeasured key entry and a real session.
- [#137](https://github.com/bgrablin/hermes-switchyard/pull/137) skips the Snap browser test assertion only after an environmental fallback; [#138](https://github.com/bgrablin/hermes-switchyard/pull/138) gives the isolated Windows turn-loop child a real home directory. CI install-scan and checksum checks protect the artifact and the exact source tree.
- [#140](https://github.com/bgrablin/hermes-switchyard/pull/140) reuses the Jev client and introduced a 0.25 s adaptive decision budget; [#141](https://github.com/bgrablin/hermes-switchyard/pull/141) sets the default to 0.4 s (allowed range 0.1–1.5 s). On timeout your level is sent unchanged. Under the prior 0.25 s guard, 14 installed cold one-shot non-trivial decisions had a Jev p50 of 248.85 ms and p95 of 281.5 ms (nearest-rank); 8 of 14 timed out. A fresh TUI measured one client-reused Jev call at 178.4 ms. One warm sample cannot establish a p95 for the current default.

### Not included

- **Research Navigator (F1):** its [frozen end-to-end release evaluation on closed PR #132](https://github.com/bgrablin/hermes-switchyard/pull/132#issuecomment-5859656369) **failed** on latency, cost completeness, and false final assertions. It is not shipped.
- **DOM Progress & Recovery (F2):** its [frozen release evaluation on closed PR #135](https://github.com/bgrablin/hermes-switchyard/pull/135#issuecomment-5859656659) **failed** on outcome: the candidate had no live early-stop gain. It is not shipped. [Issue #139](https://github.com/bgrablin/hermes-switchyard/issues/139) tracks follow-up work.

## 0.5.4

- Fix public DOM search in real Chromium sessions: `TYPE_TEXT` now runs on the browser session rather than only the protocol; href-less form buttons on Wikipedia Special pages remain offered instead of being filtered as special links; and a field already filled on the same page is not offered for a second hosted typing decision. Caller values still stay local, and navigation still goes through the destination policy (issue #109).
- Fix adaptive reasoning effort so your `/reasoning` level is the cap (issue #108). Before this fix the adapter replaced your level with its own: sessions started at `medium`, one failed tool call raised effort to `max`, and `max` stayed for the rest of the session, even after a manual change. Now Jev picks only from levels at or below yours, and your level is sent unchanged when Jev fails or no lower level exists (no Jev call then). A manual `/reasoning` change on the same model pins the session. New in-session command `/switchyard effort status|pin|auto`. New settings `adaptive_reasoning_effort_mode` (`auto` or `pinned`), `adaptive_reasoning_effort_exclude_models` (for example `["*astra*"]`), and `adaptive_reasoning_effort_allow_raise` (off by default; one level above yours only while the latest tool call failed). Tool outcomes and the stuck flag reset each user turn. The Jev deadline default drops from 8 to 1.5 seconds. Every decision appends a sanitized record to `effort-history.jsonl`, and `hermes switchyard stats` reports a `reasoning_effort` section. `adaptive_reasoning_effort_default` is deprecated and unused.
- Offer visible links on public DOM pages, preserving the existing destination checks; locally upgrade only an approved HTTPS document's same-host HTTP redirect to validated HTTPS without sending plaintext traffic (issue #110). Keep the redirect-hop budget across that upgrade. A renderer crash now returns a bounded reason at startup or capture with no Jev request; the browser does not automatically recover from a crash.
- Use bounded two-stage automatic skill routing on allowed interactive turns by default (issue #94). A needs-skill gate can stop a no-fit turn early; later catalog partitions run in parallel under the existing request and deadline caps. Stage two rechecks the top candidates. Names remain the default hosted boundary; bounded descriptions or SKILL.md excerpts require an explicit detail setting and existing egress approval. Machine-generated platforms skip by default, with a typed platform allowlist. The bundled offline benchmark is a simulation, not a hosted accuracy claim.
- Per-session receipt history, routing statistics, and exact git source SHA (issue #93). Every finished automatic turn appends one sanitized record (`session_id`, `turn_id`, `platform`, UTC `recorded_at`; no task or conversation text) to a bounded `receipt-history.jsonl` next to the latest-receipt file (last 500 records / 1 MiB, atomic rotation). `hermes switchyard receipt --session <id>` and `hermes switchyard receipt --last <n>` read the history; `hermes switchyard stats [--since 24h]` reports turns, selection/abstention rates, hosted failures by sub-code, skill load rate, p50/p95 latency, requests per turn, and known cost per turn and total. Receipt `plugin_identity.source_sha` now falls back to the checked-out git commit (`.git/HEAD` plus loose/packed refs, no subprocess) when no release manifest exists, so git installs report an exact SHA. `hermes_switchyard/receipt_history.py` is included in the release archive.
- Retry a hosted Jev request once on a new connection when a reused keep-alive connection was closed by the server while idle (`RemoteDisconnected`, connection reset or abort, broken pipe before any response byte). Replace a pooled connection that has been idle longer than the configurable limit (default 60 seconds) before reuse. Timeouts, failures on a new connection, and failures after a response starts are not retried. Refs #92.
- Retry hosted Jev HTTP 429 and 529 responses at most twice per request, with exponential backoff that honors a numeric or HTTP-date `Retry-After` up to 8 seconds. A retry does not start when its wait would not fit in the routing deadline, and the wait stops on deadline or host cancel. One operation allows at most 4 transport retries in total. Also treat `ssl.SSLEOFError` on a reused connection as a stale connection. Refs #92.
- Hosted request failures now carry a closed-set diagnostic sub-code (for example `stale_connection`, `connect_failed`, `timeout`, `http_429`, `http_5xx`, `invalid_response`) in receipts and never provider text. Call metadata records closed-set retry counts (`transport_retries`). A failure logs one WARNING per sub-code per 5 minutes and counts suppressed repeats. Refs #92.
- Make routing-receipt persistence best-effort: a receipt write error of any type is logged once as a WARNING (exception class only) and never stops skill recommendation or skill loading. A failed save sets `receipt_persist_failed: true` in the in-memory routing metadata for that turn. A failed write closes its temporary file on every path before removing it. Receipt writes and `hermes switchyard receipt` remove `.receipt-*.tmp` regular files older than one hour; symlinks, directories, and other names are never removed, and cleanup errors never change the result. Refs #91.

## 0.5.3

- Keep adaptive reasoning effort on the Codex Responses wire as `reasoning.effort`; never forward the internal `reasoning.enabled` flag. The fix selects the wire shape by provider/API mode, not by model version, and covers existing and future model IDs. Corrects the HTTP 400 that affected the default-on middleware in 0.5.2.
- Use a version-independent safe `max` ceiling for native Anthropic adaptive `xhigh` decisions; OpenRouter Chat Completions keeps its own wire contract. This favors a common supported effort over model-name guesses.
- Package the session-search and adaptive-reasoning docs referenced by the changelog, reject broken changelog links during release verification, and remove a stale current-version claim in the automatic-integration guide.

## 0.5.2

- Clamp adaptive `reasoning_effort` for Codex / Responses / Astra: map `none` and `minimal` to `low` (Astra accepts only `low|medium|high|xhigh|max`). `wire_efforts_for_provider` no longer offers `none` for those models, so middleware never writes `reasoning_effort: none` (provider HTTP 400). Fixes #63.

## 0.5.1

- Adds `jev_session_search_rerank`: Jev Choice re-rank over a Hermes `session_search` FTS shortlist (compact redacted cards only). Fail-open returns the first FTS candidate when Jev is down or below confidence thresholds. Optional second Choice picks `match_message_id` among anchors. See `docs/SESSION-SEARCH-RERANK.md`.
- Adaptive reasoning-effort picker remains enabled by default. Disable it with `adaptive_reasoning_effort: false` or `hermes config set plugins.entries.hermes-switchyard.settings.adaptive_reasoning_effort false`; see [`docs/ADAPTIVE-REASONING-EFFORT.md`](docs/ADAPTIVE-REASONING-EFFORT.md).

## 0.5.0

- Publish tip-main with/without feature scorecard (skill select, multi-skill 5/5 via `select_many`, model route, assess, automatic local_only, computer-use DOM) in README and `docs/BENCHMARKS.md`; refresh live selector report to plugin hash on `c8e6008`; computer-use Felidae row re-bound to tip-main dual-gate (`goal_verified: false` on `local_predicate` early-stop).
- DOM browser receipts dual-gate verification: `goal_verified` / `verified` are true only when Hermes agreed `DONE` (`completion_source: provider_decision`) **and** a local completion condition is satisfied; `verification_owner` is then `hermes_and_url`. A `local_predicate` early-stop (caller-supplied `completion_condition` or derived `derived_goal_title` / `derived_goal_url`) may still be `completion_candidate` but keeps `goal_verified` / `verified` false. Provider `DONE` without a satisfied condition stays unverified (`verification_owner: coordinator`). Derived predicates never self-certify alone.
- Enable automatic features by default after install: `automatic_skill_routing_mode` → `hosted_sanitized`, `automatic_skill_consumer_mode` → `load`, `automatic_skill_public_or_sanitized_data_ack` → `true` (plugin.yaml + Python DEFAULT constants). Happy path is install --enable → `hermes switchyard setup` → fresh session; opt down to `local_only` / `advisory` for privacy.
- Standing operator acknowledgement authorizes hosted construction when no host `turn_egress_policy` envelope is present and the local restricted-pattern scan is clean (`egress_authority: standing_ack`, `policy_reason: standing_ack_allowed`). Host allow envelopes record `egress_authority: host_envelope`. Explicit deny/unknown/malformed/restricted envelopes and restricted local scans still fail closed.
- DOM browser `url_contains` completion matching is case-insensitive (aligned with title/text predicates) so a needle like `United_Nations` matches `https://en.wikipedia.org/wiki/United_Nations` regardless of casing; unit coverage proves the Wikipedia-shaped regression.
- DOM browser `no_progress` aborts now wait until `min_actions_before_done` actions have been dispatched, so scenic multi-hop wiki races are not cut short by early consecutive stalls while the minimum action floor is still unmet.
- DOM browser completion predicates (issue #25): derive a fixed local predicate from quoted `url equals|is "https://…"` / `url contains "…"` goal text (in addition to quoted title forms); refuse unquoted URL mining; after an action that satisfies a caller or derived URL/title/DOM predicate, stop without a second Jev `DONE` decision; paired Wikipedia-shaped unit fixture proves fewer `jev_request_count` / `jev_total_latency_ms` while preserving `completion_candidate` and `verified: false` on the local-only path (dual-gate requires provider `DONE` as well).
- DOM browser backend (issue #29): expose backend identity, `session_mode` (`headless_ephemeral`), and an explicit capability set on every receipt; fail closed before the first Jev request for upload, authentication, existing-session, hotkey, or typing-without-values requirements; implement locally validated `TYPE_TEXT` for ordinary text fields via caller `text_inputs` (values never sent to Jev); do not attach to a user browser profile. Managed persistent and attached-existing-session modes remain named but unimplemented.
- Windows / CLI session exposure: `hermes switchyard status` reports `toolset_composition` (required toolsets + Plugin Doctor vs callable boundary); `hermes switchyard ensure-toolsets` (also run from `setup`) adds `computer_use` and `hermes_switchyard` to `platform_toolsets.cli` without enabling unrelated toolsets; after-install documents PowerShell pin quoting and that native `computer_use` fallback is not a Jev success (issue #21).
- Automatic E2E harness follow-up (post-merge Copilot on #43): unknown qualified skill identifiers no longer fall through to bare-leaf matching; Hermes CLI shebang parsing accepts ``env``/Python invocations and rejects unsupported wrappers as ``harness_invalid``.
- Automatic skill routing consumption contract (issue #19): resolve explicit skill instructions before any local/hosted selection with zero provider requests (`explicit_override` pre-routing skip); record `delivery_status`, `adoption_status`, and `outcome_status` separately (outcome stays `unverified` at the plugin boundary); hosted automatic routing abstains with `consumer_contract_unmet` unless consumer mode is `load` (Hermes `skill_view` seam); advisory delivery is never treated as adoption. Explicit-override detection consults the full active skill registry (not only the configured routing subset). Load mode preflights mandatory-skill adoptable candidates before authorizing hosted work. `mandatory_conflict` omits recommendation context so `skipped`/`suppressed` matches observable delivery. Receipt validation enforces exact delivery/adoption pairs and cross-checks them against consumer status.
- Automatic E2E harness: resolve bare and namespace-qualified skill identifiers through the runtime registry before scoring (preserve raw and canonical forms in evidence); ship portable fixture skills instead of copying from an operator Hermes home; re-exec under the Hermes interpreter (`HERMES_PYTHON` or `hermes` CLI) without inventing `PYTHONPATH`; fail as `harness_invalid` when registry/import prerequisites are broken; expand the paired suite to three unique tasks (issue #24).
- Fail closed when a clean local restricted-pattern scan has no host per-turn allow envelope: unmarked turns stay `data_class=unknown` (`local_scan_no_envelope`) and are not forwarded to hosted Jev as `sanitized`.
- Align operator docs and the clean-scan regression with that fail-closed envelope requirement (leftover Copilot threads from #41): assert exact `policy_reason` / `policy_data_class`, and stop describing the host envelope as optional for hosted construction.
- Persist `local_scan_no_envelope` in `HOSTED_SKIP_REASONS` so routing receipts keep the fail-closed reason; align `plugin.yaml` and `egress.py` contract wording with the required host envelope.
- Document that hosted automatic routing also requires `automatic_skill_consumer_mode=load` (advisory yields `consumer_contract_unmet`), and list only markers the local restricted-word scan actually matches.
- Persist supplied-envelope fail-closed codes (`per_turn_policy_unknown` / `invalid` / `denied`, `restricted_data_class`) in routing receipts; a missing envelope on the automatic path remains `local_scan_no_envelope`. Align `after-install.md` and the operations skill with load-mode / ack-false defaults.
- Automatic hosted routing uses a separate intervention deadline (default 20s, below the typical Hermes ~30s plugin callback) from explicit decision/computer-use deadlines (60s). Remaining budget is checked before each partition/reduction request; late results after the deadline are discarded; distinct receipt codes record `deadline_exceeded`, `host_cancelled`, and `late_result_discarded`; intervention timeout is reported separately from provider I/O timeout (issue #26).
- DOM browser receipts: preserve the action ledger on provider timeout/validation failures as `partial_failure` with `reconcile_before_retry`; distinguish `action_dispatched` / `effect_observed` / `goal_verified`; do not set `effect_confirmed` for same-document clicks with no observed URL/title/DOM delta (issue #28).
- Hermes 0.19 status and live collector: when `parse_config_string_list` is missing, fall back to list/CSV parsing so `hermes switchyard status` resolves platform selection and reports real `callable` values (parser errors still fail closed); tolerate missing `hydrate_profile_secret_sources` and fall back to a process `OPENROUTER_API_KEY` when the scoped value is missing or unusable.
- Host config fallback for Hermes 0.19 reads `plugins.entries.<id>.settings` (legacy `.config` accepted); outer entry fields such as `allow_tool_override` are no longer mistaken for plugin settings.
- Tolerates Hermes 0.19.0 PluginContext hosts that omit `get_config`: `register()` falls back to `plugins.entries.<plugin_id>` (or install defaults) so tools, hooks, and `hermes switchyard` still register when the plugin is enabled.
- Makes automatic skill routing `local_only` by default. Hosted automatic routing now requires an explicit `hosted_sanitized` profile setting; the legacy boolean remains compatibility-only and never authorizes hosted egress.
- Resolves Ubuntu's `chromium-browser` Snap wrapper to `/snap/bin/chromium` and places each temporary browser profile under the Snap-accessible `~/snap/chromium/common` directory with exact per-run cleanup. Snap confinement is detected from the resolved executable and from a bounded wrapper-script prefix (quoted, unquoted, or `--`-separated exec targets; compiled binaries are never inspected as scripts). Directory preparation and per-run profile creation both raise a typed `snap_profile_unavailable` diagnostic, and `jev_computer_use` returns that code instead of a generic execution failure.
- Stores routing receipts under Hermes' profile-scoped `plugin-data/hermes-switchyard/` directory and migrates one valid legacy receipt without overwriting unrelated state. Receipt files are atomically published with owner-private permissions: `0600` on POSIX; on Windows a protected DACL granting only the current user, SYSTEM, and Administrators, applied to the published file on both the normal replace and legacy migration paths and failing closed when the descriptor cannot be enforced.
- DOM browser backend: observations are now scroll-relative and offer targets near the current viewport instead of a fixed document-order prefix, so targets beyond the first snapshot become reachable after scrolling. Targets keep one stable identifier across scrolls and recaptures.
- DOM browser backend: each action record now separates `action_dispatched`, `effect_observed`, and `goal_verified`, and `effect_confirmed` is never true without an observed delta. A same-document click that changes nothing reports `no_observed_effect` instead of a confirmed effect.
- DOM browser backend: provider timeout, malformed response, validation failure, deadline exit, browser startup failure, and unexpected errors now return structured partial-progress receipts that preserve the actions already attempted, the attempted request count, the last state hash, and the failure phase instead of collapsing into a generic plugin error.
- DOM browser backend: ineffective scrolls are retried locally inside the step, and repeated no-progress observations stop with `failure_phase: no_progress` and `reconcile_before_retry: true` instead of paying for another provider decision on unchanged state.
- DOM browser backend: adds an optional bounded `completion_condition` (URL, title, text, or element label) evaluated locally after every action. The predicate is fixed before execution, is never sent to the provider, and stops the loop without another provider decision when satisfied. `completion_candidate` and `verified: false` are unchanged, and `completion_source` distinguishes a local predicate stop from a provider `DONE`.
- DOM browser backend: results now report `backend`, `session_mode`, `browser`, `browser_confinement`, `session_setup_ms`, and `jev_total_latency_ms`, and a goal that needs upload, authentication, an existing signed-in session, or typing without `text_inputs` returns `unsupported_capability` with a request-free reason code before the first provider request.
- DOM browser backend: candidate scanning is windowed around the current viewport, so on a long page the scan follows the viewport and a target beyond the first scan window becomes reachable by scrolling instead of being stranded behind a fixed document-order prefix.
- DOM browser backend: a low-confidence or ambiguous provider choice abstains with `failure_phase: low_confidence` or `ambiguous_decision` instead of dispatching an action, and `last_state_hash` always matches the page state the receipt reports.
- DOM browser backend: receipts carry the explicit capability set and session identity (`capabilities`, `session_identity`), the session mode is named `headless_ephemeral` to distinguish it from the modes this backend does not provide, and the capability wording families cover inflected and phrasal forms (sign-up, registration, "log into", typing, uploads, attachments).
- DOM browser backend: a code-owned destination policy (`hermes_switchyard/destination_policy.py`) now decides every destination before any provider work and at every request boundary of the browser. An approved public https start URL can no longer expand into a private, loopback, link-local, credentialed, local-name, or non-https destination through a redirect hop, a subresource, a frame, a worker request, or a navigation after a click. Host resolution that fails or returns any non-public address is a refusal, and interception that cannot be proven stops the run before the first provider request.
- DOM browser backend: the browser now starts on `about:blank` and loads the start URL only after request-stage `Fetch` interception is installed. Previously the start URL was a launch argument, so its first load and redirects happened before any check. New windows are blocked, and the per-run profile disables network prediction, because Chromium preconnected six TCP sockets to a refused navigation target before interception could answer.
- DOM browser backend: the lexical URL check also refuses numeric host spellings (`0x7f.0.0.1`, octal, decimal, short forms), IPv6 addresses that embed a private IPv4 address (NAT64, 6to4, Teredo, mapped), percent-encoded or non-ASCII hosts that normalize to an address, local-only suffixes (`.internal`, `.lan`, `.home.arpa`, and similar), and control characters or backslashes in the URL.
- DOM browser backend: receipts gain `destination_policy` (checked and blocked counts, redirect hops, cross-origin redirects, sanitized refusal records, `interception_active`, and named residual risks). A refused navigation stops the run with `failure_phase: destination_blocked`, a bounded `failure_reason`, and `reconcile_before_retry: true`. Refused URLs are reduced to scheme and host in every receipt field, so credentials, paths, and queries are never echoed. `verified` stays `false`.
- DOM browser backend: review fixes for confidence margin, recovery observations, idle-timeout fail-closed interception, child-target setup acknowledgements, fatal evidence retention past the nonfatal cap, deadline reconciliation from the action ledger, bounded action `failure_reason`, nonconsecutive observation-cycle detection, truthful recovery action records, and an explicit `SWITCHYARD_LIVE_BROWSER_TESTS=1` opt-in for live external-browser tests.
- DOM browser backend: closes the DNS-rebinding gap between the request check and the browser's own connect. Chrome now sends every connection through a loopback validating proxy that resolves each host once, requires every answer to be public, and dials the validated address literal, so no second resolution can be rebound. A private answer at the proxy is a fatal `proxy_connect` violation. Loopback targets are routed to the proxy instead of bypassing it, which also puts WebSocket connections behind the pin. Receipts report `connection_pinning`, `tunnels_opened`, and `tunnels_refused`, and the residual-risk list shrinks accordingly.
- DOM browser backend: the profile now disables non-proxied WebRTC UDP (the `--force-webrtc-ip-handling-policy` switch did not stop a STUN datagram to loopback; profile preferences did), and QUIC and Chrome's background time query are disabled so the proxy sees only page traffic.
- Adds [docs/DOM-BROWSER-BACKEND.md](docs/DOM-BROWSER-BACKEND.md).
- Adds `hermes_switchyard.record_triage`, a bounded second demonstration of the `jev_assess` primitive. It qualifies public or synthetic records in bounded `jev_assess`-shaped batches with code-defined alternatives, an aggregate deadline, and request accounting; decides some records locally without a provider request; and feeds only accepted decisions to a deterministic consumer that writes a local work-queue artifact. `verify_artifact` re-checks that artifact from disk. It is a library module, not a registered tool, so the tool surface and `plugin.yaml` are unchanged. See `docs/RECORD-TRIAGE.md`.
- Adaptive reasoning-effort picker (Codex-style): on by default after install. Jev chooses Hermes `reasoning_effort` (`none|minimal|low|medium|high|xhigh|max|ultra`) per turn / after tools; `llm_request` middleware applies it for the next generation; fail closed keeps the previous effort; prompt-cache friendly (messages untouched). Disable with `adaptive_reasoning_effort: false`. Receipts record effort, reason code, and whether applied. `jev_model_route` remains advisory (`applied: false`).
- Model-routing adapter for issue #11: `hermes_switchyard.model_route_adapter` makes the approved-registry path first-class with typed receipts (`applied: false`, `no_fallback`, account boundary, distinct empty/stale/budget/provider outcomes). `register_model_route_adapter` probes Hermes for a model-selection apply seam and records a safe no-op on Hermes 0.19 (no silent model swap). `accept_model_route` refuses apply unless the host supplies an explicit callback. Documents the Hermes 0.19 honesty boundary in `docs/MODEL-ROUTING.md`. Status JSON includes `model_route_adapter`.

## 0.4.2

- Automatic hosted Jev is on after install. `automatic_skill_public_or_sanitized_data_ack` defaults true. Set it false to skip hosted automatic routing. Topic words such as "private" or "verification" no longer skip Jev; high-confidence secrets, payment values, and verification codes still do.
- OpenRouter Decisions requests send Hermes-Switchyard app headers (`HTTP-Referer`, `X-Title`, `X-OpenRouter-Title`) so usage shows as Hermes-Switchyard instead of Unknown. Direct TypeSafe requests do not send those headers.
- Adds an operation-level finalization boundary to computer use: an expected failure after execution began returns the partial ledger (known actions, uncertain-effect actions, provider decisions, last observation) instead of a generic error, and marks reconciliation before retry.
- Normalizes OpenRouter's documented Decisions response `id` to the canonical internal `request_id`; contradictory dual identifiers are rejected and no identifier is invented.
- Receipt validation now checks the record exactly as supplied: unknown fields are rejected with a set-difference test, persistence and readback round-trip the canonical record, a successful load requires the complete evidence group, and `cost: null` is the single unknown-cost representation.
- Public tool errors preserve `PartialAccountingError` evidence: a known request subtotal with explicit `total_usage_incomplete`, redacted identifiers, and no doubled accounting. Multi-skill and model-routing outer batch failures merge accounting from earlier successful batches.
- Documents the credential-ownership boundary: keys come from Hermes' profile secret scope; host-managed native Decisions transport awaits a Hermes core capability and is an explicit host-change dependency.
- `hermes switchyard status --json` reports the registered automatic routing mode, consumer mode, standing acknowledgement, and whether hosted construction is allowed. A fresh process must register the plugin before those fields are populated.
- Load mode skips automatic `skill_view` when the selected identifier conflicts with configured mandatory skills, records `mandatory_conflict`, and does not load.
- `jev_model_route` remains the documented Hermes routing point. `route_model_from_registry` uses a code-owned approved candidate registry; stale `registry_generation` values abstain as `stale_registry` with no egress, and an empty registry abstains as `empty_registry`. A selected route does not change the Hermes runtime model.
- Registers `jev_computer_use` in the `computer_use` toolset by default on Windows, macOS, and Linux. Catalog visibility no longer requires Jev credentials. Standing `public_or_sanitized_data_ack` is on after install; callers may omit it. A live Jev route is still required. Other Switchyard tools remain on `hermes_switchyard`.
- Web goals on `jev_computer_use` use a DOM browser loop: one Jev request per step chooses operation and click target together, then a Chromium-family browser clicks the page. Hermes `computer_use` is not between those clicks. Desktop apps without a URL still use Cua Driver.
- Decision tools stay visible without a saved key so operators are not left hunting for a missing catalog entry. Install prints `after-install.md`; `hermes switchyard guide` reprints the same next steps.
- Adds an opt-in typed automatic skill consumer that invokes Hermes' normal `skill_view` loader once per accepted turn while retaining advisory mode as the default.

- Uses standalone `hosted_sanitized` automatic routing with persistent acknowledgement and strict local per-turn scanning.
- Resolves provider, model, endpoint, and profile-scoped secret settings at each invocation.
- Adds local `status` and `guide` commands plus an explicit `test --live` billed-request gate.
- Adds the separate typed `jev_skill_select_many` catalog-selection contract without loading or mutating skills.
- Renames the internal prototype package, command, bundled skill, auxiliary task, and prompt-section surfaces under `hermes-switchyard`; Jev tool names remain stable.

## 0.4.1

- Corrected two security-scanner false positives in operator documentation so the repository passes Hermes' plugin security gate without `--force`.

## 0.4.0

- Adds `jev_assess` with validated Choice, Score, and Noul support.
- Adds direct TypeSafe routing with automatic provider selection and pooled HTTPS connections; OpenRouter remains supported with fallbacks disabled.
- Searches skill catalogs larger than Jev's per-Choice limit through partition fan-out and recursive reduction instead of truncating the tail.
- Prepares explicit automatic routing modes (`off`, `local_only`, and `hosted_sanitized`) and a fail-closed per-turn egress contract. The plugin keeps candidate descriptions and history local, but current Hermes core does not yet propagate the envelope or consume routing metadata; production hosted-routing integration remains a separate core change.
- Expands Cua Driver-backed computer use to Windows, macOS, and Linux, broader native roles/actions, dense target partitioning, and 100-step bounded runs while preserving fresh identity checks and coordinator-owned verification.
- Stages CUA operation and target decisions, compares dense-partition finalists, uses macOS Command shortcuts, batches oversized assessment/model-routing requests, validates typed questions before transport, and aggregates multi-request receipts.
- Enforces aggregate provider-request budgets across nested fan-out and CUA runs, with identical preflight and wire serialization.

## 0.3.2

- Ships as a root-layout native Hermes plugin with `plugin.yaml` and a root `register(ctx)` entrypoint.
- Provides `jev_skill_select`, `jev_model_route`, and the Windows-only `jev_computer_use` pilot.
- Requires `public_or_sanitized_data_ack: true` before model-facing operations; this remains a caller attestation, not DLP or authorization.
- Keeps endpoint, model aliases, provider fallback behavior, action budgets, hotkey allowlists, and target re-capture checks closed in code.
- Returns advisory selections and `completion_candidate` results without loading skills, changing the runtime model, or certifying GUI completion.
- Verifies behavior with offline synthetic transports, dispatch fixtures, portability checks, and a source-bounded release archive.
