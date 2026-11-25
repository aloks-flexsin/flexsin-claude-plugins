---
description: Run one Supervisor pass (approve or send back plans, merge green pull requests) and print a summary
---

Run this and show the output as it is. It needs the supervisor account's token and settings in the environment (see the
README); it refuses to run, with a non-zero exit code, if the token is not a supervisor's.

`python "${CLAUDE_PLUGIN_ROOT}/bin/autosupervisor.py"`

Then say in one sentence what the pass did, or, on a non-zero exit, what is missing (exit 2: a setting is not set; 3: the token
is not a supervisor's or the service did not answer; 1: a step reported an error). Never print or ask for a token.
