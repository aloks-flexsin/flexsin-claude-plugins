# Benchmark: leave requests with approval (workflow-heavy internal app)

Build a small internal web service where employees request leave and managers approve it.

Roles:
- **employee**: creates, edits (while draft), submits and cancels their own requests; sees their own balance and history.
- **manager**: also an employee; approves or rejects the submitted requests of their direct reports.
- **hr_admin**: may approve, reject or cancel any request and adjust balances, with a reason.

A request has a type (annual, sick, unpaid), a start date, an end date and an optional note. Its states are
`draft -> submitted -> approved | rejected`, and `submitted | approved -> cancelled`. Any other transition is refused.
Rejecting needs a reason. Annual leave is deducted from the balance only when a request is approved, and given back
when an approved request is cancelled. Every state change is recorded with who did it and when.

Ask the owner about anything this brief does not settle (for example half days, or what happens when a manager is away).
The acceptance criteria are in `criteria.json`. Ship tests with the service.
