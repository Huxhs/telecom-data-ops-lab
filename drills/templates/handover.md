# Handover: <team>, <date> <shift> (<start>-<end> UTC), <your name>

DELIVERED
- <PO / customer>: <batch> (<qty> <type>). QC passed, files on SFTP; <EDI documents sent>.
- <856 or other shipping notice sent, with its ID>

INCIDENTS
- <INC-number> <priority>, <one-line cause> on <order ID>: <RESOLVED hh:mm | OPEN, waiting on who>.

OPEN / WATCH
- <what is left> TODO <who, by when>: <the next action>.
- <false alarms the next shift should ignore, and why>

<!--
Rules, so the next person can scan it in 30 seconds:
1. Same three headings every day: done, broke, still open.
2. Every item has an ID (PO, batch, order, ticket) so it can be searched in Splunk, the database or the ticket tool.
3. Every incident has a state (RESOLVED with the time, or OPEN and waiting on whom) and a one-line cause.
4. Every open item has a next action and an owner.
5. Warn about false alarms so nobody loses an hour on them.
6. No secrets, keys, activation codes or MatchingIDs. No story: the detail is in the ticket.

Example from my lab run, 2026-10-20:

# Handover: data ops, 2026-10-20 evening (23:00-01:20 UTC), Nick

DELIVERED
- PO45001 / MNO-NORTH: B26101901 (500 SIM) and E26102012 (1,000 eSIM). QC passed twice, files on SFTP; 997 + 855 sent, ERP EDIPO45001, SAP IDoc out.
- 856 sent for B26101901 (SHB26101901, carrier ABCD). eSIMs: activation file delivered, no 856 needed.
- ORD-20261019-0002 / PO45002 (200 SIM, B26101902): delivered 00:16 after a corrected file (INC-0001).

INCIDENTS
- INC-0001 P2, range overlap on ORD-20261019-0002: RESOLVED 00:17, carrier sent a new start range.
- 23:08 Oracle down, workers restart-looping (Docker Desktop took over the docker engine). Fixed 23:09, no orders affected. Keep Docker Desktop closed.

OPEN / WATCH
- error/ has 3 files: 2 duplicate PO45001 uploads (rejected, already delivered) + the original overlap file. TODO next shift: tell MNO-NORTH they sent PO45001 twice, then archive all 3.
- Worker health panel shows 2 dead worker names from the outage; ignore. New Splunk alert "simgen failures" is live (every 15 min).
-->
