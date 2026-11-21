#!/bin/sh
# One-time hardening + Docker install for an Ubuntu 24.04 VPS. Run as a sudo-capable,
# non-root user who already logs in with an SSH key:  sh prepare-host.sh /path/to/bundle
set -eu
bundle=${1:?Usage: prepare-host.sh /path/to/coach-bundle}
test -f "$bundle/coach.env" && test -d "$bundle/data" || { echo "Not a coach bundle: $bundle" >&2; exit 1; }
test -n "$(ls -A "$HOME/.ssh/authorized_keys" 2>/dev/null)" || { echo "Add your SSH key before disabling passwords" >&2; exit 1; }

# SSH: keys only, no root login.
sudo tee /etc/ssh/sshd_config.d/10-coach-hardening.conf >/dev/null <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
EOF
sudo systemctl reload ssh

# Firewall: nothing inbound except SSH (the bot only makes outbound connections).
sudo apt-get update
sudo apt-get install -y ufw unattended-upgrades ca-certificates curl
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw --force enable
sudo dpkg-reconfigure -f noninteractive unattended-upgrades

# Docker Engine from Docker's apt repository.
if ! command -v docker >/dev/null 2>&1; then
    sudo install -m 0755 -d /etc/apt/keyrings
    sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
        | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
    sudo apt-get update
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi

# The container runs as uid 1000; lock the bundle down to it.
sudo chown -R 1000:1000 "$bundle/data" "$bundle/claude"
sudo chmod 700 "$bundle" "$bundle/data" "$bundle/claude"
sudo find "$bundle" -maxdepth 1 -name '*.env' -exec chmod 600 {} +
echo "Host ready. Next: cd deployment/coach && COACH_BUNDLE=$bundle docker compose up -d --build"
