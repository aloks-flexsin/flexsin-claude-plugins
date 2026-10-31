# Delivery contract (roadmap K6)

How an answered blocker travels from wherever a person answered it to the runner that raised it. A future central service must be
built to this page; today two implementations meet it: `LocalTransport` in `bin/autodeliver.py` (answers saved in the project's own
`.auto/` folder) and the in-memory fake `tests/auto_runner/fake_central.py`. The same scenarios run against both in
`tests/auto_runner/test_delivery_contract.py`. **Unverified:** no real service exists, nothing here has run over a network.

## Calls

The runner is a client that pulls; nothing is pushed into a running session (each `claude -p` iteration starts fresh).

### `pull_pending(project) -> [delivery]`
Every delivery for `project` that is not yet acknowledged, oldest delivery id first. One delivery:

```json
{"delivery_id": "B-abc.v1", "blocker_id": "B-abc", "version": 1, "project": "proj", "task": "A1",
 "decision": {"schema": "decision.v1", "blocker_id": "B-abc", "version": 1, "responder": "pat", "role": "product owner",
              "answer": {"option": "Orange", "text": "..."}, "context": "", "created_at": "2026-10-30T09:00:00+00:00",
              "applies_to": null}}
```

### `ack(delivery_id) -> row`
Records that the runner has taken the delivery up again (its item was selected and the resume packet built). Unknown id: `KeyError`.

### Errors
Either call raises `autodeliver.TransportUnavailable` (a `ConnectionError`) when the service cannot be reached or answers with a
server error. A call that raises has changed nothing. Any other error is a bug, not an outage.

## Rows and states (service side)

One row per delivery: `delivery_id`, `project`, `task`, `blocker_id`, `version`, `decision`, `state`, `attempts`, `last_error`.

| state | meaning | next |
|---|---|---|
| `pending` | answered, not handed over yet | `delivered`, `failed`, `acknowledged` (local only) |
| `delivered` | handed over in a pull reply | `acknowledged`; offered again by the next pull until then |
| `failed` | a hand-over did not reach the runner (`last_error` says why) | offered again by the next pull |
| `acknowledged` | the runner has resumed from it | final: never offered again |

- `attempts` counts hand-overs, including failed ones.
- `LocalTransport` has no hand-over to confirm, so its rows stay `pending` until acknowledged and it accepts an ack for any pulled
  row. The fake central refuses an ack for a row that was never delivered (`ValueError`).

## HTTP binding (central service, `auto-runner/central/deliveries.py`)

Both calls take `Authorization: Bearer <token>` with the `pull` scope (401 missing/bad/revoked token, 403 wrong scope); the
project is the token's project.

- `GET /api/v1/deliveries/pending` -> `200 {"deliveries": [delivery, ...]}` (the shape above). Each row handed over moves
  pending/failed -> `delivered` and counts an attempt; a row whose reply cannot be built is left out and moves to `failed`
  with `last_error` (also an attempt).
- `POST /api/v1/deliveries/<delivery_id>/ack` -> `200` with the row (`delivery_id`, `project`, `task`, `blocker_id`,
  `version`, `state`, `attempts`, `last_error`); a second ack answers the same row and changes nothing. `404` unknown id or
  another project's id (the transport maps it to `KeyError`); `409` a row never delivered (maps to `ValueError`).
- Decisions are recorded at the centre by owners, managers and admins of the project (`record_decision`), each as the
  blocker's next version with a `pending` delivery row and an audit row.

## Rules both sides keep

1. **Identity.** `delivery_id` = `<blocker_id>.v<version>`. A row whose id, `blocker_id` and `decision.version` disagree is not applied.
2. **No cross-delivery.** `pull_pending(project)` returns only that project's rows; the runner also drops any row whose `project`
   differs from its own, or whose `task` differs from the task of the local blocker with that id.
3. **Idempotent.** Pulling the same delivery again (in later pulls or twice in one reply) writes one inbox file, one local decision
   copy and one DELIVERED move. A second `ack` changes nothing. Re-running `resume` after it finished changes nothing.
4. **Never overwritten.** A decision received for a version already stored locally must be identical; a different one is refused
   (`CorruptBlocker`) and the stored one kept.
5. **Order of moves.** Pull: store the decision copy (a blocker answered at the centre is still OPEN locally:
   OPEN -> ANSWERED -> PENDING_DELIVERY), write `.auto/inbox/<delivery_id>.json`, then PENDING_DELIVERY -> DELIVERED.
   Resume: build every packet, ack every delivery, and only then move the blockers DELIVERED -> ACKNOWLEDGED -> RESUMING -> RESUMED.
   An unreachable service at ack time therefore leaves every blocker DELIVERED, and the next resume repeats the (idempotent) acks.
6. **Accurate status.** A blocker is reported as resumed only in RESUMED (or CLOSED). Answered but not delivered reads
   "answer saved, waiting for delivery" (local) or, when the answer is still only at the centre, "waiting for an answer" with the
   service row `pending` or `failed`; delivered reads "answer delivered, waiting for the runner".

## Recovery

- **Service outage on pull:** the runner says so and goes on; the next pull (between iterations, or the next run) delivers.
- **Service outage on ack:** the item is held `blocked (delivery unavailable)` and goes back to `queued` at the next pull.
- **Lost reply:** the row is `failed`; the next pull offers it again.
- **Runner killed** after a pull or mid-resume: everything needed is in `.auto/` (inbox, decisions, blocker history), so the next
  start finishes the moves exactly once.
