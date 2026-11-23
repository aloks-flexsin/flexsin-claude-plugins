# 0006: The runner follows the team git rules

Status: accepted (2026-11-23). Tested with local bare remotes, a fake `gh` and a fake `claude`; never with a real model,
real GitHub pull requests or two runners on one project. **Unverified** beyond that.

## Context

The team works by one convention for every Claude member: branches are `<user>/<account>-claude-<surface>/<type>/<brief>`
(the real person working, the Claude account they use, where it runs), each new request gets a new branch from the
freshly fetched base, and every change is pushed and opened as a pull request into the base. Before 0.14.0 the runner did
none of this: branches were `auto/<stamp>`, it started from the local base without fetching, one worktree branch absorbed
every request, and publishing was off. Several people's runners on one project could not be told apart, could build on old
code, and produced one pull request covering several requests. Its agent was kept from pushing only by the project's
`.claude/settings.json`, which also stopped the people working in that project from pushing their own branches.

## Decision

- **Who runs it comes from the environment.** `AUTO_RUNNER_USER` and `AUTO_RUNNER_ACCOUNT` are set per person or machine;
  a committed `auto.json` is shared by everyone and cannot hold them. The preflight refuses a run without them, the
  runner's form of "ask your name when unsure". Run branches are `<user>/<account>-claude-auto-runner/<type>/<brief>`.
  `branch_prefix` is kept as an accepted but ignored key so old files still load.
- **Fetch first.** Each run fetches `<remote>/<base>` and starts from it; a failed fetch stops the run rather than
  building on old code. No remote, or a remote without the base yet, falls back to the local base with a notice.
- **One request per branch, strictly.** A new branch takes only the oldest pending request. While the worktree's branch
  has commits not in the base, later requests wait and are listed; once it is merged, the next run starts a new branch.
  Runs on one checkout become sequential, which is the price of one reviewable pull request per request. Running requests
  side by side (a worktree each) and claiming requests across machines are later work.
- **The runner blocks push for its agents itself** with `--disallowedTools Bash(git push:*) PowerShell(git push:*)`, so
  the project's settings no longer need a `git push` rule and people can push their own branches.
- **Publishing is on by default** (push and pull request), since that is the team flow; `false` turns each off. Left at the
  default, a repository with no remote is simply not published.

## Consequences and known limits

- Existing projects must set the two variables after updating, or every run is refused with a message saying how.
- "Merged" is judged by ancestry. A squash-merged pull request still looks unmerged, so the worktree must be removed after
  a squash merge.
- A second clean run on an unmerged branch pushes again; `gh` reports that the pull request exists (recorded, harmless).
- The branch-name check in `gh`/git and two runners picking the same request at the same moment are not coordinated; the
  name suffix (`-2`) only sees branches already known locally or fetched.
