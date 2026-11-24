# 0009: Hands-free work: routing, the headless planner, the watch loop and runner presence

Status: **accepted** (2026-11-24). Builds on 0007 (central tasks and claims) and 0008 (no shared secrets).
Everything here is **unverified** until it runs on the live service.

## Context

Today a person still drives every task: they open Claude, type a request, run `/auto-runner:auto` to plan it, approve
the plan and start the runner. Work typed straight into a Claude session also bypasses the queue, the worktree, the
tests and the event ledger. The intent is that a project's work goes through the runner by default and that a runner
can pick up, plan and build approved work on its own, while a person keeps control over what gets built and can stop it.

## Decision

The work is split into two layers, each usable on its own.

### Layer 1: route work through the runner (prompt routing and an opt-in guard)
- `auto_init.py` writes a short "Work goes through the auto-runner" rule into `CLAUDE.md` (or `AUTO_RULES.md`) for
  projects with `central.enabled`, naming `autotasks.py propose`. It is shown in the preview, recorded in the manifest
  and removed by `--remove`; the rest of the file stays byte-for-byte unchanged.
- A `UserPromptSubmit` hook (`autohook.py prompt`) reminds the person, in one line, to plan with `/auto-runner:auto` and
  send the task with `autotasks.py propose`. It says nothing for other projects, slash commands or a broken config, and
  always exits 0.
- `autotasks.py propose` posts the task with its plan and items to the central service as `plan_review`, so it waits for
  a person's approval on the central page instead of starting to build.
- An **opt-in** `PreToolUse` guard (`autohook.py guard`, `"enforce_runner": true`, default off) blocks edits in the main
  checkout and points at `/auto-runner:auto`. `.auto/`, runner worktrees and paths outside the project stay allowed.
  Typing `/auto-runner:allow-edits` opens an escape for that session only, for at most 12 hours.

### Layer 2: run without a person (planner, watch loop, presence)
- **The planner** (`autoplan.py plan N`) plans a claimed task headlessly with `claude -p` and `models.planner`, from the
  task text, any sent-back comment and `auto-runner/docs/planner-prompt.md`. It runs the spec check with at most one
  revision, stops at `watch.plan_budget_usd`, and posts the plan for approval. Findings left after the revision are
  listed under **Needs a person**, for the person approving on the page. If the cost cap is hit it posts nothing, lets
  the claim lapse and records the reason in `.auto/run/watch.json`.
- **The watch loop** (`autowatch.py`, started by `/auto-runner:auto watch`) repeats: claim the next task, plan it or
  build it (`autotasks.start` then `auto_run.run`), report. When idle it writes a heartbeat to `.auto/run/watch.json`
  and sleeps `watch.poll_seconds`. It stops on `.auto/STOP`; one failed task never ends the loop. Every `claude -p` it
  starts counts towards a daily spend file; at `watch.daily_budget_usd` it pauses until local midnight. It reuses the
  usage-limit wait of `auto_run` and the checkout lock, so one build runs per checkout.
- **Presence**: the runner sends `POST /api/v1/runners/beat` (personal token; label, state, task, spend) each step and
  when idle. The service shows runners on Overview, Runs and My work, marks one silent after 2 hours, and returns
  pause or stop, which the loop obeys between steps (pause waits without claiming, stop ends cleanly). Pause, Resume
  and Stop are confirmed and audited.

## Decisions

- **Approval: on the central page.** A planned task waits as `plan_review` until a person approves it on the central
  service; nothing is built from an unapproved plan.
- **Guard: opt-in per project.** The edit guard is off unless the project sets `"enforce_runner": true`.
- **Daily cap: 20 USD per runner.** `watch.daily_budget_usd` defaults to 20; planning, spec checks and builds all count.
- **Poll: 5 minutes.** `watch.poll_seconds` defaults to 300.
- **Pause: the runner's person and admins.** Only they see and can use Pause, Resume and Stop for a runner.
- **Planner: opus, may plan without a person.** `models.planner` is opus, and the watch loop may plan a claimed task
  with no person present; the plan still needs approval on the page before it is built.

## Consequences

- New config: `enforce_runner` and a `watch` section (`poll_seconds`, `plan_budget_usd`, `daily_budget_usd`).
- New service table `runner` and the `runners/beat` endpoint; the task API itself does not change apart from
  `"approval": "page"` on `POST /api/v1/tasks`.
- A runner can spend up to the daily cap without a person watching; the cap, the per-plan budget, presence and the
  Pause/Stop controls are the safeguards.
- Build order: Layer 1 first (routing reminder, then the opt-in guard), then the headless planner, the watch loop,
  presence and controls, and finally setup and documentation.
