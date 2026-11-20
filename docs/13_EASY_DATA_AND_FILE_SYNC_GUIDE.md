# 13. Easy Guide: Add Any Text, Files, Pricing & Business Data (Auto-Synced System-Wide)

This guide is written for business owners and non-technical operators. Whenever you add or update any text, pricing rule, policy, or document using **any method below**, the platform automatically extracts, chunks, and synchronizes that information across:
- **AIOS Web Chat** (`/`)
- **MongoDB Knowledge Store** (`kb_articles`, `knowledge_chunks`, `aios_super_memory`)
- **n8n Workflow `#17` (RAG Engine)** & fallback **Workflow `#00`**
- **Telegram CEO / PR / Trader Bots**
- **Discord Tri-Bots** (immediately loaded in prompt & FAQ search without restarting bots)

---

## Method 1: Drag-and-Drop or Paste in the Web Knowledge Studio (Easiest)

1. Open **`http://YOUR_VPS_IP:3000/knowledge.html`** (or click **Knowledge Studio** inside the AIOS Dashboard sidebar or DB Explorer).
2. Choose either card:
   - **Card 1 — Paste Any Text, Rules, Pricing, or FAQs:**
     - Enter a short **Title** (e.g., `2026 VIP Pricing & Mentors Schedule`).
     - Select a **Category** (`Pricing & Plans`, `Customer FAQ`, `Mentors & Schedule`, `Policies`, or `General`).
     - Paste any English or Arabic text into **Knowledge Content**.
     - Click **Save & Sync Across Entire System**.
   - **Card 2 — Drag & Drop Any File:**
     - Drag one or multiple files directly into the dropzone:
       - Plain text & docs: `.txt`, `.md`
       - Microsoft Office: `.docx` (Word), `.xlsx` (Excel)
       - Spreadsheets & structured files: `.csv`, `.tsv`, `.json`
       - Web & digital documents: `.html`, digital text-based `.pdf`
3. Verify the green **Sync Receipt** confirming Disk KB, MongoDB Chunks, n8n RAG Route, Discord, and Telegram are all updated.
4. Use **Section 3 (Test Live RAG Recall)** on the same page to ask a test question and verify instant recall.

---

## Method 2: Add Knowledge Directly from Telegram or AIOS Chat

Authorized executives can update the system in one message inside **AIOS Web Chat** or the **CEO Telegram Bot**:
```text
/addkb Refund Policy | Digital memberships can be canceled anytime before the next billing cycle inside Whop.
```
You can also type:
```text
add kb: Our live coaching sessions run Monday through Friday at 9:30 PM local time inside Discord VIP.
```
The system stores the entry, updates MongoDB + `rag-knowledge-base/custom_knowledge.json`, triggers n8n Workflow `#17`, and replies with a verification receipt.

---

## Method 3: Folder Drop & 1-Command Bulk Upload (For Large Archives)

If you have 10, 50, or 100 files on your VPS:
1. Copy your files (`.pdf`, `.docx`, `.xlsx`, `.csv`, `.json`, `.md`, `.txt`) into:
   ```text
   rag-knowledge-base/uploads/
   ```
2. Run one command on the server:
   ```bash
   python3 scripts/sync_knowledge.py
   ```
Every file in `uploads/` is extracted, split into searchable RAG chunks, saved in MongoDB, and pushed to n8n Workflow `#17`.

You can also sync a single text snippet from the terminal:
```bash
python3 scripts/sync_knowledge.py --title "VIP Schedule" --category schedule --text "Sessions start at 9:30 PM daily."
```

---

## Method 4: Update White-Label Variables, Leads & Structured CRM Data

- **Brand Name, Mentors, Pricing Numbers, Session Hours:** Edit `.env` (`BRAND_NAME`, `MENTORS_LIST`, `SUPPORTED_PLATFORMS`, `VIP_MONTHLY_USD`, `WORKSHOP_MEMBER_PRICE`, `LIVE_SESSION_SCHEDULE`) and run `docker compose up -d`.
- **Add / Edit Individual Customers or Leads Visually:** Open **`http://YOUR_VPS_IP:3000/db`** (Live MongoDB CRM Explorer) to browse, insert, update, or export collections (`trading_leads`, `contacts`, `kb_articles`, `conversations`).
- **Lead Intake Web Form:** Share **`http://YOUR_VPS_IP:3000/form`** — every submission automatically syncs into MongoDB, AIOS Dashboard KPIs, Discord roles, and n8n Workflows `#05` / `#18` / `#21`.

---

## Troubleshooting File Sync

- **Scanned image-only PDFs:** If a PDF contains only scanned photographs rather than selectable text, convert or OCR it into `.txt`, `.docx`, or searchable PDF first so every word is indexed accurately.
- **Check live system sync status anytime:** Run `python3 scripts/client_health_check.py` or open the top status cards inside `/knowledge.html`.
