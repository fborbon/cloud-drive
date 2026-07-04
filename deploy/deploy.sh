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

step "Installing Python dependencies on server"
$SSH -i "$KEY" "$SERVER" bash <<'REMOTE'
    cd ~/cloud-drive
    if [[ ! -d venv ]]; then
        python3 -m venv venv
    fi
    source venv/bin/activate
    pip install --quiet --upgrade pip
    pip install --quiet boto3 pyyaml streamlit pandas
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

if [[ "${1:-}" == "--ssl" ]]; then
    step "Provisioning SSL certificate"
    $SSH -i "$KEY" "$SERVER" bash <<'REMOTE'
        sudo certbot certonly --nginx \
            -d drive.forwardforecasting.eu \
            --non-interactive \
            --agree-tos \
            --email correoprincipal2021@hotmail.com \
            --expand
REMOTE
    ok "SSL certificate issued"

    step "Installing nginx config (with SSL)"
    rsync -az -e "ssh -i $KEY" \
        "$LOCAL_DIR/deploy/nginx-drive.conf" \
        "$SERVER:/tmp/nginx-drive.conf"

    $SSH -i "$KEY" "$SERVER" bash <<'REMOTE'
        sudo cp /tmp/nginx-drive.conf /etc/nginx/sites-available/drive.conf
        sudo ln -sf /etc/nginx/sites-available/drive.conf /etc/nginx/sites-enabled/drive.conf
        sudo nginx -t
        sudo systemctl reload nginx
REMOTE
    ok "nginx config active with SSL"

else
    step "Installing nginx config (HTTP only)"
    $SSH -i "$KEY" "$SERVER" bash <<'REMOTE'
        cat > /tmp/nginx-drive-http.conf <<'EOF'
server {
    listen 80;
    server_name drive.forwardforecasting.eu;

    location / {
        proxy_pass         http://127.0.0.1:8505;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade $http_upgrade;
        proxy_set_header   Connection "upgrade";
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_read_timeout 86400;
    }
}
EOF
        sudo cp /tmp/nginx-drive-http.conf /etc/nginx/sites-available/drive.conf
        sudo ln -sf /etc/nginx/sites-available/drive.conf /etc/nginx/sites-enabled/drive.conf
        sudo nginx -t
        sudo systemctl reload nginx
REMOTE
    ok "HTTP nginx config active"
    warn "Run with --ssl once DNS points to 54.78.82.101"
fi

# ── Final status ──────────────────────────────────────────────────────────────

echo ""
echo "────────────────────────────────────────────────────────"
$SSH -i "$KEY" "$SERVER" bash -c "
    echo 'Streamlit:' \$(systemctl is-active cloud-drive-web)
    curl -sf http://localhost:8505/_stcore/health && echo 'Health: OK' || echo 'Health: not ready yet'
"
if [[ "${1:-}" == "--ssl" ]]; then
    ok "Live at → https://drive.forwardforecasting.eu/"
else
    ok "Live at → http://drive.forwardforecasting.eu/ (HTTP only)"
fi
