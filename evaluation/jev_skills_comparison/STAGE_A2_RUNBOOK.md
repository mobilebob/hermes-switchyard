# Stage A2: direct-model correctness runbook (owner-authorized scope)

This runbook prepares **only** the direct Sol/Luna correctness comparison. It does not execute any main-model call, install another plugin, start a gateway, change a profile, run Jev decisions, or promote model routing. The owner directed this study to continue **without a dollar ceiling**. No spending reservation or monetary cap is required. The **48 distinct request** scope, privacy rules, token/output limits, and non-retry controls remain binding.

## Scope and frozen identity

- Study: `public-synthetic-downstream-model-correctness`.
- Plan: `heldout_cases.json`, fixed 24 synthetic cases, six per category.
- Models: `openai-codex:gpt-6.1-sol` and `openai-codex:gpt-6-luna` on the **same owner-approved provider/account**.
- Execution: each pair directly requests both models, *without* changing a production route or activating a model router. Up to 48 unique main-model requests total, zero additional Jev requests in this stage.
- No tools, memory, web, skills, background jobs, file input, customer data, session history, messages, scheduling, or connection to live Coble systems. Reject attempts to add capabilities or substitute other models.
- Validate exact source and plan hash, provider connection, effective model, and token/output caps before execution; stop on mismatch or missing provenance. A clean checkout is not proof a live gateway loaded matching code. Do not use the gateway.

## Collection driver requirements

1. Use a **new independent study directory** outside the repository. Never import or write the original Stage A ledger. Write immutable trial metadata: source SHA, task-plan SHA, model/provider identifiers, interpreter/runtime and collector SHA.
2. Before each network dispatch, fsync an intent record binding a stable ID to the exact case, model, prompt, body/hash, provider/account, output-token limit and attempt index. Reject duplicate case/model pairs and any previously attempted intent on resume. Intent uncertainty persists and forbids replay.
3. Use an owner-controlled execution process and separate empty session/home per request (not default/Coble profile). Keep secrets out of worker arguments, files, logs and prompts. Explicitly block tools, fallback models, user memory and messaging. Verify the effective model through provider/runner evidence; a requested model name alone is insufficient.
4. Interleave the frozen case/model order or use a precommitted deterministic shuffle seed. Capture request/response IDs, exact effective model, answer, status, elapsed wall time, input/output tokens and usage/billing evidence without publishing raw account details. No retry on uncertain dispatch or timeout; record `uncertain` instead. Fail closed on any source, plan, account or model mismatch.
5. Record usage and estimated cost as **observability**, not a financial authorization control. List rates are estimates, never invoices. Unresolved usage must remain explicitly unknown, not `0` or `free`. Do not silently add a monetary ceiling through the runner's defaults.
6. Do not conceal unscored, failed, late, incorrect or excluded cases. No automatic approval, promotion, production model switch, or claim of realized savings.

## Read-only commands

From a clean evaluation-branch checkout, before any model dispatch:

```text
python3 -m unittest discover -s tests -p test_jev_skills_correctness.py -v
python3 evaluation/jev_skills_comparison/correctness.py --preflight
```

After a separately reviewed and locally executed collector produces `observations.jsonl` in a durable external study directory:

```text
python3 evaluation/jev_skills_comparison/correctness.py --observations /path/to/new-study/observations.jsonl
```

The scorer validates the declared observation format and task-plan hashes; it **does not** independently authenticate provider receipts. A reviewer must cross-check raw provider/runner evidence and actual dispatched/effective model before treating `observed_direct` rows as live results.

## Existing work preserved

The earlier TypeSafe Jev Stage A trial (20 paid Jev requests, all HTTP 200, no main-model calls) and its durable ledger are historical evidence and **must not** be reset, deleted, reused or replayed. The Stage A manual-model-pin safety rejection remains blocked in its report. The signed draft PR remains evaluation-only. A future production release or activation requires a separate owner decision and verifiable outcome evidence.

## Isolated Stage A2 execution collector (evaluation-only)

`stage_a2_collector.py` and `stage_a2_native_worker.py` implement a separate,
sequential, **up-to-48 logical request** collector. They are not registered Hermes
plugins. The native worker uses the same `hermes --print-runtime-command`
launcher approach as existing Switchyard native evaluation runners, and constructs
an `AIAgent` with no tools, plugins, memory, history, fallback, background review
or default/Coble gateway context. Every call receives a fresh empty trial home.
Only the approved Codex Sol/Luna pair is allowed. The worker sets a 128-token
output limit and allows at most one model-stream invocation per process.

Offline validation (no credentials, no provider calls):

