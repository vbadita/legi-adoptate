#!/usr/bin/env bash
# Headless Chrome on the LIVE site: the home page and the first law it links
# to render their 3 status columns, and Chrome logs no CSP violation.
#   usage: scripts/e2e-site.sh <base URL>
set -euo pipefail
BASE=${1%/}
CHROME=${CHROME:-google-chrome}
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
"$CHROME" --version

dump() {  # url -> dom file, console log file
  timeout 90 "$CHROME" --headless=new --disable-gpu --no-first-run --enable-logging=stderr --v=0 \
    --dump-dom "$1" >"$2" 2>"$3"
}
dump "$BASE/" "$tmp/index.html" "$tmp/index.log"
law=$(grep -o 'href="/legi-adoptate/lege/[0-9]*\.html"' "$tmp/index.html" | head -1 | cut -d'"' -f2)
test -n "$law" || { echo "no law linked from the home page" >&2; exit 1; }
dump "https://apps.badita.com$law" "$tmp/law.html" "$tmp/law.log"

python3 - "$tmp" "$law" <<'PY'
import pathlib, re, sys
tmp, law = pathlib.Path(sys.argv[1]), sys.argv[2]
ok = True
for name, must in (("index", "Ce legi a adoptat Parlamentul"), ("law", "Parcursul în Parlament")):
    dom = (tmp / f"{name}.html").read_text(encoding="utf-8")
    log = (tmp / f"{name}.log").read_text(encoding="utf-8", errors="replace")
    cols = len(re.findall(r'<ol class="cols"', dom))
    csp = [l for l in log.splitlines() if "Content Security Policy" in l or "Refused to" in l]
    good = must in dom and cols > 0 and not csp
    ok &= good
    print(f"{name}: heading {'ok' if must in dom else 'MISSING'}, status blocks {cols}, CSP violations {csp or 'none'}")
print("law page tested:", law)
sys.exit(0 if ok else 1)
PY
