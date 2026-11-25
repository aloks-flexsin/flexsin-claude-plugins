## Intent
The goal is for the planner to see task screenshots. Task 16's UI/UX audit plan came from a task with an attached screenshot, so the plan has to be written against that screenshot.

The request says the screenshot is missing from the plugin and that it is fetched with the repo's current `autotasks.py`. I followed the intent rather than the wording, because the screenshot is not missing and the fetch works. This session found:
- **The file exists.** `.auto/run/tasks/16/attachments/002f784c…-kanban.png` is a valid image of the board running wider than the page. It sits in the project's git-ignored `.auto/`, not inside the plugin folder, and that is by design.
- **The current code is correct.** In `autotasks.py`, `fetch_attachments` and `attachment_name` are correct and covered by `test_central_tasks.py`. The central endpoint `GET /api/v1/tasks/<N>/attachments/<id>` is correct too. The installed plugin cache (`0.22.0`, the same as the repo) has the code.

Success means a task with screenshots reaches its plan with those screenshots read, in every route that plans. Out of scope: changing central or the screenshot upload rules.

## Key decisions
**Root cause (found, not guessed).** Only one route downloads screenshots. `autotasks.py next` calls `fetch_attachments`, and `commands/auto.md` tells the interactive session to open the listed paths. The headless planner has no such step:
- `autoplan.plan()` fetches the task and calls `build_prompt()`.
- `build_prompt()` (`autoplan.py:82`) uses only `number`, `title`, `description` and `comment`.
- It never calls `fetch_attachments`, and the prompt names no image path.
- `claude -p` is allowed `Read`, which can open images, so only the download and the pointer to the files are missing.

A task planned by the watcher (`autowatch` → `autoplan`) is therefore planned without its screenshots. Task 17, this one, is in the same situation. The attachments folder is also per checkout, because `.auto/*` is git-ignored. A second clone, a worktree or an older cached plugin (`0.20.0` has no `fetch_attachments`) shows an empty `attachments/` folder. That is probably the "missing from the plugin" symptom.

**A gap that adds to it.** Downloads happen only as a side effect of claiming a task with `next`. There is no way to fetch screenshots for a task that is already claimed, done or being re-planned, such as task 16. Re-fetching or recovering a wiped folder is currently impossible.

| Option | Verdict |
|---|---|
| A. Download in `autoplan.plan()` and add the paths to the prompt | Chosen. It fixes the real path and reuses `fetch_attachments`. A download failure only drops the screenshots. |
| B. Add `autotasks.py attachments N` for on-demand fetching | Chosen as slice 2. Small, and it closes the re-fetch gap. |
| C. Move the download into `take_next` | Rejected. It changes `next` output and makes every claim download, including claims that skip planning. |
| D. Commit screenshots into the repo | Rejected. Task screenshots may show real data, and the repo rules forbid committing it. |

**Failure behaviour.** Fetching is best effort. In `plan()`, a failed download becomes a `problems` entry and planning continues. The prompt then names the missing screenshots, so the plan can list them under **Needs a person** instead of guessing.

**Error contract of the shared helper (slice 2).**
- The helper that prints the `--- Screenshots ---` block takes a `(paths, problems)` pair and only prints it. It neither catches nor raises.
- `next` keeps its existing `try/except TaskError`, which turns a failed listing into a "could not list them" problem.
- `attachments N` does not catch. A `TaskError` for an unknown task reaches `main`'s existing `Not done: …` handler, which exits 1.
- `attachments` without a number fails through `ap.error`, like the other subcommands.

**Doc-map.** `docs/doc-map.json` ties `auto-runner/bin/*.py` to `auto-runner/README.md`. Every item that touches `bin/` therefore updates the README itself, so each item can be committed with `python -m pytest -q` green.

**Left out on purpose.** There is no change to central, the upload limits or the claim logic. There is no migration, config key or deploy step, and rollback is a revert. CHANGELOG entries are made per item (`*Kind:*`).

## Slices
**Slice 1, Planner sees the screenshots.** A task planned by the watcher with attached screenshots gets them downloaded and listed in the planner's prompt. A person sees plans that describe what the screenshot shows.

**Slice 2, Fetch by number.** A person can run `autotasks.py attachments N` for any task of the project, including done ones. They get the paths, or "Not downloaded" lines, printed.

