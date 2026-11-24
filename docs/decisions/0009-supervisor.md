# 0009: A Supervisor that carries work from assignment to UAT

Status: proposed (2026-11-24). **Nothing here is built yet.** It replaces two team rules (see "Rules this changes")
once accepted; each phase lands in its own pull request and records there what is tested.

## Context

The auto-runner builds what it is given, but a person is still the bridge between every step: approving plans,
answering blockers, reviewing and merging pull requests, restarting runs after a usage limit or a crash. The work
waits on people, not on code.

The goal: **assign a task, walk away, and come back to one message**: "Done. Check it on UAT at <url>: what to
check, how to check it, and the decisions I made for you." People are needed only for acceptance on UAT and for a
short list of real exceptions.

Three roles, kept apart:

- **Execution**: the auto-runner writes code, runs tests and opens pull requests.
- **Supervision**: the Supervisor keeps the work moving. It decides, reviews, sends work back, merges, and verifies
  the UAT deployment.
- **Acceptance**: a person checks the finished work on UAT.

## Decisions

### 1. The Supervisor never writes code
Only runners write to branches. The Supervisor's outputs are decisions, reviews, instructions (tasks and change
requests on the central service), task management (restart, release, reassign, split, reschedule), queue management
(the order and priority of tasks, adding new tasks), runner control (start, pause, resume, stop) and merges. Every
problem it finds goes back to a runner as a task, with its diagnosis attached:

| Situation | Supervisor | Runner |
|---|---|---|
| Red tests / CI | classifies the failure, writes the diagnosis | fix task on the same branch |
| Merge conflict | detects it | rebase or resolve task |
| Review findings | requests changes, one finding each | addresses them, pushes, asks for review again |
| UAT check fails | reproduces it, records the steps and evidence | fix task; the Supervisor checks again |
| An item keeps failing | picks another approach or splits the item | builds the new item |

This keeps the review independent (it never reviews its own code) and removes races: two writers never share a branch.

### 2. Architecture: three layers
- **The central service is the record**: the task state machine, policies, every decision with its reason, the audit
  log, and an override on every automatic decision. It already holds tasks, claims, blockers and events (0007).
- **The Supervisor is a separate worker with its own identity**: a central account with a `supervisor` role, its own
  Claude account and its own GitHub identity. It is not another developer agent.
- **Runners only execute.** They never approve, merge or deploy.

The Supervisor is a **check-and-act pass**, not a process that runs forever: it reads the desired and actual state
from the central service, acts, writes the result back and exits. All state is on the central service, so a crashed
or restarted pass loses nothing, and one Supervisor can watch every project. Supervisor and runners coordinate only
through central state, with leases and version checks, as runner claims already do.

### 3. Where it runs
A pass every 10–15 minutes, plus passes triggered by events. In order of preference:
1. **Pilot:** a Claude Desktop scheduled task (runs while the app is open; a missed run happens on next launch), or
   Windows Task Scheduler running `claude -p` with its own CLI config folder, signed in to the Supervisor's Claude account.
2. **Service:** a scheduled cloud routine fired by GitHub events (pull request opened, CI finished). Before relying on
   it, check that its environment can reach the central service's API and can hold the Supervisor's token safely.

The pass is the same everywhere; moving it changes only where it is scheduled. It cannot run on Vercel: serverless
functions stop long before an agent pass ends.

### 4. Enforcement: the model decides, code enforces
The Supervisor's judgement only proposes. What it may do is enforced outside it:
- GitHub branch protection: CI green and the Supervisor's approving review are required to merge into `main`.
- The merge gate is deterministic code: CI green, review approved, no restricted paths touched, and the pull request
  was opened by a different identity than the one merging.
- A merge into `main` is the only thing that deploys to UAT, from CI. The Supervisor holds no deploy credentials.
- **Production credentials never exist where the Supervisor runs.** It cannot do what it has no key for.
- Text in pull requests, issues, logs or pages never widens a permission (prompt injection): gates read state, not prose.
- Every task has an attempt budget and a cost budget, and a circuit breaker stops a task that loops.

