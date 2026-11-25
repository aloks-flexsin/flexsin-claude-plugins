# auto-runner central service

A small Flask service where an unattended run's blockers can be seen and answered away from the terminal, and where the
runner picks up the answers. It implements `auto-runner/docs/delivery-contract.md` over HTTP; the runner's side is
`HttpTransport` in `auto-runner/bin/autodeliver.py`. Why it is built this way: `docs/decisions/0001-central-service.md`.

**Status: unverified.** Everything below is proven only by tests on SQLite through the Flask test client. Nothing has run on
Vercel, against a real Postgres, or with a real runner talking to a deployed service.

The runner sends its events here when `central` is on in its project's `.claude/auto.json` (see the plugin README,
"Delivery contract"); a blocker it raises then shows on the project's **Blockers** page with its question and options,
and an owner, manager or admin answers it there.

## Layout

| File | What it does |
| --- | --- |
| `__init__.py` | `create_app(config=None)`, security headers (strict CSP, nosniff, no-referrer), `GET /healthz` |
| `config.py` | Reads the environment variables below; the production check |
| `db.py`, `schema.sql` | DB-API layer (sqlite3 for `sqlite:` URLs, psycopg for `postgres:` URLs) and the ANSI SQL schema |
| `passwords.py`, `accounts.py` | scrypt hashing, password policy, account store |
| `auth.py`, `sessions.py` | Sign-in and sign-out, lockout, server-side sessions, CSRF |
| `admin.py` | Account administration and own password change |
| `access.py`, `projects.py` | Projects, members, the access guard, token pages |
| `tokens.py` | Per-project API tokens and the `bearer_token(scope)` guard |
| `ingest.py` | `POST /api/v1/events` |
| `blockers.py` | Blocker list, blocker page and the answer form |
| `deliveries.py` | Decisions and the delivery endpoints |
| `identities.py` | Claude identities (`claude_identity`): the username and Claude account a signed-in person's runners report in `run_start` events; project-token events record nothing; first sighting audited as `identity_seen`; each row also has a surface (`auto-runner`, `cli`, `desktop` or `browser`; today every caller records `auto-runner`), and `init_db` rebuilds an older table without `surface` once, copying its rows as `auto-runner` (unverified in production) |
| `audit.py` | The audit log |
| `runners.py` | Runner presence: `POST /api/v1/runners/beat`, silent after 2 hours, pause/stop returned; Pause, Resume and Stop on the pages |
| `notifications.py` | In-app notifications: `notify` (members only, no duplicates), `resolve`, `unread_count`, `for_account`, `delivery`, `recent`; the Notifications page, open and read-all (the admins' log is in `admin.py`) |
| `push.py` | Browser push subscriptions: `POST /push/subscribe`, `POST /push/unsubscribe`, the Account page's push section |
| `attachments.py` | Task screenshots (`task_attachment`): `add`, `for_task`, `get`, `delete`; upload, view and delete routes; the 4 MB request cap and its 413 page |
| `cli.py` | `python -m central init-db` and `python -m central create-admin --email X` |
| `api/index.py`, `vercel.json` | Vercel entry point; every route is rewritten to it |
| `templates/`, `static/style.css` | The pages (`base.html` holds the top bar) and their one stylesheet, served by Flask at `/static/style.css` because the CSP allows only same-origin styles; no inline styles or scripts |

## Environment variables

Listed in `.env.example`; no value is ever kept in the repo.

| Name | Meaning |
| --- | --- |
| `SECRET_KEY` | Random key, at least 32 characters in production. The service refuses to start without it. |
| `DATABASE_URL` | `sqlite:///path/to/central.db` locally, the Supabase `postgres://...` connection string in production. |
| `CENTRAL_ENV` | `production` turns on the production check (so does Vercel's `VERCEL_ENV=production`). |
| `PUBLIC_URL` | The service's `https://` address; required in production. |
| `CENTRAL_VAPID_PUBLIC_KEY` | VAPID public key for browser push (shown to the page). Optional: push is off unless all three VAPID settings are set. |
| `CENTRAL_VAPID_PRIVATE_KEY` | VAPID private key that signs pushes; never shown on a page. |
| `CENTRAL_VAPID_SUBJECT` | Contact for push services, `mailto:...` or an `https://` address. |

## Local run and tests

From the repository root:

```bash
pip install -r requirements-dev.txt
python -m pytest -q tests/central tests/auto_runner/test_delivery_contract.py
```

To run it locally (from `auto-runner/`, with `SECRET_KEY` and `DATABASE_URL=sqlite:///central.db` set):

```bash
python -m central init-db
python -m central create-admin --email you@example.com
flask --app "central:create_app()" run
```

The session cookie is `__Host-` and Secure; browsers that do not treat `http://localhost` as secure will drop it, so sign-in
may not stick over plain http. The API endpoints do not use the cookie.

## Sign-in and access

- **No shared passwords** (decision record 0008, `invites.py`). Nobody types a password for someone else:
  - On a project's members page an owner or admin **invites** an email with a role. An existing account is added; an
    email without one gets an account with **no password** (it cannot sign in), the membership, and a **one-time link**
    shown once on the page, which the inviter sends on. "New invite link" makes a fresh one for a member still marked
    *invited*. Admins can also add an account on `/admin/accounts` (a link, no password).
  - An admin's **Send reset link** takes the current password away at once, ends every session and makes a link. An
    admin changes their own password on the Account page, not here.
  - A link (`/invite/<token>`) works **once**, **expires after 72 hours**, and a newer link for the same account ends the
    older one; a disabled account's link does not work. Only its SHA-256 is stored (table `invite`), it is never in the
    audit log, and setting a password with it follows the password policy, ends every session and signs the person in.
    The service sends no email. Audited: `invite.create`, `invite.accept`, `account.password_reset`, `reset.accept`.
- There is no self sign-up. Five failed sign-ins lock an account for 15 minutes.
- Sessions expire after 12 hours, or 2 hours idle. A password change or reset signs the person out everywhere.
- Global roles `admin` and `user`; project roles `owner`, `manager`, `developer`, `viewer`. Non-members get 404.
- Every sign-in (success and failure, never the password) and every account, token, membership and decision action is audited.
- **One project per repository.** On the create form the admin pastes the repository URL; the slug and name may be left
  empty and are then filled in from it (`projects.slug_from_repo`: the repository's name, lower case, other characters as
  `-`; `<owner>-<repo>` when that slug is taken). Every URL form of one repository has one `repo_key` (`host/owner/repo`,
  lower case, no user, port or `.git`; `projects.repo_key`, kept equal to the plugin's `autocentral.repo_key` by a test),
  and a unique index refuses a second project for it, on create and on a repository URL change.
- A project has a repository URL (`repo_url`), required when an admin creates it and changed by owners and admins on the project
  page (audited as `project.repo_url` with the old and new value; a refusal as `project.repo_url.refused`, without the value).
  Only `https://host/owner/repo`, `ssh://[user@]host[:port]/owner/repo` and `user@host:owner/repo` are taken; a password, any
  user in an https URL, queries, fragments, other schemes and local paths are refused. The service never holds git credentials.

## Tasks (decision record 0007, step 4; `tasks.py`)

- `/projects/<slug>/tasks` (any member): the project's tasks under state tabs with counts (`?state=approval|progress|open|done`;
  with none chosen, the first tab that has tasks), "Mine only" (`?mine=1`) and a title search (`?q=`); each row shows the
  person, state, runner, build progress, pull request and when it last changed.
  The board is the default view (`?view=board`): six columns (Open, Planning, Needs approval, Ready, Building, Done),
  newest change first, with a Board | List toggle that keeps `mine` and `q`; `?view=list` or a `?state=` gives the list.
  A `?view=board|list` sets the `task_view` cookie (one year, `path=/`, Secure, HttpOnly, SameSite=Lax); with no `view`
  or `state` the cookie is used when it is exactly `board` or `list`, else the board (unverified). The Done column shows
  the 20 most recently updated tasks after the `mine`/`q` filters, its heading count is the filtered total, and a
  "Show all N in the list" link goes to `?view=list&state=done`.
  The board page is wider (`main.wide`, 1440 px) and the board scrolls sideways by itself, so the page never does;
  long titles wrap, and a card shows its runner and pull request on their own lines (unverified for looks).
  Marked **unverified** until used live.
  Labels that say what is true (unverified for looks): an item no run has touched reads "Not started"; on a done task an
  item with no run state shows no badge; a rejected task's note sits under "Why it was closed" with who closed it and
  when (from its `task.close` audit row); the screenshot upload form is hidden on done and rejected tasks; the runner
  line reads "No runner has taken it yet"; the Tasks tab count is labelled "open tasks" and the attention pill reads "N to approve".
- `/projects/<slug>/tasks/new`: people who can run runners (developer and above) add a task for themselves; admins for
  any such member. "How we know it's done" is stored as a `Done when:` section of the description (no column of its own).
- `/projects/<slug>/tasks/<N>`: a step tracker (Open → Planned → Approval → Building → Done), the decision bar while the
  plan waits (Approve; Request changes opens the required comment; Reject asks first), the build's progress, the plan
  rendered as Markdown (escaped), the items as a checklist with each item's live state and acceptance line, a side panel
  (person, runner, branch, pull request), the admin's release/reassign under **More**, the runs and an **Activity**
  history (from the audit log: added, claimed, plan posted, decisions, released, reported; plus run starts and ends).
  Pages showing work in progress reload every 30 seconds while nobody is typing.
- States: `open` → `planning` (a runner claimed it) → `plan_review` → `approved` → `building` → `done`; or
  `changes_requested` (back to the runner with a comment) and `rejected`. A task typed in the terminal is recorded as
  `queued` (its plan was approved there; shown as "Queued" under In progress). Its claim stays alive while it is
  queued, like a plan waiting for approval. A heartbeat with no state, or `"state": "building"`, moves `queued` to
  `building`; `"state": "queued"` moves an approved or building task back to `queued`. `report` accepts a queued task, the
  next-task call never takes one, and an admin release moves it to `approved`.
  A queued task's heartbeat may carry a `note` (at most 300 characters) saying why it waits, such as "waits:
  <branch> is not merged into origin/main yet"; the task page shows it under the Queued line. The note is cleared when
  the task moves to `building` (a heartbeat or the next-task call) or is released (unverified until seen live).
- **Close a task** from its page (unverified until used live): `POST /projects/<slug>/tasks/<n>/close` with a required
  `reason` (at most 500 characters), by the assignee or a project manager, owner or admin, after a confirm step. It works
  for `open`, `planning`, `changes_requested`, `approved` and `queued` tasks: the task becomes `rejected`, the reason is
  stored in `note`, the claim ends and `task.close` is audited. A `building` task is refused ("stop its runner first");
  final states are refused too. The task page shows the Close form for those states and roles only, and its Activity
  says who closed it and why.
- **Plan approval** on the task page: Approve, Request changes (a comment is required) or Reject, by the assignee or a
  project manager, owner or admin. Audited.
- **Claims:** only the runner holding a task's claim may post its plan, beat or report. A claim lives while its runner
  beats (any task call) within 2 hours, and for as long as its plan waits for a person. A silent claim expires and the task
  can be taken again. Admins **release** a claim or **give the task to someone else** on the task page (audited): the
  runner gets 409 on its next call and stops that task. Work goes back one step (planning → open, building → approved);
  a new assignee plans it afresh.
- **API** (personal tokens only, the project named as for every call): `GET /api/v1/tasks` (every task, with the runner
  working on each, never a claim id), `GET /api/v1/tasks/<N>`, `POST /api/v1/tasks/next` (`{runner}` → claims the account's
  next task: approved, building, changes requested, planning, then open; returns the task, the claim and `action`
  plan|build), `POST /api/v1/tasks` (record a terminal task as `queued`; with `"approval": "page"`
  it is proposed instead: stored as `plan_review` with its plan and items, claimed by the runner, approved on the page), `POST /api/v1/tasks/<N>/plan` (`{claim, plan, items}`),
  `.../heartbeat` (`{claim, state?: queued|building, note?}`), `.../report` (`{claim, result: done|stopped, branch, pr_url, note}`). 409 = this runner no
  longer holds the task.
  `POST /api/v1/tasks/<N>/decide` (`{decision: approve|changes|reject, comment}`) decides on a waiting plan as the
  token's person, with the same rules as the task page (assignee or manager and above; `changes` needs a comment; 409
  when no plan waits; 404 for an unknown task); it records `task.plan.<decision>` with `"via": "api"`, so the Activity
  line ends "(through the API)", and returns `{task}`.
- Table `project_task` (init-db creates it).

## Task screenshots (`attachments.py`)

- Images attached to a task, stored in the database (table `task_attachment`, data as `BYTEA`; init-db adds the table to
  an existing database). Only **PNG, JPEG, GIF and WebP** are kept, recognised by their first bytes (never by the name or
  the type the browser sends); at most **4 MB** each and **10 per task**. The name is kept as shown (last path part only).
- `POST /projects/<slug>/tasks/<N>/attachments` (multipart `file`, one or more; members except viewers, so developer and
  up; CSRF as every form): each good image is stored and audited (`task.attachment.add`); refused files are named with the
  reason on the task page (status 400) while the good ones are kept. Viewers get 403, non-members 404.
- `GET /projects/<slug>/tasks/<N>/attachments/<id>` (any member): the bytes with the stored content type,
  `X-Content-Type-Options: nosniff`, `Content-Disposition: inline` and `Cache-Control: private`.
- `POST .../attachments/<id>/delete`: the uploader or an admin, after a confirm step; audited (`task.attachment.delete`).
- **For the runner** (task API, personal tokens of a developer or above, the project named as for every call):
  `GET /api/v1/tasks/<N>` lists `attachments` (`id`, `filename`, `content_type`, `size`; no bytes), and
  `GET /api/v1/tasks/<N>/attachments/<id>` returns the bytes with the same headers as the page route. A project token gets
  403, a screenshot of another project's task (or an unknown id) 404. `autotasks.py next` downloads them (see the plugin
  README). Unverified until used against the live service.
