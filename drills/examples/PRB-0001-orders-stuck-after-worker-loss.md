# PRB-0001: an order stays stuck when its worker dies, and the automatic retry cannot free it

> Practice record from drills 5 and 6, written from what the lab actually did.

| Field | Value |
| --- | --- |
| Status | Known error (workaround tested), permanent fix not started |
| Linked incidents | Drill 5 (ORD-20261019-0007), drill 6 (ORD-20261019-0006) |
| Owner | Data operations |
| Opened | 2026-10-19 |

## Problem statement
When a worker is killed during generation, its order stays in `GENERATING`
forever. The stale-claim requeue moves the file back to the inbox after 10
minutes, but the next worker rejects it as `E_DUPLICATE_ORDER ... already GENERATING`.
Every occurrence needs a person, and the customer's SLA keeps running.

## Root cause analysis
1. Why is the order stuck? The requeued file is rejected as a duplicate.
2. Why a duplicate? `process_order` refuses any existing order whose status is
   not `FAILED` (`generator.py`), and a dead worker never sets `FAILED`.
3. Why can't the dead worker set it? It was killed (SIGKILL, node loss, OOM),
   so no code of ours ran.
4. Why does nothing else clean up? Recovery was designed for the *file*
   (stale claims) but not for the *order record*.

**Root cause:** the pipeline has no recovery path for an order record left in
`RECEIVED` or `GENERATING` by a worker that died.

Related finding from drill 6: after a database restart, workers that were idle
keep a dead connection and fail the first order they take. Same theme: the
worker assumes its resources are still alive.

## Workaround (known error)
Tested on 2026-10-19 (300,000-card order, delivered 37 s after resubmit):

1. Confirm nothing was delivered: no `.out` for the batch in `outbox/`.
2. Standard change CHG-STD-01:
   ```sql
   UPDATE orders SET status = 'FAILED', error_code = 'E_WORKER_LOST'
   WHERE order_id = '<order>' AND status IN ('RECEIVED', 'GENERATING');
   ```
3. `scripts/resubmit.sh <file>`; confirm `DELIVERED` and run `simgen qc` on the output.

After any database restart: `docker compose restart worker` (or
`kubectl rollout restart deployment/simgen-worker`) before resubmitting.

## Permanent fix
- When a claim is requeued, record the order as `FAILED` / `E_WORKER_LOST`
  (or let `process_order` take over an order in `RECEIVED`/`GENERATING` whose
  claim is older than `--stale-after`). Test first: a test that leaves an order
  in `GENERATING`, requeues the claim and expects `DELIVERED`.
- Check the database connection before each order and reconnect if it is dead.
- Delivered through a normal change, then re-run drills 5 and 6 to prove it.

## Closure
Open.
