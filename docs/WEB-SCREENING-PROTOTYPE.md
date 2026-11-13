# Explicit-call web-screening prototype

This local experiment asks Jev for **relevance, injection risk and evidence sufficiency in one request per result**. It uses the existing `DecisionClient`; no additional transport, authorization ledger, lifecycle hook or Hermes-core change is introduced.

It is **off by default**, observation-only and not wired into automatic web-result handling or included in a released plugin. The original result object, source metadata and citations are never changed. Ignore the observation on failure and let Hermes behave normally. Automatic filtering is not implemented.

## Calling the prototype

```python
from hermes_switchyard.client import DecisionClient, TYPESAFE_ENDPOINT
from hermes_switchyard.web_screening_prototype import screen_result

client = DecisionClient(
    api_key=existing_key,
    endpoint=TYPESAFE_ENDPOINT,
    model="jev-1.14.0",
    timeout=30,
)
observation = screen_result(
    client,
    question,
    result,  # {"url": public_https_url, "title": title, "content": page_text}
    enabled=True,
    public_source=True,
    public_or_sanitized_data_ack=True,
)
# Keep using result exactly as received, regardless of observation.
```

Use a dedicated client for this explicit observer; it closes its connection when work finishes. Caller attestations are not read from page content. Existing local privacy/redaction and public-URL checks apply before sending. Private/authenticated/sensitive data must stay local. Retrieved instructions are never trusted commands.

The entire bounded result is sent, including buried facts: at most 24,000 content characters and the client's existing payload limit. Oversized results abstain rather than silently dropping an answer. This is a deliberate prototype limit, not full-page coverage.

The call has a **30-second overall caller budget**, one request budget and zero transport retries, including rate-limit/stale-connection retries. A failed, late, malformed or uncertain response yields no judgments. A daemon worker cannot undo already sent bytes or forcibly interrupt blocking DNS; late results are discarded. There is no automatic replay. The experiment stops after its first failed/uncertain result.

Judgments are separate probabilities thresholded at 0.5. Relevant-but-incomplete content may have relevance=true and sufficiency=false. Useful facts next to an actual injection may have relevance=true, injection=true and sufficiency=true. A clearly quoted example of an attack is not inherently an injection attempt.

## Six fresh synthetic cases

The corpus is `evaluation/web_screening_prototype/cases.json`: relevant, irrelevant, missing evidence, buried answer, malicious instructions and harmless quotation. Neutral document URLs do not reveal expected labels. IDs and gold labels are excluded from outbound model state. The buried answer is after more than 12,000 characters and is transmitted in full.

Run mechanical offline tests first:

```text
python -m unittest discover -s tests -p test_web_screening_prototype.py -v
```

With the existing TypeSafe key available in the process environment, a separately intentional live experiment is:

```text
python evaluation/web_screening_prototype/evaluate.py --live --output /path/to/new-results.json
```

The runner refuses to overwrite an existing output file, sends at most six requests, saves each case result and stops on the first failure. It never resumes historical runs. This ordinary output file is not an authorization or spending ledger. Mock responses in unit tests test protocol/behavior only; they are not model-accuracy evidence.

Results contain per-category label accuracy, elapsed time and observed usage. `elapsed_ms` measures caller end-to-end time. SDK latency metadata should not be interpreted as isolated provider compute time. Missing usage on uncertain requests is unknown, not zero.

## What this can establish

Six live synthetic cases can show whether the client works and whether the three judgments distinguish obvious useful, irrelevant, incomplete and adversarial material. They cannot establish broad accuracy, calibrated probabilities, safe automatic filtering or improved final Hermes answers.

The next useful integration step is an opt-in call owned by the existing retrieval/result path, followed by an answer-quality A/B evaluation. Do not add another result-mutation owner or automatically enable this prototype. Production configuration remains unchanged.
