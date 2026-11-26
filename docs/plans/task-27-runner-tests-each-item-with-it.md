# The runner tests each item with its own tests and runs the full suite once per run

## Intent
After every item, an unattended run re-runs the project's whole `test_command`. In this repository that takes 15–17
minutes, so a four-item task spends about an hour just waiting on tests that the item could not have affected. The user
asked for the runner to test only what an item can change, as people now do before a commit (CLAUDE.md, PR #54).
Success: in a project that opts in, each item is checked with the tests it names or changes, and the full suite still
runs once, before the branch is pushed. A red full suite still reopens work. Out of scope: choosing tests by import
analysis or coverage, and changing CI.

## Key decisions
1. **Opt-in, per project.** A new optional key `item_test_command`, a command with a `{tests}` placeholder, for example
   `python -m pytest -q {tests}`. Empty (the default) keeps today's behaviour exactly: the full `test_command` after
   every item. Other projects (npm, go, …) are unchanged until they set it. This repository sets it.
2. **Which tests an item gets.** The test files its `*Acceptance:*` line names that exist in the worktree, plus test
   files its own commits added or changed (paths matching `test_*.py`, `*_test.py`, `*.test.*`, `*.spec.*` or under
   a `tests/` folder). Paths are passed shell-quoted. With no such file, the item gets the full `test_command`, the safe
   default.
3. **The full suite still runs once.** After the last item of the run and before publishing, the runner runs the full
   `test_command`. If it is red, it reopens the last item done in this run for a fix round, with the existing `reopen`
   and `fix_note`, up to `review.max_fix_rounds`. If it is still red after that, the run stops without pushing, with
   reason `suite_red`. The "suite at the last independent run" line and the `tests` event report this run.
4. **The agent is told the truth.** The prompt's "the runner then runs the whole suite" changes to say which tests the
   runner runs after the item, and that the full suite runs before the branch is pushed.

## End to end
- Config: `item_test_command` in `autocfg.DEFAULTS` and `config.schema.json` (string). No migration; empty means unchanged.
- Events: the `tests` event gets `scope: "item" | "full"`; the central service stores the detail as it is.
- Errors: a test path that does not exist is dropped; nothing is run from item text except existing file paths, quoted.
- Docs: `auto-runner/README.md` (config table, the test gate), `CHANGELOG.md`.
- Rollback: empty `item_test_command`.

## Slices
- **Slice 1 Items test only what they touch.** With `item_test_command` set, each item runs its own tests; without it,
  nothing changes.
- **Slice 2 The full suite guards the push.** The full suite runs once before publishing and a red one is fixed or stops
  the run; this repository turns the setting on.

## Journeys
- **J1 A two-item run is faster.** Slice 1. Roles: developer.
  1. In a project with `"item_test_command": "python -m pytest -q {tests}"`, queue two items whose acceptance lines name one test file each, and start a run.
  2. Read the run's log.
  *Expect:* after each item the log shows the test run with only that item's test file, not the whole suite.
- **J2 A red full suite is caught before the push.** Slice 2. Roles: developer.
  1. Queue an item whose own tests pass but which breaks another test file, and start a run.
  *Expect:* after the last item the full suite runs, the item is reopened for a fix round, and the branch is pushed only once the full suite is green.

## Libraries
Nothing new: `shlex.quote`, `subprocess` (already used by `run_tests`), the runner's `reopen`, `fix_note`, `git` helpers and the queue parser.

| Requirement | Existing options | Cost | Licence | Recommendation | Why |
|---|---|---|---|---|---|
| Pick tests for a change | pytest-testmon (https://github.com/tarpas/pytest-testmon, unverified), coverage-based selection | free | MIT (unverified) | build a small path rule | the plugin is standard library only and must work for any language |

## Queue items

- [ ] **T1 Config key and the item's test files.** In `auto-runner/bin/autocfg.py` add `item_test_command` (text, default `''`) to `DEFAULTS` and the documented keys, and to `auto-runner/config.schema.json` as a string. In `auto-runner/bin/auto_run.py` add `item_tests(wt, block, commits)`: the paths named in the item block's `*Acceptance:*` line that exist as files in the worktree, plus files changed by `commits` (a list of SHAs, read with `git show --name-only --format=`) whose path matches `test_*.py`, `*_test.py`, `*.test.*`, `*.spec.*` or contains a `tests/` folder; sorted, unique, worktree-relative, never outside the worktree. Add `item_test_cmd(cfg, paths)`: `None` when `item_test_command` is empty or `paths` is empty, else the command with `{tests}` replaced by the shell-quoted paths joined by spaces (a command without `{tests}` gets them appended). Nothing else changes yet. Update `auto-runner/README.md` (the config table).
  *Acceptance:* `python -m pytest -q tests/auto_runner/test_runner.py tests/auto_runner/test_config.py tests/auto_runner/test_docs_check.py` passes with new tests: `item_tests` returns the named existing test file and a test file a commit changed, drops a named path that does not exist and one outside the worktree; `item_test_cmd` gives `None` for an empty setting or no paths, fills `{tests}` with quoted paths (a path with a space stays one argument), and appends when there is no placeholder; `autocfg.load` and the schema accept the key.
  *Slice:* 1 Items test only what they touch
- [ ] **T2 Run the item's own tests after each item.** In the iteration loop of `auto_run.py`, where the independent test run calls `tests(cfg['test_command'], wt)`, use `item_test_cmd(cfg, item_tests(wt, block, commits))` when it is not `None`, else the full `test_command` as today. Compute the item's commits before that test call (`git rev-list <head before the attempt>..HEAD` in the worktree; today `after_head` is only read later). Pass the command actually used (item or full) to `fix_note` on the red path, so a red per-item run tells the agent which command failed. Log the command used, and add `scope` (`"item"` or `"full"`) to the `tests` ledger event. Keep the agent's prompt as it is (T3 changes it once the full run exists). Update `auto-runner/README.md` (the test gate: per-item tests when the key is set).
  *Acceptance:* `python -m pytest -q tests/auto_runner/test_runner.py tests/auto_runner/test_docs_check.py` passes with new tests driving `run()` with a fake `claude` and a recording `tests` function: with `item_test_command` set and an item naming `tests/test_a.py`, the per-item run gets exactly that file and the `tests` event says `scope: "item"`; an item naming no existing test file gets the full command with `scope: "full"`; with `item_test_command` empty every item gets the full command as before; a red per-item run's fix note names the per-item command, not the full one.
  *Slice:* 1 Items test only what they touch
- [ ] **T3 One full suite before the push; a red one stops the run.** When `item_test_command` is set and at least one item in this run had `scope: "item"`, after the loop ends with no open items and before publishing, run the full `test_command` once: record a `tests` event with `scope: "full"` (item `None`) and update the "suite at the last independent run" line. Green: publish as today. Red: do not publish; exit 1 with reason `suite_red`, and say which command was red with the last lines of its output. Change the prompt's green-tests sentence (today "the runner then runs the whole suite, …, itself") to say, only when `item_test_command` is set, that the runner runs the item's own tests after it and the whole suite before the branch is pushed. Add `suite_red` to `auto-runner/central/ui.py` `REASONS` ("stopped: the full suite was red before the push") and `NEXT_STEP` ("Read the run log and fix the failing tests.") if it is not there. Update `auto-runner/README.md` and `auto-runner/central/README.md`.
  *Acceptance:* `python -m pytest -q tests/auto_runner/test_runner.py tests/central/test_runs.py tests/auto_runner/test_docs_check.py` passes with new tests: a two-item run whose full suite is green runs it once, after the last item, and publishes; a red full suite exits 1 with reason `suite_red` and no publish; with `item_test_command` empty no extra full run happens and the prompt keeps today's sentence; with it set the prompt has the new sentence; the Runs page shows the `suite_red` reason.
  *Slice:* 2 The full suite guards the push
- [ ] **T4 Let a post-loop check re-enter the loop.** Restructure the end of `run()` in `auto_run.py` so that, after the iteration loop ends with no open items and before publishing, a post-loop check can reopen an item and resume the loop (for example a `while` around the loop with an explicit "reopened" flag), with the iteration budget extended by one per resume, as fix rounds already do. In this item nothing reopens yet: T3's full run still stops on red. Existing behaviour must not change. Update `auto-runner/README.md` only if behaviour visible to a user changes (it should not).
  *Acceptance:* `python -m pytest -q tests/auto_runner/test_runner.py tests/auto_runner/test_publish.py tests/auto_runner/test_docs_check.py` passes, unchanged existing tests included, plus a new test where a stub post-loop check reopens one item once: the loop runs that item again and the run then publishes.
  *Slice:* 2 The full suite guards the push
- [ ] **T5 A red full suite gets fix rounds; turn it on here.** Use T4's post-loop resume: on a red full suite, reopen the last item done in this run that is still done (not blocked or set aside meanwhile; if none, stop as T3 does) with the existing `reopen` (why "full suite red") and give it `fix_note(cfg['test_command'], <the full run's output tail>, round, max)`, as the red path after an item does. The fix-round count is kept per reopened item and counts against `review.max_fix_rounds`; after each fix round the full suite runs again before publishing. When the rounds are used up and it is still red: exit 1 with reason `suite_red`, no publish, no blocker, and set that item blocked with the text "full suite red after N fix rounds". Set `"item_test_command": "python -m pytest -q {tests}"` in this repository's `.claude/auto.json`. Update `auto-runner/README.md` and `CHANGELOG.md`.
  *Acceptance:* `python -m pytest -q tests/auto_runner/test_runner.py tests/auto_runner/test_docs_check.py` passes with new tests: a full suite red once reopens the last item with a fix note naming `test_command` and the output tail, the item is fixed, the full suite runs again and the run publishes; a full suite still red after `review.max_fix_rounds` rounds exits 1 with reason `suite_red`, no publish, no blocker, and the item is blocked with "full suite red after N fix rounds"; `.claude/auto.json` has the key.
  *Slice:* 2 The full suite guards the push

## Needs a person
None.

## Verification
The named test files per item, through the runner with a fake `claude`; the pull request's `tests` check runs the full
suite. The real time saved is unverified until a real run uses the setting.

## Spec check
Round 1 (3 findings, all fixed): the prompt sentence moved from T2 to T3, so it never promises a full run that does not
exist yet; T3 was split into T3 (the full run before the push, stop on red) and T4 (the fix rounds and turning it on
here); T4 now says the fix note carries `test_command` and the output tail as the existing red path does, and that the
round count is per reopened item.
Round 2 (3 findings, all fixed, not re-checked: two rounds is the maximum): T2 computes the item's commits before
its test run and passes the command it used to `fix_note`; the old T4 was split into T4 (restructure the end of
`run()` so a post-loop check can resume the loop, behaviour unchanged) and T5 (the fix rounds and the setting here);
T5 says which item is reopened and that exhausting the rounds blocks it with no blocker raised.
