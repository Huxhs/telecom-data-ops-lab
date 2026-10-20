# Missions: a data engineer's week, in the lab

Each mission is one thing a data engineer in a SIM data-generation center does
on a normal shift: the morning checks, an order, an eSIM batch, a bad file from
a carrier, an EDI problem, a database question, an incident, a handover. Do
them in order. Each one says what happens at work, what to do here, how you
know you are done, and the one or two sentences you can then say in an
interview because you did it.

Tick each mission in [`PROGRESS.md`](PROGRESS.md) with the date once you have
done it yourself. Keep a notebook (paper or a text file) open the whole time:
writing down what you saw is half the job.

Commands run from the folder named at the top of each mission.

---

## Mission 0: Set up your workstation

**At work:** day one is access requests, a locked-down laptop and a Linux
jump host.

**Do** (any folder):

```bash
python3 --version          # 3.11 or newer
docker version             # Docker Desktop or OrbStack running
git clone https://github.com/Huxhs/telecom-data-ops-lab && cd telecom-data-ops-lab
cd simgen     && python3 -m pip install -e ".[dev]" && python3 -m pytest -q && cd ..
cd edi-bridge && python3 -m pip install -e ".[dev]" && python3 -m pytest -q && cd ..
```

**Done when:** both test suites pass (simgen 35, edi-bridge 111).

**Linux warm-up** (do these until they are boring): `ls -l`, `cd`, `pwd`,
`cat`, `less`, `head`, `tail -f`, `grep -n`, `wc -l`, `find . -name "*.out"`,
`chmod 600`, `df -h`, `du -sh`, `ps aux`, `top`, `ssh`, `scp`, `sftp`.

---

## Mission 1: Read the order before you touch it

**At work:** before running anything, you match the carrier's input file to
the order: customer, PO, quantity, profile, ICCID and IMSI ranges, transport
key.

**Do** (`simgen/`):

```bash
cat samples/order_good.txt
simgen init-keys lab-keys.json
simgen generate samples/order_good.txt --out out --keys lab-keys.json
head -15 out/MNO-NORTH_B26101901.out
head -3 out/MNO-NORTH_B26101901.perso.csv
cat out/MNO-NORTH_B26101901.summary.json
```

**Done when:** you can explain, out loud and without notes, every header
line of the input file, every column of the output file (ICCID, IMSI, PIN1,
PUK1, PIN2, PUK2, KI, OPC, ADM1) and why the perso file uses a different key
from the output file. Then do simgen exercises 1 and 2 (Luhn by hand).

**Interview line:** "An input file gives you the starting ICCID and IMSI and
a quantity. Every card gets a unique ICCID with a Luhn check digit, an IMSI,
a random Ki, and an OPc derived from the Ki and the operator's OP. The
operator's copy of the keys is encrypted with their transport key, the
factory's copy with the perso key."

---

## Mission 2: Morning checks

**At work:** first thing, you check that overnight jobs ran, nothing is
stuck, and no SLA is about to be missed.

**Do** (`ops-stack/`):

```bash
docker compose up -d --build --scale worker=2
docker compose ps                                   # everything Up / healthy?
scripts/folders.sh                                  # anything sitting in error/ or processing/?
docker compose exec worker simgen status            # every order and its status
docker compose exec worker df -h /data              # disk space on the exchange volume
docker compose logs --since 1h worker | tail -50    # anything red in the last hour?
```

Then write a three-line morning note: orders open, anything failed, anything
due in the next 24 hours.

**Done when:** you can do the checks in under five minutes and your note
would let a manager decide what comes first.

---

## Mission 3: Process an order the way a carrier sends it

**At work:** the carrier uploads an input file over SFTP, you generate, run
QC, a second person checks, and you release the output file.

**Do** (`ops-stack/`), password `lab-only-sftp`:

```bash
sftp -P 2222 mno@localhost
sftp> cd exchange
sftp> put ../simgen/samples/order_good.txt inbox/
sftp> bye

docker compose exec worker simgen status
docker compose exec worker simgen qc /data/outbox/MNO-NORTH_B26101901.out --keys /keys/lab-keys.json
```

The `qc` run is your **four-eyes check**: a second, independent run of the
same checks the pipeline did. Then fetch the file back over SFTP
(`get outbox/MNO-NORTH_B26101901.out`) the way the carrier would.

