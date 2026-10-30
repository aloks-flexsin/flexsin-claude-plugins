# Benchmark: add a feature to an unfamiliar existing codebase

You are given an existing invoicing service you have not seen before. It has its own conventions: a permission
decorator for routes, a money type for amounts, a migration tool and a test suite. Learn them before changing anything.

Add **invoice export as CSV**: an account user may download their account's invoices for a date range, optionally
filtered by status (draft, sent, paid, overdue). Columns: number, customer, issue date, due date, status, total, currency.

Rules:
- Follow the codebase's existing conventions; do not introduce a second way of doing something it already does.
- Do not add a dependency and do not change files unrelated to the feature.
- The existing test suite must still pass; add tests for the export in the suite's existing style.

Ask the owner how the date range is interpreted. The acceptance criteria are in `criteria.json`.

Note for operators: the harness does not yet copy a starting codebase into the agent's working directory, so this
benchmark cannot be run meaningfully until a seed codebase and that copy step exist (see `../../README.md`).
