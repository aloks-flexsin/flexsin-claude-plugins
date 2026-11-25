# Task 5: a Kanban board view of the Tasks page

## Intent
The project's Tasks page is a list split into state tabs (Needs approval, In progress, Open, Done), so a person sees
one state at a time. The request: keep that list, add a Kanban board that shows every state side by side as columns, let
a person switch between the two, and open on the board by default. It is for everyone who opens a project's Tasks page
(developers, managers, admins). Success: opening Tasks shows the board; one click shows the list as it is today, and the
page remembers that choice. Out of scope: moving cards by drag and drop (a task's state is changed by its runner and by
plan approval, not by hand), new task states, and the "My work" page.

## Key decisions
1. **Read-only cards, no drag and drop.** Options: (a) cards are links to the task page; (b) drag to change state.
   **(a):** states move through runners, approval, release and close, each with its own rules and audit row. Dragging
   would bypass them. The board shows where work is; the task page stays the place to act.
2. **Columns.** One per stage of a task's life, using the existing statuses: **Open** (`open`), **Planning**
   (`planning`, `changes_requested`), **Needs approval** (`plan_review`), **Ready** (`approved`, `queued`), **Building**
   (`building`), **Done** (`done`, `rejected`). Six columns, in that order. Defined once as `BOARD_COLUMNS` in
   `tasks.py`, next to `STATES`, and every status appears in exactly one column (a test checks this).
3. **Done column size.** It grows forever, so it shows the 20 most recently updated and a "Show all N in the list" link
   to the list's Done tab. Other columns show everything.
4. **The toggle.** A two-option control ("Board" | "List") in the toolbar, as plain links with `?view=board` or
   `?view=list` that keep `mine` and `q`. No JavaScript, as on the rest of the page.
5. **Remembering the choice.** Options: (a) a cookie, like the colour theme; (b) a column on the account.
   **(a):** no schema change, the same pattern as `theme`. A `?view=` on the page sets the `task_view` cookie (one year,
   `httponly`, `secure`, `SameSite=Lax`, path `/`). With no `view` in the URL the cookie decides, and with no cookie the
   board shows. Only `board` and `list` are accepted; anything else is ignored.
6. **Filters.** "Mine only" and the title search apply to both views. On the board the state tabs are not shown (the
   columns replace them); `?state=` still works in the list view and implies `view=list` for that request, so old links
   to a tab keep opening that tab.

## End to end
- Data: no schema change. The board uses `for_project()` and `claim_live()`, as the list does.
- Security: read-only. Same `project_access("viewer")` as today. The cookie holds only `board` or `list`.
- Errors: an unknown `view` value falls back to the cookie or the default. An empty column shows a short muted line.
- Layout: columns sit side by side and scroll sideways on narrow screens (CSS only, both themes, existing variables).
- Deployment: a person deploys the service; nothing else changes. Rollback: revert the commit.
- Docs: `auto-runner/central/README.md` and `auto-runner/docs/central-guide.md`.

## Slices
- **Slice 1 Board by default, list one click away.** The Tasks page opens as a board with six columns, and the toggle
  shows the list as it is today.
- **Slice 2 The page remembers the view.** A person who picks the list keeps seeing the list on their next visit, the
  Done column stays short, and the docs describe the board.

## Journeys
- **J1 Board first.** Slice 1. Roles: developer.
  1. Open a project that has tasks in several states, then its Tasks tab.
  *Expect:* the board shows the columns Open, Planning, Needs approval, Ready, Building and Done, each task as a card in its state's column with its number, title, assignee and status.
  2. Choose "List" in the toolbar.
  *Expect:* the list with its state tabs, exactly as before.
- **J2 The choice sticks.** Slice 2. Roles: developer.
  1. On the Tasks page choose "List", go to the Runs tab, then back to Tasks.
  *Expect:* the list shows. Choosing "Board" and coming back shows the board.
- **J3 Filters on the board.** Slice 1. Roles: developer.
  1. On the board, tick "Mine only" and filter.
  *Expect:* only your tasks remain on the board, still in their columns.

## Libraries
Nothing new: the existing Flask view, Jinja templates, `_ui.html` macros (`ui.status`, `ui.person`, `ui.runner`,
`ui.progress`, `ui.when`, `ui.empty`), `static/style.css` variables, and the `theme` cookie pattern in `auth.py`.

| Requirement | Existing options | Cost | Licence | Recommendation | Why |
|---|---|---|---|---|---|
| Kanban board | the repo's own templates and CSS; JS board libraries such as SortableJS (https://github.com/SortableJS/Sortable, unverified) | free | repo's own; MIT (unverified) | build with the existing templates | read-only cards need no library and the page has no JavaScript |

## Queue items

