# Using the central service

The central service is a small website where an unattended run's **blockers** (questions the runner cannot decide alone)
show up and are answered away from the terminal. The runner picks the answer up and carries on with that item.

**Status: unverified against the live service.** Every step below is tested only against the real app on SQLite through
its test client. How the service itself is deployed is in `auto-runner/central/README.md`.

What the page shows today: your projects; each project's **runs** (who is running what: the person and Claude account,
branch, state, the item in progress, items done and blocked, the last test result and the pull request), its blockers
(question, options, recommendation, evidence, the answers so far), its members and its tokens. Runs are built from the
events each runner sends (plugin 0.17.0 or later names who ran it; older runners show as "unknown"). A run with no end
shows as *running*, then as *silent* after 2 hours without an event (the runner stopped without saying so).

## Who can do what

| Role | See blockers and members | Answer blockers | Tokens | Members and repository URL | Create projects and accounts |
|---|---|---|---|---|---|
| viewer, developer | yes | no | no | no | no |
| manager | yes | yes | yes | no | no |
| owner | yes | yes | yes | yes | no |
| admin (site-wide) | every project | yes | yes | yes | yes |

There is no sign-up and nobody passes passwords around: people are **invited** (below) and set their own password from a
one-time link. Someone who is not a member of a project gets "not found" on its pages.

## Admin: once per project
1. Create the project on `<service>/projects` by pasting its **repository URL** (the `origin` everyone clones, for example
   `https://github.com/org/acme-web`). Leave slug and name empty: they are filled in from the repository (`acme-web`; or
   `org-acme-web` when that is taken). **One project per repository:** a second project for the same repository, in any
   URL form, is refused and names the existing one.
2. On the project's **Settings › Members** (`<service>/projects/<slug>/settings/members`), **invite** each teammate: their email and a role (see the table). Someone who
   already has an account is added at once. Someone new gets an account and you get a **one-time link**, shown only then:
   send it to them (chat or email). It works once and expires after 72 hours; they open it, give their name, set their own password and
   are signed in. The member waits under **Pending invites** until then; **New invite link** makes a fresh one (the old one stops) and
   **Cancel invite** takes it back.
   Owners can invite to their own project; admins to any. Forgotten password: an admin presses **Send reset link** on
   Accounts and sends that link the same way.
3. **No tokens to hand out.** Each teammate signs their own computer in from Claude (below) and gets a personal token
   that works only while they are a developer or above in the project. Project tokens (on **Settings › Machine tokens**, `<service>/projects/<slug>/settings/tokens`,
   **ingest** and **pull** scopes, shown once) are only for machines nobody signs in on, such as a build server. Someone
   leaving: remove them from the project, or on Accounts press **Revoke tokens** or disable the account.
4. Tell teammates the service address (and, for `/auto-runner:join`, the slug). That replaces sharing clone commands: `/auto-runner:join <slug>`
   reads the repository URL from the service.

## Teammate: the quick way
Open Claude Code in the project folder (or any folder, to clone) and run **`/auto-runner:central`**. It checks what is
missing and, with your yes at each step:
- connects this checkout (`/auto-runner:init --central-url ...`; the project is read from `origin`, no slug to type) or clones the project from the
  service (`/auto-runner:join <slug>`),
- in a folder that is not a checkout yet: signs you in, lists **your projects** and clones the one you pick (step 5 of
  0007: nobody shares clone commands any more); in a checkout: fetches and fast-forwards the base branch when that is safe,
- sets your runner identity (`AUTO_RUNNER_USER`, `AUTO_RUNNER_ACCOUNT`),
- **signs this computer in**: it shows an address and a short code (like `KQ4M-7RXD`); you open the address, sign in,
  check that it names your computer and repository, and press **Approve**. The token goes straight to your user
  environment (Windows) or a file only you can read (`~/.config/auto-runner/credentials`, macOS/Linux). Claude never
  sees it. Approve only a code you started yourself, just now.

On Windows restart Claude Code after signing in or setting a variable, then run `/auto-runner:central` again until it says
**CONNECTED (signed in as you)**. Your tokens are on **My tokens** on the site: one per computer, revoke any of them there;
one unused for 90 days stops working (sign in again).

## Teammate: by hand
1. **Get the code.** New machine: `/auto-runner:join <slug> --central-url <service>` (previews the folder, clones with your
   own git credentials, then shows init's preview). Existing checkout: `/auto-runner:init --central-url <service>` (the
   project is read from `origin`). Both check with the service that your token is that project's and that your `origin` is its
   repository, and write nothing until you agree.
