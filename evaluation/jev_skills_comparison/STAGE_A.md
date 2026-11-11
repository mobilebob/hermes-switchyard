# Stage A findings: offline collection ready; live evaluation deferred

## What was actually executed

The collector invoked the pinned decision libraries, not installed gateway hooks:

- Switchyard: `cc45394c6c04627f6e62736f987fceb84117a6d9`.
- Jev Skills: `8316358bf60e98a21477582af829ef11c9cf52fb`.
- The existing 12 public synthetic routing cases, unchanged.
- One explicit synthetic-only catalog: `fixture:small` and `fixture:large` on the
  same `fixture` provider, with `fixture:large` as the recorded current model.
- Two scenarios, each with 24 fresh subprocess/home pairs: unavailable classifier
  and explicitly generated simple/high-fit classifier answers.

There were **zero provider requests, zero observed worker network/process escape
attempts, zero applied routes and zero worker failures**. These are offline
wiring/fallback observations, not actual Jev inference or downstream model work.
Repeated execution is reproducibility checking, not additional unique coverage.
Manifests and JSONL receipts remain outside the repository; no fabricated live
observation file or raw test log is committed.

## Observed fixture behavior

| Scenario | Arm | Rows | Switch proposals | Abstentions | Forbidden proposals rejected | p50 ms | p95 ms |
|---|---|---:|---:|---:|---:|---:|---:|
| No response | Switchyard | 12 | 0 | 12 | 0 | 159.3 | 218.8 |
| No response | Jev Skills | 12 | 0 | 12 | 0 | 147.8 | 170.2 |
| Forced simple fixture | Switchyard | 12 | 12 | 0 | 8 | 154.1 | 198.9 |
| Forced simple fixture | Jev Skills | 12 | 9 | 1 | 5 | 136.7 | 164.4 |

Timing is measured subprocess startup + imports + offline decision logic on one
host, with nearest-rank p95. **It is not inference latency, a speed benchmark or
end-to-end production timing.** Every arm/scenario attempted 12 stub invocations;
these are not external or billed requests.

The no-response comparison is `offline_no_response_only`; it does not establish
classifier coverage. Both implementations fall back without applying a route.

The forced-simple comparison is deliberately `incomplete_or_blocked`. The
independent policy screen rejects unsafe raw proposals instead of hiding them:

- Switchyard: missing context, manual model pin, security change, irreversible
  action, both privacy cases, provider-boundary task and tool-dependent task.
- Jev Skills: missing context, both privacy cases, provider-boundary task and
  tool-dependent task. The manual model pin abstains; security/irreversible tasks
  retain the current fixture model.

These outcomes are consequences of **supplied answers and API/configuration
boundaries**, not measured Jev classification errors or production regressions.
Switchyard's advisory API lacks Jev's explicit current-model/manual-pin inputs.
The task text and approved candidate set are shared, but classifier questions,
confidence rules and library safeguards differ. The external safety envelope is
common; it does not rewrite or conceal implementation proposals. No automatic
switching path exists in the collector.

The worker retains allowlisted scalar fields only: raw decisions include no task/state
payloads or credential values. The supplied catalog has no qualified vision/tool
capabilities, so the adapters do not fabricate them. A real trial needs approved
capability metadata and hard privacy/egress pre-rules **before** dispatch; the
current offline post-decision screen is not outbound-payload authorization.

## Verification boundaries

The new regression tests cover provenance/candidate/task parity, credential-free
worker environments, independent temporary homes, policy rejection, missing and
failed observations, timeout accounting, source pin/dirty-tree refusal, forbidden
application, bounded injected transports, attempted network/process escapes,
schema downgrade/mixed evidence rejection, canonical catalog and full-case-policy
binding, and CLI exit semantics. An independent review found evidence-validation
and exit-status gaps; each was fixed with regression tests.

The existing isolated Python 3.11.15 test environment was reused; no dependency
installation was performed. Full-suite verification permits local synthetic
fixture servers and blocks/counts non-loopback connections in the parent Python
runner. Python guards are not a complete subprocess or OS network sandbox.
The routing workers additionally deny Python process launch and all socket I/O.

The first harness attempt was discarded: overbroad socket blocking broke local
fixture servers, and an unnecessary upstream `PYTHONPATH` caused test-package
collisions. The corrected pristine-branch baseline ran 1,702 tests with zero
failures/errors, 1,689 successes, 13 test-case skips and three class-setup skips.
Class-setup skips are reported separately from `testsRun`; discovered cases in
skipped classes do not execute. Focused selections overlap the complete suite
and must not be added to its unique test count.

The final reviewed tree's full suite ran **1,730 tests: 1,717 successes, zero
failures/errors, 13 test-case skips and three class-setup skips**, with zero
non-loopback connection attempts observed by the parent runner. The 35 focused
comparison/collector/worker tests all pass, comprising the original seven plus
28 new regression tests; these are included in the full-suite count. The
synthetic evaluator (`evaluation/evaluate.py --validate`) passes all 10 fixtures
and both malformed-input/transport-failure probes; those requests are injected
fixture calls, not provider traffic. `git diff --check` also passes.

Known unrelated validation limitations:

- Portability scanning fails on the unchanged baseline and updated tree for
  host-path patterns in `runtime_provenance.py` and `usage_ledger.py`, plus a
  credential-shaped fixture assignment in `test_typesafe_model_resolution.py`.
  These files are byte-identical to the branch baseline; no actual credential is
  asserted by this finding. This evaluation does not alter production code to
  repair the existing scanner findings.
- The history-wide portability command exceeded a 120-second execution limit;
  it is **not** reported as passed.
- Changed Python files pass the repository's selected Ruff rule families under
  installed Ruff 0.15.10 with an isolated equivalent-rule invocation. Exact
  repository-pinned Ruff 0.11.13 was unavailable and was not installed.
- Real browser and Windows-only checks remain skipped where the environment
  does not support or opt into them. No live provider, GUI or gateway trial ran.
- No line/branch coverage percentage was collected; test counts and scenario
  coverage are not a substitute for code-coverage instrumentation.

## Next experiment and required authorization

**Keep this draft; do not promote or activate either routing implementation.**

Before a measured shadow trial, obtain all of:

1. Exact provider/account/current-model and approved same-provider candidate IDs.
2. Approved public/synthetic outbound payloads for each implementation, including
   privacy/no-egress pre-rules. Synthetic fixture approval authorizes no live model.
3. An explicit maximum Jev request count **and monetary ceiling**, plus timeout,
   retry and uncertain-dispatch handling. No main-model calls in the first
   decision-only trial; any later downstream trial needs its own budget.
4. A separately reviewed live transport with intent recorded before dispatch,
   provider-request/readback accounting and usage captured without treating
   failed or uncertain requests as free headroom.

Then run a bounded decision-only shadow comparison at these pins and 12 cases,
without applying any model selection. Follow with independently held-out tasks
and objective downstream correctness/fallback/verification measurements only
under separate authorization. Shadow proposals cannot establish realized avoided
main-model calls, billed savings, task correctness or promotion readiness.

Production plugin source, manifests, profiles and services are outside this
change. Jev Skills was not installed into the shared gateway, no service was
restarted, and no automatic model switching was enabled.
