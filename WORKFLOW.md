# Workflow: one shift in a SIM data center, in about two hours

This runs the whole job once, start to finish. A carrier, MNO-NORTH, orders
500 physical SIMs and 1,000 eSIMs on one purchase order. You take that order
from the EDI message to the ship notice. Along the way you check the input
files, generate and QC the data, deliver it over SFTP, deal with a bad file,
answer your manager in SQL, check Splunk, handle a volume peak on Kubernetes,
and write the handover.

The times are a rough guide. Keep a notebook open and write down what you
see at each step. [`MISSIONS.md`](MISSIONS.md) goes deeper on each part once
you've done this run.

## What stands in for what

| At work | What it does | In this lab |
| --- | --- | --- |
| Profile generation platform (in-house) | Turns a carrier's input file into SIM and eSIM data | `simgen` workers |
| eSIM platform (SM-DP+) | Holds eSIM profiles until a phone downloads them | the `.smdp.csv` file and activation codes (no real SM-DP+) |
| HSMs and key ceremonies | Keep the keys | a LAB ONLY key file in a Docker volume or Kubernetes Secret |
| SFTP / managed file transfer | Files in from carriers, out to carriers and the factory | an SFTP container |
| Linux servers, Docker, microservices | Run everything | Linux containers in Docker Compose |
| Kubernetes | Runs and scales the services | `ops-stack/k8s` on OrbStack |
| Oracle Database | Order tracking and reporting | Oracle Database Free |
| Splunk | Dashboards and alerts | Splunk Enterprise in a container |
| EDI translator (e.g. Sterling Gentran) | Reads and writes X12 with carriers | `edi-bridge` (850, 997, 855, 856) |
| ERP (e.g. Infor SyteLine) | Customer orders | `erp_orders.json`, `co_header.csv`, `co_lines.csv` |
| SAP | Downstream business system | ORDERS05 IDoc XML |
| Ticketing (ServiceNow, Jira) | Incidents, problems, changes | the markdown templates in `drills/templates/` |
| Python and shell scripts | Checks and automation | `simgen`, `edi-bridge`, `ops-stack/scripts/` |

## Before you start (about 25 minutes, once)

