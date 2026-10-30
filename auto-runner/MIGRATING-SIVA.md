# Moving SIVA from its project scripts to the plugin

Not done yet. A person does this, after the plugin has run once on a scratch project. Until then SIVA keeps `scripts/auto_run.ps1`, `scripts/auto_queue.py` and `.claude/commands/auto.md`.

## `.claude/auto.json` that reproduces today's behaviour
```json
{
  "test_command": "venv/Scripts/python -m pytest tests -q",
  "check_only": false,
  "queue_file": "mission-control/AUTO_QUEUE.md",
  "rules_files": ["docs/autonomous-mode.md", "CLAUDE.md"],
  "share_dirs": ["venv"],
  "worktree_suffix": "-auto",
  "base_branch": "main",
  "branch_prefix": "auto/",
  "commit_trailer": "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
}
```
Differences to know about:
- Requests wait in `.auto/pending/<slug>/items.md` (the PowerShell script used `auto_runs/pending/`). Logs and the run history go to `.auto/` (`runs.jsonl`), not `auto_runs/`. Add `.auto/` to `.gitignore` if `init` has not.
- The queue file keeps its name; the runner adds a status line under each item it handles. `scripts/auto_queue.py` is a thin wrapper over the plugin's `queue.py`, so both read the same format.
- SIVA's `.claude/settings.json` already holds every deny rule the preflight demands (checked against `bin/autosafe.py`'s `required_rules('main')`).
- The plugin prompt tells the agent to read `rules_files`; SIVA's extra instructions (Mission Control ritual, one commit, tick the box) live in `CLAUDE.md` section 6b/6c and in the queue items, so keep them there.

## Steps
1. Install the plugin (see its README) and run `/auto-runner:init`; check the preview shows nothing to change except `.auto/` entries.
2. Put the JSON above in `.claude/auto.json`.
3. Run `python plugins/auto-runner/bin/auto_run.py --dry-run` and compare with `scripts/auto_run.ps1 -DryRun`.
4. Run one small queue item with the plugin on a throwaway `auto/` branch; read the diff and `.auto/runs.jsonl`.
5. Only then retire the project copies in a separate commit: `scripts/auto_run.ps1`, `.claude/commands/auto.md`, and make `scripts/auto_queue.py` point at the plugin copy (or remove it with its tests). Update `docs/autonomous-mode.md` and CLAUDE.md section 6c.
6. Keep the `Needs a person` list in `AUTO_QUEUE.md`; the plugin does not know about it.

Rollback: restore the files from git; nothing in the plugin writes outside `.auto/`, the queue file, the config and the `auto/` branches.
