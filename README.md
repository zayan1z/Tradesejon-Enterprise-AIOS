# Enterprise Autonomous Trading AIOS & n8n VPS Suite (White-Label Edition)

> **Turnkey Multi-Agent Trading System, 1-Click Docker Deployment, 20 n8n Workflows, RAG Knowledge Engine, Discord Tri-Bots, and CRM Platform.**  
> 100% White-Label Parameterized • Zero Hardcoded Credentials • Zero Client Data • Remotely Maintainable

---

## Executive Overview

This repository is a complete, **legal white-label enterprise platform** ready for instant deployment on any client VPS. With **one click** (`1_CLICK_DEPLOY.sh` or `1_CLICK_DEPLOY.bat`), the system provisions:
- **Containerized n8n Workflow Engine** (`mongodb`, `nodemailer`, and `axios` pre-installed) with **automatic workflow import & activation on first boot**.
- **Built-in MongoDB 7 + Redis Queue Stack** (or seamless connection to your own MongoDB Cluster).
- **Dynamic White-Label Branding** via `$env.BRAND_NAME`, `$env.MENTORS_LIST`, `$env.SUPPORTED_PLATFORMS`, and custom pricing tiers.
- **20 Autonomous n8n Workflows + Single-File UI Entrypoint (`00_Master_SingleFile_UI_Import_Gateway.json`)**.

---

## One-Click Launch (2 Steps)
1. Clone this repository and run the 1-Click launcher:
   - Linux VPS: `./1_CLICK_DEPLOY.sh`
   - Windows: Double-click `1_CLICK_DEPLOY.bat`
2. Populate your API credentials in the wizard / `.env`, open n8n (`http://localhost:5678` or your HTTPS domain), and click **Execute** on Workflow `#20` (`20_Tradesejon_OneClick_Telegram_And_Discord_Provisioner`).

---

## Documentation Index

| Document | Coverage |
| :--- | :--- |
| [01. System Architecture](docs/01_SYSTEM_ARCHITECTURE.md) | End-to-end architecture diagrams and zero-trust boundaries |
| [02. One-Click VPS Deployment](docs/02_VPS_DEPLOYMENT_GUIDE.md) | Docker Compose stack, auto-import architecture, and SSL termination |
| [03. n8n Credentials & Auto-Provisioning](docs/03_N8N_CREDENTIALS_AND_PROVISIONING.md) | Adding API keys and 1-click webhook binding via Workflow #20 |
| [04. Complete Workflows Catalog](docs/04_WORKFLOWS_CATALOG.md) | Full reference for Workflow #00–#20 |
| [05. AI Agents & RAG Engine](docs/05_AI_AGENTS_AND_RAG_ENGINE.md) | Executive NLP, Voice STT/TTS, and 17-FAQ retrieval |
| [06. CRM & Database Schema](docs/06_CRM_AND_DATABASE_SCHEMA.md) | MongoDB collections, indexes, and Sheets sync |
| [07. API & Webhook Reference](docs/07_API_AND_WEBHOOK_REFERENCE.md) | All REST webhook contracts and auth headers |
| [08. Client Operations Manual](docs/08_CLIENT_OPERATIONS_MANUAL.md) | Executive bot and Web AIOS daily usage handbook |
| [09. Remote Maintenance & Development](docs/09_REMOTE_MAINTENANCE_AND_DEV_GUIDE.md) | Zero-knowledge post-sale upgrades via GitHub |
| [10. Security & Verification Protocol](docs/10_SECURITY_AND_VERIFICATION_PROTOCOL.md) | Verified Delivery Guard preventing false confirmations |
| [11. Testing & Troubleshooting](docs/11_TESTING_AND_TROUBLESHOOTING.md) | Automated verification checks & unit tests |
| [12. White-Label Branding Guide](docs/12_WHITE_LABEL_CUSTOMIZATION.md) | Customizing brand name, mentors, brokers, and pricing in `.env` |
