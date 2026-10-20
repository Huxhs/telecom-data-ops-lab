# simgen: SIM profile generation lab

simgen turns an operator's **input file** into the data a SIM card needs, the
way a SIM vendor's data center does: one unique ICCID, IMSI, secret key set
and PIN set per card, an encrypted **output file** for the operator, a
**perso file** for the factory, and a quality check before anything is
released. Every step is tracked in a database and logged as JSON for Splunk.

> **Synthetic data only.** Numbers use the ITU test network (MCC 001, MNC 01)
> and keys are random LAB ONLY keys. Real input and output file layouts differ
> by operator and vendor, and real keys live in an HSM, not a JSON file.

## Quick start

```bash
cd simgen
python3 -m pip install -e ".[dev]"
python3 -m pytest -q

simgen init-keys lab-keys.json                     # LAB ONLY keys, file mode 600
simgen generate samples/order_good.txt --out out   # writes .out, .perso.csv, .summary.json
simgen qc out/MNO-NORTH_B26101901.out --keys lab-keys.json
simgen status                                      # orders and their status
simgen generate samples/order_overlap.txt --out out  # fails: E_RANGE_OVERLAP
```

Run `simgen watch --root data` to process files dropped into `data/inbox`
continuously, as the Docker stack does.

The order database is SQLite by default. Set `DATABASE_URL` to use a server:
`postgresql://user:password@host:5432/orders` or
`oracle://user:password@host:1521/FREEPDB1` (install the extras first:
`pip install -e ".[postgres,oracle]"`).

## The pipeline

1. **Parse and validate** the input file (`inputfile.py`): required fields,
   number formats, quantity limits, ranges that fit their digit width.
2. **Refuse duplicates**: an Order_ID already delivered is rejected.
3. **Reserve ranges** (`db.py` allocations table). The overlap check and the
   reservation run under one table lock, so two workers can never both claim
   the same numbers. Two orders sharing an ICCID or IMSI would put duplicate
   SIMs in the field. A failed order releases its ranges.
4. **Generate** (`generator.py`): ICCID with Luhn check digit and IMSI
   (`identifiers.py`), a random 128-bit Ki per card, OPc derived from Ki and
   the operator's OP with the Milenage formula (`keys.py`, tested against the
   3GPP TS 35.208 test vector), PUKs and ADM codes.
5. **Write** the output file (Ki and OPc encrypted with the operator's
   transport key) and the perso file (encrypted with the factory key). Files are
   written to a temporary name and renamed, so nobody reads half a file.
6. **QC** the output file: row count, Luhn, lengths, duplicates, and it
   decrypts every Ki and re-derives OPc to prove they belong together.
7. **Release**: write the shipment summary (input for `edi-bridge asn`) and
   mark the order DELIVERED.

Error codes you will see: `E_FORMAT`, `E_TRUNCATED`, `E_MISSING_FIELD`,
`E_BAD_VALUE`, `E_BAD_QUANTITY`, `E_BAD_ICCID`, `E_BAD_IMSI`,
`E_RANGE_OVERFLOW`, `E_DUPLICATE_ORDER`, `E_RANGE_OVERLAP`, `E_UNKNOWN_KEY`,
`E_WRITE`, `E_QC`, `E_INTERNAL`.

## eSIM orders

An eSIM has no card to personalize in a factory. The phone downloads its
profile over the air from an **SM-DP+** server (GSMA SGP.22). For eSIM orders
simgen prepares the data that server needs.

An order is eSIM when its `Type:` starts with `ESIM`. It needs one more header
field, `SMDP_Address:`, the SM-DP+ hostname (see `samples/order_esim.txt`).
eSIM orders write no perso file. They write:

| File | For | What is in it |
| --- | --- | --- |
| `.out` | operator | Same as for physical SIMs |
| `.smdp.csv` | SM-DP+ | ICCID, IMSI, KI and OPC (encrypted with the perso key), PIN1, PUK1, PIN2, PUK2, ADM1, MATCHING_ID |
| `.activation.csv` | operator | ICCID, MATCHING_ID, ACTIVATION_CODE (no keys) |

The activation code is `LPA:1$<SMDP_Address>$<MatchingID>`, for example
`LPA:1$smdp.lab.example$7KQ2M-0XW4R-B9T1C-H3ZP8`. The operator prints it as a
QR code. The phone scans it, contacts the SM-DP+ and downloads the profile the
MatchingID points to. Anyone with the code can download that profile, so
MatchingIDs never go into the event log or the summary.

QC also checks that no MatchingID repeats, every activation code has this
format and the order's SM-DP+ address, and the activation file has exactly the
ICCIDs of the `.out` file. Try it:

```bash
simgen generate samples/order_esim.txt --out out   # writes .out, .smdp.csv, .activation.csv, .summary.json
```

## Files

| File | What it does |
| --- | --- |
| `identifiers.py` | Luhn check digit, ICCID and IMSI ranges |
| `keys.py` | OPc derivation, transport encryption, LAB ONLY key file |
| `inputfile.py` | Input file parser and validation rules |
| `generator.py` | The pipeline, output, perso and eSIM files, QC |
| `db.py` | Order tracking in SQLite, PostgreSQL or Oracle; range reservation |
| `events.py` | JSON event log for Splunk (refuses to log secrets) |
| `watcher.py` | Folder watcher; workers claim files by atomic rename |
| `cli.py` | `simgen` command |

## Lab exercises (do these yourself)

1. Read `samples/order_good.txt` and the generated `.out` file side by side.
   Explain every header line and every column out loud.
2. Compute one ICCID check digit by hand with the Luhn steps, then check it
   with `python3 -c "from simgen.identifiers import luhn_check_digit as l; print(l('890010100000000001'))"`.
3. Add a new required field `Profile_Version` to the input file. Update the
   parser, the samples and the tests.
4. Add a **release sample** mode: `simgen generate --sample 10` generates 10
   profiles under batch `<Batch>-RS` without consuming the production range.
5. Write a QC rule that fails if more than 1% of PUK1 values are repeated.
6. Read `OrderDB.reserve()`. Then break it on purpose: comment out the
   `BEGIN IMMEDIATE` line, run `python3 -m pytest -q -k parallel` a few times
   and watch it fail. Explain in two sentences why the check and the insert
   must sit inside one locked transaction. (This race was found while testing
   on Oracle with two workers; see `drills/examples/PRB-0002`.)
7. Run the stack on Oracle (`ops-stack/compose.oracle.yaml`) and work through
   `ops-stack/sql/oracle_practice.sql`. Then list every place `db.py` treats
   Oracle differently from PostgreSQL and say why.
8. Time 100,000 cards. Where does the time go? Write down a capacity estimate
   in cards per hour for one worker and for three.
