#!/usr/bin/env bash
set -euo pipefail

echo "========================================================================="
echo "   White-Label Enterprise AIOS — One-Click Client VPS Cloud Deployment"
echo "========================================================================="

mkdir -p rag-knowledge-base/uploads

if [ ! -f .env ]; then
  echo "[*] Creating .env via Setup Wizard..."
  if [ -t 0 ]; then
    python3 scripts/setup_wizard.py
  else
    python3 scripts/setup_wizard.py --non-interactive
  fi
fi

echo "[*] Launching 5-Container Cloud Stack (MongoDB, Redis, n8n, AIOS Web & Knowledge Studio, Discord Tri-Bots)..."
docker compose up -d --build

echo ""
echo "[*] Waiting for services to become healthy..."
sleep 8
python3 scripts/client_health_check.py || true

echo ""
echo "========================================================================="
echo " [+] DEPLOYMENT READY!"
echo "     • n8n Automation Studio   : http://localhost:5678  (Workflow #20 -> Execute)"
echo "     • AIOS Command Dashboard  : http://localhost:3000"
echo "     • Easy Knowledge & Files  : http://localhost:3000/knowledge.html"
echo "     • Live Mongo CRM Explorer : http://localhost:3000/db"
echo "========================================================================="
