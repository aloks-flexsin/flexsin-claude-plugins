# Benchmark: room bookings paid with credits (concurrency-sensitive rules)

Build a booking service for meeting rooms. Each member has a credit balance; booking a room costs credits per
started half hour, cancelling more than 24 hours before the start refunds them in full, later cancellations refund nothing.

Rules that must hold under any number of simultaneous requests, on more than one server process:
- A room never has two bookings that overlap, even partly.
- A member's balance never goes below zero, and every change to it is recorded once.
- A cancellation refunds at most once.
- A client may retry a request (same request id) after a timeout; a retry never books or charges twice.

Rooms are in different time zones; bookings are made in the room's local time. Ask the owner about buffers between
bookings and how times are stored. The acceptance criteria are in `criteria.json`. Ship tests with the service,
including tests that run requests in parallel.
