# Jev web capability review

**Recommendation:** reuse bounded question wording, lossless chunk accounting and explicit unknown/local-only states, not the upstream plugin wholesale. Extend Switchyard's existing retrieval owners; keep any new web observation default-off and non-applying until independently qualified. Injection risk, relevance, evidence sufficiency and permission to act are different contracts.

## Source pins and scope

- Switchyard baseline: `2a90fcfc33b73c1a61eb821f5b35ba33f4871d9c`. References below describe that baseline, not concurrent implementation work.
- Reviewed upstream: [`kerpopule/hermes-jev-skills@12a7a703ed8069f0c4791844bbb807f2b2d5299c`](https://github.com/kerpopule/hermes-jev-skills/tree/12a7a703ed8069f0c4791844bbb807f2b2d5299c), from an existing clean local Git checkout; no public fetch or provider call was needed. Upstream `LICENSE` is MIT, copyright Steve Darlow; preserve attribution if copying code.
- This is source review plus an isolated native-hook contract probe, not a new benchmark, implementation security approval or served-model measurement. Historical comparison uses a different upstream pin (`8316358bf60e98a21477582af829ef11c9cf52fb`); leave its evidence unchanged.

Upstream source references below are relative to the pinned tree; Switchyard references are relative to this repository.

## Ownership and reusable capabilities

| Concern | Pinned upstream | Switchyard baseline owner / transfer boundary |
| --- | --- | --- |
| Automatic web screening | `hermes/plugin/hermes-jev/__init__.py:695–724`: one `transform_tool_result` callback handles string `web_search`/`web_extract` results of at least 200 characters; modes off/on/shadow. Browser output is deliberately excluded. | `hermes_switchyard/__init__.py:1705–1717` already conditionally owns a transform callback for output compaction/stuck advice. Do not install a competing replacement callback. A non-replacing observer must not change ordering, return a replacement string, or duplicate dispatch. |
| Local retrieved-text warnings | `jevkit/rerank.py:452–480`: normalized local patterns include instruction/credential orders, risky commands and URL exfiltration; unjudged passages receive stricter local handling. | `hermes_switchyard/retrieved_screen.py` owns bounded local warning semantics. `session_search_rerank.py:635` observes these by default and only applies withholding when explicitly enabled. Reuse warning categories only through that owner, not as a new trust verdict. |
| Browser safety | Upstream web hook does not screen logged-in browser content. | `browser_use.py` calls `screen_page` when `retrieved_screen_enabled`; `retrieved_screen.py:100–133` also screens decoded URLs, labels and hrefs. `destination_policy.py` owns destination restrictions. A web-text score cannot replace either owner, native approval or fresh action evidence. |
| Structured extraction | `jevkit/webscreen.py:37–105,225–257`: chunks reconstruct page text exactly; paths identify replaceable fields; replacement retains JSON shape and unflagged chunks. | Useful design for an isolated bounded parser/coverage manifest. Shadow mode must retain the **original result bytes/object**, not parse/reserialize output merely because no text was withheld. |
| Memory ranking | `jevkit/rerank.py` can ask relevance and injection questions together. | `session_search_rerank.py` owns ranking, dependent anchors, budgets and FTS fallback; `memory_screening.py` adds advisory observations inside the existing approved request. Do not start a second memory request or reinterpret web scores as memory ranking. |
| Evidence search loop | `jevkit/search.py:162–336` ranks already-fetched results, asks sufficiency, and selects one of up to five caller-written queries or `none`; it does not fetch pages or generate prose. | No equivalent general automatic web-research loop is established here. `source_find.py`/`source_bundle.py` locate bounded local source evidence; `source_prefetch.py` recognizes narrow explicit location requests. Browser selectors choose offered targets, not sufficient evidence. Treat search-loop adoption as a separate pilot. |

## Question contracts

- **Injection:** upstream `rerank.injection_question(label)` asks whether a passage contains instructions aimed at an AI assistant: ignore rules, reveal data, run commands, change behaviour, or fetch/render/include a link/image carrying conversation or private data to another server. `webscreen.py:163–192` sends this as `{"type":"noul","instructions":...}` under `inj_N`; the validated answer is `answers.inj_N.noul`, with a flag threshold of `0.5`. It is not a probability that the page is true, relevant, public or authorized. Upstream comments about measured wording/calibration are upstream claims, not independently reproduced results here.
- **Relevance:** memory/search reranking asks about usefulness to a particular query. The automatic upstream web screen deliberately asks **no relevance question** (`webscreen.py:10–14`). Do not attribute reranker/search-loop quality to that hook.
- **Sufficiency:** upstream `search.py:293–307` asks whether the selected passages together contain enough evidence to answer without another search, explicitly rejecting a missing named fact, figure, date or source. This bounded judgment is not factual verification or source authority. With failure it remains unknown rather than sufficient.
- **Next query:** `search.py:115–137` uses a typed `choice` over caller-supplied options plus `none`. A model does not invent the query. Round limits and failed-reading exits prevent an endless search loop; they do not prove an answer is correct.
- **Local-source lookup:** Switchyard `source_find.py:132–141` asks `where` (offered passage IDs plus `NONE`) and `exists` (direct answer present). Its local evidence/absence semantics must not be replaced by a web sufficiency score.

## Reusability limits worth preserving explicitly

1. **Coverage is not universal.** Upstream web units cover search `title`/`description` and extract `title` plus `content`/`text`/`markdown`/`raw_content`. Standalone URL fields are omitted (`webscreen.py:78–105`), unlike `search._text`, which includes the URL. Unknown shapes become raw text, but malformed nested data can produce a fail-open verdict. Recognized JSON can contain additional unscreened fields. Do not label the whole result screened from a partial field count.
2. **“Whole chunks” is narrower than whole results.** Extract bodies are chunked at 900 characters; search descriptions and titles are not chunked. Outbound `privacy.redact(text, CHUNK_CHARS)` can clip those longer units. Chunk boundaries can also separate a directive from its context. Count bytes/characters, fields and unjudged remainder before claiming full coverage.
3. **Local and model verdicts differ.** Upstream separates locally raised/plain units into different requests and permits Jev to clear local flags for judged units. Unjudged units retain stricter local flags. This is enforcement policy, not an appropriate automatic transfer into Switchyard's independent local warning owner.
4. **Budgets differ.** Upstream permits grouped batches and parallel requests (`webscreen.py:146–181`), not one guaranteed physical request. A shadow score is not “free”; bound and account for actual dispatch, timeout and uncertain completion. Importing the upstream transport would bypass Switchyard's existing fixed endpoint, egress, deadline and receipt contracts.
5. **Privacy remains independent.** Upstream supports private-profile local-only operation and sensitivity/redaction heuristics. These do not establish public classification, authenticated source ownership or comprehensive DLP. Check the entire bounded candidate input, URLs, query and all extra fields before truncation or serialization; a standing acknowledgement is not blanket egress authority.
6. **Failure is not clean.** Upstream reports local-only/none/partial coverage. Its plugin calls `withhold` in on mode based on flagged units even when remote screening fails; do not infer exact fallback behaviour from the module introduction alone. Switchyard shadow failure must leave successful primary output and existing local handling intact.

## Independent security acceptance criteria — run after implementation

These criteria were selected from the pinned contracts, independently of the concurrent web module. They are review gates, **not completed tests**:

- **Non-interference:** byte/object-identical primary outputs with feature off and shadow on, including provider error, malformed response, observer exception, cancellation and timeout. No new transform replacement or changed local/browser/ranking outcome.
- **Egress authority:** unknown/private/credential/PII-bearing query, title, body, URL, nested metadata and suffix beyond a display limit must yield zero hosted dispatch. Test model-authored public claims, encoded secrets, Unicode normalization and bounded-input overflow. No secret/config lookup merely because a disabled hook fired.
- **Native scope:** require actual native profile/session/task/turn/call identity; refuse missing/blank/forged scope and unrelated tool names. Match source/query/policy/route/credential generation before dispatch and consumption; clear/invalidate on stop/reset/unload and changes in flight. Missing authoritative provenance must abstain, not borrow a process-global value.
- **Concurrency and accounting:** same-turn overlapping calls, cross-profile/session calls, duplicate hooks, late responses and retries cannot share observations accidentally. Count physical fixture requests, including failed/uncertain dispatch; no hidden retry, fan-out or extra request when existing-request sharing is claimed.
- **Parser and score integrity:** empty/unknown/malformed JSON shapes, duplicate keys/IDs, unknown fields, raw text, long titles, long descriptions, URL-only attacks, boundary-split attacks and chunk overflow get explicit coverage/abstention. Reject booleans, non-finite/out-of-range values, missing answers and wrong question IDs rather than treating them as low risk.
- **No authority amplification:** malicious instructions cannot authorize network access, choose tools, change approvals, rewrite rankings or select a new endpoint. Benign quoted attacks, security documentation and install commands must be represented in a frozen holdout to measure false positives and clean-evidence loss separately from attack recall.
- **Artifact/lifecycle:** default-off after installed-archive load and fresh-process restart; no credentials, raw text, URLs or persistent cross-session scores in receipts. Verify packaged module inclusion and native dispatch, not checkout imports alone. Keep production configuration, native core and historical ledgers hash-identical.

## Native hook and offline-test qualification

The cached native source inspected is Hermes `dad0057271f14da3278a91afd438c901b2fcc5f3`. It is not an entirely clean checkout: one pre-existing catalog YAML modification was observed; do not reset or describe it as pristine.

`model_tools.py:643–653,843–860` provides the real `transform_tool_result` kwargs:

```text
tool_name, args, result,
task_id, session_id, tool_call_id, turn_id, api_request_id,
duration_ms, status, error_type, error_message
```

Absent identity becomes `""`. There is **no `user_task` or query field** in this transform payload; a relevant task/query must come from an independently qualified native seam, not invented kwargs. `post_tool_call` additionally includes `middleware_trace` (`model_tools.py:674–695`). Transform runs after the post-tool observer; first string return wins, and exceptions leave the original result. An isolated execution of the actual transform function with a fake lifecycle dispatcher verified these kwargs and first-string precedence without native/plugin startup or network. This is a call-contract probe, not a full lifecycle run.

For a full native regression, reuse the previously qualified Python 3.11 environment, an environment allowlist, disposable `HOME`/`HERMES_HOME`/shared-auth state, default profile, empty bundled plugins and disabled project-plugin discovery. The prior checkpoint used an external-socket audit guard (loopback allowed for fixtures); packaged native workers also block sockets independently. That historical guard is not an OS-level network sandbox. A separate current-workstream runner additionally installs inherited Linux seccomp denial for `connect`, `sendto`, `sendmsg` and `sendmmsg` before test discovery and probes that denial; prefer it with a clean environment for strict no-network execution. Its library is available, but this review did not execute the full suite or establish that every loopback-dependent test passes under the stronger policy.

The baseline full-suite report records 1,980 tests with no failures/errors and 16 documented skips. Those results belong to the baseline, not this review or the pending implementation. Several archive checks intentionally build from committed Git source (`test_windows_installed_archive.py:43–46`, `scripts/build_release.py`). Before final qualification, materialize the reviewed tree in a disposable committed fixture as the prior checkpoint did; do not weaken archive verification to read dirty worktree files. Review temporary-fixture commit permission separately from the prohibition on committing this development branch.

Protected state includes historical Stage A/A2 reports and ledgers, archived evidence, `stage-a2.patch`, frozen comparison/holdout inputs, native core and production profile config/auth files. Compare hashes without printing contents. This review rechecked the available historical preservation manifest (1,243 files), current workstream preservation manifest (1,281 files) and production config/auth baseline (six files): no hash changes or missing files. Preserve the untracked web holdout inputs too. Exact local source, manifest, interpreter and command locations belong in the private handoff, not public repository documentation.
