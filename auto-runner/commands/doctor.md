---
description: Check what browser acceptance, the changelog and the doc checks need, and what is missing (read-only)
---

Check this project's capabilities for the auto-runner. This command writes nothing.

1. Run:

   `python "${CLAUDE_PLUGIN_ROOT}/bin/autosetup.py" doctor --project "${CLAUDE_PROJECT_DIR}"`

2. Tell the user in plain words what is present, what is missing and the repair step for each missing item. Items marked
   `optional-admin` need an administrator and do not block a run.
3. If anything is missing, say that `/auto-runner:setup` previews the fix and applies it only after they agree.

Do not install, download or edit anything as part of this command.
