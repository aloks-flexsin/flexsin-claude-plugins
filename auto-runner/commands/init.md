---
description: Set this project up for unattended runs (preview first, then apply after you agree)
argument-hint: [--central-url URL --central-project SLUG | --local] [--token-env NAME] [--with-rules]
---

Set up the auto-runner for this project. The central service is the default: blockers are raised and answered there.

1. Unless `$ARGUMENTS` already holds `--central-url` and `--central-project`, or `--local`, ask the user for:
   - the central service's address (for Flexsin: `https://auto-runner-central.vercel.app`) and the project's slug there, or
   - whether to keep this project local instead (`--local`: blockers are answered on this machine only).
   Do not pick for them. Never ask for the token itself and never put a token on a command line or in a file.
2. Run the preview. It writes nothing:

   `python "${CLAUDE_PLUGIN_ROOT}/bin/auto_init.py" --project "${CLAUDE_PROJECT_DIR}" <the central options or --local> $ARGUMENTS`

3. Show the user the preview in plain words: the test command it detected (or that none was found, which means every result will be
   marked "unverified: no tests"), the deny rules it would add to `.claude/settings.json`, the files it would create, the `central`
   section (the token variable it names, default `AUTO_RUNNER_TOKEN_<SLUG>`), and anything it found and will leave alone.
   When it lists steps to connect to the central service, give them to the user as they are: what to create on the site, and the
   PowerShell line that stores the token without echoing it. The user runs that line themselves, then restarts Claude Code.
4. If the preview says REFUSED, explain why (for example: this folder's `origin` is not the project's repository, or the token is
   another project's) and stop. Do not work around it.
5. Ask whether to apply. Only after a clear yes, run the same command with `--apply` added, and report what was written.
6. Tell the user that `--remove` previews what init added and `--remove --apply` takes away only that.

Do not edit `.claude/settings.json` or any other file by hand as part of this command.
