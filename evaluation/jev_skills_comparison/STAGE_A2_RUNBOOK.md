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
