# 11. Testing & Troubleshooting Guide

Comprehensive quality assurance, health checks, and diagnosis procedures for Tradesejon Enterprise AIOS.

---

## 1. Quick Verification Commands

Run these from the repository root:

```bash
# 1. Zero-Leakage & White-Label Sanitize Audit
python scripts/sanitize_check.py

# 2. Comprehensive 43-Step Automated Test Suite
pytest -v discord-bots/

# 3. Client VPS Live Container & Engine Health Check
python scripts/client_health_check.py
```

---

## 2. Common Symptoms & Diagnostic Resolution

| Symptom | Probable Cause | Action / Fix |
|---|---|---|
| `Failed to connect to 127.0.0.1:27017` | MongoDB container not started | Run `docker compose up -d mongodb` or check `docker compose logs mongodb`. |
| Port `27017` already in use | Another MongoDB service running on host | Stop conflicting service (`Get-NetTCPConnection -LocalPort 27017` on Windows, or `ss -tulpn \| grep 27017` on Linux). |
| `mongosh` not recognized | CLI shell not added to `PATH` | Restart terminal or install via `winget install MongoDB.Shell` / `brew install mongosh`. |
| AIOS Web Dashboard returns HTTP 502 / timeout | Container dependencies initializing | Wait 10 seconds for MongoDB health check to complete; inspect `docker compose logs aios_dashboard`. |
| n8n Webhook returns 404 | Specific modular workflow unpublished | Unified fallback automatically catches incoming requests via `/webhook/tradesejon-unified`. Restore modular endpoints with `python scripts/import_to_n8n.py`. |
| Telegram Bot webhook delivery failure | Insecure or missing `WEBHOOK_URL` | Ensure your VPS reverse proxy has a valid SSL certificate (`https://`) and `WEBHOOK_URL` is configured in `.env`. |

---

## 3. Database Ping & Verification via `mongosh`

```bash
# Test connection and ping the server engine:
mongosh "mongodb://127.0.0.1:27017/tradesejon_prod" --quiet --eval "JSON.stringify(db.runCommand({ping:1}))"
# Expected response: {"ok":1}
```
