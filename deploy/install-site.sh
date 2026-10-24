#!/usr/bin/env bash
# Install the nginx config for /legi-adoptate/ and publish a built site directory.
#   usage: deploy/install-site.sh <built-site-dir>
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
src=$1
test -f "$src/index.html" || { echo "$src is not a built site" >&2; exit 1; }

sudo install -d -m 0755 /srv/legi-adoptate
if ! sudo semanage fcontext -l | grep -q '^/srv/legi-adoptate(/.\*)?'; then
  sudo semanage fcontext -a -t httpd_sys_content_t '/srv/legi-adoptate(/.*)?'
fi
sudo rsync -a --delete --chmod=D755,F644 "$src"/ /srv/legi-adoptate/
sudo restorecon -R /srv/legi-adoptate

sudo install -m 0644 "$root/deploy/nginx/legi-adoptate-maps.conf" /etc/nginx/conf.d/
sudo install -m 0644 "$root/deploy/nginx/legi-adoptate.conf" "$root/deploy/nginx/legi-adoptate-headers.inc" /etc/nginx/apps.badita.com.d/
sudo nginx -t
sudo systemctl reload nginx
