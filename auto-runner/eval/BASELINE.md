# Baseline procedure (manual, real models; never asserted by tests)

A baseline is what the current plugin actually delivers on each benchmark, measured before anyone sets a goal.
Runs are made by a person with real models; the unattended loop never makes them and no test asserts their numbers.

## Rules
1. **No targets before a baseline.** No target score or percentage is set for any dimension until a baseline file for
   that benchmark exists. Targets are then written elsewhere as improvements over the measured baseline, with its
   variance stated. A baseline file never holds targets (`validate-results` rejects a `targets` field).
2. **Record the model version and settings.** Every model used is listed with its role and its exact model version
   (the id the CLI reports, not an alias such as `opus`), plus the settings that change behaviour: effort, budget per
   iteration, iteration limit, CLI version. A results file without `model_version` is rejected.
3. **Several runs, variance reported.** Run each benchmark more than once with the same settings. Start with 3 runs;
   if a dimension's spread (`max - min`) is large compared with the change you hope to detect, add runs until it
   settles, and record the count chosen in `run_count`. The `variance` block gives, per rubric dimension, `n`, `mean`,
   sample `stdev` (null below two runs), `min` and `max` over the runs that measured it; `validate-results` recomputes
   it from the runs and rejects a file whose figures disagree or that lacks `run_count` or `variance`.
4. **Keep raw results.** Each run's `result.v1` file from `autoeval.py run` is kept unchanged (for example under
   `eval/results/raw/`) and named in `raw_result`. Summaries are derived from raw files, never typed in.
5. **Evidence class.** A baseline is E2 (real-model evaluation run) or above. Fake-runner results (E0) prove the
   wiring only and are never a baseline.
6. **Nothing measured stays null.** A dimension with nothing to measure (for example held-out tests that are only
   descriptions) stays `null` with `n: 0`; never 0, never estimated.

## Steps for one benchmark
1. Note the plugin commit, CLI version and model ids. Fix the settings for all runs.
2. For each run `i` (1..N): run the plugin on the benchmark's requirement in a fresh directory, then score it:
   `python bin/autoeval.py run --benchmark ID --runner <real runner> --run ID-baseline-i`. Copy the result file to
   `eval/results/raw/`.
3. Copy `eval/results/template.json` to `eval/results/ID-baseline.json`, fill every field from the raw files
   (`autoeval.variance_of` computes a variance entry from a list of scores), and delete the placeholders.
4. `python bin/autoeval.py validate-results eval/results/ID-baseline.json` must print `valid`.
5. Only now may targets for this benchmark be proposed, stated as change from the baseline mean with its stdev.

Today only the `fake` runner exists, so no baseline has been made; a real runner is a later roadmap item.