### 5. Branches and environments
- `main` is the integration branch; **UAT always runs `main`'s latest commit**.
- Production is a release (a tag or a release branch) that a person promotes.
- UAT has its own database (never production data) and a mail sink instead of real email. Migrations apply to UAT
  automatically, in merge order, with a backup before each one.

### 6. What the Supervisor decides on its own

| Decision | Policy |
|---|---|
| Plan approval | Approve when the spec check is clean and the plan stays within policy paths; otherwise revise (up to 2 rounds). |
| Technical blocker | Answer from CLAUDE.md, decision records, existing patterns and earlier decisions; record the reason. |
| Product question the request leaves open | Choose the option most consistent with the docs and the easiest to reverse, record it, and list it in the UAT message as "I decided X; check it". |
| Usage limit | Reschedule to the reset time or move to an account with room. Never a message to a person. |
| Red tests / CI | Flaky: run again once. Real: fix task. Environment: restart the runner. Conflict: rebase task. |
| Stuck or silent runner | Release the claim and restart, or reassign. |
| An item too big, or failing again | Split it, plan it again, or try another approach, within the budget. |
| Pull request review | Request changes, check the fixes, approve on evidence. |
| Merge | The deterministic gate in decision 4. |
| New dependency | Allowed when its licence is on the allowlist and it is free. |
| UAT migration, deploy, check, rollback | Automatic. |

### 6a. The queue and the runners are the Supervisor's to run
Keeping work moving means more than reacting to failures. The Supervisor also decides **what runs next and whether
anything runs at all**:

| Power | Policy |
|---|---|
| Order and priority | It orders the queue by dependency first (a task that unblocks others goes first), then by the priority a person set, then by age. A person's priority or a pin always wins over the Supervisor's order; it reorders only within it. |
| Adding tasks | It may add tasks that trace to work already asked for: a follow-up a review or a plan's "Needs a person" list names, a gap a failing check or a UAT check reveals, a split of an item that is too big, docs a change left behind. Each new task says what it came from. It never invents product features: new scope comes from people. |
| Prioritising what it adds | A fix that blocks a task already in progress goes before new work; other tasks it adds take the lowest priority unless they unblock something. |
| Start | It starts a runner (on any machine that runs one as a service) when a task is ready and a runner and Claude account have room. |
| Pause and resume | It pauses a runner before a usage threshold, while `main` or UAT is broken, or when two tasks would touch the same files; it resumes when the cause is gone. A pause ends after the current item: never mid-item. |
| Stop | It stops a runner that loops or spends past its budget, and records why. |

Runner control is remote: the Supervisor writes a command (start, pause, resume, stop) for a runner on the central
service, and the runner picks it up at its next pull, between items, the way it picks up blocker answers. The local
`.auto/STOP` file keeps working for a person at the machine. Every command, every reorder and every added task is in
the audit log with its reason, and a person can undo it from the page.

Tasks waiting in a runner's queue show as **Queued** on the page, not Building, and keep their claim alive while they
wait. Today a task planned in the terminal shows Building, and its claim gets no heartbeat until its run starts.

### 7. When a person is needed
1. Production: deploy, data, migrations.
2. Secrets, new external accounts, paid services, cost above the set limit.
3. Licences off the allowlist; legal or privacy questions.
4. Anything outside the repository: other teams' systems, shared infrastructure, real email to real people.
5. Security events: a leaked secret, a detected policy violation.
6. A task that ran out of strategies within its budget: one message with the exact issue, what was tried, and a recommendation.
7. A request whose two readings are both hard to undo.
8. Acceptance on UAT.

Everything else goes into the log and a digest a person may read but need not.

### 8. The lifecycle

