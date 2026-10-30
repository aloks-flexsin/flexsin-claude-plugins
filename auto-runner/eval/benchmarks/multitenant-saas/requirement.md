# Benchmark: task tracker for many organisations (multi-tenant SaaS)

Build a task-tracking service sold to many organisations (tenants) from one deployment and one database.

- Every user belongs to exactly one tenant. Users create, edit, assign, search and close tasks in their own tenant.
- A **tenant admin** invites and removes users of their own tenant.
- Users can export their tenant's tasks as CSV.
- A nightly job writes a per-tenant summary (open, closed, overdue counts).
- A **platform support** role exists for the operator's staff.

Tenant isolation is the point of this benchmark: no request, search, export, cache, file or background job may ever show
one tenant's data to another. Ask the owner what support staff may do and what happens to a tenant's data when they leave.
The acceptance criteria are in `criteria.json`. Ship tests with the service.
