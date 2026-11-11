# Switchyard documentation map

Find the right page for what you want to do. Pages near the top are for everyone. Pages further down get more technical.

## Getting started

| Page | Read it when… |
| --- | --- |
| [Project README](../README.md) | You want the overview, the quickstart, and the common settings. |
| [Concepts](CONCEPTS.md) | Hermes, Jev, skills, toolsets, or receipts are new to you. |
| [Setup guide](SETUP.md) | You are installing, adding a key, or checking that tools show up in a session. |
| [Configuration reference](CONFIGURATION.md) | You want to change any setting. |
| [First-run check](FIRST-RUN.md) | You are curious how long a clean install took in testing. |

## Feature guides

| Page | Feature | On by default? |
| --- | --- | --- |
| [Adaptive reasoning effort](ADAPTIVE-REASONING-EFFORT.md) | Lowers `/reasoning` on easy turns and shows a `Reasoning: …` receipt line | Yes |
| [Automatic skill routing: setup](AUTOMATIC-SETUP.md) | Picks and loads a matching skill each turn | Yes |
| [Automatic skill routing: how it works](AUTOMATIC-INTEGRATION.md) | The full flow, privacy gates, receipts, and diagnostics | — (reference) |
| [Browser and desktop computer use](DOM-BROWSER-BACKEND.md) | `jev_computer_use` for public web pages and desktop apps | Tool only; the model calls it |
| [Automatic source prefetch](SOURCE-FINDER.md) | Finds an exact passage in a file you name | No (opt-in pilot) |
| [Approved model routing](MODEL-ROUTING.md) | Recommends a model from your approved list; never switches | Tool only; advisory |
| [Session search re-rank](SESSION-SEARCH-RERANK.md) | Reorders past-session search results | Tool only |
| [Memory screening shadow](MEMORY-SCREENING-SHADOW.md) | Observes relevance/injection inside approved retrieval requests; never applies scores | No |
| [Skill description lint](LINT-SKILLS.md) | Flags skills whose descriptions are too similar to tell apart | Command only |
| [Local observation report (`wow`)](WOW-LOCAL-REPORT.md) | Summarizes what Switchyard recorded on this machine | Command only |
| [Offline outcome labels](OUTCOME-LABELS.md) | Labels retained receipts for offline analysis (new in 0.6.0) | Command only |
| [Record triage demo](RECORD-TRIAGE.md) | A library example that triages bug reports with `jev_assess` | Library only |

## Evidence and evaluation

| Page | What it covers |
| --- | --- |
| [Benchmarks](BENCHMARKS.md) | Measured accuracy, latency, and cost, with their limits |
| [Awesome-Jev experiments](AWESOME-JEV-EVALUATION.md) | Ideas borrowed from community Jev projects and how they tested |
| [Value evaluation policy](VALUE-EVALUATION.md) | The rules a new feature must pass before it ships |
| [Feature and test matrix](TEST-MATRIX.md) | What each test suite does and does not prove |

## For maintainers and contributors

| Page | What it covers |
| --- | --- |
| [Contributing](../CONTRIBUTING.md) | Local checks and pull-request expectations |
| [CI](CI.md) | What each CI workflow runs and why |
| [Release process](RELEASE.md) | How a release candidate archive is built and verified |
| [Changelog](../CHANGELOG.md) | What changed in each version |
| [Security](../SECURITY.md) | How to report a vulnerability |
| [Third-party references](../THIRD_PARTY.md) | Upstream guidance this plugin follows |

## Experimental opt-in features (new in 0.6.0)

[Tool review, retrieval screening, output handling, browser plan caching, and catalog review](FEATURE-EXPANSION.md) describes these opt-in features and their limits. Native context compression remains unchanged.