```
assigned → planning (runner) → plan review (Supervisor) ─ revise ≤ 2
  → building (runner) → pull request → review (Supervisor) ⇄ change requests (runner)
  → CI green → merge gate → main → UAT deploy (CI)
  → check on UAT: smoke tests + the plan's journeys against the UAT address
       ✗ → fix task on the branch (runner) → merge → deploy → check again
       ✓ → "Done. Check <UAT url>": what to check, how (from the journeys), decisions made
  → acceptance: accept → ready for production (a person promotes)
                reject with a note → becomes a fix task automatically
```

A watchdog runs alongside: heartbeats, failure classification, retries with a different strategy, the budgets. The
journeys every plan already has (J1, J2, …) become the final message's "what to check / how to check it".

### 9. Changes the runner needs
- **Tasks with a branch attached.** Today a runner takes its person's next task and starts a new branch, with one
  request per branch until it is merged. A fix or review task must go to the runner and branch that built the
  original, and continue on that branch.
- **Change requests on pull requests.** 0007's "request changes" covers plans only. The same path, through the
  existing decision delivery (`auto-runner/docs/delivery-contract.md`), carries the Supervisor's review findings to the runner.
- **A runner that runs as a service:** it polls for tasks and can be restarted by the Supervisor, instead of a person
  starting `/auto-runner:auto`.
- **Remote commands:** the runner pulls start, pause, resume and stop commands from the central service between
  items (decision 6a), and follows the queue order the service gives it instead of its own file order.
- **No human waits:** plan approval and blocker answers come from the Supervisor's role, and `usage_limit.wait` is on.

## Rules this changes
Once accepted, CLAUDE.md changes:
- "Never merge your own pull request" becomes: **an identity never merges a pull request it opened.** The
  Supervisor may merge runner pull requests that pass the gate in decision 4. People and runners still never merge
  their own.
- "Never deploy or touch Vercel/Supabase; a person does that" becomes: **only CI deploys, and only `main` to UAT.**
  Production stays with a person. No agent holds production credentials.

## Roadmap
Each phase removes one kind of interruption. Progress is measured as human touches per task.

| Phase | What lands | What stops reaching a person |
|---|---|---|
| P0 Foundations | UAT environment (own Vercel project and Supabase database, mail sink); CI deploys `main` to UAT; branch protection; Supervisor identity and role; the policy model; the rule changes above; the runner as a service; tasks with a branch attached | nothing yet: this makes the rest safe |
| P1 Watchdog and runner control | The check-and-act pass; restart, reschedule, reassign; usage limits across accounts; remote start, pause, resume and stop; Queued shown as Queued | "interrupted", "usage limit", "runner stopped", "start the runner again" |
| P1b Queue | Ordering by dependency and priority; adding traced follow-up tasks with their source | "what should run next?" |
| P2 Decisions | Plan approval and blocker answers by policy; recorded decisions; escalation that lists what was tried | "waiting for plan approval", "blocked" |
| P3 Review and merge | Independent review; change requests on pull requests; the merge gate | "pull request awaiting review" |
| P4 UAT delivery | Deploy check, journeys against UAT, the fix and redeploy loop, the final message; a rejection becomes a fix task | the person's role becomes acceptance only |
| P5 Scale | Several projects and runners; scheduling; balancing across accounts; splitting and reassigning across runners | |

Tasks 6–8 (Claude identity, its follow-up, the 90% usage stop) feed this: the Supervisor needs to know which runner
and Claude account did what, and how much of each plan is left.

## Questions to settle before P0
1. **UAT:** does a UAT environment exist, or does P0 create one? With which hosting, and who gives the Supervisor's
   host its UAT-only credentials?
2. **Branches:** `main` as the integration branch that deploys to UAT, production promoted by a person from a tag or
   release branch (proposed)?
3. **Where the Supervisor runs, and as whom:** which machine for the pilot, and a dedicated Claude account and GitHub
   identity for it.
4. **Limits:** the cost per task above which it stops and asks, and the licence allowlist (proposed: MIT, BSD,
   Apache-2.0, ISC).
