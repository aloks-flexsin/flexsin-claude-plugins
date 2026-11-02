---
description: Clone a project registered on the central service and set it up (preview first, you confirm the folder)
argument-hint: <slug> [--central-url URL] [--dest FOLDER] [--token-env NAME]
---

Join a project that is on the central service: read its repository URL there, clone it with the user's own git credentials, then
set it up with init. The central service never holds git credentials.

1. The first word of `$ARGUMENTS` is the project's slug. If there is none, ask for it. Unless `--central-url` is given, ask for the
   central service's address (for Flexsin: `https://auto-runner-central.vercel.app`). Never ask for the token itself.
2. Run the preview. It clones nothing:

   `python "${CLAUDE_PLUGIN_ROOT}/bin/auto_join.py" <slug> --central-url <URL> <other options from $ARGUMENTS>`

3. If it says REFUSED, explain why and stop. When the token is missing, give the user the steps it printed (what to create on the
   site and the PowerShell line that stores the token); they run it themselves and restart Claude Code before trying again.
4. Show the repository URL and the folder it would clone into, and ask the user to confirm that folder or name another
   (`--dest`). Only after a clear yes, run the same command with `--apply` added (and `--dest` if they chose one).
5. After the clone it prints init's preview for the new folder. Show it in plain words as `/auto-runner:init` would, and ask
   whether to apply. Only after a clear yes, run the `auto_init.py ... --apply` line it printed.

Do not run `git clone` yourself, and do not edit any file by hand as part of this command.
