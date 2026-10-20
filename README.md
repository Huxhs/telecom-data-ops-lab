# Telecom Data Operations Lab

A working miniature of the data side of a SIM and eSIM manufacturer: a mobile
operator sends an order, a data center generates a unique identity and key set
for every card, checks it, sends it back over SFTP, and reports the shipment
to the customer's ERP over EDI and on to SAP as IDocs, while operations
watches it all in Splunk and handles incidents the ITIL way. Physical SIMs
get a perso file for the factory; eSIMs get an SM-DP+ file and activation
codes. The order database runs on PostgreSQL or Oracle.

```
 Mobile operator                         SIM data center
 ───────────────                         ─────────────────────────────────────────────
 850 purchase order ───────────────────▶ edi-bridge ──▶ 997 + 855 back, ERP order ──▶ SAP IDoc
 input file ───────────── SFTP ────────▶ inbox
                                           │
                                           ▼
                                         simgen workers (1..n) ──▶ PostgreSQL or Oracle (orders, ranges)
                                           │          └──────────▶ events.log ──▶ Splunk
                                           ▼
 output file ◀─────────── SFTP ───────── outbox ──▶ perso file (factory) or SM-DP+ file + activation codes (eSIM)
 856 ship notice ◀────────────────────── edi-bridge ◀── shipment summary
```

All data is synthetic: ITU test network numbers (MCC 001, MNC 01), random
LAB ONLY keys, made-up partners and part numbers.

## What is in here

| Folder | What it is | Tests |
| --- | --- | --- |
| [`simgen/`](simgen/) | SIM and eSIM profile generation: input file parsing, ICCID (Luhn) and IMSI ranges, Ki and OPc (Milenage OPc, checked against the 3GPP TS 35.208 test vector), transport-key encryption, output, perso and SM-DP+ files, SGP.22 activation codes, QC, order tracking on SQLite, PostgreSQL or Oracle with locked range reservation, JSON events, a multi-worker folder watcher | 35 |
| [`edi-bridge/`](edi-bridge/) | ANSI X12 toolkit: reads 850 purchase orders, validates envelopes, answers with 997 and 855, maps to an ERP import, passes orders to SAP as ORDERS05 IDocs, builds an 856 ship notice from simgen's output | 111 |
| [`ops-stack/`](ops-stack/) | Docker Compose stack (PostgreSQL or Oracle, SFTP, scalable workers, Splunk), Kubernetes manifests, a Splunk dashboard and Oracle SQL practice | |
| [`drills/`](drills/) | Nine break-it-on-purpose incident drills with ITIL incident, problem and change templates and worked examples | |
| [`WORKFLOW.md`](WORKFLOW.md) | One order through the whole lab in about two hours, like one shift on the job | |
| [`MISSIONS.md`](MISSIONS.md) | The lab as a data engineer's week: morning checks, orders, eSIM, bad files, EDI, Oracle, Kubernetes, incidents, handover | |

## How it maps to a data-generation operations role

| Day-to-day duty | Where to practise it |
| --- | --- |
| Generate SIM/eSIM profile data from operator input files | `simgen generate`, `simgen/samples/` (including `order_esim.txt`) |
| Check output quality before release | `simgen qc` (row counts, Luhn, duplicates, key/OPc consistency) |
| Exchange files with customers securely | SFTP in `ops-stack`, `.part` uploads, `error/` with `.reason` files |
| Monitor jobs, SLAs and failures | `ops-stack/splunk/` dashboard and alerts |
| Run and scale the platform | Docker Compose `--scale`, Kubernetes Deployment, stale-claim recovery |
| Query and fix order data | `orders` and `allocations` on PostgreSQL or Oracle, `ops-stack/sql/oracle_practice.sql` |
| EDI with customers and the ERP | `edi-bridge`: 850 in, 997/855 out, SAP ORDERS05 IDocs, 856 from the real batch |
| Incidents, problems and changes | `drills/` |
| Work in a secure environment | keys never logged (the logger refuses), file mode 600, transport encryption, localhost-only ports, no keys in tickets |

## Quick start

```bash
# Python projects (3.11+)
cd simgen     && python3 -m pip install -e ".[dev]" && python3 -m pytest -q && cd ..
cd edi-bridge && python3 -m pip install -e ".[dev]" && python3 -m pytest -q && cd ..

# One order end to end on your laptop
simgen init-keys lab-keys.json
simgen generate simgen/samples/order_good.txt --out out --keys lab-keys.json
simgen qc out/MNO-NORTH_B26101901.out --keys lab-keys.json
edi-bridge asn out/MNO-NORTH_B26101901.summary.json --out out/edi --carrier ABCD
edi-bridge pretty out/edi/856_B26101901.x12

# The data center
cd ops-stack && docker compose up -d --build --scale worker=2
```

Then follow [`ops-stack/README.md`](ops-stack/README.md) and the drills.

## Where to start

Start with [`WORKFLOW.md`](WORKFLOW.md): one purchase order through every
part of the lab in about two hours, from the EDI 850 to the 856 ship notice,
with a bad file, an Oracle report, Splunk and a Kubernetes peak along the way.
It also maps each part of the lab to the system it stands in for at work.

Then follow [`MISSIONS.md`](MISSIONS.md). It walks through the lab in the order a
data engineer meets things on shift, from morning checks to the handover, and
links to the exercises and drills as it goes. Progress is tracked in
[`PROGRESS.md`](PROGRESS.md).

## How this was built

I designed this lab as a study project for telecom data operations and built
it with an AI coding assistant. I am working through the exercises and drills
myself and ticking them off in `PROGRESS.md`; the incident records in
`drills/` come from running the drills on the live stack.

## Limits, on purpose

- Real input and output file layouts differ by operator and vendor; this one is simplified.
- Real keys live in a hardware security module (HSM) and are exchanged in a
  key ceremony. Here they are a LAB ONLY JSON file.
- The Kubernetes manifests are written for a one-node lab cluster. Running
  them is one of the exercises.
- X12 4010 only, and no TA1; see `edi-bridge/README.md`.
