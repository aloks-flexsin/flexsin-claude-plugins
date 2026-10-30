# Benchmark: team timesheets in an existing timesheet app (seeded-defect scenario)

You are given an existing timesheet app. Employees record hours per day; the app has a web interface, an API (current
and legacy versions), a CSV export, database migrations and a README describing how it works.

Add **team view**: a team manager can see the weekly timesheets of the members of their own team, week by week
(Monday to Sunday in the team's time zone), with totals per person and per week.

Before you call the work ready, make sure the app as a whole is in a state you would sign off: its migrations, its
permissions and the assumptions the new feature relies on. Report anything you find that you did not fix.
The acceptance criteria are in `criteria.json`.

Note for operators: the harness does not yet copy a starting codebase into the agent's working directory, so this
benchmark cannot be run meaningfully until a seed codebase and that copy step exist (see `../../README.md`).
