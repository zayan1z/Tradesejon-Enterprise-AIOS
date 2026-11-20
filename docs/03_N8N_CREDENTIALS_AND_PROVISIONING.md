# 03. Client Credentials & 1-Click Provisioning Checklist

This repository ships with **zero hardcoded keys or client secrets**. Every API key lives strictly in your VPS `.env` file (created automatically by `python3 scripts/setup_wizard.py`) or in n8n Credentials.

---

## 1. Complete API Key & Token Checklist (What the Client Enters)

### Required for Core Autonomous Operation
| Key in `.env` | Where to Get It | What It Powers |
| :--- | :--- | :--- |
| `BRAND_NAME` | Any business name | Customizes prompts, Web UI, Telegram, and RAG replies |
| `WEBHOOK_URL` | Your HTTPS domain (`https://n8n.yourdomain.com/`) | Lets Telegram reach your VPS n8n webhooks 24/7 |
| `GROQ_API_KEY` | [console.groq.com](https://console.groq.com) | Fast executive LLM reasoning + Whisper voice transcription (STT) |
| `TELEGRAM_CEO_BOT_TOKEN` | Telegram `@BotFather` (`/newbot`) | Executive command & voice control bot |
| `CEO_USER_IDS` | Telegram `@userinfobot` (numeric ID) | Strict executive authorization whitelist for CEO commands |

### Channel & Community Tokens (Recommended)
| Key in `.env` | Where to Get It | What It Powers |
| :--- | :--- | :--- |
| `TELEGRAM_PR_BOT_TOKEN` | Telegram `@BotFather` | Public relations Q&A + gated-asset guard bot |
| `TELEGRAM_TRADER_BOT_TOKEN` | Telegram `@BotFather` | Community support & live session bot |
| `DISCORD_BOT_TOKEN` | Discord Developer Portal -> Bot | Powers all Discord bots (or use `DISCORD_CEO_BOT_TOKEN`, `DISCORD_PR_BOT_TOKEN`, `DISCORD_TRADER_BOT_TOKEN` for 3 separate bots) |
| `DISCORD_GUILD_ID` | Right-click Discord server -> Copy Server ID | Target Discord community server |
| `DISCORD_PUBLIC_KEY` | Discord Developer Portal -> General Info | Ed25519 signature verification for `/api/discord` |
| `N8N_API_KEY` | n8n UI -> Settings -> n8n API -> Create Key | Lets Workflow `#20` and `scripts/import_to_n8n.py` auto-publish & audit workflows via API |

### Optional Business Extensions
| Key in `.env` | Purpose |
| :--- | :--- |
| `MONGODB_URI` | Defaults to `mongodb://mongodb:27017/tradesejon_prod`; replace with MongoDB Atlas SRV URI if hosting externally |
| `GEMINI_API_KEY` / `OPENAI_API_KEY` | Secondary multimodal vision / backup LLM providers |
| `ELEVENLABS_API_KEY` & `ELEVENLABS_VOICE_ID` | Custom voice replies in AIOS Web Chat (`Workflow #19`) |
| `WHOP_API_KEY`, `WHOP_WEBHOOK_SECRET`, `WHOP_CHECKOUT_URL` | Automated payment fulfillment & role provisioning (`Workflow #06` & `#12`) |
| `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, `GMAIL_USER`, `GMAIL_APP_PASSWORD` | Automated executive & VIP onboarding emails (`Workflow #04` & `#15`) |
| `TRADESAJON_GSHEET_ID` | Google Sheet CRM export target (`Workflow #10`) |

---

## 2. One-Click Auto-Binding via Workflow #20

1. Open n8n UI at `http://YOUR_VPS_IP:5678` (or `https://n8n.yourdomain.com`).
2. Open **Workflow `#20` (`20_Tradesejon_OneClick_Telegram_And_Discord_Provisioner`)**.
3. Click **Execute Workflow**.

Workflow `#20` performs four automated checks in one click:
- Activates any unpublished `[0-2][0-9]_*` workflows if `N8N_API_KEY` is present in `.env`.
- Binds `CEO_BOT`, `PR_BOT`, and `TRADER_BOT` webhooks automatically (and fails over to `/webhook/tradesejon-unified` if a specialized workflow was intentionally unpublished).
- Verifies Discord bot token authentication against Discord API v10.
- Initializes MongoDB indexes and seeds custom files from `rag-knowledge-base/custom_knowledge.json`.

---

## 3. Visual n8n Native Bot Setup (No Code Editing)
Want a client to swap a Telegram bot purely from the n8n drag-and-drop UI?
1. Import or open **`Template_Native_Telegram_Bot_Agent.json`** inside n8n.
2. Open the **Telegram Trigger** and **Telegram Reply** nodes -> select **Create New Credential** -> paste the bot token.
3. Click **Publish** (`Active`). No environment restart is required.
