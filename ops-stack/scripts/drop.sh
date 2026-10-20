#!/bin/sh
# Drop an input file into the inbox the way a good upload lands: written under
# a .part name first, then renamed, so no worker ever reads half a file.
# Usage (from ops-stack/): scripts/drop.sh ../simgen/samples/order_good.txt [name]
set -eu
src="$1"
name="${2:-$(basename "$src")}"
docker compose exec -T worker sh -c 'umask 027; cat > "/data/inbox/$1.part" && mv "/data/inbox/$1.part" "/data/inbox/$1"' sh "$name" < "$src"
echo "dropped $name"