- **Every request is capped at 4 MB** (`MAX_CONTENT_LENGTH`): a larger one gets 413 with a page (JSON under `/api/`)
  that names the limit. So one upload request carries at most 4 MB of files in total.
- The task page has a **Screenshots** section: a thumbnail of each image (the image itself, sized by CSS in
  `static/style.css`) linking to the full image, its filename, uploader and time, and a delete button under "⋯" for the
  uploader and admins (confirm step). Members except viewers get an upload form (`multipart/form-data`, `multiple`,
  `accept` limited to the four types) with the limits in words; viewers see the screenshots but no form. A refused file
  shows the reason above the page, which stays usable.
- **While adding a task**: the New task form (`multipart/form-data`) has the same file input for members except viewers.
  `tasks.task_add` creates the task first, then stores the files (`attachments.store_files`, audited as above), so a
  refused file never loses the task. The browser is sent on to the task page with the refusals in `?refused=`, signed
  with `SECRET_KEY` and bound to that task (itsdangerous, which ships with Flask), and the page names each refused file
  and why; a tampered value or one from another task shows nothing. The 4 MB request cap covers the whole form.
- **Paste and drag-and-drop** (`static/app.js`): forms with `data-paste-images` (the New task form and the task page's
  upload form) take pasted or dropped PNG/JPEG/GIF/WebP images into their file input through `DataTransfer` (pasted
  images are named `pasted-<n>.<ext>`), list the chosen files with their sizes, and warn when together they are over
  `data-max-bytes`. Text pastes are left alone; on a page with one such form a paste anywhere goes to it. Without
  JavaScript the plain file input still works. The browser behaviour is unverified until tried live (tests check the
  HTML and that the handlers exist).
