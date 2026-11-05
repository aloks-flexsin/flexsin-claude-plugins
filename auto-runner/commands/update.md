---
description: Update the auto-runner plugin to the newest version from its marketplace (check first, then one approval)
---

Update the installed auto-runner plugin.

1. Run the check. It refreshes the marketplace's catalogue and installs nothing:

   `python "${CLAUDE_PLUGIN_ROOT}/bin/autoupdate.py" check`

2. Tell the user the installed and the available version. If it is up to date, say so and stop. Otherwise summarise the
   changelog entries it printed in plain words (what is new, what is fixed, anything marked breaking).
3. Ask once whether to install the update. Only after a clear yes, run:

   `python "${CLAUDE_PLUGIN_ROOT}/bin/autoupdate.py" apply`

   and report the old and new version it prints.
4. Tell the user to fully quit and restart Claude so the new version is loaded; until then this session keeps the old one.
   If a project needs anything new from the update (new deny rules or config keys), `/auto-runner:init` previews it there.

If either step prints "update not done", show its message; it names the commands to run by hand. Do not edit Claude's plugin
files or settings by hand as part of this command.
