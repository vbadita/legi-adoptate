#!/usr/bin/env bash
# Install the VM collector (legi-collect.timer). Re-runnable.
# Needs: gh logged in with admin on the repo (registers the deploy key once),
# ~/.gemini.env with GEMINI_API_KEY=... (copied, never printed).
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
REPO=vbadita/legi-adoptate
CONF=/etc/legi-collector
STATE=/var/lib/legi-collector

id legi-collector >/dev/null 2>&1 || sudo useradd --system --no-create-home --shell /usr/sbin/nologin legi-collector
sudo install -d -m 0750 -o root -g legi-collector "$CONF"
sudo install -d -m 0750 -o legi-collector -g legi-collector "$STATE" "$STATE/pdf-cache"

# Deploy key: write access to THIS repository only.
if ! sudo test -f "$CONF/deploy_key"; then
  sudo ssh-keygen -q -t ed25519 -N '' -C "legi-collector@$(hostname -s)" -f "$CONF/deploy_key"
  sudo chown legi-collector:legi-collector "$CONF/deploy_key" "$CONF/deploy_key.pub"
  sudo chmod 0600 "$CONF/deploy_key"
  gh api -X POST "repos/$REPO/keys" -f title="legi-collector@$(hostname -s)" \
    -f key="$(sudo cat "$CONF/deploy_key.pub")" -F read_only=false --jq '"deploy key added: id \(.id), read_only=\(.read_only)"'
fi

# known_hosts: GitHub's host keys, accepted only if their fingerprints match the
# ones GitHub publishes in its API (no blind trust on first use).
tmp=$(mktemp)
ssh-keyscan -t ed25519 github.com 2>/dev/null > "$tmp"
fp=$(ssh-keygen -lf "$tmp" | awk '{print $2}')
official=$(curl -fsS https://api.github.com/meta | python3 -c 'import json,sys; print(json.load(sys.stdin)["ssh_key_fingerprints"]["SHA256_ED25519"])')
[ "$fp" = "SHA256:$official" ] || { echo "github.com host key mismatch: $fp" >&2; rm -f "$tmp"; exit 1; }
sudo install -m 0644 "$tmp" "$CONF/known_hosts"; rm -f "$tmp"
echo "github.com ed25519 host key verified: $fp"

# Gemini key (same file format as ~/.gemini.env), readable only by the collector.
sudo install -m 0600 -o legi-collector -g legi-collector ~/.gemini.env "$CONF/gemini.env"

# Working copy, cloned over SSH with the deploy key.
if ! sudo test -d "$STATE/repo/.git"; then
  sudo -u legi-collector env GIT_SSH_COMMAND="ssh -i $CONF/deploy_key -o IdentitiesOnly=yes -o UserKnownHostsFile=$CONF/known_hosts -o StrictHostKeyChecking=yes" \
    git clone -q "git@github.com:$REPO.git" "$STATE/repo"
fi

sudo install -m 0755 "$root/deploy/legi-collect.sh" /usr/local/libexec/legi-collect
sudo install -m 0644 "$root/deploy/systemd/legi-collect.service" "$root/deploy/systemd/legi-collect.timer" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable -q --now legi-collect.timer
systemctl list-timers legi-collect.timer --no-pager | sed -n 2p
