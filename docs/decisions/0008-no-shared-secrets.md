# 0008: No shared secrets: invite links, sign-in from Claude, personal tokens

Status: **proposed** (2026-11-23). The four questions at the end need an answer before it is accepted and built.
Builds on 0001 (central service), 0002 (central-first setup) and 0007 (direction, step 3).

## Context

Getting one person onto a project today takes three secrets passed by hand:

1. **A password.** An admin creates the account on `/admin/accounts`, types a first password and sends it somehow.
   "Add a member" fails with "no account with that email" until that has happened.
2. **A project token.** An owner or manager creates one on the project's tokens page, it is shown once, and everyone who
   runs the runner on that project gets a copy and stores it in an environment variable.
3. **Which variable.** Each project has its own token variable (`AUTO_RUNNER_TOKEN_<SLUG>`), which people must match.

Shared project tokens also mean the service cannot tell people apart. A token leaked from one laptop must be rotated for
everyone, and a person who leaves keeps a working token until someone rotates it. Runs say who ran them (0.17.0) only
because the runner says so, not because the service knows.

## Decision

### 1. Invite instead of a shared password
- On a project's members page, an owner or admin enters an **email and a role** and presses **Invite**.
  - The email has an account → it is added as a member, as today.
  - It has none → the service creates the account (**no password; it cannot sign in yet**), adds the membership and makes a
    **one-time invite link**, shown once to the person inviting, who sends it to the invitee (chat, email). The service
    sends no email itself: that needs a mail provider (cost, setup, another place for data), left for later.
- The link opens a page where the invitee sets their own password (the existing rules: 12+ characters, not a common
  password) and is signed in. The link works **once**, **expires after 72 hours**, and is stored only as its SHA-256
  (like tokens and sessions). Inviting again replaces an unused link; the old one stops working.
- The same mechanism replaces admin-typed passwords for **password resets**: "reset" makes a one-time link, the old
  password stops working at once and every session is signed out (as today).
- Every invite, acceptance, expiry and reset is in the audit log, never with the link itself.

### 2. Sign in from Claude instead of a shared project token
A device sign-in, the pattern `gh auth login` and other command-line tools use:
1. `/auto-runner:central` runs `autocentral.py login`. It asks the service for a sign-in request and prints a **short code**
   (8 characters, for example `KQ4M-7RXD`) and the address `<service>/device`.
2. The person opens that address in their browser, signs in as usual, types the code and sees what they approve: the
   computer's name and the repository. They press **Approve**. The page warns: approve only a code you started yourself.
3. The script, which has been polling (every 5 seconds, for at most 10 minutes), receives a **personal token** and stores
   it **without printing it**. Claude only sees "signed in as alok@flexsin.com".

The code expires after 10 minutes and works once; only its hash is stored. Approving needs a signed-in session and the
CSRF check like every other form.

### 3. Personal tokens
- A personal token belongs to **one account**, not to one project. It works for every project the account is a member of
  with role **developer or above** (sending events, fetching answers); viewers cannot run runners. Losing membership, or
  the account being disabled, takes effect at once.
- The API learns the project from the request (the event's `project`, the slug in the path or a `project` parameter) and
  checks membership, instead of from the token.
- Each person sees and revokes their tokens on a **My tokens** page (one per computer, named after it, with last use);
  an admin can revoke anyone's. Every API call records the token's last use; a token unused for 90 days stops working.
- Stored as SHA-256 with a short prefix for display, like project tokens today.
- Events from a personal token are stamped with that account by the service, so the runs page shows who ran a run
  from what the service knows, not only from what the runner says.

### 4. One variable for every project
The plugin reads one variable, `AUTO_RUNNER_TOKEN`, for every project. `central.token_env` stays for the old project
tokens.

### 5. Project tokens stay, for machines
Project tokens keep working unchanged, for runners no person signs in on (a build server, for example), and as the way
older plugins connect. They are no longer the way people join.

## Consequences

- Joining a project becomes: an admin invites by email and role → the person sets a password from the link → they install
  the plugin and run `/auto-runner:central`, which signs them in from Claude. Nothing secret passes between people.
- New tables: invite (account, link hash, expiry, used), device sign-in request (code hashes, expiry, approved by, token),
  personal token (account, hash, prefix, name, last use, revoked). A migration a person runs, as before.
- The API changes from "the token decides the project" to "the request names the project, membership decides". Every
  endpoint gets a membership test and a test that no project's data reaches another project's member.
- Build order inside step 3: invites and resets first (usable on their own), then device sign-in and personal tokens.
- Unverified until it runs on the live service, like everything before it.

## Questions to settle before building

1. **Invite links expire after 72 hours.** Shorter is safer; longer suits people who are away. 72 hours proposed.
2. **Who may invite:** admins and the project's owners (proposed), or admins only?
3. **Where the plugin keeps the personal token on macOS and Linux.** Windows has a per-user environment variable the
   script can set without printing it; macOS and Linux have nothing like it. Proposed: a file only the user can read
   (`~/.config/auto-runner/credentials`, mode 600), which the plugin reads when `AUTO_RUNNER_TOKEN` is not set. That is an
   exception to the repository rule "config comes from environment variables only", so it needs your yes. The
   alternative: the person adds the token to their shell profile themselves, which means seeing it once.
4. **Unused personal tokens stop working after 90 days** (proposed), or never (only revoking stops them)?
