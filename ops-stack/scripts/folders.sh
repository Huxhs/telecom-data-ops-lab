#!/bin/sh
# Show what is in each exchange folder, and why each parked file failed.
# Usage (from ops-stack/): scripts/folders.sh
set -eu
docker compose exec -T worker sh -c '
for d in inbox processing outbox archive error; do
  echo "== $d"; ls -l "/data/$d" | tail -n +2
done
for r in /data/error/*.reason; do
  [ -e "$r" ] && echo "-- $(basename "$r"): $(cat "$r")"
done
true'
