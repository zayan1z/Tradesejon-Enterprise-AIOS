"""Configuration loader reading strictly from environment variables / ./data/workspace"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import dotenv_values

DEFAULT_ENV_PATH = Path(r"./data/workspace")


def _split_csv_set(raw: str) -> frozenset[str]:
    return frozenset(item.strip() for item in (raw or "").split(",") if item.strip())


@dataclass(frozen=True)
class DiscordConfig:
    """Validated runtime configuration with zero hardcoded secrets."""

    trader_token: str
    pr_token: str
    ceo_token: str
    guild_id: str
    ceo_user_ids: frozenset[str]
    pr_user_ids: frozenset[str]
    pr_role_ids: frozenset[str]
    ceo_channel_ids: frozenset[str]
    pr_channel_ids: frozenset[str]
    trader_channel_ids: frozenset[str]
    announce_channel_id: str
    bridge_secret: str
    aios_internal_token: str
    discord_public_key: str
    aios_base_url: str
    mongo_local_uri: str
    mongo_atlas_uri: str
    pg_dsn: str
    groq_api_key: str
    gemini_api_key: str
    openai_api_key: str
    elevenlabs_api_key: str
    elevenlabs_voice_id: str
    resend_api_key: str
    gmail_user: str
    gmail_app_password: str
    whop_checkout_url: str
    default_zoom_url: str
    default_zoom_id: str
    default_zoom_passcode: str
    google_form_url: str = ""
    videos_url: str = "https://youtube.com/YOUR_TRAINING_PLAYLIST"
    whop_community_vip_url: str = "https://whop.com/Tradesejon/exp_TEMPLATE_VIP"
    whop_plan_offline_member_4000egp: str = "plan_TEMPLATE_03"
    whop_checkout_offline_member_4000egp: str = "https://whop.com/checkout/plan_TEMPLATE_03"
    whop_plan_offline_nonmember_6000egp: str = "plan_TEMPLATE_04"
    whop_checkout_offline_nonmember_6000egp: str = "https://whop.com/checkout/plan_TEMPLATE_04"
    whop_plan_online_MentorAlpha: str = "plan_TEMPLATE_05"
    whop_checkout_online_MentorAlpha: str = "https://whop.com/checkout/plan_TEMPLATE_05"
    whop_plan_online_MentorBeta: str = "plan_TEMPLATE_06"
    whop_checkout_online_MentorBeta: str = "https://whop.com/checkout/plan_TEMPLATE_06"
    whop_plan_online_mentor_3: str = "plan_TEMPLATE_07"
    whop_checkout_online_mentor_3: str = "https://whop.com/checkout/plan_TEMPLATE_07"
    whop_plan_online_MentorDelta: str = "plan_TEMPLATE_08"
    whop_checkout_online_MentorDelta: str = "https://whop.com/checkout/plan_TEMPLATE_08"
    whop_plan_online_mentor_5: str = "plan_TEMPLATE_09"
    whop_checkout_online_mentor_5: str = "https://whop.com/checkout/plan_TEMPLATE_09"
    next_offline_workshop_date: str = "Scheduled Upcoming Workshop Date"
    whop_api_key: str = field(default="", repr=False)
    zoom_account_id: str = ""
    zoom_client_id: str = ""
    zoom_client_secret: str = field(default="", repr=False)
    zoom_gateway_url: str = ""
    discord_live_channel_id: str = "900000000000000002"
    telegram_trading_token: str = field(default="", repr=False)
    telegram_ceo_token: str = field(default="", repr=False)
    telegram_ceo_chat_id: str = "1000000001"
    enable_members_intent: bool = False
    marhaban_channel_id: str = "900000000000000005"
    prospects_channel_id: str = "900000000000000006"
    workshop_channel_id: str = "900000000000000007"
    viproom_channel_id: str = "900000000000000008"
    prospect_role_id: str = "900000000000000009"
    workshop_role_id: str = "900000000000000010"
    viproom_role_id: str = "900000000000000011"
    human_agent_role_id: str = "900000000000000012"
    marhaban_invite_url: str = "https://discord.gg/YOUR_INVITE_CODE"
    marhaban_webhook_url: str = field(default="", repr=False)
    prospect_webhook_url: str = field(default="", repr=False)
    workshop_webhook_url: str = field(default="", repr=False)
    viproom_webhook_url: str = field(default="", repr=False)
    marhaban_token: str = field(default="", repr=False)
    prospect_token: str = field(default="", repr=False)
    workshop_token: str = field(default="", repr=False)
    viproom_token: str = field(default="", repr=False)
    whop_product_id: str = "prod_TEMPLATE_10"
    whop_plan_workshop_45: str = "plan_TEMPLATE_01"
    whop_checkout_workshop_45: str = "https://whop.com/checkout/plan_TEMPLATE_01"
    whop_plan_legacy_45: str = "plan_TEMPLATE_11"
    whop_plan_external_60: str = "plan_TEMPLATE_01"
    whop_checkout_external_60: str = "https://whop.com/checkout/plan_TEMPLATE_01"
    whop_plan_viproom_75: str = "plan_TEMPLATE_01"
    whop_checkout_viproom_75: str = "https://whop.com/checkout/plan_TEMPLATE_01"
    whop_webhook_secret: str = field(default="", repr=False)
    tradesajon_gsheet_id: str = "YOUR_GOOGLE_ID"
    tradesajon_gsheet_url: str = "https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit"
    Tradesejon_drive_folder_id: str = "YOUR_GOOGLE_ID"
    Tradesejon_drive_folder_url: str = "https://drive.google.com/YOUR_DRIVE_RESOURCE"
    Tradesejon_blueprint_id: str = "YOUR_GOOGLE_ID"
    Tradesejon_blueprint_url: str = "https://drive.google.com/YOUR_DRIVE_RESOURCE"
    tradesejon_knowledge_base_dir: str = r"./data/workspace"
    tradesajon_crm_csv_path: str = r"./data/workspace"
    n8n_base_url: str = "http://localhost:5678"
    vps_n8n_base_url: str = "https://n8n.yourdomain.com"
    vps_n8n_api_key: str = field(default="", repr=False)
    mongodb_ai_endpoint: str = "https://ai.mongodb.com/v1"
    mongodb_ai_api_key: str = field(default="", repr=False)
    mongodb_ai_embed_model: str = "voyage-3.5"
    agent_reach_exe: str = r"./data/workspace"
    gsheets_webhook_url: str = field(default="", repr=False)
    bridge_host: str = "127.0.0.1"
    bridge_port: int = 8095
    known_secrets: tuple[str, ...] = field(default_factory=tuple, repr=False)


def load_config(env_path: Path | None = None) -> DiscordConfig:
    """Load configuration from explicit env file merged with process environment."""
    target = env_path or DEFAULT_ENV_PATH
    file_vals = dotenv_values(str(target)) if target.exists() else {}

    def get(key: str, default: str = "") -> str:
        val = os.environ.get(key)
        if val is None:
            val = file_vals.get(key)
        return str(val).strip() if val is not None and str(val).strip() else default

    trader_token = get("DISCORD_TRADER_BOT_TOKEN")
    pr_token = get("DISCORD_PR_BOT_TOKEN")
    ceo_token = get("DISCORD_CEO_BOT_TOKEN")
    bridge_secret = get("DISCORD_BRIDGE_SECRET")
    aios_internal_token = get("AIOS_INTERNAL_TOKEN")
    mongo_atlas_uri = get("MONGODB_ATLAS_URI") or get("MONGODB_URI")
    mongodb_ai_api_key = get("MONGODB_AI_API_KEY")
    pg_dsn = get("QA_POSTGRES_DSN")
    groq_api_key = get("GROQ_API_KEY")
    gemini_api_key = get("GEMINI_API_KEY")
    openai_api_key = get("OPENAI_API_KEY")
    elevenlabs_api_key = get("ELEVENLABS_API_KEY")
    resend_api_key = get("RESEND_API_KEY")
    gmail_app_password = get("GMAIL_APP_PASSWORD") or get("SMTP_PASSWORD")
    telegram_trading_token = get("TELEGRAM_TRADING_TOKEN")
    telegram_ceo_token = get("TELEGRAM_CEO_TOKEN")
    marhaban_token = get("DISCORD_MARHABAN_BOT_TOKEN")
    prospect_token = get("DISCORD_PROSPECT_BOT_TOKEN")
    workshop_token = get("DISCORD_WORKSHOP_BOT_TOKEN")
    viproom_token = get("DISCORD_VIPROOM_BOT_TOKEN")
    marhaban_webhook_url = get("DISCORD_MARHABAN_WEBHOOK_URL")
    prospect_webhook_url = get("DISCORD_PROSPECT_WEBHOOK_URL")
    workshop_webhook_url = get("DISCORD_WORKSHOP_WEBHOOK_URL")
    viproom_webhook_url = get("DISCORD_VIPROOM_WEBHOOK_URL")
    whop_webhook_secret = get("WHOP_WEBHOOK_SECRET")
    vps_n8n_api_key = get("VPS_N8N_API_KEY")
    gsheets_webhook_url = get("TRADESAJON_GSHEETS_WEBHOOK_URL")

    secret_bag = tuple(
        s for s in (
            trader_token,
            pr_token,
            ceo_token,
            telegram_trading_token,
            telegram_ceo_token,
            marhaban_token,
            prospect_token,
            workshop_token,
            viproom_token,
            marhaban_webhook_url,
            prospect_webhook_url,
            workshop_webhook_url,
            viproom_webhook_url,
            whop_webhook_secret,
            vps_n8n_api_key,
            gsheets_webhook_url,
            bridge_secret,
            aios_internal_token,
            mongo_atlas_uri,
            mongodb_ai_api_key,
            pg_dsn,
            groq_api_key,
            gemini_api_key,
            openai_api_key,
            elevenlabs_api_key,
            resend_api_key,
            gmail_app_password,
        )
        if s and len(s) >= 8
    )

    return DiscordConfig(
        trader_token=trader_token,
        pr_token=pr_token,
        ceo_token=ceo_token,
        guild_id=get("DISCORD_GUILD_ID", "900000000000000001"),
        ceo_user_ids=_split_csv_set(get("DISCORD_CEO_USER_IDS")),
        pr_user_ids=_split_csv_set(get("DISCORD_PR_USER_IDS")),
        pr_role_ids=_split_csv_set(get("DISCORD_PR_ROLE_IDS")),
        ceo_channel_ids=_split_csv_set(get("DISCORD_CEO_CHANNEL_IDS")),
        pr_channel_ids=_split_csv_set(get("DISCORD_PR_CHANNEL_IDS")),
        trader_channel_ids=_split_csv_set(get("DISCORD_TRADER_CHANNEL_IDS")),
        announce_channel_id=get("DISCORD_ANNOUNCE_CHANNEL_ID"),
        bridge_secret=bridge_secret,
        aios_internal_token=aios_internal_token,
        discord_public_key=get("DISCORD_PUBLIC_KEY"),
        aios_base_url=get("AIOS_BASE_URL", "http://127.0.0.1:8088").rstrip("/"),
        mongo_local_uri=get("MONGODB_LOCAL_URI", "mongodb://127.0.0.1:27017/"),
        mongo_atlas_uri=mongo_atlas_uri,
        mongodb_ai_endpoint=get("MONGODB_AI_ENDPOINT", "https://ai.mongodb.com/v1").rstrip("/"),
        mongodb_ai_api_key=mongodb_ai_api_key,
        mongodb_ai_embed_model=get("MONGODB_AI_EMBED_MODEL", "voyage-3.5"),
        pg_dsn=pg_dsn,
        groq_api_key=groq_api_key,
        gemini_api_key=gemini_api_key,
        openai_api_key=openai_api_key,
        elevenlabs_api_key=elevenlabs_api_key,
        elevenlabs_voice_id=get("ELEVENLABS_VOICE_ID", "pFZP5JQG7iQjIQuC4Bku"),
        resend_api_key=resend_api_key,
        gmail_user=get("GMAIL_USER", "trader@example.com"),
        gmail_app_password=gmail_app_password,
        whop_api_key=get("WHOP_API_KEY", ""),
        zoom_account_id=get("ZOOM_ACCOUNT_ID", ""),
        zoom_client_id=get("ZOOM_CLIENT_ID", ""),
        zoom_client_secret=get("ZOOM_CLIENT_SECRET", ""),
        zoom_gateway_url=get("ZOOM_GATEWAY_URL", ""),
        discord_live_channel_id=get("DISCORD_LIVE_CHANNEL_ID", "900000000000000002"),
        telegram_trading_token=telegram_trading_token,
        telegram_ceo_token=telegram_ceo_token,
        telegram_ceo_chat_id=get("TELEGRAM_CEO_CHAT_ID", "1000000001"),
        enable_members_intent=get("DISCORD_ENABLE_MEMBERS_INTENT", "").lower() in ("1", "true", "yes"),
        marhaban_channel_id=get("DISCORD_MARHABAN_CHANNEL_ID", "900000000000000005"),
        prospects_channel_id=get("DISCORD_PROSPECTS_CHANNEL_ID", "900000000000000006"),
        workshop_channel_id=get("DISCORD_WORKSHOP_CHANNEL_ID", "900000000000000007"),
        viproom_channel_id=get("DISCORD_VIPROOM_CHANNEL_ID", "900000000000000008"),
        prospect_role_id=get("DISCORD_PROSPECT_ROLE_ID", "900000000000000009"),
        workshop_role_id=get("DISCORD_WORKSHOP_ROLE_ID", "900000000000000010"),
        viproom_role_id=get("DISCORD_VIPROOM_ROLE_ID", "900000000000000011"),
        human_agent_role_id=get("DISCORD_HUMAN_AGENT_ROLE_ID", "900000000000000012"),
        marhaban_invite_url=get("DISCORD_MARHABAN_INVITE_URL", "https://discord.gg/YOUR_INVITE_CODE"),
        marhaban_webhook_url=marhaban_webhook_url,
        prospect_webhook_url=prospect_webhook_url,
        workshop_webhook_url=workshop_webhook_url,
        viproom_webhook_url=viproom_webhook_url,
        marhaban_token=marhaban_token,
        prospect_token=prospect_token,
        workshop_token=workshop_token,
        viproom_token=viproom_token,
        whop_product_id=get("WHOP_PRODUCT_ID", "prod_TEMPLATE_10"),
        whop_plan_workshop_45=get("WHOP_PLAN_WORKSHOP_45", "plan_TEMPLATE_01"),
        whop_checkout_workshop_45=get("WHOP_CHECKOUT_WORKSHOP_45", "https://whop.com/checkout/plan_TEMPLATE_01"),
        whop_plan_legacy_45=get("WHOP_PLAN_LEGACY_45", "plan_TEMPLATE_11"),
        whop_plan_external_60=get("WHOP_PLAN_EXTERNAL_60", "plan_TEMPLATE_01"),
        whop_checkout_external_60=get("WHOP_CHECKOUT_EXTERNAL_60", "https://whop.com/checkout/plan_TEMPLATE_01"),
        whop_plan_viproom_75=get("WHOP_PLAN_VIPROOM_75", "plan_TEMPLATE_01"),
        whop_checkout_viproom_75=get("WHOP_CHECKOUT_VIPROOM_75", "https://whop.com/checkout/plan_TEMPLATE_01"),
        whop_webhook_secret=whop_webhook_secret,
        tradesajon_gsheet_id=get("TRADESAJON_GSHEET_ID", "YOUR_GOOGLE_ID"),
        tradesajon_gsheet_url=get("TRADESAJON_GSHEET_URL", "https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit"),
        Tradesejon_drive_folder_id=get("Tradesejon_DRIVE_FOLDER_ID", "YOUR_GOOGLE_ID"),
        Tradesejon_drive_folder_url=get("Tradesejon_DRIVE_FOLDER_URL", "https://drive.google.com/YOUR_DRIVE_RESOURCE"),
        Tradesejon_blueprint_id=get("Tradesejon_BLUEPRINT_ID", "YOUR_GOOGLE_ID"),
        Tradesejon_blueprint_url=get("Tradesejon_BLUEPRINT_URL", "https://drive.google.com/YOUR_DRIVE_RESOURCE"),
        tradesejon_knowledge_base_dir=get("tradesejon_knowledge_base_DIR", r"./data/workspace"),
        tradesajon_crm_csv_path=get("TRADESAJON_CRM_CSV_PATH", r"./data/workspace"),
        n8n_base_url=get("N8N_BASE_URL", "http://localhost:5678").rstrip("/"),
        vps_n8n_base_url=get("VPS_N8N_BASE_URL", "https://n8n.yourdomain.com").rstrip("/"),
        vps_n8n_api_key=vps_n8n_api_key,
        agent_reach_exe=get("AGENT_REACH_EXE", r"./data/workspace"),
        gsheets_webhook_url=gsheets_webhook_url,
        whop_checkout_url=get("WHOP_CHECKOUT_URL", get("WHOP_CHECKOUT_WORKSHOP_45", "https://whop.com/checkout/plan_TEMPLATE_01")),
        default_zoom_url=get("ZOOM_MEETING_URL", "https://discord.com/channels/900000000000000001/900000000000000002"),
        default_zoom_id=get("ZOOM_MEETING_ID", "900000000000000002"),
        default_zoom_passcode=get("ZOOM_PASSCODE", "DISCORD-LIVE"),
        google_form_url=get("TRADESAJON_GOOGLE_FORM_URL", ""),
        videos_url=get(
            "TRADESAJON_VIDEOS_URL",
            "https://youtube.com/YOUR_TRAINING_PLAYLIST",
        ),
        bridge_host="127.0.0.1",
        bridge_port=int(get("DISCORD_BRIDGE_PORT", "8095")),
        known_secrets=tuple(dict.fromkeys(
            s for s in (*secret_bag, get("WHOP_API_KEY", ""), get("ZOOM_CLIENT_SECRET", "")) if s
        )),
    )
