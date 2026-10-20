-- Oracle SQL practice on the order-tracking database.
--
-- Run the stack on Oracle first:
--   docker compose -f compose.yaml -f compose.oracle.yaml up -d --build
-- then open SQL*Plus inside the Oracle container (password: ORACLE_PASSWORD,
-- default LabOnly-Oracle-1):
--   docker compose -f compose.yaml -f compose.oracle.yaml exec oracle sqlplus simgen@//localhost/FREEPDB1
-- and paste one query at a time. Read each query, predict the result, then run it.
--
-- Timestamps are stored as UTC text like 2026-10-19T13:38:00Z, so the queries
-- convert them with TO_TIMESTAMP and this format mask:
--   'YYYY-MM-DD"T"HH24:MI:SS"Z"'

SET LINESIZE 200
SET PAGESIZE 50

-- 1. The morning picture: orders and cards by status
SELECT status, COUNT(*) AS orders, SUM(quantity) AS cards
FROM orders
GROUP BY status
ORDER BY orders DESC;

-- 2. Open orders and hours left on their SLA (most urgent first)
SELECT order_id, customer, status,
       ROUND((CAST(TO_TIMESTAMP(due_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS DATE)
              - CAST(SYS_EXTRACT_UTC(SYSTIMESTAMP) AS DATE)) * 24, 1) AS hours_left
FROM orders
WHERE status NOT IN ('DELIVERED', 'FAILED')
ORDER BY hours_left;

-- 3. Orders past their due time and not delivered
SELECT order_id, customer, status, due_at
FROM orders
WHERE status <> 'DELIVERED'
  AND TO_TIMESTAMP(due_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') < SYS_EXTRACT_UTC(SYSTIMESTAMP);

-- 4. Failures by error code
SELECT error_code, COUNT(*) AS failures
FROM orders
WHERE status = 'FAILED'
GROUP BY error_code
ORDER BY failures DESC;

-- 5. The ten slowest delivered orders, in minutes
SELECT order_id, customer, quantity,
       ROUND((CAST(TO_TIMESTAMP(completed_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS DATE)
              - CAST(TO_TIMESTAMP(received_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS DATE)) * 24 * 60, 1) AS minutes
FROM orders
WHERE status = 'DELIVERED'
ORDER BY minutes DESC
FETCH FIRST 10 ROWS ONLY;

-- 6. Number ranges used per customer
SELECT o.customer, a.kind,
       MIN(a.first_value) AS first_used, MAX(a.last_value) AS last_used,
       COUNT(*) AS orders
FROM allocations a
JOIN orders o ON o.order_id = a.order_id
GROUP BY o.customer, a.kind
ORDER BY o.customer, a.kind;

-- 7. QC: overlapping ranges between two orders. This must return no rows;
--    a row here means duplicate SIM identities.
SELECT a1.order_id, a2.order_id AS clashes_with, a1.kind,
       a1.first_value, a1.last_value, a2.first_value AS other_first, a2.last_value AS other_last
FROM allocations a1
JOIN allocations a2
  ON a1.kind = a2.kind
 AND a1.order_id < a2.order_id
 AND a1.first_value <= a2.last_value
 AND a1.last_value >= a2.first_value;

-- 8. Orders that never got a range (LEFT JOIN, keep the ones with no match)
SELECT o.order_id, o.status, o.error_code
FROM orders o
LEFT JOIN allocations a ON a.order_id = o.order_id
WHERE a.order_id IS NULL;

-- 9. Stuck orders: received or generating for more than 15 minutes (drill 5)
SELECT order_id, status, received_at
FROM orders
WHERE status IN ('RECEIVED', 'GENERATING')
  AND TO_TIMESTAMP(received_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
      < SYS_EXTRACT_UTC(SYSTIMESTAMP) - INTERVAL '15' MINUTE;

-- 10. The drill 5 standard change. Only with a change record, and only after
--     checking that nothing was delivered for the batch. Oracle does not
--     auto-commit in SQL*Plus: check the row count, then COMMIT (or ROLLBACK).
-- UPDATE orders SET status = 'FAILED', error_code = 'E_WORKER_LOST'
--  WHERE order_id = 'ORD-20261019-0007' AND status IN ('RECEIVED', 'GENERATING');
-- COMMIT;

-- Oracle vs PostgreSQL, the differences you will hit first:
--   current UTC time   SYS_EXTRACT_UTC(SYSTIMESTAMP)      now() AT TIME ZONE 'utc'
--   15 minutes         INTERVAL '15' MINUTE                interval '15 minutes'
--   first N rows       FETCH FIRST 10 ROWS ONLY (both; older Oracle: WHERE ROWNUM <= 10)
--   null default       NVL(x, 'none') or COALESCE           COALESCE
--   one-row table      SELECT SYSDATE FROM dual            SELECT now()
--   text type          VARCHAR2                            varchar / text
--   transactions       explicit COMMIT                     autocommit in psql
