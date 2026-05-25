#!/bin/bash
# deploy.sh — ติดตั้ง NovelFlow บน Ubuntu 22.04 Droplet
# รัน: sudo bash deploy.sh
# ────────────────────────────────────────────────────────────

set -e
DEPLOY_DIR="/var/www/novel-translation"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  NovelFlow — Deploy Script"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── 1. System packages ──────────────────────────────────────
echo "[1/7] Installing system packages..."
apt-get update -qq
apt-get install -y -q python3.11 python3.11-venv python3-pip nodejs npm nginx

# ── 2. Create deploy dir ────────────────────────────────────
echo "[2/7] Setting up directories..."
mkdir -p $DEPLOY_DIR/backend $DEPLOY_DIR/dist

# ── 3. Copy backend files ───────────────────────────────────
echo "[3/7] Copying backend files..."
cp backend/*.py $DEPLOY_DIR/backend/
cp backend/requirements.txt $DEPLOY_DIR/backend/

# ── 4. Python virtualenv + install ─────────────────────────
echo "[4/7] Creating Python venv and installing dependencies..."
python3.11 -m venv $DEPLOY_DIR/venv
$DEPLOY_DIR/venv/bin/pip install --upgrade pip -q
$DEPLOY_DIR/venv/bin/pip install -r $DEPLOY_DIR/backend/requirements.txt -q

# ── 5. Build frontend ───────────────────────────────────────
echo "[5/7] Building React frontend..."
cd frontend
npm install --silent
VITE_API_URL="" npm run build --silent
cp -r dist/* $DEPLOY_DIR/dist/
cd ..

# ── 6. Nginx config ─────────────────────────────────────────
echo "[6/7] Configuring Nginx..."
cp nginx.conf /etc/nginx/sites-available/novelflow
ln -sf /etc/nginx/sites-available/novelflow /etc/nginx/sites-enabled/novelflow
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

# ── 7. Systemd service ──────────────────────────────────────
echo "[7/7] Installing systemd service..."
cp novelflow.service /etc/systemd/system/novelflow.service
chown -R www-data:www-data $DEPLOY_DIR
systemctl daemon-reload
systemctl enable novelflow
systemctl restart novelflow

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✅ Deploy สำเร็จ!"
echo ""
echo "  🌐 เปิดได้ที่:  http://$(curl -s ifconfig.me)"
echo "  📋 Logs:       journalctl -u novelflow -f"
echo "  🔄 Restart:    systemctl restart novelflow"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
