#!/usr/bin/env bash
set -euo pipefail
echo "==================================================================="
echo "   White-Label Enterprise AIOS — One-Click Cloud VPS Deployment"
echo "==================================================================="
if [ ! -f .env ]; then
  echo "[*] Creating .env from .env.example and running interactive wizard..."
  python3 scripts/setup_wizard.py
fi
echo "[*] Starting Full Cloud Stack (n8n Auto-Import, AIOS Web, Discord Tri-Bots, MongoDB, Redis)..."
docker compose up -d --build
echo "[+] Wait 10 seconds for n8n initialization, then open Workflow #20 -> Execute!"
