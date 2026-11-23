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

2. **A git worktree.** If the check says this folder is a worktree of another folder, tell the user to open Claude Code in
   that main checkout and run `/auto-runner:central` there. Change nothing here.

3. **NOT CONNECTED (no `central` section).** Ask once whether to connect *this checkout* or to clone a project *from* the
   central service into a new folder. Ask for the service address (Flexsin: `https://auto-runner-central.vercel.app`)
   unless `$ARGUMENTS` gives it. Never guess it.
   - This checkout: do not ask for a slug; the project is read from this folder's origin and the service confirms it.
     Run `python "${CLAUDE_PLUGIN_ROOT}/bin/auto_init.py" --project "${CLAUDE_PROJECT_DIR}" --central-url <URL>`
     (a preview; it writes nothing). Show it as `/auto-runner:init` would. Only after a clear yes, run the same with `--apply`.
   - A fresh clone: ask for the project's slug as it appears on the central page (or clone the repository with git as
     usual and run `/auto-runner:central` inside it, which needs no slug), then follow `/auto-runner:join` (preview,
     confirm the folder, `--apply`, then init's preview and, after a yes, its `--apply` line).

4. **OFF (`central.enabled` is not true).** Explain that answers stay local. Do not change the file; tell the user that
   `"enabled": true` in the `central` section turns it back on, and offer to show them the line.

5. **Token NOT SET: sign this computer in.** Explain it in one line (they approve a short code in their browser; Claude
   never sees the token) and ask. Only after a clear yes:
   1. Run `python "${CLAUDE_PLUGIN_ROOT}/bin/autocentral.py" login start --project "${CLAUDE_PROJECT_DIR}"`. Show the
      user the address and the code it prints, exactly, and tell them to approve it only because they started it now.
   2. Right away run `python "${CLAUDE_PLUGIN_ROOT}/bin/autocentral.py" login wait --project "${CLAUDE_PROJECT_DIR}"`
      (in the foreground, with a 10-minute timeout). It returns once they approve or deny, or after 10 minutes.
   3. Report what it printed (the account it signed in as, and where the token went). On Windows the token is a user
      environment variable: tell them to restart Claude Code. On macOS/Linux it is a file only they can read, used at once.
   If they are not a member of the project yet, an owner or admin must invite them on the project's page first. A machine
   nobody signs in on (a build server) uses a project token instead (the check names its variable). Never ask for a token,
   never let anyone paste one into the conversation, and never put one on a command line or in a file; if they paste one
   anyway, tell them to revoke it (My tokens, or the project's tokens page) and sign in again.

6. **Runner identity MISSING.** Ask the user for their username (the person working) and the Claude account they run the
   runner with. Never guess them. Show the lines, and only after a clear yes:
   - Windows: run `powershell -NoProfile -Command "[Environment]::SetEnvironmentVariable('AUTO_RUNNER_USER', '<user>', 'User'); [Environment]::SetEnvironmentVariable('AUTO_RUNNER_ACCOUNT', '<account>', 'User')"`
     with the values they gave (lower-case letters, digits, `.`, `_`, `-` only; refuse anything else).
   - macOS/Linux: do not edit their shell profile; give them the two `export` lines to add.
   Then tell them to restart Claude Code.

7. **The service REFUSED the token, is NOT REACHABLE, or answers with a mismatch** (another project's token, no repository
   URL, this folder's origin is a different repository). Explain what it said and what fixes it, and stop. Never work around it.

8. After any change, run the check again and report what is still missing, until it says CONNECTED or only steps the
   user must do themselves are left (the token, a restart, something on the site).

Do not edit `.claude/auto.json`, `.claude/settings.json` or any other file by hand as part of this command.
