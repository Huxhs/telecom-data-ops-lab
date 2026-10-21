# Lab guide: one shift in a SIM data center

This is my write-up of running [`WORKFLOW.md`](../WORKFLOW.md) start to finish on
my own MacBook Pro (8 GB, OrbStack) on the evening of 20 October 2026. I took
one purchase order from the EDI 850 to the 856 ship notice, handled a bad file
and an outage along the way, answered a question in Oracle SQL, checked the
same events in Splunk and ran the workers on Kubernetes. The screenshots are
from that run. Mistakes I made are left in, with what I learned from them.

Times are UTC unless I say otherwise (Toronto was UTC-4 that night).

## The setup

| At work | In my lab |
| --- | --- |
| Profile generation platform | `simgen` workers (Python, in Docker) |
| SFTP / managed file transfer | an SFTP container on port 2222 |
| Oracle Database | Oracle Database Free 23 in a container |
| Splunk | Splunk Enterprise 9.4 in a container |
| Kubernetes | the one-node cluster built into OrbStack |
| EDI translator, ERP, SAP | `edi-bridge` (850, 997, 855, 856 and an ORDERS05 IDoc) |
| Ticketing | markdown tickets from `drills/templates/` |

My Mac only has 8 GB of memory, so I ran the heavy parts one at a time:
Oracle for steps 1 to 6, then Splunk instead of Oracle for step 7, then
Kubernetes on its own for step 8.

## 0. Getting ready

I pushed the lab to GitHub, ran both test suites (35 tests for `simgen`, 111
for `edi-bridge`) and built the stack with two workers.

![Pushing the repo to GitHub](img/00-github-push.png)
![Tests passing and the stack building](img/01-tests-and-build.png)

## 1. Start of shift, and my first incident

When I went to run the morning checks, nothing was running. I had opened
Docker Desktop to look at my containers, and that switched my `docker`
command over to Docker Desktop instead of OrbStack. The Oracle container shut
down at 23:08:09 and both workers went into a restart loop, because they could
no longer find the database (`socket.gaierror: Name or service not known`).

![Workers restarting](img/02-workers-restarting.png)
![Worker and Oracle logs](img/03-troubleshooting-workers.png)

What I did: read the worker logs first instead of guessing, saw the database
name could not be resolved, realised Docker Desktop had taken over the
`docker` command, switched back with `docker context use orbstack` and started
the stack again. Docker Desktop now stays closed while the lab runs.

Then the real start-of-shift checks: every service up, no files stuck in
`error/` or `processing/`, every order's state, and disk space.

![Morning checks](img/04-morning-checks.png)

**What I learned:** when something breaks, start from the logs and the error
text. "Name or service not known" pointed at the network/engine, not at my code.

## 2. A purchase order arrives over EDI

MNO-NORTH sent purchase order PO45001 as an X12 850: line 1 is 500 physical
SIMs (UICC-TRIO-128K at $0.85), line 2 is 1,000 eSIM profiles (ESIM-PROF-A at
$0.40), due 2026-11-02, ship to NDC01.

![The 850 purchase order](img/05-the-850.png)

How I read it: ISA/GS/ST are the envelopes (who sent it, to whom, what kind of
document), BEG has the PO number, DTM the date, N1 the ship-to, PO1 one line
per product and CTT the line count. The trailers at the end (SE, GE, IEA) have
to match their headers, or the file is rejected.

`edi-bridge inbound` then did what the translator does at work:

- sent a **997** back (we received your file and it is valid),
- sent an **855** (we accept the order: BAK02=AC, each line IA, accepted),
- created ERP order EDIPO45001,
- and passed it to SAP as an ORDERS05 IDoc (AG = sold-to MNO-NORTH, WE = ship-to NDC01).

![The 997 acknowledgment](img/06a-997-ack.png)
![The 855 order acknowledgment](img/06b-855-ack.png)

## 3. The input files arrive

The carrier sends one input file per order line. Before generating anything I
matched both files to the ERP order: customer, PO_Ref, type and quantity. They
matched (ORD-20261019-0001, batch B26101901, 500 SIMs and ORD-20261020-0012,
batch E26102012, 1,000 eSIMs). Rule: never generate on a file you haven't matched.

![Matching the files to the order](img/07-match-files-to-order.png)

