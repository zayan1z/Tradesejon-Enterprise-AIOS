# 06. CRM & Database Architecture, Native Tooling & Atlas

Tradesejon Enterprise AIOS provides a dual-topology MongoDB architecture:

---

## 1. Zero-Config Bundled Local MongoDB (Default)
When deployed via `1_CLICK_DEPLOY.sh` or `1_CLICK_DEPLOY.bat`:
- Runs official **MongoDB 7 Community** on port `27017` in the `tradesejon_mongodb` container.
- Persistent named volume: `mongo_data:/data/db`.
- Auto-initializes on first launch via `crm-database/init_mongodb.js`.
- Automatically provisions standard schema collections and high-performance compound indexes:
  - `leads` (Indexed on `email`, `phone`, `status`, `created_at`)
  - `members` (Indexed on `discord_id`, `email`, `role`, `access_tier`)
  - `trading_leads` (Indexed on `email`, `telegram_id`, `created_at`)
  - `contacts` (Omnichannel cross-platform directory)
  - `audit_logs` (Security and compliance trail)
  - `kb_articles`, `knowledge_chunks`, `aios_super_memory` (Vector and text RAG indexing)

---

## 2. Remote Cloud Database (MongoDB Atlas)
If the enterprise client prefers managed cloud hosting:
1. In `.env`, set `MONGODB_URI` to your cloud connection string provided by MongoDB Atlas (e.g., standard SRV connection string with database name `tradesejon_prod`).
2. The AIOS dashboard, n8n workflows, and Discord bots automatically connect to Atlas over TLS with auto-reconnection.

---

## 3. Host Native MongoDB Tooling (`mongosh` & Compass GUI)
To inspect, query, or backup MongoDB directly from your host or VPS terminal:

### MongoDB Shell (`mongosh`)
- **Windows (Winget):**
  ```powershell
  winget install --id MongoDB.Shell --accept-package-agreements --accept-source-agreements
  ```
- **macOS (Homebrew):**
  ```bash
  brew tap mongodb/brew && brew install mongosh
  ```
- **Linux (Debian/Ubuntu):**
  ```bash
  sudo apt-get install -y gnupg curl
  curl -fsSL https://pgp.mongodb.com/server-7.0.asc | sudo gpg -o /usr/share/keyrings/mongodb-server-7.0.gpg --dearmor
  echo "deb [ arch=amd64,arm64 signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg ] https://repo.mongodb.org/apt/ubuntu $(lsb_release -cs)/mongodb-org/7.0 multiverse" | sudo tee /etc/apt/sources.list.d/mongodb-org-7.0.list
  sudo apt-get update && sudo apt-get install -y mongodb-mongosh
  ```

### Instant Database Health Verification:
```bash
mongosh "mongodb://127.0.0.1:27017/tradesejon_prod" --quiet --eval "JSON.stringify(db.runCommand({ping:1}))"
# Expected output: {"ok":1}
```

### MongoDB Compass (Desktop GUI)
For visual, point-and-click management of CRM leads, members, and AI knowledge:
- **Windows:** `winget install --id MongoDB.Compass.Full`
- **macOS:** `brew install --cask mongodb-compass`
- **Connect URI:** `mongodb://127.0.0.1:27017`
