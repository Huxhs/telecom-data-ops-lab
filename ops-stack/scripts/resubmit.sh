#!/bin/sh
# Send a parked file from error/ back to the inbox once its cause is fixed.
# Usage (from ops-stack/): scripts/resubmit.sh order_good.txt
set -eu
docker compose exec -T worker sh -c 'mv "/data/error/$1" "/data/inbox/$1" && rm -f "/data/error/$1.reason"' sh "$1"
echo "resubmitted $1"