- Postgres storage is unverified until used live (tests run on SQLite).

## Runner presence (decision record 0009; `runners.py`)

- `POST /api/v1/runners/beat?project=<slug>` (personal tokens only, developer or above) with
  `{label, state, task: N|null, spend_usd}`, sent by a watching runner each step and when idle. The first beat for a
  label creates the runner, owned by that person; a label belongs to one person per project, so another person's token
  gets 403. Returns `{runner, control}`, where `control` is `null`, `"pause"` (wait without claiming) or `"stop"` (end
  cleanly).
- A runner whose last beat is 2 hours old or more is **silent** (`silent: true`); its next beat brings it back.
- Audit: `runner.start` (first beat of a label) and `runner.back` (a beat after silence); ordinary beats are not audited.
- Table `runner` (init-db adds it to an existing database). The task API is unchanged. Unverified on the live service.
- **On the pages** (`templates/_runners.html`): Overview and Runs list the project's runners to every member, My work
  lists **Your runners** (your own; every runner for an admin). Each shows the person, the label, the state in words
  (*Watching for work*, *Building · task #3*, *Planning*, *Paused*, *Silent*), the last beat, today's spend and a pending
  request ("Asked to pause", "Asked to stop").
- **Pause, Resume, Stop:** `POST /projects/<slug>/runners/<id>/<pause|resume|stop>`, for the runner's own person and
  admins only (anyone else gets 404, as for every hidden thing). Each asks first (the `data-confirm` dialog, or the
  confirm page without JavaScript; nothing changes without `confirm=1`), sets `control` (Resume clears it), and writes
  `runner.pause`, `runner.resume` or `runner.stop` to the audit (shown in Recent activity). Pause and Stop take effect at
  the runner's next beat; the watch loop obeys them from H12. Unverified on the live service.
- **Starting a watcher:** `/auto-runner:central` offers watch mode (`/auto-runner:auto watch`) as its optional last
  step once a checkout is connected and signed in; people read about it in `docs/central-guide.md` ("Hands-free work").
  The service needs nothing for it beyond `runners/beat` and the task API. Unverified on the live service.

## Notifications (`notifications.py`)

A `notification` row is kept per person for each thing that waits on them. Only enabled **members** of the project
get one: a site admin who is not a member gets none (they still see everything on My work). While a person has an
unresolved notification with the same key, the same event again adds nothing; once the thing no longer waits, every
notification with its key is marked resolved. Each new notification is also pushed to the person's browsers (see
"Sending pushes" below).

