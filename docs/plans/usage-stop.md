# Stop the runner at 90% of the Claude plan's usage limit

## Intent
Today the runner only notices Claude's usage limit once it is hit (100%): the attempt is cut off mid-item, its
unfinished work is stashed, and the run stops or waits. The person wants the runner to **stop cleanly before that**,
at a threshold (90%), between iterations, so no item is cut off and some of the plan is left for their own work.

Measured on 2026-11-24: in the runner's output format (`--output-format stream-json --verbose`) the CLI prints a
`rate_limit_event` line with `rate_limit_info.unifiedWindows.five_hour` / `seven_day` → `utilization` (0–1) and
`resetsAt` (epoch seconds), plus `status` (`allowed`). These are the plan's real numbers, not the list-price
`total_cost_usd`. So the stop can use them with no extra calls.

Success: when an iteration ends with either window at or above the threshold, the runner says so (window, percentage,
reset time), stops before the next item (or waits for the reset when `usage_limit.wait` is on), and the central Runs
page shows why the run stopped. `/auto-runner:status` shows the last known usage. Out of scope: predicting usage
mid-iteration (the line only arrives with a model reply), and per-account quotas the CLI does not report.

## Key decisions
1. **Where to read usage.** Options: (a) the `rate_limit_event` already in each iteration's output; (b) a separate
   probe call (about 8¢ each, and it adds to usage itself). **Choice (a)**: free, and fresh as of the iteration that just ended.
2. **Which window.** Both `five_hour` and `seven_day`; the stop uses the highest utilization. Windows the CLI adds
   later are read the same way, without code changes.
3. **When to stop.** Only between iterations, after the item's commit, tests and state are recorded, never mid-item.
4. **Config.** `usage_limit.stop_at_percent` (integer 1–100, default **90**); `100` turns the early stop off. It uses
   the existing `usage_limit.wait` / `max_wait_minutes`: when wait is on, the runner waits until that window resets
   (a STOP file still ends the wait), then continues; otherwise it exits with the existing usage-limit exit code 75,
   so a wrapper can start it again later.
5. **Missing data.** No `rate_limit_event` in an iteration (older CLI, API-key use) → no early stop, a note in the
   log, and the existing 100% handling stays as it is.

## End to end
- Data: `.auto/runs.jsonl` entries get `plan_usage` (`{window: {"utilization", "resets_at"}}`); the `run_end` event
  detail gets `usage` (the window that stopped it) when the reason is `usage_threshold`. No database change: the event
  body is stored as it is.
- Errors: malformed lines are skipped; never a crash. Security: no account data in the line; nothing new is sent.
- Config/docs: `auto-runner/config.schema.json`, `auto-runner/README.md`, `auto-runner/central/README.md`.
  Deployment: the plugin release as usual; the central part needs a deploy by a person.

## Slices
- **Slice 1 The runner stops at the threshold.** A run whose iteration reports ≥ 90% stops before the next item,
  says why and when it resets; status shows the last usage.
- **Slice 2 The central page shows why.** The Runs page shows "Stopped at 91% of the 5-hour limit (resets 14:30 UTC)".

## Journeys
- **J1 Early stop.** Slice 1. Roles: developer.
  1. Queue two items and start a run while the 5-hour window is above 90%.
  2. Watch the run's output, then run `/auto-runner:status`.
  *Expect:* the first item finishes and is committed; the runner then says it stopped at the 5-hour limit's
  percentage with the reset time and does not start the second item; status shows the same usage.
- **J2 Stop reason on the page.** Slice 2. Roles: developer.
  1. After J1, open the project's Runs page.
  *Expect:* the run shows as stopped with "Stopped at 9x% of the 5-hour limit (resets HH:MM UTC)".

## Libraries
Nothing new: the standard library (`json`, `datetime`) and the CLI output the runner already reads.

| Requirement | Existing options | Cost | Licence | Recommendation | Why |
|---|---|---|---|---|---|
| Plan usage percentage | `rate_limit_event` in Claude Code's `stream-json --verbose` output (measured 2026-11-24) | free | Anthropic terms | reuse | already in every iteration's output; no extra call |

