# Benchmark: order checkout against external payment and shipping services (integration-heavy)

Build the order service of a small shop. It talks to two external services through HTTP; their contracts are below.
Do not call any real provider: build against stubs that follow these contracts.

**Payment provider**
- `POST /payments` with `amount`, `currency`, `order_id` and an `Idempotency-Key` header; replies `201` with a payment id
  and status `pending`. The same key always returns the same payment.
- Webhooks `payment.succeeded` and `payment.failed`, signed with HMAC-SHA256 over the raw body in the header
  `Provider-Signature`. Webhooks can arrive twice and out of order.
- Errors: `400`/`422` for bad requests, `429` with `Retry-After`, `5xx` and timeouts when unavailable.

**Shipping service**
- `POST /shipments` once an order is paid; replies `201` with a tracking number. Same error behaviour as the payment provider.

The order moves `created -> payment_pending -> paid -> shipped`, or `payment_failed`. When a provider is unavailable the
order must not be lost, charged twice or shipped unpaid, and the person looking at it must see why it is waiting.
Ask the owner about time limits and currencies. The acceptance criteria are in `criteria.json`. Ship tests with the service.
