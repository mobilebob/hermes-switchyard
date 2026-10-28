# Configuration reference

Every Switchyard setting, grouped by feature, with its default and what it does in plain language. The defaults are good for most people. You only need this page when you want to change something.

## How to change a setting

All settings live under `plugins.entries.hermes-switchyard.settings` in the active Hermes profile. Use `hermes config set`:

```text
hermes config set plugins.entries.hermes-switchyard.settings.<key> <value>
```

To go back to the default, unset the key:

```text
hermes config unset plugins.entries.hermes-switchyard.settings.<key>
```

List values are passed as one quoted YAML/JSON argument, for example `'["*astra*"]'`.

> **Restart after every change.** Settings are read when the plugin loads. Start a fresh Hermes session, and restart the gateway (`hermes gateway restart`) if you use messaging platforms. A running session keeps its old values.

An empty string (`""`) or empty list (`[]`) means "nothing configured." The defaults below match this release's `plugin.yaml`.

## The settings most people touch

| I want to… | Setting | Value |
| --- | --- | --- |
| Keep automatic skill routing on my machine | `automatic_skill_routing_mode` | `local_only` |
| Get skill suggestions without auto-loading | `automatic_skill_consumer_mode` | `advisory` |
| Turn automatic skill routing off entirely | `automatic_skill_recommendation` | `false` |
| Stop sending message text for effort decisions | `adaptive_reasoning_effort` | `false` |
| Always use my `/reasoning` level (no lowering) | `adaptive_reasoning_effort_mode` | `pinned` |
| Hide the `Reasoning: …` line under replies | `adaptive_reasoning_effort_receipt_mode` | `off` |
| Use a specific Jev provider | `jev_provider` | `typesafe` or `openrouter` |

## Provider and connection

| Key | Default | What it does |
| --- | --- | --- |
| `jev_provider` | `auto` | Which provider to call. `auto` uses TypeSafe when a TypeSafe key exists, otherwise OpenRouter. You can also set `typesafe` or `openrouter`. |
| `api_endpoint` | `https://openrouter.ai/api/alpha/decisions` | The Jev endpoint. Only the fixed TypeSafe (`https://api.typesafe.ai/v1/systemone`) and OpenRouter endpoints are accepted; anything else is refused. |
| `jev_model` | `""` | Jev model alias. Leave empty to use the provider default: `jev-latest` on TypeSafe, `typesafe/jev-1.13` on OpenRouter. |

API keys are **not** settings. Save them with `hermes switchyard setup --provider typesafe` (or `openrouter`), which uses a masked prompt.

For the direct TypeSafe `jev-latest` alias, Switchyard accepts strictly formatted
`jev-MAJOR.MINOR[.PATCH]` response versions from 1.13 onward, including future
major releases. Previously unseen versions produce a bounded once-per-process
warning. Explicitly pinned TypeSafe models and OpenRouter models still require
an allowed exact model; unrelated names, malformed versions, and invalid answer
schemas are rejected. This compatibility rule does not guarantee that future
model behavior will be equivalent to the current release.

## Adaptive reasoning effort

Lets Jev lower your `/reasoning` level on routine turns. Full guide: [ADAPTIVE-REASONING-EFFORT.md](ADAPTIVE-REASONING-EFFORT.md).

