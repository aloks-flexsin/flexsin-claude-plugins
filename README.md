# Flexsin Claude plugins

A private Claude Code plugin marketplace for Flexsin. It currently holds one plugin:

| Plugin | What it does | Status |
|---|---|---|
| `auto-runner` | Plan a task with `/auto`, queue it, and let Claude work through the queue unattended in a separate git worktree: one item per iteration, an independent test run after each, one commit per green item. Nothing is pushed, merged or deployed. | **Unverified.** Built and tested with a fake `claude`; never run on a real second project. Pilot use only. |

## Install

You need Claude Code, `git` and Python 3 on your PATH. The project you automate must be a git repository.

```
claude plugin marketplace add aloks-flexsin/flexsin-claude-plugins
claude plugin install auto-runner@flexsin-claude-plugins
```

This is a private repository, so `git` must be signed in to GitHub with an account that has access (see "Access" below).

Try it for one session without installing, from a clone of this repository:

```
claude --plugin-dir ./auto-runner
```

Check the plugin manifest (needs the `claude` CLI on PATH):

```
claude plugin validate ./auto-runner
```

## First use in a project

1. `/auto-runner:init` shows a preview of the files and deny rules it would add. Nothing is written until you approve.
2. `/auto-runner:setup` shows what browser-testing support it would install and asks once. `/auto-runner:doctor` re-checks it at any time.
3. `/auto-runner:auto <task>` plans first. When you accept the plan it queues the items and starts the runner.
4. `/auto-runner:status` and `/auto-runner:log` are read-only reports.

Read `auto-runner/README.md` before the first run: it lists exactly what the runner may and may never do, the config keys, and what is still unverified.

## Update

```
claude plugin marketplace update flexsin-claude-plugins
```

## Access

The repository is private. Each teammate needs a GitHub account with read access to it, and `git` on their machine signed in to that account (Git Credential Manager does this in a browser the first time).

## Contributing

Change the plugin in `auto-runner/`, bump `version` in `auto-runner/.claude-plugin/plugin.json`, and open a pull request. Never commit secrets, `.env` files or client data.
