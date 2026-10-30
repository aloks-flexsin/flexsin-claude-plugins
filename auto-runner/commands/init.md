---
description: Set this project up for unattended runs (preview first, then apply after you agree)
argument-hint: [--with-rules]
---

Set up the auto-runner for this project.

1. Run the preview. It writes nothing:

   `python "${CLAUDE_PLUGIN_ROOT}/bin/auto_init.py" --project "${CLAUDE_PROJECT_DIR}" $ARGUMENTS`

2. Show the user the preview in plain words: the test command it detected (or that none was found, which means every result will be marked "unverified: no tests"),
   the deny rules it would add to `.claude/settings.json`, the files it would create, and anything it found and will leave alone (an existing auto command or scripts).
3. Ask whether to apply. Only after a clear yes, run the same command with `--apply` added, and report what was written.
4. Tell the user that `--remove` previews what init added and `--remove --apply` takes away only that.

Do not edit `.claude/settings.json` or any other file by hand as part of this command.
