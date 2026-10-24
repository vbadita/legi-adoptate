#!/usr/bin/env bash
# Pull-based deploy of the static site (legi-site-sync.timer, every 5 minutes).
# CI pushes a tested build to branch "site"; this pulls it. Nothing connects in.
#   /srv/legi/releases/<sha>/       one directory per build (last 3 kept)
#   /srv/legi/legi-adoptate         symlink to the live release (switched atomically)
set -euo pipefail
REPO=${REPO:-vbadita/legi-adoptate}
ROOT=${ROOT:-/srv/legi}
MAX_BYTES=$((200 * 1024 * 1024))
UA="legi-site-sync (+https://github.com/$REPO)"
log() { printf '{"event":"site_sync","result":"%s","sha":"%s","detail":"%s"}\n' "$1" "${2:-}" "${3:-}"; }

if ! api=$(curl -fsS --max-time 20 -A "$UA" -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/$REPO/commits/site"); then
  log no_site_branch "" "GitHub API error (branch missing or rate-limited)"
  exit 1
fi
sha=$(printf '%s' "$api" | python3 -c 'import json,sys; print(json.load(sys.stdin)["sha"])')
live=$(basename "$(readlink "$ROOT/legi-adoptate" 2>/dev/null || true)")
if [ "$sha" = "$live" ]; then
  log up_to_date "${sha:0:12}"
  exit 0
fi

mkdir -p "$ROOT/releases"
tmp=$(mktemp -d "$ROOT/releases/.tmp-XXXXXX")
trap 'rm -rf "$tmp"' EXIT
curl -fsSL --max-time 120 --max-filesize "$MAX_BYTES" -A "$UA" \
  "https://codeload.github.com/$REPO/tar.gz/$sha" -o "$tmp/site.tgz"
tar -xzf "$tmp/site.tgz" -C "$tmp" --strip-components=1 --no-same-owner --no-same-permissions
rm "$tmp/site.tgz"
if [ ! -f "$tmp/index.html" ] || [ ! -f "$tmp/version.json" ]; then
  log rejected "${sha:0:12}" "not a built site"
  exit 1
fi
chmod -R u=rwX,go=rX "$tmp"
release="$ROOT/releases/$sha"
rm -rf "$release"
mv "$tmp" "$release"
trap - EXIT
ln -sfn "releases/$sha" "$ROOT/legi-adoptate.new"
mv -T "$ROOT/legi-adoptate.new" "$ROOT/legi-adoptate"   # rename(2): atomic switch

# keep the 3 newest releases (instant rollback = point the symlink back)
ls -1dt "$ROOT"/releases/*/ | tail -n +4 | xargs -r rm -rf
log deployed "${sha:0:12}" "previous ${live:0:12}"