| Kind | When | Who | Text and link | Resolved when |
|------|------|-----|---------------|---------------|
| `plan` | a plan is posted (`tasks.post_plan`) or proposed for page approval (`POST /api/v1/tasks` with `"approval": "page"`) | the assignee, managers and owners | "Plan waiting: #N title" → the task page | any decision on the plan; a reassignment |
| `changes` | a plan is sent back with changes | the assignee | "Changes asked on #N title" → the task page | the next plan is posted; a reassignment |
| `assigned` | an admin adds a task for someone else, or gives a task to a new person (release with an assignee) | the new assignee | "Assigned to you: #N title" → the task page | not resolved |
| `blocker` | a new raised blocker arrives through ingest | managers and owners | "Blocker waiting: question" → `/projects/<slug>/blockers/<id>` | the blocker is answered (`deliveries.record_decision`) |

Taking a task back without a new person leaves a plan that still waits for approval (and its notification) as it is.
`python -m central init-db` adds the table to an existing database.

**Bell and Notifications page.** Signed in, the top bar shows a bell linking to `/notifications`, with a count of the
person's notifications that are unread and still waiting (`notifications.unread_count`, from `shell`'s context
processor); the count is hidden at zero. The page lists only the person's own notifications (`for_account`): waiting ones
first, newest first, each with its project and time, and below them the resolved ones marked "Done" (never counted).
Each notification is a small form: `POST /notifications/<id>/open` marks it read and redirects to its link, only when
the link is a same-site path (`ui.safe_next`; otherwise back to the page); another person's id gets 404.
`POST /notifications/read-all` ("Mark all as read") clears the count. Both carry the CSRF token like every form; no
inline styles or scripts.

**Push subscriptions (`push.py`).** A `push_subscription` row (account, endpoint (unique), `p256dh`, `auth`, user agent,
`created_at`, `last_ok_at`, `failures`) records each browser where a person turned push on; `init-db` adds the table to
an existing database. `POST /push/subscribe` takes the browser's `PushSubscription` JSON (`endpoint`, `keys.p256dh`,
`keys.auth`) and `POST /push/unsubscribe` takes `{"endpoint": ...}`; both need a signed-in session (401 otherwise) and
the page's CSRF token in the `X-CSRF-Token` header (`auth._check_csrf` accepts the header when the form field is
missing; a missing or wrong token gets 400). The endpoint must be `https://` (400 otherwise). One row per endpoint:
subscribing again updates the keys, and if another person turns push on in the same browser the row becomes theirs.
Unsubscribe deletes the endpoint only when it is your own. Without all three `CENTRAL_VAPID_*` settings, subscribe
answers 409 and the Account page's "Notifications on this browser" section says push is not set up on this service.
With them, the section carries the public key, the CSRF token and both URLs as `data-` attributes, says in how many
browsers the account has it on, and holds the Turn on / Turn off buttons, hidden until the browser script (N4) finds
push support.

