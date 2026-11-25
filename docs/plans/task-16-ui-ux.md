# Task 16: audit the task UI and fix what it finds

## Intent
People use the central service's task screens (the Tasks board and list, the task page, the New task form) to see work
and decide on plans. The attached screenshot shows the board wider than the page: the whole page scrolls sideways and
the top bar and project header are cut off. The request is to audit these screens properly and fix the issues found.
Success: no task screen scrolls sideways at any width; plans read as their authors wrote them; every label on a task
says what is true for its state; text meets WCAG AA contrast in both themes. Out of scope: new features, the Runs,
Blockers and admin pages, the bell's own menu, and drag and drop.

## The audit (2026-11-25)
Every task screen was rendered through the app's test client (admin and viewer, every task status, long titles, a plan
with a table) and measured in a browser at 1366, 1280, 768 and 375 px wide, in both themes:
1. **Board wider than the page** (the screenshot). Six columns at `minmax(220px,1fr)` need 1380 px; `main` is at most
   1080 px and `.board` only scrolls by itself below 900 px. At 1366 px the page's scroll width is 1534 px.
2. **Cards:** the status pill stretches to the card's full width (the card is a flex column), and the "· runner" and
   "· PR" separators wrap onto a line of their own.
3. **A long word in a task title** (a path, a URL) pushes the task page wider than a 375 px phone (`h2.page-title`
   551 px).
4. **Plans lose their formatting.** `ui.md` knows headings, lists, bold and code only. Every plan's Reuse check table
   shows as one line of `| … |`, `*Acceptance:*` and `*Expect:*` keep their asterisks, fenced code blocks and links show raw.
5. **Items say "Queued"** before anything is built, the same word as the task status Queued; on a done task with no run
   record (tasks 7 and 8 were built while the service was down) every item still says "Queued".
6. **A closed task's reason** is shown under "Runner's last report".
7. **Screenshots can still be uploaded** to a done or rejected task.
8. **"Tasks 7 1"** in the project tabs: two bare numbers (open tasks, plans to approve) with nothing saying which is which.
9. **Contrast:** the attention pill (white on `--attn-mark`) is 3.46:1 in light and 1.61:1 in dark; the muted line in
   the decision bar is 4.41:1. AA needs 4.5:1.
10. The side panel says "Runner: none" for a task nobody has taken yet.
Checked and fine: the top bar, the project tabs and the step tracker scroll inside their own strip on a phone; the New
task form and My work do not overflow.

## Key decisions
1. **Board width.** Options: (a) a wider page for the board only and the board scrolling inside its own box; (b) fewer
   columns. **(a):** six columns are the decision of task 5. The board page gets `main.wide` (max 1440 px) and `.board`
   always has `overflow-x:auto`, so the page never scrolls sideways; columns are `minmax(200px,1fr)`.
2. **Markdown.** Extend the existing escape-first `ui.md`, no library: pipe tables (header row plus `---` row), `*italic*`,
   fenced code blocks (escaped, no highlighting) and `[text](url)` links for `http`/`https` only (`rel="noopener
   noreferrer"`); anything else stays text. A library (Markdown, mistune) would need its HTML sanitised again.
3. **Item words.** An item without a state is "Not started"; on a done task it shows no state badge (the runs are the
   record, and there may be none).
4. **Contrast.** Darken `--attn-mark` for the pill text in light and give the dark theme's pill dark text, keeping the
   colours' meaning. A test computes the ratios from `style.css` for both themes.

## End to end
Templates, `ui.py` and `style.css` only; no data or schema change. Rollback: revert. Deploy by a person. Docs:
`auto-runner/central/README.md`.

## Slices
- **Slice 1 Nothing scrolls sideways.** The board and task pages fit the screen at every width; cards read cleanly.
- **Slice 2 Plans read as written.** Tables, italics, code blocks and links render on the task page.
- **Slice 3 Every label tells the truth.** Item words, the close reason, uploads, tab counts, the runner line and contrast.

## Journeys
- **J1 The board fits.** Slice 1. Roles: developer.
  1. On a 1366 px wide screen open a project's Tasks tab (the board).
  *Expect:* the top bar and project header are not cut off and the page does not scroll sideways; if the columns do not fit, only the board scrolls.
  2. Open a task whose title is one long word on a phone.
  *Expect:* the title wraps and the page does not scroll sideways.
- **J2 A plan with a table.** Slice 2. Roles: manager.
  1. Open a task whose plan has a Reuse check table and items with *Acceptance:* lines.
  *Expect:* the table shows as a table and the labels in italics without asterisks.
- **J3 Truthful labels.** Slice 3. Roles: admin.
  1. Open a closed task, a done task without runs, and an open task.
  *Expect:* the closed task shows its reason as why it was closed; the done task's items show no "Queued"; the open task's side panel says no runner has taken it; the Tasks tab says what each number counts.

## Libraries
Nothing new: the existing templates, `_ui.html` macros, `ui.md` and `style.css` variables.

| Requirement | Existing options | Cost | Licence | Recommendation | Why |
|---|---|---|---|---|---|
| Markdown tables and links | the repo's `ui.md`; Python-Markdown (https://python-markdown.github.io/), mistune (https://github.com/lepture/mistune) | free | repo's own; BSD; BSD (unverified) | extend `ui.md` | escape-first output stays safe without a second sanitiser |

## Queue items

