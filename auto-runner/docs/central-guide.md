# Using the central service

The central service is a small website where an unattended run's **blockers** (questions the runner cannot decide alone)
show up and are answered away from the terminal. The runner picks the answer up and carries on with that item.

**Status: unverified against the live service.** Every step below is tested only against the real app on SQLite through
its test client. How the service itself is deployed is in `auto-runner/central/README.md`.

What the page shows today: your projects, each project's blockers (question, options, recommendation, evidence, the
answers so far), its members and its tokens. Each run also sends its events (start, end, item changes, test results,
publish) and the service stores them, but **there is no runs page yet**, so progress is still read with
`/auto-runner:status` and `/auto-runner:log`.

## Who can do what

| Role | See blockers and members | Answer blockers | Tokens | Members and repository URL | Create projects and accounts |
|---|---|---|---|---|---|
| viewer, developer | yes | no | no | no | no |
| manager | yes | yes | yes | no | no |
| owner | yes | yes | yes | yes | no |
| admin (site-wide) | every project | yes | yes | yes | yes |

There is no sign-up: an admin creates each account. Someone who is not a member of a project gets "not found" on its pages.

## Admin: once per project
1. Create the project on `<service>/projects` with its **slug** (the short name, for example `acme-web`) and its
   **repository URL** (the `origin` everyone clones, for example `https://github.com/org/acme-web`).
2. On `<service>/projects/<slug>/members`, add each teammate with a role (see the table).
3. On `<service>/projects/<slug>/tokens`, an owner or manager creates a token with the **ingest** and **pull** scopes. It
   is shown once. Each teammate who runs the runner stores it in their own environment (step 3 below); never send it
   in chat or commit it. Several people may share one project token; revoke and rotate it there when someone leaves.
4. Tell teammates the service address and the slug. That replaces sharing clone commands: `/auto-runner:join <slug>`
   reads the repository URL from the service.

## Teammate: the quick way
Open Claude Code in the project folder (or any folder, to clone) and run **`/auto-runner:central`**. It checks what is
missing and, with your yes at each step:
- connects this checkout (`/auto-runner:init --central-url ... --central-project <slug>`) or clones the project from the
  service (`/auto-runner:join <slug>`),
- sets your runner identity (`AUTO_RUNNER_USER`, `AUTO_RUNNER_ACCOUNT`),
- tells you the one step you always do yourself: storing the token with a hidden prompt. Claude never sees the token.

Restart Claude Code after setting a variable, then run `/auto-runner:central` again until it says **CONNECTED**.

## Teammate: by hand
1. **Get the code.** New machine: `/auto-runner:join <slug> --central-url <service>` (previews the folder, clones with your
   own git credentials, then shows init's preview). Existing checkout: `/auto-runner:init --central-url <service>
   --central-project <slug>`. Both check with the service that your token is that project's and that your `origin` is its
   repository, and write nothing until you agree.
2. **Your identity** (per person and machine, never in a file):
   `[Environment]::SetEnvironmentVariable('AUTO_RUNNER_USER', 'alok', 'User')` and
   `[Environment]::SetEnvironmentVariable('AUTO_RUNNER_ACCOUNT', 'bhawish', 'User')` in PowerShell
   (macOS/Linux: `export` lines in your shell profile). Run branches are then `alok/bhawish-claude-auto-runner/<type>/<brief>`.
3. **The token**, with the hidden-prompt line init prints for the variable it names (default `AUTO_RUNNER_TOKEN_<SLUG>`):
   `$t = Read-Host 'Token for <slug>' -AsSecureString; [Environment]::SetEnvironmentVariable('<VARIABLE>', [System.Net.NetworkCredential]::new('', $t).Password, 'User')`
4. **Restart Claude Code**, then `/auto-runner:central`.

## Answering a blocker
1. A run raises a blocker and stops working on that item; other ready items go on. `/auto-runner:central` and
   `/auto-runner:status` list it, and the page shows it at `<service>/projects/<slug>/blockers`.
2. An owner, manager or admin opens it, reads the question, options and recommendation, and picks an option and/or writes
   an answer. An answer cannot be changed from the page afterwards.
3. The runner fetches answers between iterations and when it next starts, puts the item back in the queue and resumes it;
   the page then shows the blocker as RESUMED.

## Troubleshooting (`/auto-runner:central` says ...)

| Message | Fix |
|---|---|
| NOT CONNECTED | Connect this checkout with init, or clone with join (the command offers both). |
| OFF | `"central": {"enabled": true, ...}` in `.claude/auto.json`; or keep answers local on purpose. |
| Token: NOT SET | An owner or manager makes a token; you store it with the hidden-prompt line; restart Claude Code. |
| Service: REFUSED the token | The token was revoked or mistyped: make a new one and store it again. |
| Service: NOT REACHABLE | Check the address and your connection; the service may be down. |
| the token belongs to project "x" | You stored another project's token in this project's variable: use this project's. |
| no repository URL on the central service | An owner or admin sets it on the project page. |
| this folder's origin ... is not project ...'s repository | Wrong folder or wrong slug: run it in a clone of that repository. |
| Runner identity: MISSING | Set `AUTO_RUNNER_USER` and `AUTO_RUNNER_ACCOUNT`, then restart Claude Code. |
| N not sent yet | Normal between runs: the next run sends them. If it stays high while connected, look at `.auto/central-push.json`. |
