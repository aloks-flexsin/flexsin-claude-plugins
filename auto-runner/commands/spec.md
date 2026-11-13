---
description: Check a plan's queue items against the code before queuing them (a read-only reviewer; changes nothing)
argument-hint: <plan file>
---

Check the queue items in **$ARGUMENTS** before they are queued. This command changes nothing.

1. Run:

   `python "${CLAUDE_PLUGIN_ROOT}/bin/autospec.py" check --plan "$ARGUMENTS" --project "${CLAUDE_PROJECT_DIR}"`

   The file must have a "Queue items" heading with the items under it (a "Journeys" heading is read too).
2. Tell the user in plain words what it found: per finding the item, what is missing or wrong and the suggested fix. Kinds are
   missing_dependency, order, untestable_acceptance, open_decision, too_big and journey_gap.
3. If it printed "spec check not done", show the reason; never present that as a pass.
4. Offer to apply the fixes to the plan file. Do not queue anything as part of this command.
