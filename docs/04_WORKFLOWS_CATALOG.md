# 04. Complete Workflows Catalog & Resilience Design

All workflows reside in `n8n-workflows/` (24 JSON files: Gateway `#00`, Modular Workflows `#01`–`#21`, Visual Template `Template_Native_Telegram_Bot_Agent`, and `Tradesejon_Enterprise_Complete_Suite_Master_Bundle`).

| ID | Workflow Name | Role & Resilience |
| :-: | :--- | :--- |
| **00** | `00_Master_SingleFile_UI_Import_Gateway` | **Universal Single-File Entrypoint & Fail-Safe Router** (`/webhook/tradesejon-unified`): handles CEO commands, RAG search/ingest, CRM funnel sync, and Telegram dispatch even when individual workflows `#01`–`#21` are unpublished |
| **01** | `01_Tradesejon_CEO_Autonomous_AI_Agent` | Executive Masri/English brain, Whisper Voice STT, live market quotes, Discord DMs, Verified Delivery Guard |
| **02** | `02_Tradesejon_PR_Community_AI_Agent_17FAQs` | Public relations Q&A and Prospect Asset Guard |
| **03** | `03_Tradesejon_Trader_Support_Agent` | Strategy coaching & session reminders |
| **04** | `04_Tradesejon_Omnichannel_CrossPlatform_Router` | Verified delivery hub across Discord, Telegram & Email |
| **05** | `05_Tradesejon_Lead_Intake_Validation_And_Whop_OTP_Gate` | Email/phone validation, CRM upsert, OTP checkout link |
| **06** | `06_Tradesejon_Whop_Webhook_ZeroTrust_Fulfillment_And_Revoke` | HMAC-SHA256 verified billing role grant & auto-revoke |
| **07** | `07_Tradesejon_Agentic_Trader_Support_17FAQ_And_Prospect_Guard` | Community Q&A + gated resource firewall |
| **08** | `08_Tradesejon_CEO_Admin_Command_Center_And_Targeted_DM` | Executive direct Discord messaging command |
| **09** | `09_Tradesejon_Offline_Workshop_Launch_Form_And_Seats` | Token-protected workshop booking with seat limit (`WORKSHOP_MAX_SEATS`) |
| **10** | `10_Tradesejon_Google_Sheets_Master_Auto_Sync_Forever` | 5-min MongoDB to Google Sheets synchronization |
| **11** | `11_Tradesejon_Self_Healing_Error_Watcher_And_Audit` | Global Error Trigger logging redacted traces & alerting CEO |
| **12** | `12_Tradesejon_Whop_Payment_Reconciler_Cron` | Scheduled billing state reconciliation |
| **13** | `13_Tradesejon_Daily_Live_Session_Broadcast_Cron` | Daily 15-min single-use Discord/Zoom invite generator |
| **14** | `14_Tradesejon_12_Markets_Hourly_Pulse_Watcher` | Hourly market volatility briefing |
| **15** | `15_Tradesejon_Lead_Approval_And_VIP_Email_Dispatcher` | One-click VIP onboarding HTML email sender |
| **16** | `16_Tradesejon_Zoom_And_Live_Session_Gateway` | Authenticated member live link issuance |
| **17** | `17_Tradesejon_RAG_Knowledge_Base_Indexer_And_Retriever` | Dual webhook (`/webhook/tradesejon-rag-query` + `/webhook/tradesejon-rag-ingest`): searches and indexes custom uploaded files, MongoDB `knowledge_chunks`, Disk KB, and FAQs |
| **18** | `18_Tradesejon_CRM_Full_Pipeline_And_Lead_Scoring` | Live MongoDB CRM stats, leads list, and scoring |
| **19** | `19_Tradesejon_AIOS_Web_Chat_And_ElevenLabs_Voice` | Web AIOS Orb backend with ElevenLabs TTS |
| **20** | `20_Tradesejon_OneClick_Telegram_And_Discord_Provisioner` | 1-click workflow auto-publish, Telegram webhook binding (with `#00` fallback), Discord check, MongoDB index & KB seeder |
| **21** | `21_Tradesejon_Discord_Events_And_Funnel_Sync` | Receives Discord Tri-Bot modals, leads, role changes & syncs CRM (`POST /webhook/tradesejon-funnel-sync`) |
| **Template** | `Template_Native_Telegram_Bot_Agent` | Visual n8n Native `Telegram Trigger` + Groq AI + `Telegram Send` (plug in `@BotFather` credential in UI) |

---

## Unpublished-Workflow Behavior
Each workflow operates independently inside the client's VPS n8n:
- Unpublishing any workflow does **not** crash other workflows or containers.
- AIOS Web (`aios-dashboard/api/_n8n_bridge.js`) and Discord Tri-Bots (`crm_sync.py`) automatically fail over to **Workflow `#00` (`/webhook/tradesejon-unified`)** or built-in local handlers whenever a modular webhook returns `404` or `503`.