## Queue items

- [ ] **U1 Read plan usage from each iteration's output.** In `auto-runner/bin/auto_run.py` add `plan_usage(output)` next to `parse_cost()`: it reads the last line whose JSON `type` is `rate_limit_event` and returns `{window: {"utilization": float 0–1, "resets_at": epoch int}}` from `rate_limit_info.unifiedWindows` (only numeric values; malformed lines skipped), or `{}` when there is none. It handles every key of `unifiedWindows`, and labels them with one table `WINDOW_LABELS = {"five_hour": "5-hour", "seven_day": "7-day"}` (an unknown key is shown as its name with `_` as a space). U1's status line, U2's message and U3's check all use this table, and the central service keeps its own copy of it. The tests use this measured line (2026-11-24) as their fixture: `{"type":"rate_limit_event","rate_limit_info":{"status":"allowed","resetsAt":1795530600,"rateLimitType":"five_hour","overageStatus":"rejected","overageDisabledReason":"org_level_disabled","isUsingOverage":false,"unifiedWindows":{"five_hour":{"utilization":0.16,"resetsAt":1795530600},"seven_day":{"utilization":0.03,"resetsAt":1796086800}}},"uuid":"8123a927-d088-4980-a417-1927fefdcf23","session_id":"929ab07b-4492-4c23-9c3c-da3e9fbd1dfd"}`. Store it as `plan_usage` on each `.auto/runs.jsonl` entry (beside `cost_usd`). In `auto-runner/bin/auto_report.py` `status`, add a line "Plan usage (last iteration): 5-hour 16% (resets 20:00), 7-day 3% (resets 1 Dec 06:30)", or "Plan usage: not reported" when the latest entry has none. Reset times are shown in the computer's local time (`HH:MM` today, `D Mon HH:MM` on another day). Do not change how the run stops.
  *Slice:* 1 The runner stops at the threshold
  *Acceptance:* `python -m pytest -q tests/auto_runner/test_runner.py tests/auto_runner/test_report.py` passes with new tests: the measured `rate_limit_event` line gives both windows, the last of two such lines wins, output without one gives `{}`, malformed JSON and non-numeric values are skipped, a fake iteration's `runs.jsonl` entry carries `plan_usage`, and, with the time zone pinned to Asia/Kolkata in the test (an injected tzinfo), `status` prints exactly `5-hour 16% (resets 20:00)` and `7-day 3% (resets 1 Dec 06:30)`, or "not reported"; full `python -m pytest -q` green.
- [ ] **U2 Stop between iterations at the usage threshold.** Add a `percent` kind (integer 1–100) to `auto-runner/bin/autocfg.py` (`KIND_TEXT` and the `usage_limit` entry of its nested-key table) and a matching `$defs.percent` (minimum 1, maximum 100) to `auto-runner/config.schema.json`; add `stop_at_percent` of that kind to the `usage_limit` section and to `USAGE_LIMIT_DEFAULTS` (default 90). In the iteration loop of `auto_run.py`, after an item's iteration has been recorded (commit, tests and state done), and only when open items remain and the run would otherwise start another iteration. If no open item is left, the run ends normally (exit 0, published as usual) whatever the usage. The red fix-round, budget-handover, usage-limit and no-progress paths are unchanged. At that point, take the window with the highest `utilization` from U1; if `utilization * 100 >= stop_at_percent`, say "plan usage at N% of the <window> limit (resets HH:MM): stopping before the next item". With `usage_limit.wait` on, wait until that window's `resets_at` + 60 s (within `max_wait_minutes`, a STOP file ends the wait, the wait does not use up an iteration), then continue. When that reset (+ 60 s) is further away than the wait time left under `max_wait_minutes` (the 7-day window can be days away), it does not wait at all and stops as when `wait` is off. Otherwise end the run with exit code `USAGE_LIMIT_EXIT` and reason `usage_threshold`, and put `{"window", "utilization", "resets_at"}` as `usage` in the `run_end` detail. `stop_at_percent: 100` never stops early. No usage in the output: no early stop and a log note. Update `auto-runner/README.md` (the threshold, the default, how to turn it off, that it is checked only between items).
  *Slice:* 1 The runner stops at the threshold
  *Acceptance:* `python -m pytest -q tests/auto_runner/test_runner.py` passes with new tests that drive `run()` with a fake `claude` printing a `rate_limit_event`: with two items and 5-hour utilization 0.91, the first item is committed, the second never starts, the exit code is 75 and the `run_end` event has reason `usage_threshold` and the window; at 0.89 both items run; with `stop_at_percent: 100` both run; with `wait` on and a fake clock and sleep, the run waits until the reset and then runs the second item; with `wait` on and a reset further away than `max_wait_minutes` it does not wait and exits 75 with reason `usage_threshold`; output without the line stops nothing; with a single item done at 0.95 the run exits 0, publishes, and its reason is not `usage_threshold`; `autocfg.load` and the config schema accept 90 and refuse 0 and 101; the `run_end` event of the stopped run, fed through `central.runs.summarise()`, gives reason `usage_threshold` and the window (one shape for runner and page); `status` after the stopped run prints the plan usage line; the docs check passes; full suite green.
