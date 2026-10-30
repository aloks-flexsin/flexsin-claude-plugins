# Evaluation harness (skeleton, unverified with any real model)

Measures what the auto-runner delivers, judged by held-out tests the implementing agent never sees. Code: `bin/autoeval.py`.

```
python bin/autoeval.py validate                         # rubric and every benchmark
python bin/autoeval.py run --benchmark smoke --runner fake
python bin/autoeval.py validate-results eval/results/template.json   # a baseline.v1 file (see BASELINE.md)
```

Baselines are made by hand with real models (`BASELINE.md`: model versions and settings, several runs, variance per
dimension, raw results kept, no targets before a baseline). `results/template.json` is the format; no baseline exists yet.

| Path | Holds |
|---|---|
| `rubric.json` | the eleven scoring dimensions, each with a measure and whether higher or lower is better |
| `benchmarks/<id>/benchmark.json` | `benchmark.v1`: id, shape, requirement file, acceptance criteria with IDs (`negative: true` for negative cases), scripted owner-simulator answers (`match`, `answer`, `sufficient`), expected-outcome fixtures under `expected/` |
| `heldout/<id>/*.json` | held-out tests: id, description, criteria, dimensions, optional `check` (`matches_expected`, `file_absent`, `file_contains`) |
| `results/<run>.json` | `result.v1`, one per run name; re-running unchanged input rewrites the same bytes |

Benchmarks (briefs and held-out test descriptions only; no application is implemented and no model has been run on them):

| ID | Shape | Notes |
|---|---|---|
| `smoke` | harness smoke test | the only one with executable checks; proves the wiring |
| `workflow-approval` | workflow-heavy internal app | leave requests, roles, state machine, no self-approval |
| `multitenant-saas` | multi-tenant SaaS | tenant isolation across ids, search, export, cache and jobs |
| `integration-orders` | integration-heavy app | payment and shipping contracts, retries, signed and out-of-order webhooks |
| `existing-codebase` | feature added to an unfamiliar existing codebase | needs a seed codebase (not yet provided) |
| `concurrency-bookings` | concurrency-sensitive rules app | overlaps, balances, refunds and retries under parallel requests |
| `ambiguity-decisions` | ambiguity needing owner decisions | one deliberately insufficient scripted answer and one question with no answer |
| `seeded-defects` | seeded-defect scenario | permission hole, broken migration, wrong assumption; described only in `heldout/`; needs a seed codebase |

Every benchmark has unique criterion IDs, at least one negative case and a held-out test for every criterion
(`tests/auto_runner/test_eval_benchmarks.py`). Their held-out tests are descriptions with no `check`, so a run scores
them `not_run` and every criterion `untested`; nothing passes until executable checks exist. The harness does not yet
copy a starting codebase into the working directory, so `existing-codebase` and `seeded-defects` cannot be run
meaningfully until a seed codebase and that copy step are added.

Rules:
- The agent works in a fresh temporary directory outside this folder that holds only the requirement and `criteria.json`.
  The harness refuses to start the runner if any held-out file is in it. Held-out tests, expected fixtures and owner answers are never copied.
- A criterion passes only when its executable held-out checks pass; the runner's approval never counts. A held-out test with no `check` is a description and is reported `not_run`.
- A score with nothing to measure is `null` with its reason, never 0 and never estimated.
- The `fake` runner writes the expected fixtures (evidence class E0). It proves the wiring only; no real model has been run through this harness.
