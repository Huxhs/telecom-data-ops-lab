# drills: incident practice on the ops stack

A data-generation team is judged on what happens when something breaks: how
fast you notice, whether you protect the customer's data, and whether the same
failure comes back. These drills break the lab on purpose so you can practise
the ITIL loop on something real:

```
detect ──▶ log an incident ──▶ restore service ──▶ problem (root cause) ──▶ change (permanent fix)
```

- **Incident**: something is broken for a customer right now. Goal: restore
  service. (Templates: [`templates/incident.md`](templates/incident.md))
- **Problem**: the underlying cause of one or more incidents. Goal: stop it
  happening again. A cause with a documented workaround is a *known error*.
  ([`templates/problem.md`](templates/problem.md))
- **Change**: any controlled modification to the system (a config, a key, a
  code fix, a manual database update). ([`templates/change.md`](templates/change.md))

Worked examples: [`examples/INC-0001-range-overlap.md`](examples/INC-0001-range-overlap.md),
[`examples/PRB-0001-orders-stuck-after-worker-loss.md`](examples/PRB-0001-orders-stuck-after-worker-loss.md)
and [`examples/PRB-0002-two-workers-same-range.md`](examples/PRB-0002-two-workers-same-range.md).

## Setup

```bash
cd ops-stack
docker compose up -d --build --scale worker=2
docker compose --profile splunk up -d          # optional, for drill 8
```

Helpers, all run from `ops-stack/`:

| Command | Does |
| --- | --- |
| `scripts/drop.sh FILE [NAME]` | drops an input file into the inbox safely (`.part`, then rename) |
| `scripts/folders.sh` | lists every exchange folder and prints each `.reason` |
| `scripts/resubmit.sh NAME` | moves a parked file from `error/` back to the inbox |
| `docker compose exec worker simgen status` | every order and its status |
| `docker compose exec postgres psql -U simgen -d orders` | the database |
| `docker compose exec worker tail -f /logs/events.log` | the event stream Splunk reads |

You can also upload over SFTP (`sftp -P 2222 mno@localhost`) the way a
customer would; the drills use the scripts because they are repeatable.

## Severity used in this lab

| Priority | Meaning | Example |
| --- | --- | --- |
| P1 | Generation stopped for everyone, or wrong or secret data could leave the building | all workers down; a key in a log; duplicate ICCIDs delivered |
| P2 | One customer's order blocked and its SLA is at risk | range overlap, unknown transport key |
| P3 | One file rejected with a clear fix, SLA not at risk | truncated upload, missing field |
| P4 | Question or cosmetic | "why is the output file name in capitals?" |

## The drills

Work through them in order. For each one, write the ticket *before* you read
the "Respond" section, then compare.

### 1. Truncated upload (P3)

**Trigger:** `scripts/drop.sh ../simgen/samples/order_truncated.txt`

**You should see:** the file in `error/` with
`E_TRUNCATED: no *END marker; file may be incomplete`; an `order_rejected`
event; no row in `orders` (the file was never readable as an order).

**Respond:** compare the file size with what the customer says they sent.
Ask them to resend the complete file. Nothing was generated, so nothing needs
cleaning up. If it happens again, open a problem: is their upload tool writing
straight to the final name? (Drop scripts and SFTP clients should upload to a
temporary name and rename, which is why the worker ignores `.part` files.)

**Record:** incident, resolved by customer resend.

### 2. Range overlap (P2)

**Trigger:** `scripts/drop.sh ../simgen/samples/order_good.txt` (if not done
yet), then `scripts/drop.sh ../simgen/samples/order_overlap.txt`

**You should see:** `E_RANGE_OVERLAP: ICCID ...-... overlaps order ORD-20261019-0001`;
the order `FAILED` in `simgen status`; nothing in `outbox/` for batch B26101902.

**Respond:** never "fix" the range yourself; the operator owns its numbering
plan, and a guessed range could collide with cards already in the field.
Confirm the existing allocation (`SELECT * FROM allocations`), send the customer
the exact clash, and ask for a corrected file. Resubmit when it arrives.

**Record:** incident. See the worked example
[`INC-0001`](examples/INC-0001-range-overlap.md).

### 3. Unknown transport key (P2)

**Trigger:** `scripts/drop.sh ../simgen/samples/order_unknown_key.txt`

**You should see:** `E_UNKNOWN_KEY: unknown transport key id 'TK99'`; the order
`FAILED`; the check happened *before* any card was generated.

**Respond:** the customer refers to a key we were never given, or one that was
never loaded. Keys are only ever exchanged through the agreed key ceremony
(split components, two people, the HSM). Never accept a key by e-mail or
chat, and never paste one into a ticket. Either the customer corrects the file
to a key we hold (`TK01`), or a change is raised to load `TK99`. Then resubmit:
a `FAILED` order may be sent again.

**Record:** incident, plus a change if a new key must be loaded.

### 4. Duplicate order (P4)

**Trigger:** `scripts/drop.sh ../simgen/samples/order_good.txt order_good_resend.txt`

**You should see:** `E_DUPLICATE_ORDER: ORD-20261019-0001 already DELIVERED`.

**Respond:** this is the system protecting the customer: the same order
generated twice would mean two cards with the same identity. Tell the
customer the original was delivered, with the file name and time from
`simgen status`. Ask whether they meant to send a *new* order, which needs a
new Order_ID and new ranges.

**Record:** incident or service request, resolved with no change.

### 5. Worker lost in the middle of an order (P2)

This one teaches problem management, because the first fix you think of is wrong.

**Trigger:**

