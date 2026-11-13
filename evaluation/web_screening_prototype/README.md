# Six-case web-screening experiment

See [the prototype guide](../../docs/WEB-SCREENING-PROTOTYPE.md).

- `cases.json` contains six fresh synthetic documents and predeclared expectations.
- `evaluate.py` uses the existing Jev client, three judgments in one request, a 30-second caller deadline and no retries.
- It is live only with `--live`, uses the existing TypeSafe process credential and pins `jev-1.14.0`.
- Maximum six requests, one attempt per case; stop on the first error or uncertain response.
- Expected labels and case IDs are never sent to Jev. Source URLs are neutral identifiers. Documents are synthetic and are never fetched.
- Offline mocks prove behavior, not accuracy. Live label accuracy is a small smoke test, not a live-web benchmark or measured Hermes answer improvement.
- Save results outside the repository. Do not replay the historical halted evaluation.