```text
python3 -m unittest discover -s tests -p test_stage_a2_collector.py -v
python3 -m unittest discover -s tests -p test_jev_skills_correctness.py -v
python3 evaluation/jev_skills_comparison/correctness.py --preflight
```

Preparation (creates an independent external study directory and durable SQLite
ledger, but **does not dispatch**):

```text
python3 evaluation/jev_skills_comparison/stage_a2_collector.py \
  --prepare --output /approved/external/stage-a2-new-run
```

Inspect existing observations and current progress without new model calls:

```text
python3 evaluation/jev_skills_comparison/stage_a2_collector.py \
  --inspect --output /approved/external/stage-a2-new-run
```

A future, separately controlled live invocation requires a specific selected
Hermes **authentication home** and the exact installed **Hermes source root**;
these must be checked by the operator, not inferred from a shared gateway. It
also requires `--execute --authorize-direct-48 --max-new-requests N` with N in
1..48. Start with N=1 only after reviewing the native wire/transport on the
actual Hermes version. The CLI does **not** create any model calls at `--prepare`
or `--inspect` time. This branch's Python fixture tests **do not** establish
that the current Hermes runtime can execute either model or that the Codex
transport has no internal HTTP retries. Inspect the loaded runtime and transport
behavior before attempting real dispatch; the worker's one-stream guard counts
logical invocation, **not independently verified physical HTTP attempts**.

### Durable records and failure behavior

- New independent `manifest.json` pins frozen task SHA, source HEAD and collector,
  native-worker and scorer file hashes. An existing output folder is never reset.
- Every case/model is unique in SQLite. An `intent` is committed with SQLite
  `synchronous=FULL` **before** launching the model process. Each source/prompt,
  output-cap and model binding is checked again before later intents.
- If the worker fails, times out, reports a different effective model or sends an
  unusable receipt, the attempt remains `uncertain` with unknown usage. A crash
  between intent and record leaves an unresolved durable intent. Neither is
  replayed; both block subsequent live execution pending review.
- Observations, comparison summaries and response text remain in the external
  directory, not in Git, and permissions are restricted. The scorer still marks
  them `observed_direct_unverified`; a separate trusted review of original
  provider/runner evidence is needed for independent verification.
- Reported token usage is recorded; **there is no dollar ceiling, billing gate
  or spending reservation**. Unknown usage is never treated as zero. No
  production routing, deployment or promotion is authorized by any output.

The previous Stage A TypeSafe Jev decision ledger is neither read nor modified.


## Native retry-boundary finding and offline transport audit

Hermes' native `agent.codex_runtime.run_codex_stream` permits **one outer
physical-stream retry** for selected connection failures (`max_stream_retries = 1`
in the reviewed upstream source). `agent.client_lifecycle` creates its
request-scoped OpenAI SDK client with `max_retries=0`, but the prior Stage A2
worker counted `_run_codex_stream` calls, **not physical HTTP requests**. That
count alone never proved a no-retry wire contract.

The new worker guards the actual synchronous httpx `request` event hook:
only one HTTP POST to the official Codex `/backend-api/codex/responses` endpoint
is allowed. It rejects a missing request-scoped client, SDK retries other than
integer zero, missing/changed output cap, unexpected tools/history/model, an
unknown HTTP transport, redirect or second request. It does not pretend that an
SDK/transport hook proves the backend's effective model or an invoice charge.

**Read-only, no network**: Run the source audit using the *actual installed
Hermes Python interpreter*; don't use system `python3` unless it is the Hermes
runtime. The audit imports Hermes transport modules, examines the native retry
paths and executes a mocked `httpx` request/second-request guard, without
loading authentication, creating model requests or contacting providers. Its
output includes imported source paths and hashes. It always reports
`live_dispatch_authorized: false` regardless of pass status. The worker tests
also check the strict HTTP guard with generated, credential-free fake requests.

After obtaining Hermes' runtime prefix with `hermes --print-runtime-command`,
from the evaluation checkout, execute:

```text
python3 evaluation/jev_skills_comparison/stage_a2_transport_audit_launcher.py
```

The launcher queries the native Hermes runtime command, sets an isolated
throwaway `HERMES_HOME`, then runs the audit through that interpreter. It does
not use production gateway state and has **no execution flag**. This is a
separate inspection command from the collector's `--execute`. Do not treat the
source audit as proof of successful live Codex execution or provider billing.

**Remaining gate:** Verify the installed Hermes version and runtime module
identity, a request-scoped SDK client with zero internal retries, the HTTP guard,
and effective-model/usage readback with a separate controlled evaluation. No
live Stage A2 requests are performed in this patch, and the original Stage A
Jev ledger remains untouched. A preflight success does not authorize deployment.
