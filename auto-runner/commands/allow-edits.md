---
description: (Old name, kept one release: use /auto-runner:manual edits) Let this session edit the main checkout for 12 hours when "enforce_runner" is on
---

The auto-runner's UserPromptSubmit hook has already handled this command: when the project's `.claude/auto.json` has
`"enforce_runner": true`, it wrote `.auto/run/allow-<this session's id>`, which lets this one session edit files in the
main checkout until the file is deleted or 12 hours pass. Other sessions stay guarded.

Change no file now. Tell the user in one or two sentences that edits in this checkout are allowed for this session for
12 hours, and that deleting `.auto/run/allow-<session id>` ends it sooner. If the hook printed nothing above (no session
id, or the plugin's hooks are not loaded), say that the escape could not be turned on and that the user can run
`python "${CLAUDE_PLUGIN_ROOT}/bin/autohook.py" allow` with the hook input on stdin, or turn `enforce_runner` off.
