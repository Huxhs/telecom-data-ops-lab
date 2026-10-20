# ops-stack: a mini data center in Docker and Kubernetes

```
 operator ──SFTP──▶ exchange/inbox ──▶ simgen workers (1..n) ──▶ exchange/outbox ──SFTP──▶ operator
                                            │            │
                              PostgreSQL or Oracle    events.log ──▶ Splunk dashboard
                                      (order status)
```

Everything binds to localhost only. Passwords in this folder are LAB ONLY
defaults; override them in a `.env` file (`POSTGRES_PASSWORD=...`,
`SFTP_PASSWORD=...`, `SPLUNK_PASSWORD=...`).

## Docker Compose

```bash
cd ops-stack
docker compose up -d --build                 # postgres, keygen, 1 worker, sftp
docker compose ps
docker compose logs -f worker                # watch it work
```

**Send an order like an operator would, over SFTP** (password `lab-only-sftp`):

```bash
sftp -P 2222 mno@localhost
sftp> cd exchange
sftp> put ../simgen/samples/order_good.txt inbox/
sftp> ls outbox
sftp> get outbox/MNO-NORTH_B26101901.out
sftp> ls error            # failed files, each with a .reason file
sftp> bye
```

**Look at the database** (password `lab-only-change-me`):

```bash
docker compose exec postgres psql -U simgen -d orders
orders=# SELECT order_id, status, error_code FROM orders;
```

**Scale out** and prove each order is still processed exactly once:

```bash
docker compose up -d --scale worker=3
# put several order files at once, then:
docker compose exec postgres psql -U simgen -d orders -c "SELECT count(*) FROM orders"
docker compose logs worker | grep order_completed
```

**Run on Oracle instead of PostgreSQL.** `compose.oracle.yaml` adds Oracle
Database Free (`gvenzl/oracle-free`, about 2 GB of RAM, ready after a minute
or two) and points the workers at it:

```bash
docker compose down
docker compose -f compose.yaml -f compose.oracle.yaml up -d --build --scale worker=2
docker compose -f compose.yaml -f compose.oracle.yaml exec oracle sqlplus simgen@//localhost/FREEPDB1
```

The password is `LabOnly-Oracle-1` unless you set `ORACLE_PASSWORD`. Then
work through [`sql/oracle_practice.sql`](sql/oracle_practice.sql): status
counts, SLA hours left, failures by code, ranges per customer, and the QC
query that proves no two orders share numbers. Use the same two `-f` flags
on every command while you run on Oracle.

**Splunk** (heavy: about 2 GB of RAM; x86 image, runs under Rosetta on Apple silicon):

```bash
docker compose --profile splunk up -d
# wait 2-3 minutes, then open http://localhost:8000  (admin / LabOnly-Splunk-1)
```

Then follow `splunk/README.md` to import the dashboard and build an alert.

**Helper scripts** (used by the [drills](../drills/README.md)):
`scripts/drop.sh FILE` drops an order into the inbox, `scripts/folders.sh`
shows every exchange folder and why each parked file failed, and
`scripts/resubmit.sh NAME` sends a parked file back to the inbox.

**Stop / reset:** `docker compose --profile splunk down` keeps data;
add `-v` to delete the volumes and start clean.

## Kubernetes (OrbStack, Docker Desktop or any one-node cluster)

```bash
# 1. build the image where the cluster can see it (OrbStack and Docker Desktop share it)
docker build -t simgen:lab ../simgen

# 2. database and namespace
kubectl apply -f k8s/postgres.yaml

# 3. LAB ONLY keys into a Secret (never commit this file)
python3 -m pip install -e ../simgen && simgen init-keys lab-keys.json
kubectl -n telecom-lab create secret generic simgen-keys --from-file=lab-keys.json
rm lab-keys.json

# 4. workers
kubectl apply -f k8s/worker.yaml
kubectl -n telecom-lab get pods -w

# 5. drop an order and watch
POD=$(kubectl -n telecom-lab get pod -l app=simgen-worker -o name | head -1)
kubectl -n telecom-lab cp ../simgen/samples/order_good.txt ${POD#pod/}:/data/inbox/order_good.txt
kubectl -n telecom-lab logs -f deploy/simgen-worker

# 6. scale, and kill a pod to see Kubernetes replace it
kubectl -n telecom-lab scale deployment simgen-worker --replicas=3
kubectl -n telecom-lab delete $POD
kubectl -n telecom-lab get pods

# clean up
kubectl delete namespace telecom-lab
```

Troubleshooting commands to know cold: `kubectl get pods`, `describe pod`,
`logs` (and `logs --previous` after a crash), `get events --sort-by=.lastTimestamp`,
`exec -it <pod> -- sh`, `rollout status`, `rollout undo`.

## What was tested

The Compose stack was run end to end: SFTP upload, two and three workers,
PostgreSQL tracking, Splunk ingestion and every dashboard search, and the
database-outage drill. The Oracle override was run with two workers and
every query in `sql/oracle_practice.sql`, and two overlapping orders dropped
at the same moment were tested on both databases (drill 9). The Kubernetes manifests were written for a one-node
lab cluster and checked for structure, but were not run on a cluster yet:
running them is the first Kubernetes exercise.

## Exercises

1. Pin every image by digest (`image: postgres:16-alpine@sha256:...`) and
   explain why that matters in a secure environment.
2. Add a Postgres backup job (`pg_dump` on a schedule) and restore it into a
   fresh volume.
3. Add a Kubernetes liveness probe for the worker. What should it check?
4. Write a Splunk alert for "no `order_completed` event in 30 minutes during
   business hours" and test it by stopping the workers.
