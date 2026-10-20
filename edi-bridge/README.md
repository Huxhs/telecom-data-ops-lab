# edi-bridge

A small, readable ANSI X12 toolkit for learning how EDI connects a customer to
an ERP. It reads an inbound **850 purchase order**, checks the envelopes,
answers with a **997** and an **855**, turns the order into a SyteLine-style
import record, and builds an **856 ship notice** from the shipment summary
written by the sibling project [`simgen`](../simgen). The ERP orders can also
be passed on to SAP as **ORDERS05 IDocs** in XML.

> **All data here is synthetic.** Partner IDs (`MNONORTH`, `GDMOBILE`), part
> numbers, prices and ICCIDs are made up. Real trading partners each publish an
> *implementation guide* that narrows the X12 standard (which segments, which
> qualifiers, which codes), and those guides differ. This project follows the
> standard closely but keeps only what a learner needs.

Python 3.11+, standard library only at runtime. `pytest` for the tests.

## What EDI is, in five lines

1. EDI (Electronic Data Interchange) is companies exchanging business documents as structured files instead of PDFs or e-mail.
2. ANSI X12 is the North American standard; each document type has a number: 850 = purchase order, 856 = ship notice, 810 = invoice.
3. A file is a chain of *segments* (`BEG*00*SA*PO45001**20261019~`), each made of *elements* separated by a delimiter.
4. Segments sit inside envelopes (ISA/GS/ST ... SE/GE/IEA) whose trailers repeat control numbers and counts, so nothing gets lost unnoticed.
5. A *translator* (e.g. Sterling Gentran) checks the envelopes and maps the data into the ERP (e.g. Infor SyteLine), and the other way round.

## The order-to-cash flow

```
 Customer (mobile operator)                     Us (SIM manufacturer)
 ──────────────────────────                     ──────────────────────────────────
 850 Purchase Order        ───────────────────▶ translator: validate + map → ERP order
                           ◀─────────────────── 997 Functional Ack  "file arrived, syntax OK"
                           ◀─────────────────── 855 PO Ack          "order accepted, dates"
                                                ... simgen personalises the SIM batch ...
                           ◀─────────────────── 856 Ship Notice     "shipped: batch, ICCIDs"
                           ◀─────────────────── 810 Invoice         (lab exercise)
 997 for each of our files ───────────────────▶
```

The 997 is about **syntax** (was the file well formed?). The 855 is about
**business** (can we deliver these items?). An order can pass the 997 and still
be rejected in the 855, which is exactly what `850_unknown_item.x12` shows.

## Quick start

```bash
cd edi-bridge
python3 -m pip install -e ".[dev]"      # optional: gives you the edi-bridge command
python3 -m pytest -q                    # run the tests

edi-bridge pretty   samples/850_good.x12              # read a file like a human
edi-bridge validate samples/850_bad_control.x12       # exit 1, one line per error
edi-bridge inbound  samples/850_good.x12 --out out/   # 997 + 855 + ERP files
edi-bridge sap      out/erp_orders.json --out out/    # ERP orders -> SAP IDoc XML
edi-bridge inbound  samples/850_unknown_item.x12 --out out/
edi-bridge asn      samples/shipment_summary.json --out out/ --carrier ABCD
```

Without installing, use `python3 -m edi_bridge ...` from this folder.

`inbound` writes into `--out`:

| File | What it is |
|---|---|
| `997.x12` | functional acknowledgment, always written when the ISA was readable |
| `855.x12` | PO acknowledgment, one 855 per order that passed the 997 |
| `orders.json` | the orders as read from the 850s |
| `erp_orders.json`, `co_header.csv`, `co_lines.csv` | the ERP import, only for orders that mapped completely |
| `.edi_control.json` | last used ISA/GS control numbers, so they keep increasing between runs |

Exit code 0 means everything went through; 1 means a human should look
(envelope error, mapping error or unreadable file).

## Modules

| Module | Does |
|---|---|
| `x12.py` | Finds the delimiters in the ISA, builds Interchange → FunctionalGroup → TransactionSet → Segment, and validates control numbers, counts and required segments. Returns `EdiError`s, never crashes. |
| `pretty.py` | One labelled line per segment, with a small dictionary of segment names and code meanings. |
| `m850.py` | Maps an 850 onto an `Order` (BEG, DTM*002, N1*ST, PO1 lines with VP/BP/UP part numbers). |
| `mapping.py` | Cross-references partner and items (`samples/partners.csv`, `samples/items.csv`) and writes the SyteLine-style customer order as JSON and CSV. |
| `ack997.py` | Builds the 997: AK1, AK2/AK3/AK4/AK5 per set, AK9 per group. |
| `ack855.py` | Builds the 855: BAK AC or RJ, PO1 + ACK (IA/IR) per line. |
| `asn856.py` | Builds the 856 from the simgen summary: shipment → order → item HL tree. |
| `idoc.py` | Passes ERP orders on to SAP as ORDERS05 IDocs in XML: control record `EDI_DC40` + `E1EDK*` header and `E1EDP01` line segments. |
| `envelope.py` | Wraps any outbound set in ST/SE, GS/GE, ISA/IEA (fixed-width 106-character ISA) with control numbers from a counter file. |
| `cli.py` | The `edi-bridge` command. |

