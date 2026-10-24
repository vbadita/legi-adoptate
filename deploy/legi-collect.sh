#!/usr/bin/env bash
# Scheduled collection ON THE VM (legi-collect.timer). cdep.ro drops connections
# from GitHub Actions' network, so the steps that need it run here; GitHub then
# tests, builds and publishes the site from the data pushed below.
#   update (cdep.ro) -> mo-check (1 call/day) -> explain (<=20 Gemini calls/day)
#   -> commit data/ -> push (deploy key for this repository only)
set -uo pipefail
STATE=/var/lib/legi-collector
REPO=$STATE/repo
VENV=$STATE/venv
CONF=/etc/legi-collector
export GIT_SSH_COMMAND="ssh -i $CONF/deploy_key -o IdentitiesOnly=yes -o UserKnownHostsFile=$CONF/known_hosts -o StrictHostKeyChecking=yes"
set -a; . "$CONF/gemini.env"; set +a            # GEMINI_API_KEY (never printed)
log() { printf '{"event":"collect","step":"%s","exit":%s}\n' "$1" "$2"; }

cd "$REPO" || exit 1
# Exactly the code and data on GitHub; this checkout is never edited by hand.
git fetch -q origin main && git reset -q --hard origin/main || exit 1

# The venv must match the lock file; rebuilt when the lock changes.
lock=$(sha256sum collector/requirements.txt | cut -c1-64)
if [ "$(cat "$VENV/.lock-sha256" 2>/dev/null)" != "$lock" ]; then
  rm -rf "$VENV"
  python3.12 -m venv "$VENV" &&
    "$VENV/bin/pip" install -q --disable-pip-version-check --no-deps -r collector/requirements.txt &&
    "$VENV/bin/pip" check >/dev/null &&
    echo "$lock" > "$VENV/.lock-sha256" || exit 1
fi

cd collector
failed=0
"$VENV/bin/python" -m legi update --data ../data --budget 60;                       rc=$?; log update $rc;   [ $rc -eq 0 ] || failed=1
"$VENV/bin/python" -m legi mo-check --data ../data;                                 rc=$?; log mo-check $rc; [ $rc -eq 0 ] || failed=1
"$VENV/bin/python" -m legi explain --data ../data --cache "$STATE/pdf-cache" \
  --daily-limit 20 --budget 30;                                                     rc=$?; log explain $rc;  [ $rc -eq 0 ] || failed=1
cd ..

# Publish whatever was gathered, even after a failed step.
git add data
if ! git diff --cached --quiet; then
  git -c user.name="legi-adoptate collector (VM)" -c user.email="legi-collector@apps.badita.com" \
    commit -q -m "data: $(TZ=Europe/Bucharest date '+%Y-%m-%d %H:%M') (VM collector)"
  git push -q origin HEAD:main || { git pull -q --rebase origin main && git push -q origin HEAD:main; } || failed=1
  log push $?
fi
exit $failed
