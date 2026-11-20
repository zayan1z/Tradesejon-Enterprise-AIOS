#!/bin/sh
set -e
# Auto-import all individual workflows on first boot if marker doesn't exist
MARKER="/home/node/.n8n/.tradesejon_imported"
if [ ! -f "$MARKER" ] && [ -d "/home/node/tradesejon_workflows" ]; then
  echo "[Tradesejon-Init] Auto-importing all individual n8n workflows..."
  for f in /home/node/tradesejon_workflows/[0-2][0-9]_*.json; do
    n8n import:workflow --input="$f" || true
  done
  n8n update:workflow --all --active=true || true
  touch "$MARKER"
  echo "[Tradesejon-Init] All workflows imported and activated!"
fi
exec n8n start