I uploaded them over SFTP the way the carrier would. The first time you
connect, SFTP asks you to trust the server's host key.

![SFTP host key prompt](img/08a-sftp-hostkey.png)
![SFTP connected](img/08b-sftp-connected.png)
![Uploading the two files](img/08-sftp-upload.png)

**My mistake, and why it turned out useful:** I pasted all the sftp commands
at once. The last line (`bye`) never got an Enter, so I was still inside sftp
and pasted everything again, which uploaded both files a second time.

![Pasting into sftp by mistake](img/08c-pasted-into-sftp-by-mistake.png)

The system handled it the way a data center should. The first pair was
generated; the second pair was refused with `E_DUPLICATE_ORDER: ... already
DELIVERED` and moved to `error/` with a `.reason` file. Nothing was made twice
and no numbers were used twice. My part was to spot it, confirm the real
orders were fine, and (at work) tell the carrier they sent the files twice.
Lesson: paste one line at a time into interactive prompts.

![Both orders delivered, duplicates in error/](img/09a-delivered-and-duplicates.png)

Then I ran QC myself as a four-eyes check, a second independent run of the
same checks the worker already did. QC checks the card count, that every ICCID
is 19 digits with a valid Luhn check digit, that IMSIs are 15 digits, no
duplicates, PIN/PUK lengths, and it decrypts each Ki and recomputes the OPc to
prove they belong together. For eSIMs it also checks every activation code.

![Four-eyes QC passed](img/09b-four-eyes-qc.png)

I pulled the files back over SFTP as the carrier would, and read the output
file. Each card is one line: ICCID, IMSI, PIN1, PUK1, PIN2, PUK2, Ki, OPc and
ADM1. The Ki and OPc are encrypted with the carrier's transport key (TK01), so
the file is useless to anyone without that key. (These are lab keys; real ones
never go in a screenshot or a ticket.)

![Delivered over SFTP](img/10-delivered-over-sftp.png)
![The output file](img/11-output-file.png)

## 4. The batch ships: the 856

When the physical SIMs leave the building the carrier gets an 856 ship notice:
shipment SHB26101901, one carton, carrier ABCD, bill of lading BOLB26101901,
shipped 2026-10-20 to MNO-NORTH. eSIMs get no 856 because there is no box;
their "delivery" is the profiles on the SM-DP+ server and the activation codes
(the text inside the QR code) sent to the carrier. Both lines get billed on an
810 invoice later.

![The 856 ship notice](img/12-the-856.png)

## 5. Afternoon: a bad file

The carrier sent another order (ORD-20261019-0002, 200 SIMs) that started at
ICCID ...0301. I had already delivered cards ...0001 to ...0500 that day, so
cards 301 to 500 would have had the same numbers as cards already at the carrier.

![Order failed with E_RANGE_OVERLAP](img/13-range-overlap.png)
![The reason file](img/14-overlap-reason.png)

What I did, and what I did not do:

1. Checked the order state and read the exact reason.
2. Confirmed nothing was generated and nothing left the building.
3. Did **not** change the start numbers myself. The carrier owns its numbering plan.
4. Wrote incident INC-0001 (P2) and e-mailed the carrier the exact clash and
   asked for a corrected file with a new start range.

Playing the carrier, I sent a corrected file starting at ...0501. It went
through under the same order number (allowed, because the first attempt
failed), was DELIVERED and passed my QC. Then I closed the ticket.

![Corrected file delivered, QC passed](img/15-corrected-file-delivered.png)
![Incident ticket, part 1](img/16a-incident-ticket.png)
![Incident ticket, part 2](img/16b-incident-ticket.png)

**What I learned:** Resolution is short: what restored service and how I
proved it. Evidence is quoted exactly with where it came from. And in my
first draft I wrote that the clash was the Ki; it was the ICCID and IMSI.
Every card gets its own random Ki.

## 6. My manager asks a question (Oracle SQL)

"How many cards did we deliver for MNO-NORTH today, and is anything at risk?"

![Orders by status, open orders vs SLA](img/17a-oracle-status-and-sla.png)
![Failures by error code, ranges per customer](img/17b-oracle-failures-and-ranges.png)
![The overlap check returns no rows](img/17c-oracle-overlap-check.png)

