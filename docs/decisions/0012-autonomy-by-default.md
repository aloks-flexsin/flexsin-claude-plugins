# 0012: Autonomy by default, manual mode on request

Status: **accepted** (2026-11-26, decided with Alok). Amends 0009 (the "Approval: on the central page" decision) and
0010 sections 4, 6 and 7. The work is described phase by phase in `docs/plans/autonomy-by-default.md`; this record
keeps the decisions and the rules that follow from them.

## Context

Auto-runner was built to keep a person in the loop: plans waited for approval, "Needs a person" escalated, tasks the
Supervisor added waited for a person, the merge gate refused a pull request opened by the identity that merges it,
CLAUDE.md forbade merging and deploying, only admins reassigned, a runner took only its own person's tasks, and a run
pushed only at the end. With 30+ such gates, work stopped whenever nobody was watching. Alok wants work to go from
request to merged and deployed with no person in the loop, and a person to step in only when they ask for it.

## Decisions

1. **One switch, `autonomy`, default `auto`.** It exists in two places and both must say auto for a task to be decided
   without a person:
   - the repository: `"autonomy": "auto" | "manual"` in `.claude/auto.json` (read from the base branch; unreadable or
     unknown means manual);
   - the central project: Settings › Repository, owners and admins, audited (`project.autonomy`).
   A person can also mark a single task **manual** on its page (`task.manual`). `manual` anywhere restores the earlier
   behaviour exactly; the tests that pin that behaviour run in manual mode.
2. **Gates decide instead of escalating** (auto mode). Open "Needs a person" points become reasons recorded with the
   decision; after two rounds of sent-back changes the Supervisor approves the latest plan with its reasons; it decides
   plans of tasks it added; it needs no manager membership to decide. This amends 0009's "Approval: on the central
   page" and 0010 section 6 ("Plan approval").
3. **Self-merge is allowed** (auto mode, `merge.allow_self_merge`, default true in auto). Every change still lands on
   the base branch only through a pull request whose required checks are green, and restricted paths still refuse.
   This amends 0010 section 4 (the merge identity condition).
4. **Fully automatic deploys.** After a merge the Supervisor deploys the new base head with the project's
   `deploy.command`, checks `deploy.smoke_url`, and on failure runs `deploy.rollback_command` and adds a fix task to
   the pool; every deploy is recorded (`supervisor.deploy.*`). A project deploys nothing until it sets a command. This
   amends 0010 section 4 ("the Supervisor holds no deploy credentials") and section 7 item 1 (production deploys no
   longer need a person; production data and destructive migrations still do).
5. **Shared task pool.** A runner may take another person's task when that person's watcher is not active or the claim
   expired (`task.take`); assignees, managers and the Supervisor release and reassign.
6. **Runners push as they go**, so stopped work is visible and any runner can continue the branch.
7. **Item agents run in Claude Code's sandbox** where it works; the shell allowlist remains only where it does not.

## Kept on purpose (security, not process)

- Secrets are never read or committed (`.env*`, keys, `.auto/run/accounts.json` deny rules); tokens come from the
  environment only and are never printed.
- No force-push and no direct push to the base branch: everything lands through a pull request with green CI.
- Restricted paths: the Supervisor never merges a change to its own policy, the settings deny rules, the CI workflows
  or deploy configuration such as `vercel.json` (this repository's `.claude/supervisor-policy.json`).
- A person is still needed for: production data and destructive migrations, new secrets or paid accounts, licences off
  the allowlist, anything outside the repository, security events, and a task that ran out of strategies.

## Consequences

- `CLAUDE.md` rules change: a pull request may be merged by the Supervisor or by the session that opened it once its
  `tests` check is green; deploys run only through the Supervisor's deploy step (never by hand from a session); the
  session identity comes from the environment and a session asks only when a part is missing.
- New config: `autonomy`, `deploy`, `release`, `sandbox`; new central columns `project.autonomy`, `project_task.manual`
  (`python -m central init-db`).
- The Supervise stage runs under each person's own sign-in (Phase 8): the supervisor API accepts a developer-or-above
  member's personal token, audited as the stage; the supervisor account stays only as a back-compat alias, and the
  plugin needs no supervisor settings (the `AUTO_SUPERVISOR_*` variables only override).
- Manual mode is the way back: switching a project or a task to manual brings back every person-in-the-loop step.
