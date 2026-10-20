# INC-0001: MNO-NORTH order ORD-20261019-0002 rejected, ICCID range overlaps a delivered order

> Practice record from drill 2. The customer, data and times are from the lab.

| Field | Value |
| --- | --- |
| Priority | P2: one customer's order blocked, SLA at risk if not fixed today |
| Status | Resolved |
| Customer | MNO-NORTH (PO45002) |
| Order / batch / file | ORD-20261019-0002 / B26101902 / order_overlap.txt |
| Detected by | Splunk alert "simgen failures" (`order_failed`) |
| Opened | 2026-10-19 13:38 UTC |
| SLA due | 2026-10-20 13:38 UTC (24 h) |
| Assigned to | Data operations |

## Impact
200 UICC-TRIO-128K profiles for MNO-NORTH were not generated. No data left the
building: the overlap check runs before generation, and the outbox has no file
for batch B26101902. If the order had gone through, 200 cards would have shared
ICCIDs and IMSIs with cards already delivered in batch B26101901.

## Evidence
- `error/order_overlap.txt.reason`:
  `E_RANGE_OVERLAP: ICCID 8900101000000003013-8900101000000005000 overlaps order ORD-20261019-0001`
- `simgen status`: `ORD-20261019-0002 ... 200 FAILED E_RANGE_OVERLAP`
- `SELECT * FROM allocations WHERE order_id = 'ORD-20261019-0001'`:
  ICCID 8900101000000000019 to 8900101000000005000, IMSI 001010000000001 to 001010000000500
- The file asks for ICCID_Start 890010100000000301 and IMSI_Start 001010000000301,
  which fall inside that allocation (cards 301 to 500 of batch B26101901).

## Timeline (UTC)
- 13:38 alert fired; incident opened
- 13:41 confirmed the clash in the allocations table; nothing generated
- 13:45 e-mailed the MNO-NORTH order desk with the exact clash and asked for a corrected file
- 15:10 corrected file arrived over SFTP: ICCID_Start 890010100000000501, IMSI_Start 001010000000501
- 15:11 processed automatically; order DELIVERED, QC clean
- 15:20 customer confirmed receipt of MNO-NORTH_B26101902.out

## Actions taken
1. Did not change the ranges ourselves: the operator owns its numbering plan.
2. Sent the customer: "Order ORD-20261019-0002 asks for ICCIDs ...0301 to ...0500,
   which were delivered to you on 2026-10-19 in batch B26101901 (order ORD-20261019-0001).
   Please send a corrected file with a new start range."
3. Watched the corrected file go through. It keeps the same Order_ID, which is
   allowed because the first attempt is `FAILED`.

## Resolution
Corrected file processed at 15:11, order DELIVERED, `simgen qc` reported no
problems, customer confirmed receipt. Within SLA.

## Follow-up
- Problem record: not yet. Open one if MNO-NORTH sends another overlapping range
  this quarter (the cause would be on their side: range planning).
- Suggested to the account manager: send MNO-NORTH a monthly report of allocated ranges.
