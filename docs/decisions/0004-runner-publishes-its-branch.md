# 0004: The runner publishes its own branch; no release agent

Status: accepted (2026-11-02). Tested with a local bare remote and a fake `gh`; never against GitHub.

## Context

Unattended runs end with commits on a local branch, and a person has to push it before anyone else can see the work. The
agent is denied `git push` and `git remote` because it is a model working without supervision: it can be wrong, or be
steered by text it reads. A dedicated "release agent" was considered, and rejected, because it would be a second model
holding the same rights.

## Decision

- **The runner pushes, not a model.** `auto_run.publish` is plain code that runs after a run ends cleanly (exit 0; every
  commit passed the runner's own test run). It uses the person's git credentials and is off unless
  `"publish": {"push": true}`.
- **Only its own branch.** It pushes an explicit refspec for the run branch. The branch must start with `branch_prefix` and
  must not match `lanes.protected`, and it is never the base branch. There is no force push, and `GIT_TERMINAL_PROMPT=0`
  makes an unattended push fail instead of hanging. Preflight refuses settings where a run branch could be the base branch
  or a protected one, and a remote that does not exist.
- **A pull request, not a merge.** With `pull_request`, `gh pr create` opens a PR whose body lists the commits. Merging,
  tagging and deploying stay with a person, so branch protection and CI on the host still apply.
- **Reported, never fatal.** A `publish` event records the outcome (also accepted by the central service, as a run-level
  event). A failed push or PR leaves the work on the local branch, as before.

## Consequences and known limits

- `gh` must be installed and signed in for pull requests. Without it the push still happens and the event says why there
  is no PR.
- Pushing makes the branch visible to everyone with access to the remote, including any secrets a commit might hold. The
  runner's secret scan still runs on every iteration before this point.
- Release tagging stays manual. A later step could let a person approve a tag on the central service, for the runner to
  apply (the same blocker and answer flow, not a new agent).