## Walk-through: reading `850_good.x12`

The raw file is hard to read on purpose; machines don't need spaces:

```
ISA*00*          *00*          *ZZ*MNONORTH       *ZZ*GDMOBILE       *261019*0930*U*00401*000000101*0*T*>~
GS*PO*MNONORTH*GDMOBILE*20261019*0930*101*X*004010~
ST*850*0001~
BEG*00*SA*PO45001**20261019~
...
```

`edi-bridge pretty samples/850_good.x12` labels everything. `(...)` explains a
code, `[...]` names a free-text element, the left number is the segment
position (the same number `validate` reports) and indentation shows nesting.
Here the ISA and GS lines are shortened and the `# segment name` comment that
ends every line is left out:

```
delimiters: element '*'  component '>'  segment '~'
  1  ISA  ISA05=ZZ (Mutually defined)  ISA06=MNONORTH [Sender ID]  ...  ISA13=000000101 [Interchange control no]  ISA15=T (Test)  ...
  2    GS   GS01=PO (Purchase orders)  GS02=MNONORTH [App sender]  GS03=GDMOBILE [App receiver]  ...  GS06=101 [Group control no]  ...
  3      ST   ST01=850 (Purchase Order)  ST02=0001 [Set control no]
  4        BEG  BEG01=00 (Original)  BEG02=SA (Stand-alone order)  BEG03=PO45001 [PO number]  BEG05=20261019 [PO date]
  5        DTM  DTM01=002 (Delivery requested)  DTM02=20261102 [Date]
  6        N1   N101=ST (Ship to)  N102=North Mobile DC [Name]  N103=92 (Assigned by buyer)  N104=NDC01 [Party ID]
  7        PO1  PO101=1 [Line no]  PO102=500 [Quantity]  PO103=EA (Each)  PO104=0.85 [Unit price]  PO106=VP (Vendor part)  PO107=UICC-TRIO-128K [Product ID]  PO108=BP (Buyer part)  PO109=MN-SIM-0128 [Product ID]
  8        PO1  PO101=2 [Line no]  PO102=1000 [Quantity]  PO103=EA (Each)  PO104=0.40 [Unit price]  PO106=VP (Vendor part)  PO107=ESIM-PROF-A [Product ID]  PO108=BP (Buyer part)  PO109=MN-ESIM-A01 [Product ID]
  9        CTT  CTT01=2 [Line/HL count]
 10      SE   SE01=8 [Segment count ST..SE]  SE02=0001 [Set control no (= ST02)]
 11    GE   GE01=1 [Set count]  GE02=101 [Group control no (= GS06)]
 12  IEA  IEA01=1 [Group count]  IEA02=000000101 [Interchange control no (= ISA13)]
```

Reading it top to bottom:

- **ISA (1)** is fixed width: always 106 characters, so a reader finds the
  delimiters at fixed places: character 4 (`*`) separates elements, ISA16
  (`>`) separates components, and the character after it (`~`) ends segments.
  `ZZ*MNONORTH` → `ZZ*GDMOBILE` says who sends to whom. `T` = test data.
- **GS (2)** opens a group of purchase orders (`PO`), control number 101.
- **ST (3)** starts one 850, control number `0001`.
- **BEG (4)**: an original (`00`) stand-alone (`SA`) order, PO `PO45001`, dated
  2026-10-19. BEG04 (release number) is empty, which is why you see `**`.