| Key | Default | What it does |
| --- | --- | --- |
| `adaptive_reasoning_effort` | `true` | Master switch. `false` turns the feature off and sends no message text to Jev for it. |
| `adaptive_reasoning_effort_mode` | `auto` | Starting mode for new sessions. `auto` may lower effort; `pinned` always sends your level. Change the current session with `/switchyard effort auto` or `/switchyard effort pin`. |
| `adaptive_reasoning_effort_exclude_models` | `[]` | Model name patterns (case-insensitive wildcards such as `"*astra*"`) the feature never touches and never calls Jev for. |
| `adaptive_reasoning_effort_allow_raise` | `false` | When `true`, effort may go **one** level above your cap while the latest tool call has failed. |
| `adaptive_reasoning_effort_step_adaptation` | `true` | In long turns, allows a few extra checks after routine read-only tool calls (at most one level below your cap). `false` means one decision per turn. |
| `adaptive_reasoning_effort_receipt_mode` | `auto` | The `Reasoning: …` line under replies. `auto` shows it when Switchyard decided something, `always` also shows pinned or pass-through turns, and `off` hides it. The legacy values `work` and `on` mean `auto`. |
| `adaptive_reasoning_effort_receipt_line` | `true` | Legacy on/off version of the setting above (`true` → `auto`, `false` → `off`). |
| `adaptive_reasoning_effort_deadline_seconds` | `0.4` | How long to wait for Jev, from 0.1 to 1.5 seconds. On timeout, your level is sent unchanged. |
| `adaptive_reasoning_effort_default` | `medium` | Deprecated and unused since 0.5.4. |

## Automatic skill routing: everyday settings

Picks and loads a matching skill at the start of a turn. Full guides: [setup](AUTOMATIC-SETUP.md) and [how it works](AUTOMATIC-INTEGRATION.md).

| Key | Default | What it does |
| --- | --- | --- |
| `automatic_skill_recommendation` | `true` | Master switch for automatic skill routing. |
| `automatic_skill_routing_mode` | `hosted_sanitized` | `hosted_sanitized` may ask Jev: after a local privacy scan when Hermes supplies no policy for the turn, or directly (redacted, not re-scanned) when Hermes authorizes the text. `local_only` uses only on-device matching, and `off` disables routing. |
| `automatic_skill_consumer_mode` | `load` | `load` loads the chosen skill through Hermes' normal loader. `advisory` only adds a suggestion to the context, and never calls Jev. |
| `automatic_skill_public_or_sanitized_data_ack` | `true` | Your standing agreement that turns sent for hosted routing are public or sanitized. `false` stops hosted routing. This is not a data classifier. |
| `automatic_skill_candidates` | `[]` | Empty uses every skill in the active profile. Otherwise, an explicit list of names or `{name, description}` objects to choose from. |
| `automatic_skill_mandatory_skills` | `[]` | Exact skill IDs that an automatic load must never displace. A conflicting pick is recorded as `mandatory_conflict` and not loaded. |
| `automatic_skill_platforms` | `[]` | Empty covers interactive platforms and skips API-server, batch, cron, webhook, and Kanban worker turns. `[all]` covers every platform; `[cli, telegram]` covers just those. |
| `automatic_skill_hosted_detail` | `names` | What Jev sees about finalist skills. `names` sends names only. `descriptions` adds short descriptions, and `excerpt` also adds short `SKILL.md` excerpts. Only send more if you have approved it. |
| `automatic_skill_light_turn_bypass` | `true` | Skips hosted routing for obvious no-skill turns ("thanks", a greeting, or "List the files in the current directory. Do not modify anything."). Listings of other paths still use Jev. |

## Automatic skill routing: tuning (advanced)

You rarely need these. Thresholds are local policy values, not calibrated probabilities.