**Done when:** you can draw the path of one file from upload to `outbox/` and
say what happens at each folder (`inbox`, `processing`, `outbox`, `archive`,
`error`) and why uploads are written as `.part` and then renamed.

**Interview line:** "I ran SIM orders end to end in my lab: SFTP in, generate,
automated QC, an independent second QC run, release, with the status of
every order in a database."

---

## Mission 4: An eSIM batch

**At work:** for eSIM there is no card to personalize. The profile data goes
to the SM-DP+ (at G+D, the AirOn360 platform) and the carrier gets activation
codes to print as QR codes.

**Do** (`simgen/`):

```bash
cat samples/order_esim.txt
simgen generate samples/order_esim.txt --out out --keys lab-keys.json
head -3 out/MNO-NORTH_E26101908.smdp.csv
head -3 out/MNO-NORTH_E26101908.activation.csv
grep -c MATCHING_ID events.log        # 0: MatchingIDs never go in the log
```

Read the "eSIM orders" section of `simgen/README.md`.

**Done when:** you can explain the activation code
`LPA:1$<SM-DP+ address>$<MatchingID>`, what the phone does with it, and why a
MatchingID is treated as a secret.

**Interview line:** "For eSIM, the data goes to the SM-DP+ instead of the
factory, and the carrier gets SGP.22 activation codes. My QC checks every
code points at the right SM-DP+ and that no MatchingID repeats."

---

## Mission 5: A bad file from a carrier

**At work:** a large part of the afternoon is files that fail: truncated
uploads, missing fields, ranges that overlap an earlier order.

**Do:** drills 1, 2 and 3 in [`drills/README.md`](drills/README.md). For
each, write the ticket first, then the e-mail to the carrier.

**Done when:** each e-mail says exactly what is wrong (field, line, range,
clashing order) and what you need back, in three sentences or fewer, and
never offers to change their numbers for them.

**Interview line:** "When a carrier's ranges overlap an earlier order, I stop
the order, show them the exact clash and ask for a corrected file. I never
fix their numbering myself, because a guessed range can collide with cards
already in the field."

---

## Mission 6: EDI and the ERP

**At work:** purchase orders come in as EDI, get acknowledged, go into the
ERP (SyteLine), and on to SAP. When the batch ships, an 856 goes back.

**Do** (`edi-bridge/`):

```bash
python3 -m edi_bridge pretty samples/850_good.x12
python3 -m edi_bridge inbound samples/850_good.x12 --out out/
python3 -m edi_bridge sap out/erp_orders.json --out out/ --sender SYTELINE --receiver SAPCLNT100 --client 100
less out/idoc_orders.xml
python3 -m edi_bridge inbound samples/850_unknown_item.x12 --out out/
```

Then do drill 7 (997 rejection) and edi-bridge exercise 4 (new trading
partner).

**Done when:** you can read an 850 segment by segment, say what a 997 and an
855 tell the customer, explain why `850_unknown_item` fails and who has to
fix it (the item cross-reference is a change request), and name the three
parts of an IDoc (control record, data segments, status records) and what
status 51 and 53 mean.

**Interview line:** "I have no EDI work experience, but I built and tested an
850-to-997/855 flow, the 856 from a real batch, and an ORDERS05 IDoc export,
so I know what each document is for and where they break."

---

## Mission 7: Oracle questions from the business

**At work:** managers and carriers ask questions the database answers: what
is late, what failed and why, which ranges a customer has used.

**Do** (`ops-stack/`):

```bash
docker compose down
docker compose -f compose.yaml -f compose.oracle.yaml up -d --build --scale worker=2
# drop a few orders (scripts/drop.sh ../simgen/samples/order_good.txt, the drill samples, ...)
docker compose -f compose.yaml -f compose.oracle.yaml exec oracle sqlplus simgen@//localhost/FREEPDB1
```

Work through `sql/oracle_practice.sql` one query at a time. Predict the
result before you run it.

Service request to answer with SQL: "MNO-NORTH wants every ICCID range they
used, with the order and date." Write the query yourself (start from query 6).

**Done when:** you can write queries 1, 4, 6 and 7 from memory, and you
know why query 7 must return no rows.