- **DTM (5)**: `002` = the customer wants delivery on 2026-11-02.
- **N1 (6)**: ship to "North Mobile DC", whose code `NDC01` the buyer assigned (`92`).
- **PO1 (7, 8)**: two lines. 500 physical triple-cut SIMs at 0.85 and 1000 eSIM
  profiles at 0.40. Each line names the product twice: `VP` (our part number)
  and `BP` (the customer's). `mapping.py` uses either to find our ERP item.
- **CTT (9)**: 2 line items, a cross-check for the receiver.
- **SE (10)**: 8 segments from ST to SE inclusive, and `0001` again.
- **GE (11)** and **IEA (12)** close the group and the interchange with their
  counts and control numbers. If any of these numbers is wrong, `validate`
  says so and the 997 rejects the file.

## Samples and what they teach

| Sample | Result |
|---|---|
| `850_good.x12` | valid; 997 `AK5*A`, 855 `BAK*00*AC`, ERP order `EDIPO45001` |
| `850_bad_control.x12` | SE02 `0002` ≠ ST02 `0001` and SE01 says 9 segments (there are 7) → 997 `AK5*R*3*4`, nothing imported |
| `850_unknown_item.x12` | valid syntax (997 accepts) but line 2's part is not in `items.csv` → 855 `BAK*00*RJ`, `ACK*IR` on line 2 |
| `850_missing_beg.x12` | no BEG → 997 `AK3*BEG*7**3` + `AK5*R*5` |
| `850_multi.x12` | one interchange, two 850s → two ERP orders, two 855s, 997 `AK9*A*2*2*2` |
| `shipment_summary.json` | simgen output for batch B26101901 → 856 |

## Troubleshooting cheat sheet

| `validate` code | Meaning | 997 code | Usual cause |
|---|---|---|---|
| `NO_ISA`, `ISA_MALFORMED` | not X12, ISA shorter than 106 characters, or not 16 ISA elements | none (no reply possible) | wrong file, truncated upload, padding lost in an ID |
| `ISA_IEA_CONTROL_MISMATCH`, `IEA_GROUP_COUNT` | interchange envelope broken | every group `AK9*R` (in production: a TA1) | file cut or two files concatenated |
| `GS_GE_CONTROL_MISMATCH`, `GE_SET_COUNT`, `GE_MISSING` | group envelope broken | `AK9*R*4` / `*5` / `*3` | sender's translator bug, partial resend |
| `ST_SE_CONTROL_MISMATCH`, `SE_SEGMENT_COUNT`, `SE_MISSING` | transaction set envelope broken | `AK5*R*3` / `*4` / `*2` | a segment added or removed by hand |
| `REQUIRED_SEGMENT_MISSING` | e.g. 850 without BEG or PO1 | `AK3 ... *3`, `AK5*R*5` | sender's map does not follow the guide |
| `REQUIRED_ELEMENT_MISSING`, `INVALID_NUMBER` | bad element | `AK3 ... *8` + `AK4` | empty PO number, `5O0` instead of `500` |
| `UNKNOWN_PARTNER`, `UNKNOWN_ITEM` (mapping) | not in the cross-reference | none: 997 accepts, 855 rejects | new partner or new part not set up yet |

## SAP IDocs

SAP does not read X12. Between SAP and its neighbours the usual format is the
**IDoc** (intermediate document), and every IDoc has three parts:

- a **control record** (`EDI_DC40`): IDoc type and message type, SAP client
  (`MANDT`), who sends it to whom (partner type `LS` = logical system) and an
  IDoc number;
- **data segments**: named, nested structures such as `E1EDK01` (order
  header) and `E1EDP01` (one per line), in the order the IDoc type defines;
- **status records**, which SAP adds while it processes the IDoc. A sender
  never writes them.

`sap` turns every order in `erp_orders.json` into one IDoc of basic type
`ORDERS05`, message type `ORDERS`, `DIRECT=2` (inbound to SAP), all in
`idoc_orders.xml` under one `<ORDERS05>` root:

```bash
edi-bridge inbound samples/850_multi.x12 --out out/
edi-bridge sap     out/erp_orders.json --out out/ --sender SYTELINE --receiver SAPCLNT100 --client 100
```

| X12 850 | ERP field | IDoc segment / field |
|---|---|---|
| BEG03 PO number | `cust_po` | `E1EDK02` `QUALF=001` `BELNR` |
| (made from BEG03) | `co_num` | `E1EDK01` `BELNR` |
| BEG05 PO date | `order_date` | `E1EDK03` `IDDAT=012` (document date) `DATUM` |
| DTM*002 | `due_date` | `E1EDK03` `IDDAT=002` (requested delivery) `DATUM`, `E1EDP20` `EDATU` |
| ISA06, via `partners.csv` | `cust_num` | `E1EDKA1` `PARVW=AG` (sold-to) `PARTN`; `NAME1` from `partners.csv` |
| N1*ST N104 | `ship_to` | `E1EDKA1` `PARVW=WE` (ship-to) `PARTN` |
| PO101 | `co_line` (the ERP numbers lines 1, 2 ...) | `E1EDP01` `POSEX` |
| PO102 | `qty_ordered` | `E1EDP01` `MENGE`, `E1EDP20` `WMENG` |
| PO103 `EA` | `u_m` | `E1EDP01` `MENEE` = `PCE` (IDocs carry ISO unit codes) |
| PO104 | `price` | `E1EDP01` `VPREI` |
| PO107 VP / PO109 BP, via `items.csv` | `item` | `E1EDP19` `QUALF=002` (our material) `IDTNR` |
| PO109 BP | not stored; looked up again in `items.csv` | `E1EDP19` `QUALF=001` (customer's material) `IDTNR` |

Dates become `YYYYMMDD`. What the ERP record does not hold is left out, not
made up: there is no currency (`CURCY`) and no ship-to name. `DOCNUM` comes
from the same counter file as the X12 control numbers; SAP gives an inbound
IDoc a number of its own when it arrives. Some SAP ports want one IDoc per
file instead of several under one root; ask the SAP team which theirs expects.

**Status codes in support work.** The latest status record says where an
inbound IDoc stands:

| Status | Meaning | What you do |
|---|---|---|
| 64 | ready to be passed to the application | usually waits for the batch job (report `RBDAPP01`); if it stays, check that job |
| 53 | application document posted | done; the status text names the sales order created |
| 51 | application document not posted | read the status message, fix the master data or the mapping, reprocess |
| 56 | IDoc with errors added | the control record matches no partner profile (`WE20`): check sender, receiver, message type |
| 68 | error, no further processing | closed on purpose; nothing more will happen to it |

In SAP, `WE02` / `WE05` list IDocs and show the control record, segments and
status history; `WE09` finds IDocs by a value inside a segment (e.g. the PO
number in `E1EDK02`); `BD87` reprocesses selected IDocs, e.g. every 51 for
`ORDERS` once the cause is fixed. The most common 51 in an interface like this
is a code one system has and the other lacks: this IDoc sends SyteLine's
customer and item codes, so SAP must know them or convert them.

## Lab exercises

Do these yourself; each one is a realistic EDI support or development task.

1. **Invoice (810).** Write `inv810.py` that builds an 810 from an `ErpOrder`
   and the 856 (BIG, N1, IT1 per line, TDS total in cents, CTT). Add an
   `edi-bridge invoice` command and tests. Validate your output with `validate`.
2. **Change orders (860).** Parse an 860 (BCH header, POC lines with change
   codes such as `QI` quantity increase) and apply it to an existing
   `orders.json`. What should happen if the original PO was never received?
3. **Element-level date errors.** Make `validate` reject `BEG05=20261341` and
   `DTM02` values that are not real dates, and make the 997 send
   `AK3*BEG*2**8` + `AK4*5*373*8*20261341` (`8` = invalid date). Hint: copy
   how `NUMERIC_ELEMENTS` and `INVALID_NUMBER` work.
4. **New trading partner.** Onboard `MNOSOUTH` (already in `partners.csv`):
   write `850_south.x12` with their part `ST-USIM-128`, make it pass, then
   check that the 997 and 855 go back to `MNOSOUTH`, not `MNONORTH`.
5. **Duplicate PO.** Write a test that runs `inbound` twice with the same PO
   number, then change `inbound` so the second run is flagged (keep a list of
   processed PO numbers and interchange control numbers in the output folder).
6. **TA1.** Instead of rejecting every group when ISA13 ≠ IEA02, generate a
   TA1 interchange acknowledgment (`TA1*000000101*261019*0930*R*001`).
7. **EDIFACT.** Write `edifact.py` that reads an UN/EDIFACT `ORDERS` message
   (UNA/UNB/UNH ... UNT/UNZ, `'` terminator, `+` and `:` separators) into the
   same `Order` dataclass, so `mapping.py` works unchanged.
8. **Outbound error drill.** Edit a generated `855.x12` by hand (delete one
   `ACK`), run `validate` on it and explain each error the way you would to a
   customer's EDI team.
9. **Map a new field to SAP.** The ship-to name (`N1*ST` N102, "North Mobile
   DC") reaches `orders.json` but not the ERP record, so `E1EDKA1 PARVW=WE`
   has no `NAME1`. Carry it through: add it to `ErpOrder` and `co_header.csv`,
   write it into `NAME1`, and extend the mapping table above and
   `tests/test_idoc.py`. What should happen when a name is longer than
   `NAME1`'s 35 characters?

## Limitations (on purpose)

- X12 version 4010 only; no repetition separator (5010 ISA11), one interchange per file.
- Validation covers envelopes, counts, a few required segments/elements and
  numeric checks. It does not check segment order, loop limits or code lists.
- Interchange-level errors are answered by rejecting every group in the 997;
  production translators send a TA1 instead (exercise 6).
- The 855 uses `RJ` when any line fails; some partners want `RD` or `AC` with `IR` lines.
- ICCID ranges in `REF*SE` are a learning touch; real guides often put serials
  in MAN or SLN segments. TD1 assumes 500 cards per carton.
- The IDoc is a small slice of ORDERS05 and sends ERP codes unconverted; real
  interfaces agree partner and material numbers with the SAP side first.
- The ERP record is a simplified SyteLine customer order (no ship-to sequence,
  no warehouse, co_num derived from the PO instead of an ERP sequence).