**Service worker and the browser side.** `GET /sw.js` serves `static/sw.js` from the site root as `text/javascript`
with `Service-Worker-Allowed: /` and `Cache-Control: no-cache`, so it can control scope `/`. On `push` it shows a
notification from the JSON payload `{"title", "body", "link"}` (title defaults to "auto-runner"); on
`notificationclick` it focuses a tab already showing the link or opens one. Links are same-origin only: any other
link opens `/notifications`. In `static/app.js`, when the Account page has the push section and the browser supports
service workers, `PushManager` and `Notification`, the script registers `/sw.js`, shows Turn on or Turn off for this
browser's state, and: Turn on asks permission, subscribes with the VAPID public key from `data-vapid-public-key`
(`userVisibleOnly`) and posts the subscription to the subscribe URL with `X-CSRF-Token`; Turn off unsubscribes the
browser and posts its endpoint to the unsubscribe URL. Requests go only to same-origin URLs; no inline script, so it
works under the CSP (`default-src 'self'` covers the worker and `fetch`). Not yet tried in a real browser
(**unverified**); the tests check the headers and that the handlers exist.

**Ask once per browser.** When push is set up (all three VAPID settings), every signed-in page except the Account page
(which has its own section) carries a hidden bar under the top bar, `<div class="push-ask" data-push-ask>` with the
public key, the CSRF token and the subscribe URL, the text "Get a notification when a plan or blocker waits for you."
and the buttons **Turn on** and **Not now** (`shell.py` adds `push_ask_key` to the page context; `base.html` renders
it). `static/app.js` shares `enablePush(box)` (permission, register `/sw.js`, subscribe, post) between the Account page
and the bar, and shows the bar only when the browser supports push, `Notification.permission` is not `denied`, this
browser has no subscription yet and `localStorage["push-ask"]` is unset (storage errors keep the bar hidden). Turn on
subscribes, hides the bar and stores `done`; a refusal shows the same "blocked" message in the bar; Not now hides it
and stores `dismissed`. Not yet tried in a real browser (**unverified**).

**Sending pushes.** `notifications.notify` pushes each notification it adds to every subscription of its recipient
(`push.send_to_account`), with the payload `{"title": project name, "body": text, "link": link, "tag": key}`, through
`push_sender(subscription, payload, vapid, timeout=5)`: in production `push.push_sender`, which uses `pywebpush` (in
`requirements.txt`) with the VAPID private key and `CENTRAL_VAPID_SUBJECT` as the `sub` claim; tests set
`app.config["PUSH_SENDER"]` to a fake. The notification row records `push_state`: `off` (with the reason in
`push_detail`) when the VAPID settings are missing or the person has no subscription, `sent` when at least one browser
took it, or `failed` with the reason in `push_detail`. A 404 or 410 from the push service deletes that subscription
(the browser dropped it); any other failure adds one to its `failures`, and a success sets `last_ok_at` and resets
`failures`. Pushing never raises into the caller: the request that caused the notification still succeeds. A repeated
event that adds no notification sends no push. The push goes out before the caller commits, so if the caller's
change is then rolled back, the person may already have been pushed about it. Each request waits at most 5 seconds,
in the request that caused the notification. Not yet tried against a real push service (**unverified**); the tests use
a fake sender.

**Delivery and the notification log.** Each row on `/notifications` says how it reached the person
(`notifications.delivery`): "push sent", "push failed: <reason>" (the reason from `push_detail`), "push off" (no VAPID
keys or no browser with push turned on), or "in-app" for rows where no push was tried (made before pushing existed).
Admins also have `GET /admin/notifications` ("Notification log", linked from Accounts): the newest 200 notifications
across everyone (`notifications.recent`), each with its recipient, project, kind, delivery and time, newest first.
`?account=` (an account id or email) and `?kind=` (`plan`, `changes`, `assigned`, `blocker`) narrow it, together or
alone; an unknown account or kind lists nothing. Anyone else, signed out included, gets 404, like every admin page.
Delivery words for real pushes are **unverified** until push is tried live.

## Presentation (UX audit foundations)

- The CSP allows styles and scripts only from the app's own files, so no template has an inline `style=` or inline
  script (a test enforces it): looks live in `static/style.css`, behaviour in `static/app.js`, and every page works
  without JavaScript.
