# PRB-0002: two workers could deliver overlapping orders at the same time

> Practice record, written from what the lab actually did on 2026-10-20.

| Field | Value |
| --- | --- |
| Status | Closed (fixed and verified) |
| Linked incidents | Found in testing, before any delivery to a customer |
| Owner | Data operations |
| Opened | 2026-10-20 |
| Closed | 2026-10-20 |

## How it was found
While testing the stack on Oracle with two workers, two overlapping orders
were dropped a second apart. Both were `DELIVERED`. The QC query for
overlapping ranges (query 7 in `ops-stack/sql/oracle_practice.sql`) returned
a row: ORD-20261019-0002 shared ICCIDs and IMSIs with ORD-20261019-0001.
In production that is a P1: duplicate SIM identities in the field.

## Problem statement
The overlap check passed for two orders that overlapped, whenever they were
processed at the same time by different workers.

## Root cause analysis
1. Why were both delivered? Each worker's overlap check found no clash.
2. Why no clash? The check reads the `allocations` table, and neither order
   was in it yet.
3. Why not? Ranges were written to `allocations` only after generation and QC,
   seconds or minutes after the check.
4. Why did that matter? With one worker, orders run one after another, so the
   gap never showed. With two, both checks ran inside the same gap.

**Root cause:** check-then-act race. The check and the write were separate
steps with time between them, and nothing stopped a second worker in between.

## Fix (change)
- `OrderDB.reserve()` checks for a clash and inserts the ranges in one
  transaction under a table lock (`LOCK TABLE allocations IN EXCLUSIVE MODE`
  on PostgreSQL and Oracle, `BEGIN IMMEDIATE` on SQLite). A second worker
  waits for the lock, then sees the first order's ranges.
- Ranges are reserved before generation, not after QC.
- A failed order releases its ranges (`OrderDB.release()`), so a corrected
  file can use them.
- New tests: an order still generating blocks an overlapping one, six threads
  racing for one range leave exactly one winner, and a failed order frees its
  numbers. With the lock removed the race test fails.

## Verification
Re-ran the same back-to-back drop (drill 9) on PostgreSQL and on Oracle with
two workers: one order delivered, the other `FAILED` with `E_RANGE_OVERLAP`,
and query 7 returned no rows.

## Lessons
- Test with the number of workers production runs, not one.
- Keep a detective control (the QC query) even when a preventive control
  (the lock) exists: the query is what caught this.
