---
description: Set up or check everything auto-runner needs here: sign-in, config, identity, sandbox, CI, deploy (preview first)
argument-hint: [check] | [<slug> to clone a project]
---

One guided setup (decision record 0012). It replaces `init`, `setup` (capabilities), `central`, `join` and `doctor`;
each part previews first and changes nothing until the user agrees. Arguments: **$ARGUMENTS**

**`check`**: read-only. Run each check below, change nothing, and finish with one list: what is ready, what is missing
and the command that fixes it.
**A project slug** (for example `acme-web`): clone that project and set it up: read
`${CLAUDE_PLUGIN_ROOT}/commands/join.md` and follow it with these arguments, then continue at step 3.
**Nothing**: go through the steps in order. Skip a step that is already done, and say so in one line.

1. **Sign-in and the central service.** Read `${CLAUDE_PLUGIN_ROOT}/commands/central.md` and follow it (status check,
   device sign-in, connect this project). Stop before its optional watch-mode step: `/auto-runner:start` does that.
   With `check`: run only `python "${CLAUDE_PLUGIN_ROOT}/bin/autocentral.py" status --project "${CLAUDE_PROJECT_DIR}"`.
2. **This project's config.** Read `${CLAUDE_PLUGIN_ROOT}/commands/init.md` and follow it. Its preview includes the deny
   rules, `.claude/auto.json` (a new one turns the changelog on), the queue and, when the project has no CI workflow,
   `.github/workflows/tests.yml`. With `check`: run the preview only, never `--apply`.
3. **Identity.** `AUTO_RUNNER_USER` (the person) and `AUTO_RUNNER_ACCOUNT` (the Claude account) must be set; branches and
   commits use them. If one is missing, say which and how to set it for this shell; do not guess a value.
4. **Capabilities** (browser acceptance, changelog, doc checks): run
   `python "${CLAUDE_PLUGIN_ROOT}/bin/autosetup.py" doctor --project "${CLAUDE_PROJECT_DIR}"`. If something is missing
   and this is not `check`, read `${CLAUDE_PLUGIN_ROOT}/docs/steps/capabilities.md` and follow it.
5. **Sandbox.** Run `python "${CLAUDE_PLUGIN_ROOT}/bin/auto_run.py" --project "${CLAUDE_PROJECT_DIR}" --dry-run` and
   report its sandbox line. Item agents run in Claude Code's sandbox where it works (macOS, Linux, WSL2); on native
   Windows say that WSL is needed for it.
6. **Deploy.** Say whether `.claude/auto.json` has a `deploy` section. Without one, nothing is deployed after a merge;
   offer to add `command`, `smoke_url` and `rollback_command` (never a secret: those come from the environment) and add
   them only after a clear yes, on a branch, like any change.
7. Finish with the next step: `/auto-runner:start` to work hands-free, or `/auto-runner:add <what>` to add a task.

Never print or ask for a token, and never remove a deny rule.
