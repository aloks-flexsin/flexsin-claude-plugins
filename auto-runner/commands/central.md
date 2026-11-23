---
description: Check whether this project is connected to the central service, and set it up step by step after you agree
argument-hint: [--central-url URL] [--central-project SLUG]
---

Check this project's connection to the central service (where blockers are answered away from the terminal), explain how
the page is used, and help the user finish the setup. Every change previews first and is made only after a clear yes.

1. Run the check. It only reads; it changes nothing and never pulls answers:

   `python "${CLAUDE_PLUGIN_ROOT}/bin/autocentral.py" status --project "${CLAUDE_PROJECT_DIR}"`

   Tell the user in plain words what it found: connected or not, the token (set or not; never its value), the runner
   identity, events not sent yet, blockers waiting (with their page links) and how the page is used. Exit code 0 means
   connected and nothing to do; then stop here.

2. **NOT CONNECTED (no `central` section).** Ask once whether to connect *this checkout* or to clone a project *from* the
   central service into a new folder. Ask for the service address (Flexsin: `https://auto-runner-central.vercel.app`) and
   the project's slug there, unless `$ARGUMENTS` gives them. Never guess either.
   - This checkout: run `python "${CLAUDE_PLUGIN_ROOT}/bin/auto_init.py" --project "${CLAUDE_PROJECT_DIR}" --central-url <URL> --central-project <SLUG>`
     (a preview; it writes nothing). Show it as `/auto-runner:init` would. Only after a clear yes, run the same with `--apply`.
   - A fresh clone: follow `/auto-runner:join` (preview, confirm the folder, `--apply`, then init's preview and, after a yes,
     its `--apply` line).

3. **OFF (`central.enabled` is not true).** Explain that answers stay local. Do not change the file; tell the user that
   `"enabled": true` in the `central` section turns it back on, and offer to show them the line.

4. **Token NOT SET.** Give the user the steps the check printed, as they are. If the project or a token does not exist on
   the site yet, say exactly what an admin (project) or an owner or manager (token) must create there; this command cannot
   do anything on the site. The user stores the token themselves with the hidden-prompt line, then restarts Claude Code.
   Never ask for the token, never let the user paste it into the conversation, and never put it on a command line or in
   a file. If they paste it anyway, tell them to make a new token and revoke that one.

5. **Runner identity MISSING.** Ask the user for their username (the person working) and the Claude account they run the
   runner with. Never guess them. Show the lines, and only after a clear yes:
   - Windows: run `powershell -NoProfile -Command "[Environment]::SetEnvironmentVariable('AUTO_RUNNER_USER', '<user>', 'User'); [Environment]::SetEnvironmentVariable('AUTO_RUNNER_ACCOUNT', '<account>', 'User')"`
     with the values they gave (lower-case letters, digits, `.`, `_`, `-` only; refuse anything else).
   - macOS/Linux: do not edit their shell profile; give them the two `export` lines to add.
   Then tell them to restart Claude Code.

6. **The service REFUSED the token, is NOT REACHABLE, or answers with a mismatch** (another project's token, no repository
   URL, this folder's origin is a different repository). Explain what it said and what fixes it, and stop. Never work around it.

7. After any change, run the check again and report what is still missing, until it says CONNECTED or only steps the
   user must do themselves are left (the token, a restart, something on the site).

Do not edit `.claude/auto.json`, `.claude/settings.json` or any other file by hand as part of this command.
