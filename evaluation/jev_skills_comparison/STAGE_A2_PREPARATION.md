# Stage A2 — held-out downstream correctness: preparation only

**Status:** evaluator and public synthetic case set prepared; **no downstream model calls have been made**. The owner has requested continuation of the 48-request paired study without a dollar-ceiling gate. The read-only scorer does not execute requests. This stage is separate from the completed Stage A Jev decision-only trial. Do not restart or reset the Stage A ledger, or replay a prior case/arm through it.

## Why this experiment exists

Stage A observed that both advisers proposed Luna for extraction, formatting and arithmetic, while Jev Skills proposed Luna and Switchyard retained Sol for one code-reading task. **This does not tell us whether Luna actually answers correctly.** Stage A also did not test tool-dependent work or full turn response costs.

This proposal freezes **24 new, public, self-contained prompts**, with six each for extraction, formatting, arithmetic and Python code reading. Each prompt has a deterministic expected response and a predeclared, strict `strip_exact` scorer. These are additional synthetic cases prepared before any downstream model execution, not an untouched model benchmark or a measurement of real Coble/Hermes traffic. They were not Stage A decision prompts.

Both the current `openai-codex:gpt-6.1-sol` and candidate `openai-codex:gpt-6-luna` are intended to answer every prompt in a **paired direct-model study**. No model router is to be activated. No prompt contains customer content, external tools, files, private memory, production state or irreversible operations. No Python snippet is executed by the scorer; the answers are fixed in the plan.

## Frozen plan and offline preflight

The two model identifiers are the Stage A owner-approved candidates. The owner has now directed preparation of the separate 48-request downstream study **without a monetary ceiling**. This is a finite experimental scope, not blanket authorization to alter routing, use other models, run extra requests, or deploy. `correctness.py` only scores an existing JSONL file and does not contain any client or dispatch code.

The preflight calculates a SHA-256 digest across the full plan: models, prompts, expected answers, labels and scoring. Freeze and review that digest before any real task execution. The preflight output reports `dispatch_available: false` because this evaluator is read-only; it is **not** a judgment about the owner’s study authorization.

```text
python3 evaluation/jev_skills_comparison/correctness.py --preflight
python3 -m unittest discover -s tests -p test_jev_skills_correctness.py -v
```

Store live observations outside the repository under a new durable study directory, with a new immutable study identity and request ledger. Never initialize a new ledger to replay the original Stage A Jev trial. The new Stage A2 ledger must record **up to 48 distinct direct-model request intents** before dispatch; leave ambiguous dispatch/usage explicitly unresolved and do not replay uncertain requests. Track usage and estimated provider charges without imposing a dollar ceiling or pre-reserving a dollar amount.

## Proposed evidence format, one record per case/model

The example below is **fictional fixture format**, not a provider result or a price claim. Each row must bind the complete plan digest and that case's prompt digest; record unique, immutable attempt IDs. The `observed_direct` label is only a caller assertion until independently matched with original provider/runner receipts.

```json
{
  "case_id": "code-a",
  "model_id": "openai-codex:gpt-6.1-sol",
  "plan_sha256": "<preflight reported SHA-256>",
  "prompt_sha256": "<SHA-256 of exact UTF-8 prompt>",
  "attempt_id": "<independent immutable request identity>",
  "evidence_mode": "fixture",
  "routing_applied": false,
  "status": "completed",
  "answer": "3",
  "elapsed_ms": 123,
  "input_tokens": 80,
  "output_tokens": 5
}
```

Use `status: failed` or `uncertain` (with `answer: null`) if completion is not known. Usage may be `null` in those states. Missing, failed, uncertain, duplicate and mixed-evidence cases are reported as incomplete or refused; unknown requests are **not** treated as zero billable cost. Real evidence must have provider/account, exact outgoing prompt/model, observed effective model, request/response identity and usage independently verified **by a separately built and reviewed collector**. This scorer does not perform such verification.

Once a separate, owner-approved collection has been completed, validate the file read-only:

```text
python3 evaluation/jev_skills_comparison/correctness.py --observations /separate-study/observations.jsonl
```

Output distinguishes Sol-only correct, Luna-only correct, both correct, both wrong and not scored. Per-category exact-match scores, completed-model latency and known response token totals are reported. The scorer does **not** infer list-rate model prices, actual invoices, effective runtime billing, routing gain, end-to-end savings or permission to promote. **`promotion_authorized` is always false.** The tool returns nonzero for incomplete or invalid evidence.

## Execution prerequisites (no dollar-ceiling gate)

Before dispatch, verify and durably record these prerequisites. The owner authorized continuation of the finite 48-request study without a dollar ceiling; missing model, source or credential evidence must still stop the run:

1. Exact account/provider/model access and approved public outbound prompts, with original + effective model readback (no unapproved account or paid fallback).
2. Exactly **24 frozen cases × 2 approved main models = at most 48 unique direct-model requests**, with fixed per-request output-token and timeout bounds. No dollar ceiling, financial reservations or monetary halt condition. Record reported input/output tokens, list-rate cost estimates where grounded, actual invoice status if obtainable, and unknown amounts separately. The old Stage A TypeSafe Jev ledger and its $0.40 historical reservations are unrelated and must remain untouched.
3. Explicit no-retry and uncertain-dispatch policy, a new separate durable ledger, and stop-on-failed preflight or changed source/plan/account.
4. A pinned, independently reviewed execution collector and runtime revision, truly isolated home/session state, no tools/messaging/cron access, and offline harness tests before first paid call.
5. Paired order/randomization and independent verification of answers, request usage and execution outcomes, with the case file remaining unchanged throughout.

This first task-quality study could measure **direct Sol vs Luna correctness** for these limited cases. It still cannot prove that a specific router would choose optimally on new turns. That requires a further separately scoped fresh classifier decision study and direct end-to-end observation, not reuse of Stage A decisions on different prompts.

## Status and next gate

- Prepared offline: frozen public task set, immutable plan identity, read-only evidence scorer, synthetic regression tests.
- Not executed: real Sol/Luna calls, Jev calls, classifier comparison, billing verification, production integration or gateway restart.
- Gate to dispatch: verify frozen tasks and approved model/account availability, pinned and independently tested collector, immutable intent ledger, fixed 48-request scope, and privacy boundaries. No dollar-ceiling approval or reservation gate.
- Keep draft PR #1 unmerged and production source unchanged.