## Journeys
- **J1 Watcher plans from the screenshot.** Slice 1. Roles: developer.
  1. On the central page, add a task with a PNG screenshot and a short description that refers to "the screenshot".
  2. Start the watcher (`/auto-runner:auto` in watch mode) and let it claim the task.
  3. Open the posted plan on the task page.
  *Expect:* the plan's Intent names something visible only in the screenshot, and `.auto/run/tasks/<N>/attachments/` holds the file.

- **J2 Re-fetch a finished task's screenshot.** Slice 2. Roles: developer.
  1. Delete `.auto/run/tasks/16/attachments/`.
  2. Run `python auto-runner/bin/autotasks.py attachments 16`.
  *Expect:* the printed path points to a file that opens as the kanban image.

- **J3 Unknown task is refused.** Slice 2. Roles: developer.
  1. Run `python auto-runner/bin/autotasks.py attachments 99999`.
  *Expect:* a `Not done: …` line and exit code 1, with nothing downloaded.

## Reuse check
This reuses `autotasks.fetch_attachments` and `attachment_name` (path safety already tested), `autotasks._call` and `transport`, the `fake_central.py` fakes, and the `Read` tool the planner already has. There is no new package and no new capability, so there is no cost or licence question. Anything new for downloading would duplicate `fetch_attachments`.

## Queue items
- [ ] **R14 Planner downloads the task's screenshots.** In `auto-runner/bin/autoplan.py`, `plan()` calls `autotasks.fetch_attachments(project, number, env, send)` after fetching the task. Failures become `problems` and never stop planning. `build_prompt` takes the paths and problems and adds a `SCREENSHOTS` section before the task text. It tells the planner to open each path with Read and treat what it shows as part of the task, and it names each screenshot that could not be downloaded. The prompt is unchanged for a task with no screenshots. Update `auto-runner/README.md` (the planner downloads task screenshots) in this same item, as the doc-map check requires. Do not change `take_next`, `next` output or the central API.
  *Acceptance:* new tests in `tests/auto_runner/test_autoplan.py` pass, and `python -m pytest -q` is green including the doc-map check. With a fake central holding one PNG, the prompt contains its local path and the file exists. With a failed download, the prompt names it and `plan()` still posts. With no attachments, the prompt equals the old one.
  *Slice:* 1 Planner sees the screenshots
  *Kind:* fix

- [ ] **R15 Same step in the prompt contract.** In `docs/planner-prompt.md`, the instruction file for the headless planner, the `Read the project's rules files…` paragraph gets a sentence telling it to read the listed screenshots first. Mirror it in step 1 of `commands/auto.md` only if that wording differs, and then update `auto-runner/README.md` again as the doc-map requires for `commands/`. Add the CHANGELOG entry for R14 and R15 together.
  *Acceptance:* `python -m pytest -q` is green, including the doc-map check, and a test asserts that `docs/planner-prompt.md` mentions screenshots.
  *Slice:* 1 Planner sees the screenshots
  *Kind:* fix

- [ ] **R16 `autotasks.py attachments N`.** Add the `attachments` command to `autotasks.py`.
  - It prints the same `--- Screenshots ---` block as `next`, for task N, without claiming anything.
  - `next` and `attachments` share one helper that only prints a `(paths, problems)` pair. The helper neither catches nor raises.
  - `next` keeps its existing `try/except TaskError`. `attachments` lets `TaskError` reach `main`'s existing `Not done: …` handler (exit 1). `attachments` without a number fails with `ap.error`.
  - Add the command to the module docstring, `auto-runner/README.md` and CHANGELOG. Say in the README that screenshots live in the project's `.auto/run`, not in the plugin.
  - `next`'s output and behaviour are unchanged.
  *Acceptance:* tests in `tests/auto_runner/test_central_tasks.py` pass.
  - `main(['attachments', '1', '--project', ...])` against `fake_central` prints the path, and the file exists.
  - An unknown task prints `Not done: …` and exits 1.
  - `attachments` without a number exits through the argparse error.
  - The existing `next` tests, including the listing-failure case, still pass.
  *Slice:* 2 Fetch by number
  *Kind:* feature

## Needs a person
- **Live check of J1 and J2** against the real central service. Unattended runs cannot reach it. Recommendation: do it after the pull requests merge.
- **Update the plugin cache** with `/auto-runner:update` if any machine still has `0.20.0`. It has no screenshot support at all, which is a likely cause of "missing" on that machine. Recommendation: update now.
- **Assumption:** the "missing" report came from the headless planner or a second checkout, not from a failed download. I could not run the live service, and the planner's own output for task 16 was not available to me.
- **Not checked:** the planner reply for task 16 itself, so I can't say whether it used the screenshot.