| Key | Default | What it does |
| --- | --- | --- |
| `automatic_skill_jev_mode` | `always` | `always` asks Jev on every eligible turn. `uncertain_only` asks only when local matching is unsure, which saves latency but loses some accuracy. |
| `automatic_skill_honor_no_skill_gate` | `false` | When `true`, skip Jev in `always` mode when the request shares almost no words with any skill. |
| `automatic_skill_early_light_bypass_before_discover` | `false` | Run the light-turn check before reading the skill catalog. This saves a little work on greetings. |
| `automatic_skill_local_threshold` | `0.2` | Minimum local word-overlap score for a local pick (0–1). |
| `automatic_skill_local_margin` | `0.05` | How far the top local pick must beat the runner-up (0–1). |
| `automatic_skill_cache_seconds` | `30.0` | How long a recommendation is cached in memory (0–300 s). |
| `automatic_skill_deadline_seconds` | `20.0` | Total time budget for hosted routing on one turn. It stays below Hermes' ~30 s hook timeout. |
| `automatic_skill_two_stage` | `true` | Use the two-stage selector (shortlist, then recheck finalists). `false` uses the older single pass. |
| `automatic_skill_recheck_top_k` | `3` | How many finalists the second stage rechecks (1–8). |
| `automatic_skill_early_stop` | `true` | Allows a "no skill needed" signal to stop early. |
| `automatic_skill_early_stop_threshold` | `0.3` | Threshold for that early stop (0–1). |
| `automatic_skill_stage1_single_round` | `true` | Send all first-stage requests in parallel. `false` probes one first, which saves requests on no-fit turns but is slower otherwise. |
| `automatic_skill_stage1_min_probability` | `0.05` | Minimum first-stage score to become a finalist (0–1). |
| `automatic_skill_parallel_requests` | `4` | Maximum parallel first-stage requests (1–8). |
| `automatic_skill_jev` | `true` | Deprecated. Kept for old configs; it does not enable hosted routing by itself. |

## Automatic source prefetch (opt-in pilot)

Finds an exact passage in a named file before Hermes' first model call. Off by default. Guide: [SOURCE-FINDER.md](SOURCE-FINDER.md).

| Key | Default | What it does |
| --- | --- | --- |
| `evidence_finder_enabled` | `false` | Turns the pilot on. It also needs a root directory. |
| `evidence_finder_root` | `""` | Absolute path to a folder of public or sanitized text files you approve for lookup. Empty disables prefetch. |

## Computer use

Settings for `jev_computer_use`. Guide: [DOM-BROWSER-BACKEND.md](DOM-BROWSER-BACKEND.md).

| Key | Default | What it does |
| --- | --- | --- |
| `browser_executable` | `""` | Absolute path to a Chromium-family browser. Empty means auto-discover (Chrome, Chromium, Edge, and so on). |
| `computer_max_steps` | `100` | Maximum actions per computer-use run. A call can ask for fewer. |

## Approved model registry

Used by the advisory `jev_model_route_approved` tool. Empty by default, so it recommends nothing until you configure it. Guide: [MODEL-ROUTING.md](MODEL-ROUTING.md).

| Key | Default | What it does |
| --- | --- | --- |
| `approved_model_registry` | `[]` | Your list of approved model records. |
| `approved_model_registry_version` | `""` | A version label you manage. Required. |
| `approved_model_registry_valid_until` | `""` | Expiry time with a timezone (ISO-8601). After it passes, the registry is treated as stale. |

## Session search re-rank

Used by the `jev_session_search_rerank` tool. Guide: [SESSION-SEARCH-RERANK.md](SESSION-SEARCH-RERANK.md).

| Key | Default | What it does |
| --- | --- | --- |
| `session_search_rerank_choice_confidence_threshold` | `0.8` | Below this Jev confidence, keep Hermes' original search order. |
| `session_search_rerank_winning_probability_threshold` | `0.8` | Below this winning probability, keep the original order. |
| `session_search_rerank_max_card_chars` | `360` | Maximum characters per search result sent to Jev (after redaction). |

## Explicit tools and privacy

| Key | Default | What it does |
| --- | --- | --- |
| `public_or_sanitized_data_ack` | `true` | Your standing agreement that inputs to Jev tools and adaptive effort are public or sanitized. `false` refuses all Jev tool calls and stops adaptive effort from sending message text. A caller can also refuse a single call. |
| `defer_switchyard_tool_schemas` | `false` | Experimental. When `true`, hides the six decision-tool descriptions from the main model. They are kept when you explicitly ask for one, when the conversation already used one, or when a tool choice is forced. Automatic features keep working. Leave this off unless you understand the trade-off. |

## Settings that do not exist

The Research Navigator (F1) and DOM Progress & Recovery (F2) prototypes failed their release evaluations and are **not** in this release. Do not copy their settings from the old pull requests.
