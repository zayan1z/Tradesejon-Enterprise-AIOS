# 02. One-Click Client VPS Deployment Guide

## 1. Prerequisites on the Client VPS
Install Docker & Docker Compose v2 on any Ubuntu/Debian VPS (runs 24/7 with your laptop shut down):
```bash
curl -fsSL https://get.docker.com | sh
git clone <YOUR_REPOSITORY_URL>
cd tradesejon-enterprise-aios
```

## 2. Launch the Complete 5-Container Stack in One Click
```bash
chmod +x 1_CLICK_DEPLOY.sh
./1_CLICK_DEPLOY.sh
```
On Windows, double-click **`1_CLICK_DEPLOY.bat`**.

### What Starts Automatically:
1. **`tradesejon_mongodb` (Port `27017`):** Automatically initializes CRM (`leads`, `members`, `trading_leads`, `contacts`, `audit_logs`) and RAG Knowledge (`kb_articles`, `knowledge_chunks`, `aios_super_memory`) indexes via `crm-database/init_mongodb.js`.
2. **`tradesejon_redis`:** Queue and cache store.
3. **`tradesejon_n8n_engine` (Port `5678`):** `docker/n8n.Dockerfile` pre-installs `mongodb`, `nodemailer`, and `axios` with `NODE_PATH=/usr/local/lib/node_modules`. `docker/n8n-entrypoint.sh` automatically imports workflows `00`–`21` plus `Template_Native_Telegram_Bot_Agent` and publishes modular workflows `00`–`21` on first boot.
4. **`tradesejon_aios_cloud` (Port `3000`):** Web Command Center (`/`), **Knowledge & Data Sync Studio (`/knowledge.html`)**, Live CRM Explorer (`/db`), and Lead Intake Form (`/form`), sharing the live `./rag-knowledge-base` volume.
5. **`tradesejon_discord_bots`:** Runs all Discord personas (`CEO`, `PR`, `Trader`), connected directly to internal `http://n8n:5678`, `http://aios_dashboard:3000`, and MongoDB.

## 3. Production HTTPS Reverse Proxy (Telegram & Discord Webhooks)
Telegram requires a public HTTPS endpoint (`WEBHOOK_URL` in `.env`) to deliver messages to your VPS. Point Caddy or Nginx at your VPS domain:
- `https://n8n.yourdomain.com` -> `localhost:5678`
- `https://aios.yourdomain.com` -> `localhost:3000`

After setting `WEBHOOK_URL=https://n8n.yourdomain.com/` in `.env`, run:
```bash
docker compose up -d
python3 scripts/client_health_check.py
```

## 4. Resilience When Workflows Are Unpublished
- All workflows run inside **your client's VPS n8n** (`http://n8n:5678`), completely independent of any external developer workstation.
- **Modular Failover:** If individual workflows (`#01`–`#21`) are unpublished or deactivated in n8n UI, AIOS and Discord bots detect inactive webhooks (`404`/`503`) and automatically fail over to **Workflow `#00` (`/webhook/tradesejon-unified`)** or the local AIOS + MongoDB engine with zero service interruption.
- To restore and publish all modular workflows via the n8n REST API at any time:
  ```bash
  export N8N_API_KEY="your_key_from_n8n_settings"
  python3 scripts/import_to_n8n.py
  ```
