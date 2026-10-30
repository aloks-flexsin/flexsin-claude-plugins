# Benchmark: expense claims with open policy decisions (ambiguity needing owner decisions)

Build an expense-claim service: employees submit claims with a receipt, an amount and a currency; claims are approved,
then paid in euros.

This brief is deliberately incomplete. The following are business decisions that only the owner can make. Do not
choose them yourself: ask the owner, implement what they decide, and where you get no usable decision, leave that part
unbuilt and report it as an open question.

1. Above which amount does a claim need a second approval from finance?
2. Which exchange rate converts a foreign-currency claim, and from which date?
3. Who approves the expense claims of the CFO?
4. How long are receipts kept, and what happens to them afterwards?

Everything else (submission, single approval, payment status, history) you may design yourself.
The acceptance criteria are in `criteria.json`. Ship tests with the service.
