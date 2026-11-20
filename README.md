# Enterprise Autonomous Trading AIOS & n8n VPS Suite (White-Label Edition)

> **Turnkey Multi-Agent Platform, 1-Click Docker Deployment, 21 Modular n8n Workflows + Unified Gateway, RAG Knowledge & File Sync Studio, Discord Tri-Bots, and CRM Suite.**  
> 100% White-Label Parameterized • Zero Hardcoded Credentials • Zero Client Data • Unpublished-Workflow Resilient • Remotely Maintainable

---

## Executive Overview

This repository is a complete, **legal white-label enterprise platform** engineered for single-command deployment on any client VPS (completely independent of your local workstation). Running **`1_CLICK_DEPLOY.sh`** (or **`1_CLICK_DEPLOY.bat`**) provisions a self-contained 5-container stack:
- **Containerized n8n Workflow Engine** (`mongodb`, `nodemailer`, and `axios` pre-installed with `NODE_PATH` configured) that automatically imports and activates all workflows on first boot.
- **Built-in MongoDB 7 + Redis Queue Stack** (auto-initialized with CRM & RAG Knowledge indexes via `crm-database/init_mongodb.js`, or pointed to an external MongoDB cluster).
- **AIOS Command Dashboard & Easy Knowledge Studio (`http://localhost:3000/knowledge.html`)** — client uploads any text, Word (`.docx`), Excel (`.xlsx`), `.pdf`, `.csv`, `.json`, or `.md` file and it syncs automatically across MongoDB, n8n Workflow `#17`, AIOS Web Chat, Discord Tri-Bots, and Telegram Bots.
- **Unpublished-Workflow Resilience** — even if individual workflows (`#01`–`#21`) are unpublished or toggled off, **Workflow `#00` (`00_Master_SingleFile_UI_Import_Gateway`)** plus built-in AIOS/MongoDB fail-safes keep core chat, RAG knowledge retrieval, CRM funnel sync, and Telegram webhook execution running cleanly.
- **Dynamic White-Label Branding** via `$env.BRAND_NAME`, `$env.MENTORS_LIST`, `$env.SUPPORTED_PLATFORMS`, and custom pricing tiers in `.env`.

---

## One-Click Launch (Client VPS)

1. Clone this repository onto your VPS (or Windows machine) and run the 1-click launcher:
   - **Windows (PowerShell Beginner Wizard):** Right-click `1_CLICK_INSTALL.ps1` -> *Run with PowerShell* (or double-click `1_CLICK_DEPLOY.bat`)
   - **Linux VPS:** `chmod +x 1_CLICK_DEPLOY.sh && ./1_CLICK_DEPLOY.sh`
2. Enter your API keys in the interactive Setup Wizard (or populate `.env`).
3. Open n8n (`http://YOUR_VPS_IP:5678` or your HTTPS domain), open **Workflow `#20` (`20_Tradesejon_OneClick_Telegram_And_Discord_Provisioner`)**, and click **Execute Workflow** to bind Telegram webhooks, verify Discord, and finalize indexes.
4. Open **`http://YOUR_VPS_IP:3000/knowledge.html`** to paste your business rules/pricing or drag-and-drop your documents once — synced system-wide in seconds.

---

## Documentation Index

| Document | Coverage |
| :--- | :--- |
| [01. System Architecture](docs/01_SYSTEM_ARCHITECTURE.md) | End-to-end architecture diagrams and zero-trust boundaries |
| [02. One-Click VPS Deployment](docs/02_VPS_DEPLOYMENT_GUIDE.md) | Docker Compose 5-container stack, auto-import, SSL & client VPS independence |
| [03. Credentials & 1-Click Provisioning](docs/03_N8N_CREDENTIALS_AND_PROVISIONING.md) | Exact API keys checklist (Required vs Optional) & Workflow #20 auto-binding |
| [04. Complete Workflows Catalog](docs/04_WORKFLOWS_CATALOG.md) | Full reference for Workflows `#00`–`#21`, Templates & unpublished-workflow resilience |
| [05. AI Agents & RAG Engine](docs/05_AI_AGENTS_AND_RAG_ENGINE.md) | Executive NLP, Voice STT/TTS, and retrieval architecture |
| [06. CRM & Database Schema](docs/06_CRM_AND_DATABASE_SCHEMA.md) | MongoDB collections, auto-created indexes, and Sheets sync |
| [07. API & Webhook Reference](docs/07_API_AND_WEBHOOK_REFERENCE.md) | All REST endpoints (`/api/knowledge`, `/api/aios-chat`, `/api/system-status`) & n8n webhooks |
| [08. Client Operations Manual](docs/08_CLIENT_OPERATIONS_MANUAL.md) | Executive bot and Web AIOS daily usage handbook |
| [09. Remote Maintenance & Development](docs/09_REMOTE_MAINTENANCE_AND_DEV_GUIDE.md) | Zero-knowledge post-sale upgrades via GitHub |
| [10. Security & Verification Protocol](docs/10_SECURITY_AND_VERIFICATION_PROTOCOL.md) | Verified Delivery Guard preventing false confirmations |
| [11. Testing & Troubleshooting](docs/11_TESTING_AND_TROUBLESHOOTING.md) | Automated verification checks, health script & unit tests |
| [12. White-Label Branding Guide](docs/12_WHITE_LABEL_CUSTOMIZATION.md) | Customizing brand name, mentors, brokers, and pricing in `.env` |
| [13. Easy Data & File Sync Guide](docs/13_EASY_DATA_AND_FILE_SYNC_GUIDE.md) | User-friendly guide: paste text or drag-and-drop PDF/Word/Excel/CSV files to sync everywhere |