**Interview line:** "I ran the order database on Oracle Database Free and
wrote the reporting queries: SLA hours left, failures by code, ranges per
customer, and a self-join QC query that proves no two orders share numbers."

---

## Mission 8: Run it on Kubernetes

**At work:** the generation services run as containers. When a pod crashes,
you need to know where to look.

**Do:** follow the Kubernetes section of `ops-stack/README.md` on OrbStack or
Docker Desktop. Then:

```bash
kubectl -n telecom-lab get pods
kubectl -n telecom-lab describe pod <name>
kubectl -n telecom-lab logs <name> --previous
kubectl -n telecom-lab get events --sort-by=.lastTimestamp
kubectl -n telecom-lab scale deployment simgen-worker --replicas=3
kubectl -n telecom-lab delete pod <name>        # watch it come back
kubectl -n telecom-lab rollout restart deployment/simgen-worker
kubectl -n telecom-lab rollout undo deployment/simgen-worker
```

Break it once on purpose: change the image tag in `k8s/worker.yaml` to one
that does not exist, apply, and diagnose the `ImagePullBackOff` from
`describe` and `get events`.

**Done when:** you have done all of the above on a real cluster, and can say
what a Deployment, a Pod, a Secret and a namespace are in one line each.

**Interview line** (only after doing it): "I ran the workers as a Kubernetes
Deployment on a one-node cluster, scaled them, killed pods to watch them come
back, and diagnosed a bad image from `describe` and the events."

---

## Mission 9: Incidents

**At work:** something breaks for a customer. Restore service first, find the
cause second, fix it for good through a change.

**Do:** drills 4, 5 and 6, writing the incident ticket before reading the
answer. Compare your tickets with `drills/examples/`.

**Done when:** you have three incident tickets and one problem record of your
own, and you can explain the difference between an incident, a problem, a
known error and a change.

**Interview line:** "In my lab I practised the ITIL loop on real failures: a
worker killed mid-order, a database outage. I restored service with a
documented workaround and wrote the problem record with the root cause."

---

## Mission 10: Two orders at the same moment

**At work:** several workers run in parallel. A control that works with one
worker can fail with two.

**Do:** drill 9, then read [`PRB-0002`](drills/examples/PRB-0002-two-workers-same-range.md),
then simgen exercise 6 (break the lock, watch the test fail, put it back).

**Done when:** you can explain a check-then-act race in two sentences and how
a table lock fixes it.

**Interview line:** "In my lab, a QC query caught two workers delivering
overlapping orders at the same moment. It was a check-then-act race: the
overlap check and the write were separate steps. The fix puts them in one
locked transaction, and a test now races six workers for one range. I
reproduced it by removing the lock and watching the test fail."

---

## Mission 11: Let Splunk tell you first

**At work:** the team watches orders, SLAs and failures in Splunk dashboards
and gets alerted before a carrier calls.

**Do** (`ops-stack/`): `docker compose --profile splunk up -d`, then follow
`splunk/README.md`: import the dashboard, type every practice search, build
the alert, then drill 8.

**Done when:** your alert fired on its own during a drill, and you can write
a search for "failures by error code in the last 24 hours" without looking.

**Interview line:** "I sent the generator's JSON events to Splunk, built a
dashboard for orders, SLAs and failures, and an alert that fired during my
outage drill before I checked."

---

## Mission 12: Improve one thing

**At work:** after the first months, you are expected to make the job better:
a clearer procedure, a better search, a small script that saves a manual step.

**Do** one of these, with a test, in a branch, merged through a pull request
on your GitHub:

- simgen exercise 4: release sample mode
- the permanent fix for PRB-0001 (an order stuck after a worker dies)
- edi-bridge exercise 1: the 810 invoice

**Done when:** the pull request is merged, the tests pass in GitHub Actions,
and `PROGRESS.md` says what you changed.

**Interview line:** describe what you built, the test that proves it, and
what it would save at work. This is the best story you can bring, because it
is yours.

---

## Mission 13: The handover

**At work:** before you leave, the next shift needs to know what is open,
what is waiting on whom, and what to watch.

**Do:** with a few orders in different states (one delivered, one failed,
one stuck from drill 5), write a handover note of no more than ten lines.

**Done when:** someone who was not there could pick up every open item from
your note alone.
