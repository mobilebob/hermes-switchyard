# Explicit-call web-screening prototype

This default-off experiment asks Jev for **relevance, prompt-injection risk and evidence sufficiency in one request per result**. It uses the existing `DecisionClient`, public-URL policy and egress redactor. It installs no hooks, routing changes, retrieval, authorization framework or result filtering.

The prototype is observation-only. The original result object, source metadata and citations are never changed. Ignore the observation on failure and let Hermes behave normally. Packaging the module does not enable it or establish that it improves final answers.

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

Use a dedicated client; the worker closes its connection when work finishes. Public-source and data acknowledgements are trusted caller attestations, not fields read from a page. They are not an authorization or DLP system. Private, authenticated or sensitive material must stay local. Local checks scan the whole bounded result, including metadata, before sending. Public HTTPS URLs with userinfo, query strings, fragments or nonstandard ports are rejected. Retrieved instructions are untrusted data, never commands.

The entire bounded result is sent, including buried facts: at most 24,000 content characters, a 96,000-character/byte JSON envelope and the client's existing payload limit. Oversized results abstain rather than silently dropping an answer. These are prototype limits, not full-page coverage.

## Request and fallback contract

- Disabled or locally rejected invocations make no request.
- An admitted invocation uses one request containing all three judgments.
- The overall caller deadline is 30 seconds; callers may select a shorter timeout.
- Transport retries are disabled, including rate-limit and stale-connection replay. No uncertain request is retried automatically.
- Failed, late, cancelled or malformed responses provide no judgments. Keep the original result and continue normal Hermes behavior.
- A daemon worker cannot undo already sent bytes or forcibly stop blocking DNS or a non-cooperative transport. Late results are discarded.
- The observation is returned to the explicit caller; no receipt files, raw captures or forensic instrumentation are written.

Judgments are independent probabilities thresholded at 0.5. Relevant but incomplete content may be relevant and insufficient. Useful facts beside an actual injection may be relevant, injection-bearing and sufficient. A clearly quoted attack example is not inherently an injection attempt. These judgments are advisory, not security guarantees.

## Offline verification and scope

```text
python -m unittest discover -s tests -p test_web_screening_prototype.py -v
```

The mechanical tests use six synthetic documents under `tests/fixtures/web_screening_prototype.json`: relevant, irrelevant, missing evidence, buried answer, malicious instructions and harmless quotation. Neutral document URLs do not expose expected labels. IDs and gold labels are excluded from outbound model state. The buried answer occurs after more than 12,000 characters and is sent in full. Mock responses verify protocol and fallback behavior, not model accuracy.

This minimal port preserves the prototype's three prompts, thresholds, request contract and fallback behavior. Privacy helpers are local to this module to avoid importing the historical shadow-observer implementation. The only shared-client extension is an optional bounded transport-retry budget; existing callers retain their previous default. The historical live evaluation runner and results are intentionally not included or rerun.

No production configuration is changed. No comparative answer-quality benefit has been measured. The next milestone is measuring actual Hermes answer quality with and without screening, under a separately approved evaluation; this publication does not perform that test or enable automatic screening.
