# 03. n8n Credentials & One-Click Provisioning

Run `python scripts/setup_wizard.py` (or edit `.env`) to enter your client's API keys:
- `TELEGRAM_CEO_BOT_TOKEN`, `TELEGRAM_PR_BOT_TOKEN`, `TELEGRAM_TRADER_BOT_TOKEN`
- `DISCORD_BOT_TOKEN`, `DISCORD_GUILD_ID`
- `GROQ_API_KEY`, `ELEVENLABS_API_KEY`
- `WHOP_API_KEY`, `WHOP_WEBHOOK_SECRET`
- `MONGODB_URI` (defaults automatically to the bundled local MongoDB container `mongodb://mongodb:27017/tradesejon_prod`)

After containers start, open n8n UI, open **Workflow #20 (`20_Tradesejon_OneClick_Telegram_And_Discord_Provisioner`)**, and click **Execute Workflow** to bind all Telegram webhooks, verify Discord authentication, and install database indexes.
