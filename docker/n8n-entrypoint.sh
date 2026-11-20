#!/bin/sh
set -e

mkdir -p /home/node/rag-knowledge-base/uploads

MARKER="/home/node/.n8n/.tradesejon_imported"
if [ ! -f "$MARKER" ] || [ "${N8N_FORCE_REIMPORT:-false}" = "true" ]; then
  if [ -d "/home/node/tradesejon_workflows" ]; then
    echo "[Tradesejon-Init] Auto-importing modular n8n workflows (00-21 + templates)..."
    for f in /home/node/tradesejon_workflows/[0-2][0-9]_*.json; do
      [ -f "$f" ] && n8n import:workflow --input="$f" || true
    done
    if [ -f "/home/node/tradesejon_workflows/Template_Native_Telegram_Bot_Agent.json" ]; then
      n8n import:workflow --input="/home/node/tradesejon_workflows/Template_Native_Telegram_Bot_Agent.json" || true
    fi
    echo "[Tradesejon-Init] Auto-publishing core workflows for 1-click VPS operation..."
    n8n update:workflow --all --active=true || true
    touch "$MARKER"
    echo "[Tradesejon-Init] All client VPS n8n workflows imported & activated!"
  fi
fi

exec n8n start