- `ui.py` gives every page one way to show things: `ago` / `when_abs` (relative and absolute time, from the injectable
  clock), one status vocabulary with tone and shape (`STATUS`), runner reasons in words (`reason_text`, `next_step`),
  people (`display_name`, `initials`; `account.name` when set, else the email's local part), runners
  (`runner_label`), and `md`, an escape-first Markdown subset for plans: headings, lists, bold, italic, code, fenced code blocks, pipe tables (in a sideways-scrolling `.table-wrap`) and `http(s)` links (`rel="noopener noreferrer"`; other URLs stay text); no HTML. `templates/_ui.html` holds
  the matching macros (status chip, person, runner, time, progress, copy field, empty state, danger form).
- **Actions that cannot be undone ask first:** remove a member, disable an account, revoke all of an account's tokens,
  revoke a personal or machine token, replace a machine token, reject a plan, take a task back or give it to someone
  else. Without `confirm=1` the route answers with a page saying what will happen and posts the same form again; with
  JavaScript a dialog asks instead (`data-confirm` on the form).
- `account.name` (nullable) is added by `init-db`.
- **Theme: light by default.** Pages render `<html data-theme="light">` whatever the system setting; dark colours apply
  only under `:root[data-theme="dark"]` in `static/style.css`. `ui.theme()` reads the `theme` cookie (`dark` switches,
  anything else is light) and a context processor hands it to `base.html`.
- **Contrast rule.** Text must reach WCAG 4.5:1 in both themes. The attention pill uses `--on-attn-mark` on
  `--attn-mark`, and `.decision-bar` sets `--muted` to `--attn-muted` so muted text reads on `--attn-soft`.
  `tests/central/test_theme.py` parses `style.css` (`:root` and `:root[data-theme="dark"]`) and checks those pairs with
  the WCAG formula; change a colour there and the test tells you if it fell below 4.5.
- **Dark mode toggle.** The top bar (signed in and signed out) has one button, "Dark mode" when light and "Light mode"
  when dark. It posts `theme` and `next` (the current path) to `POST /theme` (`auth.py`, CSRF checked like every form),
  which accepts only `light` or `dark` (anything else is 400 and sets nothing), sets the `theme` cookie for one year
  (`Secure; HttpOnly; SameSite=Lax; Path=/`) and redirects to `next` through `ui.safe_next` (default `/my`).

## The frame (UX audit, PR 2; `shell.py`)

- Signed in, `/` goes to **My work**. The top bar: My work (with the number of decisions waiting for you: plans you may
  approve and blockers you may answer), Projects, Accounts (admins), and an account menu (Account, My tokens, Sign out).
- Every project page shares one header (name, repository, your role, members, New task for people who run runners) and
  tabs: **Overview** · Tasks (open, plans waiting) · Runs (live) · Blockers (waiting) · **Settings**. The counts come
  from `shell.project_nav`, injected into every page that has `g.project`.
- **Overview** (`/projects/<slug>`): Needs attention (plans waiting, blockers waiting, silent claims; deciders get the
  action, others "View"), Working now (live claims with progress, and running runs), Recent activity (audit rows in
  words). Refreshes itself every 30 s while open.
- **Settings**: Members (`/settings/members`), Repository (`/settings/repository`), Machine tokens (`/settings/tokens`;
  `/projects/<slug>/tokens` redirects). The projects list is cards with what waits in each.

## People and access (UX audit PR 5)

- **Settings › Members**: People with their email, when they were last active (newest of their sessions and personal
  token use; nothing new is stored), a role select that saves itself (a Save button without JavaScript), and Remove
  under a "⋯" menu (asks first). **Invite people** opens a small form; the one-time link comes back in a copy field.
  **Pending invites** lists people who have not set a password, with when their link expires, **New invite link** and
  **Cancel invite** (`POST /projects/<slug>/members/<id>/cancel`, owners and admins, asks first): the link stops working
  and they leave the project; the account stays without a password, so the email can be invited again.
- **Settings › Machine tokens**: a new token comes back in a copy field; Replace and Revoke ask first.
- **Admin › Accounts**: search (`?q=`), each account's projects count and last active, role select that saves itself,
  and Send reset link / Revoke tokens / Disable under "⋯". **Add account** opens a small form.
- **My tokens**: one row per computer with when it signed in and was last used; Revoke asks first.

## Sign-in, invite, device and Account (UX audit PR 6)

- **Sign in**: one focused card with Show for the password. A failed sign-in gets the same words for every cause (unknown
  email, wrong password, locked, disabled) plus how the lockout works (5 wrong tries, 15 minutes), so it never says
  whether an email has an account.
- **Invite / reset link** (`/invite/<token>`): an invited person can give their name (optional, `account.name`); the
  password has Show and a live checklist (12+ characters, both the same) from `app.js`. The server's rules are
  unchanged and decide; a bad name refuses the form and leaves the link and the password unset.
- **Device** (`/device`): the code large, the computer and repository, and a countdown to when the code expires; Deny
  and Approve.
- **Account** (`/account`, was Password): your name (`POST /account/name`) and your password (`POST /account/password`,
  as before; `GET /account/password` redirects here). Admins set anyone's name from Accounts
  (`POST /admin/accounts/<id>/name`). Name changes are audited as `account.name`. The page also lists **Your Claude
  accounts** (from `identities.of_account`); on **Runs**, a run sent with a personal token shows the person's name,
  username and Claude account (runs from project tokens keep the old text; unverified in production).
- **Team Claude accounts**: the project's Members page shows each member's Claude accounts
  (`identities.team_view`), marks one reported by more than one member as "shared" and warns when one username is
  reported by more than one member. Runs has a second chip row `?claude=<name>` ("All Claude accounts" plus one chip
  per Claude account of the project); it combines with `?outcome=` (each row's links and the outcome counts keep the
  other's value) and an unknown value shows no runs. Read-only for every member role.

## My work and task progress

- `/my` (**My work**, first in the top bar; everyone signed in): across every project the person can see (all for an admin),
  narrowed to one with `?project=<slug>` (`mywork.py`). **Waiting for you**: plans they may decide and blockers they can
  answer (manager and above), as cards; a plan of two items or fewer is approved right on its card (the decide form's
  `next`, a same-site path only, brings them back), a longer one opens to be read. **In progress**: their own unfinished
  tasks first, then every other task a runner is on, each once, with build progress and a warning when a runner went
  quiet. **Recently done** with pull requests. Reloads every 30 seconds; nothing is sent to anyone.
- Plan posts and runner reports are audited (`task.plan.post`, `task.report`) for the task's Activity.
- A run names the task it builds (`task` in its `run_start` and `run_end` events, plugin 0.21.0+), so the **task page**
  shows the build live: items done out of the task's own items, the item in progress, the latest run's state and tests,
  blocked items, and every run of the task. The runs page links each run to its task.

## Runs

- `/projects/<slug>/runs` (any member): each run of the project, newest first, and a "Working now" list. Built from the
  stored events (`runs.summarise`, no table of its own): who ran it (`user` and `account` from the `run_start` event,
  plugin 0.17.0+; "unknown" for older runners), branch, state (*running*; *finished*; *stopped* with its reason; *silent*
  when a run has no end and no event for 2 hours), the item in progress, items done and blocked, blockers raised, the last
  independent test result and the pull request (a link only when it is an `https://` address). Only the newest 5000 events
  of a project are read.
- The page shows live runs as cards (person, task, item in progress, progress bar, last event, running for how long),
  then the history with outcome chips (`?outcome=stopped|silent|finished`): the reason in words ("tests failing"), how
  long the run took, and for a stopped or silent run the next step. It reloads every 30 seconds.

## Blockers

- `/projects/<slug>/blockers` (any member): the project's blockers, question first, the ones waiting for an answer on
  top with how long they have waited.
- `/projects/<slug>/blockers/<blocker_id>` (any member): the question as the title, the item, the task and whose runner
  waits (from the run that raised it), what it holds up; for people who answer, the options as cards with their
  trade-offs and the recommended one marked and chosen in advance, optional instructions and **Send answer**; the
  context (if late, if wrong, assumptions, evidence) folded under **Context**; and the answers so far as sentences with
  their delivery state. All of it as the runner sent it (redacted at ingest). A blocker raised by a runner older than
  this version shows only its id.
- Owners, managers and admins answer a waiting (OPEN or ESCALATED) blocker with one of its options and/or text. The
  answer is stored as a `decision.v1`, queued as a delivery and audited; the runner picks it up at its next pull, and
  the blocker shows RESUMED once the runner reports that it took the item up again. A blocker cannot be answered twice
  from the page.

## API

Send a token as `Authorization: Bearer ...`. A missing, wrong or revoked token gets 401; a token without the needed scope
gets 403.

- **Personal token** (`arp_...`, one per person and computer, from signing in; `personal.py`): has both scopes and no project
  of its own. Every call names the project, `?project=<slug>` (or `?repo=<host/owner/repo>` while the slug is not known),
  and the account must be an enabled developer or above there, or an admin: otherwise 403. Membership is checked on every
  call. Unused for 90 days it stops working; people revoke their own on **My tokens** (`/account/tokens`) and an admin
  revokes all of an account's on Accounts. Events sent with one are stamped with the account (`event.account_id`), and the
  runs page shows "signed in as <email>".
- **Project token** (`art_...`, made on a project's tokens page): for machines nobody signs in on. It may also send
  `?project=`, which must then be its own project.

**Sign in from Claude** (device codes; the plugin's `autocentral.py login start` / `login wait`):
`POST /api/v1/device/start` (no token; at most 10 a minute from one address, in memory) with `client_name` and `repo` returns
`device_code` (kept by the plugin), `user_code` (shown to the person, like `KQ4M-7RXD`), `verification_uri` (`/device`),
`interval` (5 s) and `expires_in` (600 s). The person opens `/device` signed in, types the code, sees the computer and the
repository, and approves or denies (audited). `POST /api/v1/device/token` with the `device_code` answers 400 with
`authorization_pending`, `slow_down`, `access_denied` or `expired_token`, or once 200 with `token` and `email`. Both codes are
stored only as SHA-256 (table `device_login`), and a request gives a token once. The start body may also carry `user` and
`claude_account` (the runner's username and Claude account); each is kept only when it matches the runner name pattern
(`identities.NAME`) and is shown on `/device` and on **My tokens** ("not reported" when absent). When the token is
delivered, the pair is also recorded as a Claude identity of the approving account (shown under "Your Claude accounts").

`runs.summarise()` also reads `account_check` from `run_start` (only `match`, `mismatch`, `not_set` or `unknown`; anything
else, or a missing field from an older runner, is ignored). The Runs page shows "Claude account not confirmed" beside the
Claude account for `mismatch` only (the runner's own report, unverified).

**Usage stop (unverified until seen live).** A runner that stops between items at its plan usage threshold ends with reason
`usage_threshold` (shown as "stopped early: plan usage threshold", next step "Start the runner again after the limit
resets.") and a `usage` object in the `run_end` detail. `runs.summarise()` reads it only when `window` is `five_hour` or
`seven_day` (the service's own copy of the runner's `WINDOW_LABELS`: "5-hour", "7-day"), `utilization` is a number from 0
to 1 and `resets_at` an epoch integer; anything else is ignored, and the page then shows the reason without a percentage.
The Runs page shows "Stopped at 91% of the 5-hour limit (resets 14:30 UTC)": the reset is in UTC, with `D Mon` added when
it falls on a different day than the run's end.

**Who is identified how.** The person is verified: the personal token says whose account it is. The username and Claude
account are only what the runner reports (unverified). A task call (`next`, `record`, or `plan` with a `runner` field) made
with a personal token whose `runner` is `<user>/<account>-claude-auto-runner` (`identities.split_runner`, also used by
`ui.runner_label`) records that pair as a Claude identity of the signed-in account; any other `runner` text records nothing.
The task page shows the claim through the `ui.runner` macro.

| Endpoint | Scope | What it does |
| --- | --- | --- |
| `POST /api/v1/events` | `ingest` | One `event.v1` object or a list. Replies `accepted`, `duplicates`, `ids`, `duplicate_ids`. The same id with a different body gets 409, an unknown schema or bad event 400, a body over 256 KB 413, more than 120 requests a minute per token 429 (counted per process). The event's `project` must be the token's project. Run-level events (`run_start`, `run_end`, `publish`) have no item. Secrets in `detail` are redacted before storing. |
| `GET /api/v1/projects` | personal token, no project | `{"account", "projects": [{"slug", "name", "repo_url", "role", "can_run"}]}`: the signed-in person's projects (every project for an admin), for the plugin's "my projects" and clone. Project tokens get 401. |
| `GET /api/v1/project` | any | `{"slug", "name", "repo_url", "account"}` of the token's project (`account`: the signed-in email for a personal token, else null) (`repo_url` is null for a project made before the column existed). The plugin's init and join use it to check they are pointed at the right repository. |
| `GET /api/v1/deliveries/pending` | `pull` | `{"deliveries": [...]}`: the project's answers not yet acknowledged, oldest first; each is marked delivered. |
| `POST /api/v1/deliveries/<delivery_id>/ack` | `pull` | Marks a delivery acknowledged (a second ack changes nothing). Unknown or another project's id: 404; never delivered: 409. |

## Deployment (unverified; a person runs these steps)


**If a page says "This page needs a database update"** (HTTP 503): the code was deployed before
`python -m central init-db` added the tables or columns it uses. Run `init-db` once with the production `DATABASE_URL`;
it only adds what is missing. Only queries that hit a missing table or column answer this way (Postgres SQLSTATE
42P01/42703, SQLite "no such table/column"); every other error is untouched. API calls get the same as JSON.
None of these steps has been tried. Check each result before going on.

1. **Database.** Create a new Supabase project for this service only. Copy its Postgres connection string, then apply the
   schema from `auto-runner/`:
   ```bash
   DATABASE_URL="postgres://..." python -m central init-db
   ```
   (`pip install -r central/requirements.txt` first, for psycopg.) It is safe to run again; it creates any table an older
   database lacks (for example `invite`). On a database made by an older
   version it also adds the columns listed in `db.COLUMNS_ADDED` (`project.repo_url`, `project.repo_key`) and prints each
   one, fills `repo_key` from each project's repository URL and creates the unique index `project_repo_key`.
   **If two projects already name the same repository it stops before changing anything** (exit code 3) and lists them as
   `host/owner/repo: slug, slug`; keep one (move its members and tokens, delete the others) and run it again. The same
   change by hand: `ALTER TABLE project ADD COLUMN IF NOT EXISTS repo_url VARCHAR(500);`,
   `ALTER TABLE project ADD COLUMN IF NOT EXISTS repo_key VARCHAR(500);`, fill `repo_key`, then
   `CREATE UNIQUE INDEX IF NOT EXISTS project_repo_key ON project (repo_key);`.
2. **Vercel project.** Create a new Vercel project from this repository with the root directory `auto-runner/central`.
   Set the environment variables for Production: `SECRET_KEY` (from
   `python -c "import secrets; print(secrets.token_hex(32))"`), `DATABASE_URL`, `CENTRAL_ENV=production` and `PUBLIC_URL`
   (the project's https address). Deploy, then open `PUBLIC_URL/healthz`.
3. **First admin.** From `auto-runner/` with `DATABASE_URL` pointing at the live database:
   ```bash
   python -m central create-admin --email you@example.com
   ```
   Sign in at `PUBLIC_URL/signin`.
4. **Project and token.** Create the project from its repository URL (leave slug and name empty to fill them in; set the
   repository URL on the page of any project made before it existed), add members, and create a token with the
   `pull` scope (and `ingest`, for when the runner sends events). Copy it; it is shown only once.
5. **Runner.** `/auto-runner:central` (or `/auto-runner:init --central-url URL`, which reads the project from `origin`) writes
   this and checks the repository; by hand,
   in the project's `.claude/auto.json`:
   ```json
   "central": {"enabled": true, "url": "https://<PUBLIC_URL host>", "project": "<slug>"}
   ```
   and set `AUTO_RUNNER_CENTRAL_TOKEN` in the environment the runner starts from. With more than one project, give each its
   own variable: add `"token_env": "AUTO_RUNNER_TOKEN_<NAME>"` to its `central` section and set that variable instead.
   Never put the token in `auto.json`.
6. **Check.** Start a run; its preflight refuses with a reason if the url, project or token is missing.

Known limits: see "Consequences and known limits" in `docs/decisions/0001-central-service.md`.
