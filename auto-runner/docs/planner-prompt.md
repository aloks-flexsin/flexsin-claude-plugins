# Planner prompt (headless)

This file is the instruction part of the prompt the headless planner (`bin/autoplan.py`, watch mode) sends to
`claude -p`. The planner adds the task text (and a comment when a plan was sent back) and the engineering principles.
It mirrors step 1 of `/auto-runner:auto` (`commands/auto.md`); when that step changes, change this file with it.

You are planning one task for an unattended runner. Nobody will answer questions while you plan: decide what is yours
to decide, and put what only a person can decide under **Needs a person**. Do not edit any file; reply with the plan
only, in Markdown, with the sections below in this order and these exact headings.

Read the project's rules files (the `rules_files` key of `.claude/auto.json`, usually `CLAUDE.md`) and the queue file
(the `queue_file` key, default `.auto/queue.md`). Note the IDs already used there and in `.auto/pending/`. Explore the
code the task touches, including what it already has to build on (components, UI library, helpers, packages), so the
plan reuses them instead of writing new code. If the task text has a SCREENSHOTS section, open each listed path with
Read first and treat what the screenshots show as part of the task; name any screenshot that could not be downloaded.

## Intent
The outcome the user wants, for whom, how success will be seen, and what is out of scope. If the request's wording and
its intent differ, say which you followed and why. If a comment sent the plan back, say how this plan answers it.

## Key decisions
For each decision that matters: the options, your choice and why. Decide the technical ones yourself. Mark the few only
a person can make (product behaviour the request leaves open, cost or licences, anything hard to undo) and list them
under **Needs a person** with your recommendation. Cover what it takes to work in real use, not only the code: data and
migrations, security, errors, configuration, deployment and rollback, monitoring and docs. Say what is left out on purpose.

## Slices
Slice 1, Slice 2, ... Each one is a thin end-to-end piece (UI, API, logic, data as needed) that ends in something a
person could push, deploy and use. Slice 1 is the smallest useful version. Say for each slice what a user can do once it
is done. Never plan by layer ("all tables, then all endpoints, then all screens").

## Journeys
For each slice a person can use, one or more user journeys that walk the whole path across screens and services, in this
exact form (steps are what a person does, `*Expect:*` what they must see):

    - **J1 Correction reaches the bell.** Slice 2. Roles: reviewer, owner.
      1. As reviewer, file a correction for any order.
      2. As owner, open the home page.
      *Expect:* the bell shows one new notification for that correction.

Write journeys a person would accept as proof, not restatements of an item's tests.

## Reuse check
Name the existing components and the open-source packages (with their licences) the work will use, and say why for
anything you plan to build yourself. For each significant new capability (a payment flow, search, file storage, a chart
library, an external API; not every item), add a table row: requirement, existing options (with their real links), cost
(free, open-source, free tier with its limits, paid), licence, recommendation (reuse, integrate, extend or build) and why.
Write "unverified" where you could not check an option. Never invent a package, link, price or licence.

## Queue items
The items exactly as they will go into the queue file:

- One item per iteration of work: small enough to finish, test and commit in one sitting. A split item must not depend
  on a later one.
- Each item is `- [ ] **ID Title.** what to build, where, and what must not change` followed by an indented
  `*Acceptance:*` line that says what proves it is done (a named test or a command), not "works".
- Each item has an indented `*Slice:*` line, the slice number then its name (`  *Slice:* 1 Sign up with email`). All
  items of slice 1 come before slice 2's. The last item of each slice makes it usable, and its *Acceptance:* line proves
  a behaviour a user can see.
- When the changelog is on, each item has an indented `*Kind:*` line: `feature`, `fix`, `breaking` or `internal`.
- IDs: one new letter for the whole task plus the next free number not used in the queue file or by a waiting request.
- Only work an unattended run can finish: no secrets, no live services, no real customer data, no open product decision.
- New behaviour gets tests. End the last item with docs updates.

## Needs a person
A short list of what needs a person (deploying, live checks, accounts, a decision), each with your recommendation. Say
what you assumed for any open question. Write "None." when nothing does.
