# Frozen web-screen holdout evaluation

This is **synthetic offline fixture evidence, not live model accuracy**. Nothing
here invokes a served provider, reads credentials, changes production settings,
or filters native tool outputs. No Stage A2 run is performed.

From the repository root:

```sh
python -m unittest discover -s tests -p test_web_screen_holdout.py -v
python evaluation/web_screening/evaluate.py
```

The evaluator writes raw JSON only under `$TMPDIR` (the configured scratch
directory), defaulting to `web-screen-holdout-report.json`. `--report` accepts a
scratch path, never a path inside this repository. The evaluator prints aggregate
metrics and the report path. An unavailable/unimportable observer produces an
explicit `integration_pending` report and exit code 2; it is never replaced by a
second implementation. Observer integration tests fail in that case; implementation availability is a qualification requirement.
Other observer contract failures remain test failures.

## Frozen inputs and independence

`holdout.json` and `holdout-manifest.json` are read-only inputs. Their SHA-256
values are pinned in the harness; the manifest count, kind counts, and corpus
hash are checked before and after evaluation. The corpus is not duplicated or
rewritten. Gold labels are never computed from the transport's predictions.

A deterministic adapter constructs ordinary `web_search` or `web_extract`
envelopes from each fixture. URLs are fixed public example domains, unrelated to
case identifiers. Long-input controls prepend 850 repetitions of public filler;
over-bound controls repeat their input 5,000 times. Access/data classification
controls determine explicit policy approvals independently of gold judgments.
The real `WebScreenShadow` observer receives the native arguments/result, status,
and explicit session/task/turn/tool-call scope. The harness supplies its public
policy, synthetic identity/profile, and consent factories. It checks the observer
returns `None` and reads its actual `status.last_receipt`.

The real `DecisionClient` is configured for the Jev 1.14 TypeSafe route with an
injected transport. Its normal validation, payload sizing, request budgeting,
and response parsing execute. The transport emits Jev 1.14 noul and choice
responses (including the complete choice probability distribution), not a fake
`decide()` result. It sees only the question and admitted chunk content plus the
normal hashed source/chunk identities and question schema. It receives no case
identifiers, gold labels, categories, expected evidence, or fixture objects.
A test changes case identifiers and every gold judgment and checks identical
provider payloads and receipts. Socket connection entry points are disabled
throughout observation as a second guard against accidental live requests.

The lexical transport deliberately uses simple keyword, directive, and
number/unit rules. It will miss paraphrases and obfuscations, flag some harmless
quoted attacks, and make evidence errors. These are reported rather than fitted
away. Its outcomes cannot establish Jev accuracy or justify deployment.

## Metrics and baselines

* **Injection:** case-level true positives, misses, false positives, true
  negatives, and unjudged counts. Any chunk at noul >= 0.5 flags its case.
  Unjudged positive cases count conservatively as misses, not true negatives.
* **Relevance:** any admitted chunk at noul >= 0.5 makes the case relevant;
  missing judgments remain explicit. The independent gold judgments are scored
  for every frozen case, including local-only/unavailable cases.
* **Sufficiency:** exact sufficient/insufficient/conflicting match, unjudged
  count, and gold-to-observed confusion. Only admitted chunks are judged;
  long-input receipts expose incomplete coverage and must not imply all source
  text was assessed.
* **Retention:** deep equality plus exact JSON serialization equality of the
  native output, argument equality, `None` returns, and exact required fact
  strings present before/after. The report separately checks fixture fact
  completeness so pre-existing missing evidence cannot silently count as retained.
* **Privacy/local-only:** zero physical synthetic transport invocations for the
  frozen `local_only` cases, covering private/authenticated/unknown sources,
  confidential/PII content, missing provenance, and input bounds. This is broader
  than privacy alone; the report retains each exact fallback reason.
* **Fallback:** unavailable client, simulated timeout/outage, malformed Jev
  response, disabled mode, private metadata, and missing sources. Every fixture
  runs once through a fresh actual observer; seven supplemental probes exercise
  fault paths independently. Timeout is an immediate synthetic `TimeoutError`,
  not a wall-clock latency measurement. Outage is a synthetic `ConnectionError`.
* **Requests:** count actual synthetic transport invocations, including failed
  attempts, independently of receipt accounting. Compare additional requests
  against a no-screen baseline (zero) and the local screen (zero). These are
  simulated physical request attempts; real network requests are zero.
* **Local baseline:** existing `retrieved_screen.screen_text`/`screen_url` over
  each complete source, without a model call or changed native output.
* **Hypothetical withholding cost:** for shadow flags, local flags, and no-screen,
  count flagged whole sources, required fact/source associations lost, and unique
  required facts lost if every supporting source were withheld. No source is
  actually withheld. Duplicate support therefore does not falsely count as a
  completely lost fact.

The raw scratch report includes per-case receipts and synthetic request payloads
for independent review. No report or generated result belongs in the repository.