- [ ] **U3 The central Runs page shows the usage stop.** In `auto-runner/central/ui.py` add `usage_threshold` to `REASONS` ("stopped early: plan usage threshold") and `NEXT_STEP` ("Start the runner again after the limit resets."). In `central/runs.py` `summarise()`, read `usage` from the `run_end` detail (only a window name in the service's copy of `WINDOW_LABELS`, `five_hour` → "5-hour", `seven_day` → "7-day", numeric utilization 0–1 and epoch `resets_at`; anything else is ignored) and show on the run in `templates/runs.html` "Stopped at 91% of the 5-hour limit (resets 14:30 UTC)": the page formats `resets_at` in UTC as `HH:MM UTC`, adding `D Mon` when the reset is on a different day than the run's end. Update `auto-runner/central/README.md` (docs marked unverified).
  *Slice:* 2 The central page shows why
  *Acceptance:* `python -m pytest -q tests/central/test_runs.py tests/central/test_ui.py` passes: a `run_end` posted through `POST /api/v1/events` with reason `usage_threshold` and `usage: {"window": "five_hour", "utilization": 0.91, "resets_at": 1795530600}` shows the stop reason, "91% of the 5-hour limit" and "resets 14:30 UTC" on `GET /projects/<slug>/runs`; a malformed `usage` shows the reason without the percentage and no error; other reasons are unchanged; the docs check passes; full suite green.

## Needs a person
- **Default threshold.** I set 90% on by default for every run, since that's what you asked for. If you'd rather it be
  off unless a project turns it on, say so.
- The central part (U3) needs a deploy by a person.
- Assumed: the `rate_limit_event` shape stays as measured; if the CLI changes it, the early stop quietly falls back to
  the existing 100% handling (U1's tests pin the measured shape).

## Verification
Per item `python -m pytest -q`, all green. The runner tests drive `run()` with a fake `claude` that prints the measured
line. The central test posts events through the real endpoint on a temp SQLite database. A real run near 90% (J1) stays
unverified until it happens.

## Spec check
Two rounds (the configured maximum).
- Round 1: the event's shape and window names were not written down, and the wait/stop choice was open for a reset
  further away than `max_wait_minutes`. Changed: U1 now holds the measured line and one `WINDOW_LABELS` table, and U2
  stops without waiting when the reset is too far away.
- Round 2: five findings, all fixed in the items. `autocfg.py` gets a `percent` kind. The early stop only applies when
  open items remain, so a finished run still publishes. Time zones are pinned: local in `status`, UTC on the page. U3's
  example now says 14:30 UTC. One test feeds the runner's real `run_end` into the page's `summarise()`.
- Still open: the round-2 fixes were not re-checked, because only two rounds are allowed.