- [ ] **K1 Board view of the Tasks page, with a Board | List toggle.** In `auto-runner/central/tasks.py` add `BOARD_COLUMNS` next to `STATES`: `(key, label, statuses)` for `open` "Open" (`open`), `planning` "Planning" (`planning`, `changes_requested`), `approval` "Needs approval" (`plan_review`), `ready` "Ready" (`approved`, `queued`), `building` "Building" (`building`), `done` "Done" (`done`, `rejected`). In `_list_page()` read `view` from `request.args`: `board` or `list`, else `list` when `state` is given, else `board`. For the board, build the columns from the same filtered rows (`mine`, `q`) the list uses, each column newest `updated_at` first, add `progress` to building or live rows as the list does, and render `templates/tasks.html` with `view` and `columns`. In `templates/tasks.html` add a "Board" | "List" toggle to the toolbar (links keeping `mine` and `q`, the current one with `aria-current="page"`), hide the state tabs on the board, and render the board as a `<div class="board">` of `<section class="board-col">` columns, each with its label and count as a heading and one card per task (`#N`, title linking to the task page, `ui.person`, live runner or "runner quiet, claim expired" as the list shows it, `ui.status`, `ui.progress` when present, PR link when present). An empty column says "Nothing here." The list view must render exactly as today. Add the board styles to `static/style.css` with the existing colour variables (columns side by side, scrolling sideways below 900px, readable in both themes). On the board the search form carries a hidden `view=board` and no hidden `state`, and its Clear link goes to `?view=board` without `state`; in the list view the form and Clear keep `state` as today. The list's "first tab that has tasks" fallback applies only to the list view. With no tasks in the project at all, the board view shows the existing "No tasks yet" empty state instead of the columns; when filters match nothing, it shows the six columns, each saying "Nothing here.". Update `tests/central/test_work_and_tasks.py`: its checks of the default page (the first tab with tasks, and the list's empty states) request `?view=list`, and one new check there asserts the board is the default. Add a short note to the Tasks page section of `auto-runner/central/README.md`: the board is the default view, its six columns, and the Board | List toggle (marked unverified).
  *Slice:* 1 Board by default, list one click away
  *Acceptance:* `python -m pytest -q tests/central/test_tasks_board.py tests/central/test_tasks.py tests/central/test_work_and_tasks.py` passes with new tests in `tests/central/test_tasks_board.py`: every status in `STATUSES` is in exactly one `BOARD_COLUMNS` entry; `GET /projects/<slug>/tasks` with tasks in several states shows the six column headings in order with each task's card in its state's column and the count of each column; `?view=list` shows the state tabs and no board; `?state=done` shows the list's Done tab; `?view=board&mine=1` shows only the signed-in person's tasks; an unknown `view` value shows the board; the toggle links keep `q` and `mine`; on the board the search form has no `state` input and `?view=board&mine=1&q=x` stays on the board; a project with no tasks shows "No tasks yet" on the board view; a filter matching nothing shows six columns saying "Nothing here."; the existing list tests still pass; the docs check passes; full `python -m pytest -q` green.
- [ ] **K2 Remember the chosen view, keep Done short, docs.** In `_list_page()`, when `request.args` has `view` set to `board` or `list`, set the `task_view` cookie to it on the response (max age one year, `path="/"`, `secure=True`, `httponly=True`, `samesite="Lax"`, the same flags as the `theme` cookie in `auth.py`). With no `view` (and no `state`) in the URL, use the cookie when it is exactly `board` or `list`, else the board. In the board's Done column show the 20 most recently updated tasks (the cap applies after the `mine` and `q` filters; the column heading's count and the N below are the filtered total) and, when there are more, a link "Show all N in the list" to `?view=list&state=done` keeping `mine` and `q`. Update `auto-runner/central/README.md` (the Tasks page section: the cookie and the Done cap) and `auto-runner/docs/central-guide.md` (how to switch views), marked unverified.
  *Slice:* 2 The page remembers the view
  *Acceptance:* `python -m pytest -q tests/central/test_tasks_board.py` passes with new tests: `?view=list` sets `task_view=list` with HttpOnly, Secure and SameSite=Lax, and a later `GET` with no `view` shows the list; `?view=board` switches it back; a cookie value other than `board` or `list` shows the board and sets nothing; `?state=open` shows the list without changing the cookie; with 25 done tasks the Done column shows 20 cards, its heading count is 25, and "Show all 25 in the list" links to the list's Done tab; with `mine=1` and 22 of them the signed-in person's, the count and the link say 22; the docs check passes; full `python -m pytest -q` green.

## Needs a person
- **Default view:** the request asks for the board by default for everyone; done as asked. A per-project default is not planned.
- Deploy the service.
- Assumed: read-only cards (no drag and drop), since state changes have their own rules.

## Verification
`python -m pytest -q` per item, through the real central app's test client on a temporary SQLite database. How the board
looks in a browser (both themes, narrow screens) is unverified until someone opens it.

## Spec check
Round 1 (4 findings, all fixed in the items): K1 now updates `tests/central/test_work_and_tasks.py` (the default view
changes to the board) and runs it; the board's search form drops the hidden `state` and Clear stays on the board; an
empty project keeps "No tasks yet" and filters matching nothing show empty columns; K2's Done count and "Show all N"
are the filtered total.
Round 2 (1 finding, fixed): K1 now adds its own README note (the board, its columns, the toggle), so the docs gate
passes after K1; K2 adds the cookie and the Done cap. The round-2 fix was not re-checked (two rounds allowed).
