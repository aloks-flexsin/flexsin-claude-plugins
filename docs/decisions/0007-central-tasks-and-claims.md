# 0007: One project per repository; tasks, plans and claims live on the central service

Status: accepted (2026-11-23) as the direction. **Nothing here is built yet**; each part lands in its own pull request
and records there what is tested.

## Context

The target team flow: an admin adds a project by its GitHub URL, invites people and assigns them to it; each person
installs the plugin, sets it up once, and runs the auto-runner. Every runner reports to the same project page, knows
what the others are working on, and can take its person's next task without a prompt. Admins add tasks for anyone,
users for themselves.

Today (0.15.0) the plugin connects a checkout to the central service, sends events and gets blocker answers back, but:
the admin types a slug by hand and two projects may name the same repository; passwords and project tokens are shared by
hand; events are not attributed to a person and no page shows progress; tasks live in each checkout's `.auto/queue.md`,
so runners cannot see each other's work and can do the same task twice.

## Decisions

1. **One project per repository.** The admin pastes the repository URL; the name and slug are filled in from it (the
   repository name, lower case, other characters as `-`; `<owner>-<repo>` when that slug is taken). The service refuses a
   second project for the same repository, comparing URLs as the plugin does (`autocentral.repo_key`: https, ssh and
   `user@host:` forms are equal). Teammates never type a slug: the plugin reads `origin`, and the service confirms it.
   A git worktree is pointed to its main checkout; a project is set up and run from the main checkout.
2. **Tasks live on the central service: the single source.** A runner keeps only a working copy (`.auto/queue.md`)
   while it works and reports every change back.
3. **Who adds tasks.** Users add tasks for themselves in projects they belong to; admins add tasks for anyone in any
   project. **Every member sees every task of the project** (read-only for other people's tasks).
4. **Claims.** A runner claims a task before it plans or builds it; a claimed task is skipped by every other runner and
   shows "in progress — <user>" with its branch. The runner keeps the claim alive with the events it already sends.
   A claim **expires after 2 hours without any sign of life** from its runner. **Admins can release or reassign a claim**
   from the page; it is audited, and the original runner, if it comes back, sees the claim is gone and stops that task.
5. **Plans are approved on the central page** for tasks taken from it. With `/auto-runner:auto` and no prompt the runner
   claims its person's next task, writes the plan (with the existing spec check) and posts it. The task page shows it as
   *waiting for approval* with **Approve**, **Request changes** (with a comment; the runner revises and posts again) and
   **Reject**. The answer reaches the runner through the existing decision delivery (`docs/delivery-contract.md`), as
   blocker answers do.
6. **Who approves a plan:** the person the task is assigned to, and the project's managers, owners and admins.
7. **While a plan waits** the runner keeps the claim (nobody else plans the same task) and moves on to its person's next
   task. Waiting for a person does not count towards the 2-hour expiry; only a silent runner does.
8. **Tasks typed in the terminal** (`/auto-runner:auto <task>`) keep today's fast path: the plan is approved in the
   terminal. The task and its plan are still recorded on the central service, assigned to that person.
9. **No shared secrets** (direction; its own decision record before it is built): an admin invites by email and role,
   the person sets their own password through a one-time link, and the plugin signs in from Claude with a short code
   approved in the browser, receiving a personal token it stores without printing. Events then carry who ran them.

## Build order

Each step is usable on its own and goes through its own branch and pull request.

1. Repository-derived projects (decision 1), including the worktree guard.
2. Attribution and a runs page: events carry user and account; the project page shows who is running what, with
   branch and pull request.
3. No shared secrets (decision 9).
4. Central tasks, claims and plan approval (decisions 2–8), and `/auto-runner:auto` without a prompt.
5. One-command setup: clone or sync, and "my projects", folded into `/auto-runner:central`.

## Consequences

- The central service becomes required for teams; a project in local mode keeps today's `.auto/queue.md` behaviour.
- Schema changes (unique repository, tasks, claims, plans) need a migration that a person applies to the live
  database; one that finds two projects for one repository lists them and stops instead of choosing.
- Until step 4 lands, two runners on one project are still not coordinated.
