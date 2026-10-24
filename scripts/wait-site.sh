#!/usr/bin/env bash
# Wait until the live site reports the expected build (the VM pulls branch
# "site" every 5 minutes; CI cannot push to the VM, it verifies the pull).
#   usage: scripts/wait-site.sh <version.json URL> <expected commit>
set -euo pipefail
URL=$1
EXPECTED=$2
for i in $(seq 1 40); do   # 40 x 20 s = ~13 min
  live=$(curl -fsS --max-time 15 "$URL" | python3 -c 'import json,sys; print(json.load(sys.stdin)["commit"])' 2>/dev/null || true)
  if [ "$live" = "$EXPECTED" ]; then
    echo "live build is $live (after ~$(( (i - 1) * 20 ))s)"
    exit 0
  fi
  echo "check $i: live '${live}', waiting for $EXPECTED"
  sleep 20
done
echo "the site never reached $EXPECTED" >&2
exit 1