2. **Your identity** (per person and machine, never in a file):
   `[Environment]::SetEnvironmentVariable('AUTO_RUNNER_USER', 'alok', 'User')` and
   `[Environment]::SetEnvironmentVariable('AUTO_RUNNER_ACCOUNT', 'bhawish', 'User')` in PowerShell
   (macOS/Linux: `export` lines in your shell profile). Run branches are then `alok/bhawish-claude-auto-runner/<type>/<brief>`.
3. **Sign in:** `python <plugin>/bin/autocentral.py login start --project .`, approve the code in your browser, then
   `python <plugin>/bin/autocentral.py login wait --project .`. (A machine nobody signs in on: a project token, stored with
   the hidden-prompt line init prints for the variable it names, default `AUTO_RUNNER_TOKEN_<SLUG>`.)
4. **Restart Claude Code**, then `/auto-runner:central`.

## Who the service thinks is running
The **person** is verified: your personal token says whose account the runner works for. The **username and Claude account**
are only what the runner reports (from `AUTO_RUNNER_USER` and `AUTO_RUNNER_ACCOUNT`), so treat them as unverified. They arrive
with sign-in, with run starts and with task claims (a runner named `alok/bhawish-claude-auto-runner`), and show on **Account**,
**Members**, **Runs** and the task page. Each identity also has a surface (`auto-runner`, `cli`, `desktop` or `browser`;
runners report `auto-runner`).

A run whose Claude CLI was signed in to a different account than `AUTO_RUNNER_ACCOUNT_EMAIL` names shows **Claude account
not confirmed** next to its Claude account on **Runs**. It only means the runner's own check said `mismatch` (it compares
emails on the runner's machine and sends neither); a match, an unset variable, an unreadable sign-in or an older runner
shows nothing. This is the runner's report and is unverified; the run is not stopped.

## Following progress: My work
**My work** (first link in the top bar) is the one page to check. Across all your projects (all projects for an admin;
the chips at the top narrow it to one project):
- **Waiting for you**: plans you may approve and blockers you can answer, as cards. A short plan (two items or fewer)
  has **Approve** right on its card; a longer one says **Read plan**. Blockers open their answer page.
- **In progress**: your tasks first ("1 of 3 items, on E2"), then every other task a runner is on, with a warning when
  a runner has gone quiet.
- **Recently done**, with pull requests.
Open a task for its full story: where it is (Open → Planned → Approval → Building → Done), the plan, each item's state,
the runs and an Activity list of who did what. Pages with work in progress reload every 30 seconds; the bell in the top
bar tells you when something waits on you (see Notifications).

## Notifications (push unverified on the live service)
- **What you get:** a notification when a plan waits for your approval, changes are asked on your task, a task is given
  to you, or a blocker in your project waits for an answer (managers and owners). Only members of the project get them.
- **The bell** in the top bar shows how many are waiting that you have not opened. **Notifications** lists yours,
  waiting ones first; open one to go to its task or blocker. Once the thing is decided or answered it moves to **Done**.
- **On your phone or desktop:** on **Account**, "Notifications on this browser" › **Turn on** (once per browser). It says
  "push is not set up on this service" until an admin sets the push keys.
- **Did it arrive?** Each notification says how it was delivered: *push sent*, *push failed: reason*, *push off* (push
  not set up, or no browser of yours turned on) or *in-app* (only on the page).
- **Admins:** **Accounts › Notification log** lists recent notifications for everyone, with recipient, project, kind,
  delivery and time; filter by person and kind to see why someone missed one.

## Tasks: what each person's runner does next
- On a project, **New task** (top right) adds a task for yourself (title, what and why, and optionally how we know it's
  done); admins can add one for anyone who can run runners. The **Tasks** tab sorts them into Needs approval, In progress,
  Open and Done, with "Mine only" and a search. Everyone in the project sees every task and who is working on what.
- Run **`/auto-runner:auto`** with no task. Your runner takes your next task: a new one gets a plan (the same planning and
  spec check as always), which then **waits for approval** on the task's page; meanwhile the runner takes your next one.
- On the task page you, a manager, an owner or an admin **Approve** the plan, **Request changes** (with a comment; the
  runner revises it on its next `/auto-runner:auto`) or **Reject** it (it asks first). Short plans can be approved from My work.
- Run `/auto-runner:auto` again: an approved task is built on its own branch and pull request, and shows **done** with the
  PR when the run finishes.
