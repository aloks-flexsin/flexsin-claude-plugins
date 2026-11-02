# Live central round trip (test)

One item, D1, to prove the deployed central service end to end: the runner raises a blocker, its events reach
https://auto-runner-central.vercel.app, a person answers on the Blockers page, the next run pulls the answer and
finishes the item. Cap: 2 iterations x $2. The branch is a test and can be deleted afterwards.