```bash
scripts/drop.sh ../drills/samples/order_big.txt        # 300,000 cards, about 40 s
docker compose exec worker simgen status                # repeat until ORD-...-0007 is GENERATING
docker compose kill worker                              # the "node" dies
docker compose up -d --scale worker=2                   # Kubernetes would do this for you
scripts/folders.sh                                      # the claim is still in processing/
```

**You should see:** `order_big.txt@<dead worker id>` sitting in `processing/`
and the order stuck in `GENERATING`. After `--stale-after` (600 s by default)
a worker moves the claim back to the inbox (`claim_requeued`). To skip the
wait: `docker compose exec worker sh -c 'touch -d "1 hour ago" /data/processing/*'`.

Then the requeued file fails with
`E_DUPLICATE_ORDER: ORD-20261019-0007 already GENERATING`. The automatic retry
is blocked by the duplicate check, which is doing its job.

**Respond (the workaround):** confirm nothing was delivered for this batch
(`ls` the outbox: no `.out`, maybe a stale `.part`), mark the order failed with
a reason, and resubmit:

```sql
UPDATE orders SET status = 'FAILED', error_code = 'E_WORKER_LOST'
WHERE order_id = 'ORD-20261019-0007' AND status IN ('RECEIVED', 'GENERATING');
```

```bash
scripts/resubmit.sh order_big.txt
```

A manual database update is a change: write the change record first, even if
it is a pre-approved *standard change* with this exact SQL.

**Record:** incident for the stuck order, problem for the cause (no automatic
recovery of in-flight orders), change for the SQL. See
[`PRB-0001`](examples/PRB-0001-orders-stuck-after-worker-loss.md). The
permanent fix is a code change, and a good exercise: let a worker take over an
order whose status is `RECEIVED` or `GENERATING` when its claim was requeued,
and write the test first.

### 6. Database outage (P1)

**Trigger:**

```bash
docker compose up -d --scale worker=2
docker compose stop postgres
scripts/drop.sh ../drills/samples/order_dbdown.txt
docker compose ps                     # a worker is "Restarting"
docker compose logs worker --tail 5   # psycopg.OperationalError
```

**You should see:** a `worker_error` event with `E_INTERNAL` and the database
error, the file parked in `error/`, and the worker that took it restarting
over and over (it stops on purpose so it comes back with a fresh connection,
and cannot start while the database is gone).

**Respond:**

1. Restore the database: `docker compose start postgres`, wait for `healthy`.
2. Restart **all** workers: `docker compose restart worker`. A worker that sat
   idle during the outage still holds a dead connection and fails the first
   order it touches. When this drill was tested, the first resubmit landed on
   exactly that worker and was parked a second time.
3. Check the order in `simgen status`. Missing or `FAILED`: resubmit with
   `scripts/resubmit.sh order_dbdown.txt`. `RECEIVED` or `GENERATING`: use the
   drill 5 workaround first.
4. Tell affected customers when the outage started and ended.

**Record:** P1 incident with a timeline, and a problem with two causes to
find: why the database went down, and why workers don't reconnect on their
own (fix idea: check the connection before each order). Then drill 8: how
would you have known before a customer did?

### 7. EDI 997 rejection (P3)

**Trigger:**

```bash
cd ../edi-bridge
python3 -m edi_bridge validate samples/850_bad_control.x12
python3 -m edi_bridge inbound  samples/850_bad_control.x12 --out out/
cat out/997.x12
```

**You should see:** `ST_SE_CONTROL_MISMATCH` and `SE_SEGMENT_COUNT`, and a 997
with `AK5*R*3*4`: the set was rejected, so nothing reached the ERP.

**Respond:** read the file with `edi-bridge pretty`, find the exact segment,
and write the customer's EDI team a two-line explanation ("SE02 is 0002 but
ST02 is 0001; SE01 says 9 segments, the set has 7"). They fix their map and
resend. Our side has nothing to change.

**Record:** incident, resolved by partner resend.

### 8. Monitoring catches it first (P1 prevention)

**Trigger:** with Splunk running, create the alert from
[`ops-stack/splunk/README.md`](../ops-stack/splunk/README.md), then repeat
drills 2 and 6.

**You should see:** the alert fire within its schedule, before you looked.

**Respond:** add a second alert for "no `order_completed` in 30 minutes during
business hours" and a dashboard panel for orders past `due_at`. Write down
how long each failure took to notice with and without the alert.

**Record:** change (new monitoring), linked to the problem from drill 6.

### 9. Two overlapping orders at the same moment (P1 prevention)

**Trigger:** with two workers running, drop both files in the same second:

```bash
scripts/drop.sh ../drills/samples/order_race_a.txt & scripts/drop.sh ../drills/samples/order_race_b.txt; wait
```

**You should see:** exactly one order `DELIVERED`; the other `FAILED` with
`E_RANGE_OVERLAP ... overlaps order <the first one>`, even though the first
was still generating when the second arrived. The QC query that looks for
overlapping ranges (query 7 in `ops-stack/sql/oracle_practice.sql`) returns
no rows.

**Respond:** this is the control working, so treat it like drill 2: confirm
the clash in `allocations`, tell the customer, ask for a corrected file.
Then prove the control yourself: run the QC query and check both workers'
logs to see which one won.

**Record:** none for the drill. Read [`PRB-0002`](examples/PRB-0002-two-workers-same-range.md),
the problem record for what this drill did before the fix: both orders were
delivered with duplicate SIM identities.

## Reset

`docker compose --profile splunk down -v` deletes every volume (orders, files,
keys, logs) so you can run the drills again from a clean start.
