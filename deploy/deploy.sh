#!/usr/bin/env bash
# Deploy the Cloud Drive web UI to drive.forwardforecasting.eu
set -euo pipefail

SERVER="ubuntu@54.78.82.101"
KEY="$HOME/.ssh/forwardforecasting.pem"
REMOTE_DIR="/home/ubuntu/cloud-drive"
LOCAL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SSH="ssh -i $KEY"

step() { echo -e "\n\033[1;34m▶ $1\033[0m"; }
ok()   { echo -e "\033[1;32m✓ $1\033[0m"; }
warn() { echo -e "\033[1;33m⚠ $1\033[0m"; }

# ── Sync code to server ───────────────────────────────────────────────────────

step "Syncing code to $SERVER:$REMOTE_DIR"
rsync -az --progress -e "ssh -i $KEY" \
    --exclude "__pycache__" \
    --exclude "*.pyc" \
    --exclude "venv/" \
    --exclude ".venv/" \
    --exclude "*.egg-info/" \
    --exclude "backup.log" \
    --exclude "folders.txt" \
    "$LOCAL_DIR/" "$SERVER:$REMOTE_DIR/"
ok "Code synced"

# ── Install deps on server ────────────────────────────────────────────────────

step "Installing system dependencies on server"
$SSH -i "$KEY" "$SERVER" bash <<'REMOTE'
    if ! command -v ffmpeg &>/dev/null; then
        sudo apt-get install -y -qq ffmpeg
    fi
REMOTE
ok "ffmpeg ready"

step "Installing Python dependencies on server"
$SSH -i "$KEY" "$SERVER" bash <<'REMOTE'
    cd ~/cloud-drive
    if [[ ! -d venv ]]; then
        python3 -m venv venv
    fi
    source venv/bin/activate
    pip install --quiet --upgrade pip
    pip install --quiet boto3 pyyaml streamlit pandas pillow
    python3 deploy/pwa/generate_icons.py
REMOTE
ok "Dependencies installed"

# ── systemd services ──────────────────────────────────────────────────────────

step "Installing systemd services (web + api)"
rsync -az -e "ssh -i $KEY" \
    "$LOCAL_DIR/deploy/cloud-drive-web.service" \
    "$LOCAL_DIR/deploy/cloud-drive-api.service" \
    "$SERVER:/tmp/"

$SSH -i "$KEY" "$SERVER" bash <<'REMOTE'
    sudo cp /tmp/cloud-drive-web.service /etc/systemd/system/cloud-drive-web.service
    sudo cp /tmp/cloud-drive-api.service /etc/systemd/system/cloud-drive-api.service
    sudo systemctl daemon-reload
    sudo systemctl enable cloud-drive-web cloud-drive-api
    sudo systemctl restart cloud-drive-web cloud-drive-api
    sleep 3
    sudo systemctl is-active cloud-drive-web
    sudo systemctl is-active cloud-drive-api
REMOTE
ok "Services running (web:8505, api:8506)"

# ── SSL certificate ───────────────────────────────────────────────────────────

step "Installing nginx config"
rsync -az -e "ssh -i $KEY" \
    "$LOCAL_DIR/deploy/nginx-drive.conf" \
    "$SERVER:/tmp/nginx-drive.conf"

$SSH -i "$KEY" "$SERVER" bash <<'REMOTE'
    sudo cp /tmp/nginx-drive.conf /etc/nginx/sites-available/drive.conf
    sudo ln -sf /etc/nginx/sites-available/drive.conf /etc/nginx/sites-enabled/drive.conf
    sudo nginx -t
    sudo systemctl reload nginx
REMOTE
ok "nginx config active"

# ── Final status ──────────────────────────────────────────────────────────────

echo ""
echo "────────────────────────────────────────────────────────"
$SSH -i "$KEY" "$SERVER" "systemctl is-active cloud-drive-web && curl -sf http://localhost:8505/_stcore/health && echo 'Health: OK' || echo 'Health: not ready yet'"
ok "Live at → https://drive.forwardforecasting.eu/"
