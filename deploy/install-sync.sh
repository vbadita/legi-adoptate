#!/usr/bin/env bash
# Install the pull-based site deploy on this VM (user, dirs, SELinux, timer, nginx). Re-runnable.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
id legi-web >/dev/null 2>&1 || sudo useradd --system --no-create-home --shell /usr/sbin/nologin legi-web
sudo install -d -m 0755 -o legi-web -g legi-web /srv/legi /srv/legi/releases
if ! sudo semanage fcontext -l | grep -q '^/srv/legi(/.\*)?'; then
  sudo semanage fcontext -a -t httpd_sys_content_t '/srv/legi(/.*)?'
fi
sudo restorecon -R /srv/legi
sudo install -m 0755 "$root/deploy/legi-site-sync.sh" /usr/local/libexec/legi-site-sync
sudo install -m 0644 "$root/deploy/systemd/legi-site-sync.service" "$root/deploy/systemd/legi-site-sync.timer" /etc/systemd/system/
sudo install -m 0644 "$root/deploy/nginx/legi-adoptate-maps.conf" /etc/nginx/conf.d/
sudo install -m 0644 "$root/deploy/nginx/legi-adoptate.conf" "$root/deploy/nginx/legi-adoptate-headers.inc" /etc/nginx/apps.badita.com.d/
sudo nginx -t
sudo systemctl daemon-reload
sudo systemctl enable -q --now legi-site-sync.timer