- [ ] **V1 The board and task pages never scroll sideways.** In `auto-runner/central/templates/base.html` let a page add a class to `<main>` (`{% block main_class %}{% endblock %}`), and give `templates/tasks.html` `wide` in the board view. In `static/style.css`: `main.wide{max-width:1440px}`; `.board` always `overflow-x:auto` with `grid-template-columns:repeat(6,minmax(200px,1fr))` (drop the 900 px rule's column change); `.board-card` `align-items:flex-start` so the status pill keeps its own width; `.page-title` and `.row-title` `overflow-wrap:anywhere`. In the board card (`tasks.html` macro `card`) put the runner and the PR link each on their own line without a leading "·". The list view and every other page keep their width. Add a line to `auto-runner/central/README.md` (the Tasks page: the board page is wider and the board scrolls by itself), marked unverified for looks.
  *Slice:* 1 Nothing scrolls sideways
  *Acceptance:* `python -m pytest -q tests/central/test_task_ui_audit.py tests/central/test_tasks_board.py` passes with new tests in `tests/central/test_task_ui_audit.py`: the board page's `<main>` has class `wide` and the list view's does not; `style.css` has `.board` with `overflow-x:auto` outside any media query, `main.wide` with `max-width:1440px`, `.board-card` with `align-items:flex-start` and `.page-title` with `overflow-wrap:anywhere`; a board card for a task with a runner and a PR has no text node starting with "·"; the docs check passes; full `python -m pytest -q` green.
- [ ] **V2 Plans render tables, italics, code blocks and links.** Extend `ui.md` in `auto-runner/central/ui.py`, escape-first as today: a pipe table (a header row, a `---` separator row, body rows; cells trimmed, inline Markdown inside) becomes `<table>` with `<thead>`/`<tbody>` wrapped in `<div class="table-wrap">` (scrolls sideways by itself); `*text*` becomes `<em>` (not inside `**bold**`); a fenced block between lines of three backticks becomes `<pre><code>` with its text escaped and no Markdown applied; `[text](url)` becomes a link only when the URL starts with `http://` or `https://` (escaped, `rel="noopener noreferrer"`), else it stays text. Add `.table-wrap` and table styles to `style.css` (both themes). Nothing else in `ui.md` changes. Note the supported Markdown in `auto-runner/central/README.md`.
  *Slice:* 2 Plans read as written
  *Acceptance:* `python -m pytest -q tests/central/test_ui.py tests/central/test_task_ui_audit.py` passes with new tests: a Reuse check table renders as a table with 3 header cells and its rows; `*Acceptance:*` renders as `<em>Acceptance:</em>` and `**x**` still as bold; a fenced block keeps `<script>` escaped and `**` literal; `[docs](https://example.com)` is a link with `rel="noopener noreferrer"` and `[x](javascript:alert(1))` is not a link; `<b>` in a cell is escaped; the existing `ui.md` tests still pass; the task page of a plan with a table shows `<table`; the docs check passes; full suite green.
- [ ] **V3 Item, close and runner labels say what is true.** In `ui.py` `STATUS`, the item state `queued` reads "Not started". In `templates/task.html`: on a `done` task, items with no state from a run show no status badge; a `rejected` task's `note` shows under "Why it was closed" (with who closed it and when, from its `task.close` audit row when there is one) instead of "Runner's last report"; the screenshot upload form is not shown for `done` or `rejected` tasks (existing screenshots still show); the side panel's runner line says "No runner has taken it yet" instead of "none". In `templates/_project.html` the Tasks tab's count gets `title` and screen-reader text "open tasks", and the attention pill reads "N to approve". Note the new labels in `auto-runner/central/README.md`.
  *Slice:* 3 Every label tells the truth
  *Acceptance:* `python -m pytest -q tests/central/test_task_ui_audit.py tests/central/test_tasks.py tests/central/test_work_and_tasks.py` passes with new tests: an unbuilt item shows "Not started" and never "Queued"; a done task without runs shows no item badge; a task closed with "duplicate of #10" shows "Why it was closed", the reason and the closer, and not "Runner's last report"; a done or rejected task has no upload form; an open task's side panel says "No runner has taken it yet"; the Tasks tab shows "1 to approve" and the count's label "open tasks"; existing tests updated where they expected the old words; the docs check passes; full suite green.
- [ ] **V4 Contrast in both themes, and docs.** In `static/style.css` choose `--attn-mark` and the `.pill.attention` text colour so the pill's text is at least 4.5:1 in the light and the dark theme, and make the decision bar's muted text at least 4.5:1 on `--attn-soft` (a darker muted colour inside `.decision-bar`). Add a test that parses `style.css`, takes the colours of `:root` and `:root[data-theme="dark"]`, and checks those pairs with the WCAG formula. Note the contrast rule and its test in `auto-runner/central/README.md`.
  *Slice:* 3 Every label tells the truth
  *Acceptance:* `python -m pytest -q tests/central/test_task_ui_audit.py tests/central/test_theme.py` passes: the attention pill's text and background are at least 4.5:1 in both themes and the decision bar's muted text on `--attn-soft` is at least 4.5:1 in both; the docs check passes; full `python -m pytest -q` green.

## Needs a person
- Deploy the service, then look at the board at 1366 px and on a phone, in both themes (the tests check the rules, not the look).
- Assumed: "N to approve" in the tab is acceptable wording; the pill keeps its colour family.

## Verification
`python -m pytest -q` per item through the app's test client; the contrast test computes ratios from `style.css`. How the
pages look is unverified until someone opens them.

## Spec check
Round 1: no findings. Added afterwards without a re-check: each item updates `auto-runner/central/README.md` itself,
because `docs/doc-map.json` maps `auto-runner/central/**` to it and the runner's docs gate checks every item (task 10's
run stopped there once).
