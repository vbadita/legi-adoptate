#!/usr/bin/env bash
# Fail if any locked package version is younger than MIN_DAYS on PyPI:
# fresh releases are where hijacked/yanked uploads show up first.
set -euo pipefail
MIN_DAYS=${MIN_DAYS:-14}
cd "$(dirname "$0")/../collector"
# certifi is exempt: it is the CA bundle, a fresh release removes distrusted CAs.
grep -hv '^[#-]' requirements-dev.txt | sort -u | python3 -c '
import json, sys, urllib.request
from datetime import datetime, timezone
min_days, bad = int(sys.argv[1]), 0
for line in sys.stdin:
    name, version = line.strip().split("==")
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=20) as r:
        files = json.load(r)["urls"]
    uploaded = min(datetime.fromisoformat(f["upload_time_iso_8601"].replace("Z", "+00:00")) for f in files)
    age = (datetime.now(timezone.utc) - uploaded).days
    flag = "OK " if age >= min_days else "NEW"
    bad += age < min_days
    print(f"{flag} {name}=={version} {age}d")
sys.exit(1 if bad else 0)
' "$MIN_DAYS"
