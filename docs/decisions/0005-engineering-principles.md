# 0005: Engineering principles for unattended work

Status: accepted (2026-11-03). Tested with a fake agent; no real model has worked under them yet.

## Context

The plugin had process rules (plan first, one small tested item per iteration, blockers instead of guesses) but no
engineering ones. "Small enough to finish in one sitting" even pushed plans toward horizontal splits (tables, then
endpoints, then screens) that leave nothing usable until the end. Nothing asked the agent to reuse the project's components
or open-source packages, so it was free to write its own.

## Decision

- **One principles file ships with the plugin** (`auto-runner/docs/engineering-principles.md`) and applies by default to
  every project that installs it: vertical slices, each one usable on its own; and the project's own components and
  established, permissively licensed open-source libraries before custom code.
- **Planning** (`/auto-runner:auto`) reads the file, lists the slices first, and gives every queue item a `*Slice:*` line.
- **Enforced where it can be:** the runner refuses a request with an item that has no `*Slice:*` line, or whose slices go
  backwards. "Uses a library" cannot be checked mechanically, so it stays guidance, in the plan and in every prompt.
- **In every prompt:** the runner puts the file's text into each iteration's prompt instead of pointing at the path. The
  agent may not be allowed to read files outside the worktree, and the text is short. The project's rules files and the
  item win where they differ.
- **Overridable:** `principles.file` names a project's own file, `require_slices: false` drops the check, and
  `enabled: false` turns everything off. The prompt is then exactly as before.

## Consequences and known limits

- Requests written for 0.5.0 or earlier are refused until their items have `*Slice:*` lines, or until the project sets
  `require_slices: false`. Items already in a queue are not affected.
- Whether a slice really is usable is judged by its acceptance line and the runner's test run. Nothing checks it beyond
  that.
- The prompt grows by the file's length (capped at 12,000 characters) on every iteration.

## Amendment (2026-11-06): the technical manager mindset

A third principle now comes first in the file: think like a senior technical manager. The agent starts from the intent,
owns the outcome end to end, and decides reversible technical choices itself with the reasons written down. It raises a
blocker only for product behaviour, cost or licences, or anything hard to undo. Plans open with **Intent** and **Key
decisions** sections. This is guidance, not something the runner can check: the plan review is where a person sees it.
It also aims to cut blockers about technical choices the agent should have made itself.