On a Mac, install [OrbStack](https://orbstack.dev). It runs Docker and a
one-node Kubernetes cluster in one app. 16 GB of memory is comfortable;
Oracle and Splunk take about 2 GB each.

```bash
brew install python@3.12
brew install --cask orbstack
git clone https://github.com/Huxhs/telecom-data-ops-lab && cd telecom-data-ops-lab
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e "./simgen[dev]" -e "./edi-bridge[dev]"
```

Start the data center on Oracle, with Splunk and two workers:

```bash
cd ops-stack
export COMPOSE_FILE=compose.yaml:compose.oracle.yaml COMPOSE_PROFILES=splunk
docker compose up -d --build --scale worker=2
```

Oracle needs a minute or two to start and Splunk two or three. Splunk can
show `(unhealthy)` in `docker compose ps` even when it works; if
http://localhost:8000 loads, it's fine. Keep this
terminal in `ops-stack/` (call it the **ops** terminal). Open a second
terminal, run `source .venv/bin/activate` in the repo folder, and
`cd edi-bridge` (the **EDI** terminal). If you open a new ops terminal later,
run the `export` line again.

## 1. Start of shift: checks (10 min)

At work you start by reading the handover, then checking that the overnight
jobs ran and nothing is stuck. In the ops terminal:

```bash
docker compose ps                         # all Up; oracle and postgres "healthy"
scripts/folders.sh                        # anything in error/ or processing/?
docker compose exec worker simgen status  # every order and its state
docker compose exec worker df -h /data    # disk space on the file exchange
```

Write a one-line start note: what's running, anything open.

## 2. A purchase order arrives over EDI (15 min)

The carrier's 850 purchase order reaches the EDI translator. The translator
sends back a 997 (we received it) and an 855 (we accept it), and creates the
order in the ERP. The ERP passes it on to SAP. In the EDI terminal:

```bash
edi-bridge pretty samples/850_good.x12
edi-bridge inbound samples/850_good.x12 --out ../shift/
edi-bridge pretty ../shift/997.x12
edi-bridge pretty ../shift/855.x12
cat ../shift/erp_orders.json
edi-bridge sap ../shift/erp_orders.json --out ../shift/ --sender SYTELINE --receiver SAPCLNT100 --client 100
less ../shift/idoc_orders.xml
```

Write down the PO number and both lines: what product, how many.

## 3. The input files arrive (25 min)

The carrier sends one input file per line of the order. Before you run
anything, match each file to the ERP order. In the EDI terminal:

```bash
cat ../workflow/order_PO45001_uicc.txt
cat ../workflow/order_PO45001_esim.txt
```

Check Customer, PO_Ref, Type and Quantity against `erp_orders.json`. If
anything doesn't match, stop and ask the carrier. Never generate on a file
you haven't matched.

Now upload them the way the carrier does. In the ops terminal (password
`lab-only-sftp`):

```bash
sftp -P 2222 mno@localhost
sftp> cd exchange/inbox
sftp> put ../workflow/order_PO45001_uicc.txt
sftp> put ../workflow/order_PO45001_esim.txt
sftp> bye
```

Watch the workers pick them up, then check the result:

```bash
docker compose logs --tail 20 worker      # each worker prints its events
docker compose exec worker simgen status  # both DELIVERED?
```

Run QC again yourself before anything is released. This is the four-eyes
check: a second, separate run of the same checks.

```bash
docker compose exec worker simgen qc /data/outbox/MNO-NORTH_B26101901.out --keys /keys/lab-keys.json
docker compose exec worker simgen qc /data/outbox/MNO-NORTH_E26102012.out --keys /keys/lab-keys.json
```

Then pull the files back over SFTP:

```bash
mkdir -p ../shift/delivered && cd ../shift/delivered
sftp -P 2222 mno@localhost
sftp> cd exchange/outbox
sftp> ls
sftp> get MNO-NORTH_B26101901.out
sftp> get MNO-NORTH_E26102012.activation.csv
sftp> get MNO-NORTH_B26101901.summary.json
sftp> bye
cd ../../ops-stack
```

Look at what came out and who each file is for. In the lab every file lands
in `outbox/`; at work the perso and SM-DP+ files go to the factory and the
eSIM platform over their own secure channels, never to the carrier.

| File | Goes to | Holds |
| --- | --- | --- |
| `B26101901.out` | the carrier | one line per SIM; Ki and OPc encrypted with the carrier's transport key |
| `B26101901.perso.csv` | the factory | the same cards, keys encrypted with the factory key |
| `E26102012.smdp.csv` | the eSIM platform | profile data for download |
| `E26102012.activation.csv` | the carrier | one activation code per eSIM, printed as a QR code |
| `*.summary.json` | us | the shipment record for the 856 |

```bash
head -15 ../shift/delivered/MNO-NORTH_B26101901.out
head -3 ../shift/delivered/MNO-NORTH_E26102012.activation.csv
```

Anyone holding an activation code can download that eSIM, so codes and
MatchingIDs never go into logs or tickets.

## 4. The batch ships: send the 856 (5 min)

When the physical SIMs leave the building, the carrier gets an 856 ship
notice. eSIMs have no box, so they get no 856. In the EDI terminal:

```bash
edi-bridge asn ../shift/delivered/MNO-NORTH_B26101901.summary.json --out ../shift/ --carrier ABCD
edi-bridge pretty ../shift/856_B26101901.x12
```

## 5. Afternoon: a bad file (15 min)

The carrier sends another order, and its numbers overlap the batch you just
delivered. In the ops terminal:

```bash
scripts/drop.sh ../simgen/samples/order_overlap.txt
docker compose exec worker simgen status   # FAILED, E_RANGE_OVERLAP
scripts/folders.sh                         # the file and its .reason in error/
```

Now do what you'd do at work:

1. Copy `../drills/templates/incident.md` to `../shift/INC-0001.md` and fill
   it in: what happened, impact, priority, what you did.
2. Write the e-mail to the carrier in three sentences: which order, the exact
   clash, what you need back. Don't offer to change their numbers.
3. Compare with [`drills/examples/INC-0001-range-overlap.md`](drills/examples/INC-0001-range-overlap.md).

## 6. Your manager asks a question (15 min)

"How many cards did we deliver for MNO-NORTH today, and is anything at risk?"
Answer it from the database. In the ops terminal (password `LabOnly-Oracle-1`):

```bash
docker compose exec oracle sqlplus simgen@//localhost/FREEPDB1
```

Paste queries 1, 2, 4, 6 and 7 from [`ops-stack/sql/oracle_practice.sql`](ops-stack/sql/oracle_practice.sql)
one at a time, guessing the result before each one. Then write your own query
for cards delivered per customer, and answer your manager in one line.
`EXIT` leaves SQL*Plus.

## 7. Check Splunk (15 min)

Open http://localhost:8000 and log in as `admin` / `LabOnly-Splunk-1`.
Import the dashboard from [`ops-stack/splunk/README.md`](ops-stack/splunk/README.md)
and run:

```
index=main sourcetype=_json event=order_failed | stats count by error_code
index=main sourcetype=_json order_id="ORD-20261019-0001" | table _time event node
```

Your overlap from step 5 should be there. Then build the alert described in
the Splunk README.

## 8. A volume peak: Kubernetes (20 min)

Peaks come before phone launches and the holidays. You run more workers. In
OrbStack, turn on Kubernetes (Settings > Kubernetes). Then free some memory and
follow the Kubernetes section of [`ops-stack/README.md`](ops-stack/README.md)
from the ops terminal:

```bash
docker compose stop oracle splunk
```

Once your order is through, add these:

```bash
kubectl -n telecom-lab exec deploy/simgen-worker -- simgen status
kubectl -n telecom-lab describe pod <pod name>
kubectl -n telecom-lab get events --sort-by=.lastTimestamp
```

## 9. Handover (5 min)

Write up to ten lines for the next shift: what was delivered, what failed and
who it's waiting on (the carrier's corrected file), and anything to watch.
Save it as `../shift/handover.md`.

## Clean up

```bash
docker compose down -v                  # from ops-stack/, deletes the lab data
kubectl delete namespace telecom-lab
```

`shift/` is ignored by git, so your notes stay on your machine. Delete it
when you want a fresh run.

## What you can say after this

- "I took one purchase order from the EDI 850 to the 856 ship notice: the 997
  and 855 back, the ERP order and SAP IDoc, matching the input files,
  generation, QC, delivery over SFTP."
- "I handled a range overlap the way a data center should: stop, tell the
  carrier exactly where the clash is, log the incident."
- "I answered business questions from Oracle with SQL and checked the same
  events in Splunk."
- "I ran the workers on Kubernetes and scaled them for a peak."