The overlap query returning **no rows** is the important result: no SIM
identity is used twice. The failures query also returned nothing, which
surprised me. The orders table only keeps each order's current state, and when
the corrected file came in the failed row was replaced. The history of the
failure lives in the event log (step 7). In production I'd add an order
history table so the audit trail is in the database too.

My own query came back empty the first time:

![My first try](img/18a-my-query-first-try.png)

I typed `MNO_NORTH` with an underscore; the data says `MNO-NORTH`. SQL compares
text exactly, and SUM over zero rows is NULL, so I got a blank instead of an
error. I also learned that `TO_CHAR(SYSDATE, 'YYYY-MM-DD')` takes a pattern, and
that "today" depends on the time zone: the database runs in UTC, so my first two
orders (7:35 PM Toronto) counted as the day before and only the 200-card order
counted as "today".

```sql
SELECT customer, COUNT(*) AS orders, SUM(quantity) AS cards_delivered
FROM orders WHERE status = 'DELIVERED' GROUP BY customer;
```

My answer: *MNO-NORTH: 1,700 cards delivered across 3 orders (500 SIM, 1,000
eSIM, 200 SIM). Nothing open or at risk. One order was rejected for a range
overlap and the corrected file was delivered at 00:16 UTC.*

## 7. Splunk

I stopped the workers and Oracle and started Splunk. The first boot takes a few
minutes and the page dropped once while it was still setting up; I checked the
container state and logs before assuming it was broken.

![Swapping Oracle for Splunk](img/19a-swap-oracle-for-splunk.png)
![Every event from the shift](img/20-splunk-all-events.png)
![The history of the overlap order](img/21-one-order-history.png)
![Failures by error code](img/22-failures-by-code.png)

Things I could read off it:

- The overlap was caught 47 ms after the file arrived, with no
  `generation_started` in between: proof nothing was generated.
- The duplicates have only an `order_rejected` and no `order_received`; they
  were stopped at the door.
- The `worker_started` events at 23:04 and 23:09 are my outage from step 1.
- Splunk shows what the system did. Tickets live in the ticketing tool; you
  line the two up by order number and time.

I imported the dashboard and built an alert on failed, rejected or crashed
orders, every 15 minutes over the last 15 minutes. The window has to match the
schedule, or the same old failures alert again every run.

![Dashboard, top](img/23a-dashboard-top.png)
![Dashboard, bottom](img/23b-dashboard-bottom.png)
![The alert](img/24-alert.png)

A gap I found: the worker health panel lists 4 workers, but only 2 run. It
only sees `worker_started` events, so workers replaced during my outage still
show. A heartbeat event every minute would show which workers are alive now.

## 8. A volume peak: Kubernetes

I stopped the Compose stack and turned on Kubernetes in OrbStack. Postgres
stands in for Oracle here because it is much smaller; the lab's code works
with both. The keys went into a Kubernetes Secret and I deleted the local file.

![Postgres and a worker running](img/25-k8s-pods-running.png)
![An order processed on the cluster](img/26-order-on-k8s.png)

Then I scaled to 3 workers for the peak and deleted one on purpose. I guessed
I would see 2. I saw 3: Kubernetes noticed the Deployment wanted 3, saw 2 and
started a new pod by itself. That is what "desired state" means.

![Scaled to 3, one killed, replaced](img/27-scale-and-self-heal.png)

## 9. Handover

I scaled the workers back to one after the peak and wrote the handover for the
next shift. I learned the shape from a model handover, which is now in the repo
as [`drills/templates/handover.md`](../drills/templates/handover.md): three
headings (done, broke, still open), an ID on every item, a state on every
incident, an owner and next action on every open item, and a warning about
false alarms. No secrets and no story; the detail is in the ticket.

![The handover](img/29-handover.png)

```
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
```

## What I can say after this

- I took one purchase order from the EDI 850 to the 856: the 997 and 855 back,
  the ERP order and SAP IDoc, matching the input files, generation, QC and
  delivery over SFTP.
- I handled a range overlap the way a data center should: stop, tell the
  carrier exactly where the clash is, never change their numbers, log the
  incident and close it with proof.
- I recovered from a database outage by reading the logs, and noticed when a
  duplicate file was rejected.
- I answered a business question from Oracle with SQL, and learned why the
  same events also need to be in Splunk.
- I ran the workers on Kubernetes, scaled them and watched a killed pod get replaced.
