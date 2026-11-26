<!-- Was the body of /auto-runner:setup until decision 0012; /auto-runner:setup now runs these steps as one part of the guided setup. -->


Set up the auto-runner's capabilities for this project.

1. Run the preview. It writes nothing and downloads nothing:

   `python "${CLAUDE_PLUGIN_ROOT}/bin/autosetup.py" plan --project "${CLAUDE_PROJECT_DIR}"`

2. Show the user the preview in plain words: each missing capability and why, every file it would create or change (with the
   diff), the deny rule it would append to `.claude/settings.json`, and any download with its source and size (say when the
   size is an estimate). Steps marked "needs an administrator" or "by hand" are shown only; setup never runs them.
3. Ask once whether to apply. Only after a clear yes, run:

   `python "${CLAUDE_PLUGIN_ROOT}/bin/autosetup.py" apply --project "${CLAUDE_PROJECT_DIR}"`

   and report each step's result; for a failed step give its repair action.
4. Tell the user that `remove` previews what setup added and `remove --apply` restores those files (a download cannot be undone).

Do not edit `.claude/settings.json`, `.env` or any other file by hand as part of this command, and never remove a deny rule.