- **Close a task you no longer want** with **Close task…** on its page: give a reason (up to 500 characters) and confirm.
  The assignee, managers, owners and admins can do it while the task is open, being planned, waiting for changes,
  approved or queued. A task that is being built cannot be closed: stop its runner first. The task shows as rejected
  with your reason, and its Activity says who closed it. (Unverified until used on the live service.)
- A task you type in the terminal (`/auto-runner:auto <task>`) is approved there as before and recorded on the page too.
- **Screenshots** added to a task (on its page, or while adding it) reach your runner: `/auto-runner:auto` downloads them
  to `.auto/run/tasks/<N>/attachments/` when it takes the task and looks at them before planning or building. Each is
  downloaded once. Watch mode does not fetch them yet. (Unverified until used on the live service.)
- **Claims:** a task a runner works on shows its runner, so nobody else's runner takes it. A runner silent for 2 hours
  loses its claim. An admin can release a claim or give the task to someone else; the old runner stops that task at its
  next step.

## Hands-free work (watch mode; unverified on the live service)
Instead of running `/auto-runner:auto` again and again, one computer can keep taking your tasks on its own.
- **Start it:** `/auto-runner:central` offers it as its optional last step once the checkout is connected and signed in,
  or run **`/auto-runner:auto watch`** yourself. One watcher runs per checkout.
- **What it does:** it takes your next task, plans a new one (the plan **waits for approval** on the task's page, as
  always) and builds an approved one on its own branch and pull request, then takes the next. When nothing is waiting
  it checks again every `watch.poll_seconds` (default 300 seconds).
- **Limits:** each plan is capped at `watch.plan_budget_usd` (default 2 USD); everything it runs together at
  `watch.daily_budget_usd` per local day (default 20 USD), after which it pauses until midnight. At Claude's usage limit
  it waits for the reset.
- **Seeing it:** the project's **Overview** and **Runs**, and **Your runners** on **My work**, show each runner, what it
  is doing in words (*Watching for work*, *Planning*, *Building · task #3*, *Paused*, *Silent* after 2 hours without a
  beat), its last beat and today's spend.
- **Controlling it:** its own person or an admin presses **Pause** (it finishes the step it is on, then waits without
  taking work), **Resume** or **Stop** (it ends cleanly); each asks first and is recorded in Recent activity. On the
  computer itself, creating `.auto/STOP` also stops it (delete the file before starting it again).
- Nobody merges or deploys what it builds: its pull requests wait for a person, as every run's do.

## Answering a blocker
1. A run raises a blocker and stops working on that item; other ready items go on. `/auto-runner:central` and
   `/auto-runner:status` list it, and the page shows it at `<service>/projects/<slug>/blockers`.
2. An owner, manager or admin opens it (My work shows it as a card), reads the question and what it holds up, and picks
   an option (the recommended one is marked and chosen in advance) and/or writes instructions, then **Send answer**. An answer cannot be changed from the page afterwards.
3. The runner fetches answers between iterations and when it next starts, puts the item back in the queue and resumes it;
   the page then shows the blocker as RESUMED.

## Troubleshooting (`/auto-runner:central` says ...)

| Message | Fix |
|---|---|
| NOT CONNECTED | Connect this checkout with init, or clone with join (the command offers both). |
| OFF | `"central": {"enabled": true, ...}` in `.claude/auto.json`; or keep answers local on purpose. |
| Token: NOT SET | An owner or manager makes a token; you store it with the hidden-prompt line; restart Claude Code. |
| Service: REFUSED the token | Personal token: you are not a developer or above in the project (ask an owner to invite you), or it was revoked or unused for 90 days: sign in again. Project token: make a new one and store it again. |
| Not signed in: the sign-in was denied / the code expired | Run the sign-in again; approve within 10 minutes. |
| Service: NOT REACHABLE | Check the address and your connection; the service may be down. |
| the token belongs to project "x" | You stored another project's token in this project's variable: use this project's. |
| no repository URL on the central service | An owner or admin sets it on the project page. |
| this folder's origin ... is not project ...'s repository | Wrong folder or wrong slug: run it in a clone of that repository. |
| This folder is a git worktree of ... | Open Claude Code in the main checkout it names and run `/auto-runner:central` there. |
| no "origin" remote to read the project from | Clone the repository (or add its origin), or pass `--central-project`. |
| "This link no longer works" | It was used, expired (72 hours), replaced by a newer one, or the account is disabled: ask for a new link. |
| Runner identity: MISSING | Set `AUTO_RUNNER_USER` and `AUTO_RUNNER_ACCOUNT`, then restart Claude Code. |
| N not sent yet | Normal between runs: the next run sends them. If it stays high while connected, look at `.auto/central-push.json`. |
