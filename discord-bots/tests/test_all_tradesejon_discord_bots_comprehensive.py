"""Comprehensive end-to-end test suite for ALL Tradesejon Discord Bots, views, modals, RBAC, funnel, and bridges."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import time
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from aiohttp.test_utils import TestClient, TestServer
from nacl.signing import SigningKey

PKG_ROOT = Path(__file__).resolve().parent.parent
if str(PKG_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT.parent))

from tradesejon_discord_bots.bot_ceo import CEOAdminPanelView, CEOBotClient, CEOPromptModal  # noqa: E402
from tradesejon_discord_bots.bot_pr import (  # noqa: E402
    PRDeskBotClient,
    PRReplyModal,
    PRTicketActionView,
    build_pr_ticket_embed,
    handle_pr_button_interaction,
)
from tradesejon_discord_bots.bot_trader import TraderActionView, TraderBotClient, TraderRegisterModal  # noqa: E402
from tradesejon_discord_bots.bridge_server import create_bridge_app  # noqa: E402
from tradesejon_discord_bots.config import DiscordConfig  # noqa: E402
from tradesejon_discord_bots.shared_services import SharedServices  # noqa: E402


# ---- Local in-memory test helpers (isolated to this test module) ----


class _FakeCollection:
    """Minimal Mongo-compatible in-memory collection for deterministic unit testing."""

    def __init__(self) -> None:
        self.docs: list[dict] = []

    @staticmethod
    def _match(doc: dict, filt: dict) -> bool:
        if not filt:
            return True
        for k, v in filt.items():
            if k == "$or":
                if not any(_FakeCollection._match(doc, sub) for sub in v):
                    return False
            elif isinstance(v, dict):
                if "$in" in v and doc.get(k) not in v["$in"]:
                    return False
                if "$ne" in v and doc.get(k) == v["$ne"]:
                    return False
                if "$exists" in v and ((k in doc) != bool(v["$exists"])):
                    return False
            else:
                if doc.get(k) != v:
                    return False
        return True

    def find_one(self, filt=None):
        for d in self.docs:
            if self._match(d, filt or {}):
                return dict(d)
        return None

    def find(self, filt=None):
        rows = [dict(d) for d in self.docs if self._match(d, filt or {})]
        return _FakeCursor(rows)

    def insert_one(self, doc: dict):
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id=len(self.docs))

    def update_one(self, filt: dict, upd: dict, upsert: bool = False):
        for d in self.docs:
            if self._match(d, filt):
                d.update(upd.get("$set", {}))
                return SimpleNamespace(matched_count=1, modified_count=1)
        if upsert:
            new_doc = dict(filt)
            new_doc.update(upd.get("$setOnInsert", {}))
            new_doc.update(upd.get("$set", {}))
            self.docs.append(new_doc)
            return SimpleNamespace(matched_count=0, modified_count=0)
        return SimpleNamespace(matched_count=0, modified_count=0)

    def update_many(self, filt: dict, upd: dict):
        matched = 0
        for d in self.docs:
            if self._match(d, filt):
                d.update(upd.get("$set", {}))
                matched += 1
        return SimpleNamespace(matched_count=matched, modified_count=matched)

    def count_documents(self, filt=None) -> int:
        return sum(1 for d in self.docs if self._match(d, filt or {}))


class _FakeCursor:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def sort(self, field: str, direction: int = -1):
        self._rows.sort(
            key=lambda x: str(x.get(field) or ""),
            reverse=(direction < 0),
        )
        return self

    def limit(self, n: int):
        self._rows = self._rows[:n]
        return self

    def __iter__(self):
        return iter(self._rows)


class _FakeDatabase:
    def __init__(self) -> None:
        self._cols: dict[str, _FakeCollection] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        return self._cols.setdefault(name, _FakeCollection())

    def __getattr__(self, name: str) -> _FakeCollection:
        return self[name]


def _cfg(**overrides) -> DiscordConfig:
    base = dict(
        trader_token="tok_trader_fake",
        pr_token="",
        ceo_token="tok_ceo_fake",
        guild_id="900000000000000001",
        ceo_user_ids=frozenset({"900000000000000003"}),
        pr_user_ids=frozenset({"222222"}),
        pr_role_ids=frozenset({"333333"}),
        ceo_channel_ids=frozenset({"9001"}),
        pr_channel_ids=frozenset({"9002"}),
        trader_channel_ids=frozenset({"9003"}),
        announce_channel_id="9004",
        bridge_secret="bridge-secret-value-999999",
        aios_internal_token="aios-internal-value-999999",
        discord_public_key="",
        aios_base_url="",
        mongo_local_uri="",
        mongo_atlas_uri="",
        pg_dsn="",
        groq_api_key="",
        gemini_api_key="",
        openai_api_key="",
        elevenlabs_api_key="",
        elevenlabs_voice_id="voice1",
        resend_api_key="",
        gmail_user="trader@example.com",
        gmail_app_password="",
        whop_checkout_url="https://whop.com/checkout/plan_TEMPLATE_01",
        whop_plan_workshop_45="plan_TEMPLATE_01",
        whop_checkout_workshop_45="https://whop.com/checkout/plan_TEMPLATE_01",
        whop_plan_external_60="plan_TEMPLATE_01",
        whop_checkout_external_60="https://whop.com/checkout/plan_TEMPLATE_01",
        whop_plan_viproom_75="plan_TEMPLATE_01",
        whop_checkout_viproom_75="https://whop.com/checkout/plan_TEMPLATE_01",
        tradesajon_gsheet_id="YOUR_GOOGLE_ID",
        tradesajon_gsheet_url="https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit",
        default_zoom_url="https://discord.com/channels/900000000000000001/900000000000000002",
        default_zoom_id="900000000000000002",
        default_zoom_passcode="DISCORD-LIVE",
        discord_live_channel_id="900000000000000002",
        telegram_trading_token="tg-trader-tok",
        telegram_ceo_token="tg-ceo-tok",
        telegram_ceo_chat_id="1000000001",
        tradesajon_crm_csv_path=str(Path(tempfile.gettempdir()) / "tradesejon_pytest_crm.csv"),
        n8n_base_url="",
        vps_n8n_base_url="",
        google_form_url="",
        videos_url="",
    )
    base.update(overrides)
    return DiscordConfig(**base)


def _channel(*, cid: int, name: str, private_from_everyone: bool, is_dm: bool = False):
    if is_dm:
        ch = MagicMock(spec=discord.DMChannel)
        ch.id = cid
        return ch, None
    default_role = SimpleNamespace(id="everyone")
    overwrite = SimpleNamespace(view_channel=False if private_from_everyone else True)
    guild = SimpleNamespace(id="900000000000000001", default_role=default_role)
    ch = SimpleNamespace(
        id=cid,
        parent_id=None,
        parent=None,
        name=name,
        overwrites_for=lambda r: overwrite if r == default_role else SimpleNamespace(view_channel=None),
    )
    return ch, guild


def _interaction(*, user_id: int, username: str, channel, guild, custom_id: str = "", itype=discord.InteractionType.component):
    user = SimpleNamespace(id=user_id, name=username, roles=[], send=AsyncMock())
    resp = SimpleNamespace(
        send_message=AsyncMock(),
        send_modal=AsyncMock(),
        defer=AsyncMock(),
    )
    fup = SimpleNamespace(send=AsyncMock())
    return SimpleNamespace(
        id=999000111,
        type=itype,
        data={"custom_id": custom_id} if custom_id else {},
        user=user,
        channel=channel,
        channel_id=getattr(channel, "id", 0),
        guild=guild,
        guild_id=getattr(guild, "id", None) if guild else None,
        response=resp,
        followup=fup,
    )


def _fake_services(cfg: DiscordConfig) -> tuple[SharedServices, _FakeDatabase]:
    svc = SharedServices(cfg)
    fdb = _FakeDatabase()
    svc._dual_dbs = lambda: [("local", fdb)]  # type: ignore[method-assign]
    return svc, fdb


# ---- Comprehensive Tests ----


@pytest.mark.asyncio
async def test_discord_form_funnel_strict_validation_whop_issue_and_zoom_lock():
    cfg = _cfg(gmail_user="trader@example.com", gmail_app_password="app-pass-16chars!")
    svc, fdb = _fake_services(cfg)
    pr_alerts = []
    sent_emails = []
    sent_tg = []

    async def fake_email(**kw):
        sent_emails.append(kw)
        return {"ok": True, "provider": "gmail_smtp", "to": kw.get("to_email")}

    async def fake_tg(**kw):
        sent_tg.append(kw)
        return True

    svc.send_email_secure = fake_email  # type: ignore[method-assign]
    svc.send_telegram_live_link = fake_tg  # type: ignore[method-assign]

    async def pr_notify(embed, view=None):
        pr_alerts.append((embed, view))

    pub_ch, guild = _channel(cid=9003, name="tradesejon-vip", private_from_everyone=False)

    # 1. Submitting Marhaban modal BEFORE Whop OTP confirmation -> status='pending_whop_otp', NO Prospect Role yet, Whop OTP link issued + Entry videos!
    modal_no = TraderRegisterModal(svc, cfg, pr_notify)
    modal_no.watched_videos._value = "لا"
    modal_no.country._value = "مصر"
    modal_no.trading_level._value = "entry"
    modal_no.phone._value = "+201012345678"
    modal_no.email._value = "mo@example.com"
    ix_no = _interaction(user_id=7001, username="mo_no", channel=pub_ch, guild=guild)
    await modal_no.on_submit(ix_no)
    ix_no.response.defer.assert_awaited_once_with(ephemeral=True)
    ix_no.followup.send.assert_awaited_once()
    embeds_no = ix_no.followup.send.call_args.kwargs["embeds"]
    emb_no = embeds_no[0]
    assert "plan_TEMPLATE_01" in emb_no.description
    assert "/api/zoom-join?t=" not in emb_no.description
    assert "VIDEO_TEMPLATE_01" in embeds_no[1].description
    lead_mo = fdb["trading_leads"].find_one({"discord_user_id": "7001"})
    assert lead_mo is not None and lead_mo["form_validated"] is True
    assert lead_mo["status"] == "pending_whop_otp" and lead_mo["paid"] is False and lead_mo["whop_otp_verified"] is False
    assert lead_mo["country"] == "مصر" and lead_mo["trading_level"] == "entry"
    assert cfg.prospect_role_id not in lead_mo["roles"]
    assert any(e["to_email"] == "mo@example.com" and e.get("include_zoom") is False for e in sent_emails)

    # Confirming Free Whop $0.00 Email OTP immediately grants Prospect Role (#prospects)!
    f_mo_otp = await svc.fulfill_whop_payment_for_discord(
        email_addr="mo@example.com",
        discord_user_id="7001",
        whop_payment_id="mem_mo_otp_0",
        tier="free_otp_test",
        plan_id="plan_TEMPLATE_01",
        amount_usd=0.0,
    )
    assert f_mo_otp["status"] == "prospect" and f_mo_otp["whop_otp_verified"] is True and f_mo_otp["paid"] is False
    assert cfg.prospect_role_id in f_mo_otp["roles_added"] and cfg.workshop_role_id not in f_mo_otp["roles_added"]

    # 2. Reject invalid email/phone strictly even with "نعم"
    modal_bad = TraderRegisterModal(svc, cfg, pr_notify)
    modal_bad.watched_videos._value = "نعم"
    modal_bad.country._value = "مصر"
    modal_bad.trading_level._value = "entry"
    modal_bad.phone._value = "12"
    modal_bad.email._value = "bad-mail"
    ix_bad = _interaction(user_id=7002, username="mo_bad", channel=pub_ch, guild=guild)
    await modal_bad.on_submit(ix_bad)
    ix_bad.followup.send.assert_awaited_once()
    assert fdb["trading_leads"].count_documents({"discord_user_id": "7002"}) == 0

    # 3. Direct chat message containing ONLY Mail + Number registers Prospect + sends $45/$75 Whop tier card
    bot = TraderBotClient(cfg, svc)
    bot.notify_pr_privately = pr_notify  # type: ignore[method-assign]
    sara_dm_ch, _ = _channel(cid=9100, name="dm-sara", private_from_everyone=True, is_dm=True)
    dm_author = SimpleNamespace(id=7003, name="sara_vip", display_name="Sara VIP", bot=False, send=AsyncMock())
    chat_msg = SimpleNamespace(
        id=55501,
        author=dm_author,
        channel=sara_dm_ch,
        guild=None,
        content="sara@tradesejon.test 01012345678",
        mentions=[],
        attachments=[],
        reply=AsyncMock(),
    )
    await bot.on_message(chat_msg)
    chat_msg.reply.assert_awaited_once()
    sara_reply_emb = chat_msg.reply.call_args.kwargs["embed"]
    assert "plan_TEMPLATE_01" in sara_reply_emb.description
    assert fdb["trading_leads"].count_documents({"email": "sara@tradesejon.test"}) == 1

    # 4. Unregistered stranger (7004) clicking /session is blocked until form + Whop payment!
    ix_unreg = _interaction(user_id=7004, username="new_guy", channel=pub_ch, guild=guild)
    await bot.session_btn_logic(ix_unreg)
    unreg_msg = ix_unreg.followup.send.call_args.args[0]
    assert "بريدك" in unreg_msg and "/api/zoom-join" not in unreg_msg

    # 5. Sara fulfills Whop $45 payment -> status='prospect_paid_once', Prospect Role removed, Workshop Role granted, unique link issued!
    #    Omar fulfills Whop $75 V tradesejon payment -> status='viproom_workshop_vip', BOTH V tradesejon Role + Workshop Role granted, different unique link!
    f_sara = await svc.fulfill_whop_payment_for_discord(
        email_addr="sara@tradesejon.test",
        discord_user_id="7003",
        whop_payment_id="pay_sara_45",
        tier="workshop_45",
        amount_usd=45,
    )
    assert f_sara["status"] == "prospect_paid_once"
    assert cfg.workshop_role_id in f_sara["roles_added"] and cfg.prospect_role_id in f_sara["roles_removed"]

    ix_sara_paid = _interaction(user_id=7003, username="sara_vip", channel=pub_ch, guild=guild)
    await bot.session_btn_logic(ix_sara_paid)
    paid_emb = ix_sara_paid.followup.send.call_args.kwargs["embed"]
    sara_tok_doc = fdb["zoom_access_tokens"].find_one({"discord_user_id": "7003"})
    assert sara_tok_doc is not None and sara_tok_doc["unique_zoom_url"] in paid_emb.description

    modal_omar = TraderRegisterModal(svc, cfg, pr_notify, prev_name="Omar VIP")
    modal_omar.watched_videos._value = "no"
    modal_omar.country._value = "مصر"
    modal_omar.trading_level._value = "intermediate"
    modal_omar.phone._value = "+201012345678"
    modal_omar.email._value = "omar@tradesejon.test"
    await modal_omar.on_submit(_interaction(user_id=7005, username="omar_vip", channel=pub_ch, guild=guild))
    f_omar = await svc.fulfill_whop_payment_for_discord(
        email_addr="omar@tradesejon.test",
        discord_user_id="7005",
        whop_payment_id="pay_omar_75",
        tier="viproom_75",
        amount_usd=75,
    )
    assert f_omar["status"] == "viproom_workshop_vip"
    assert cfg.viproom_role_id in f_omar["roles_added"] and cfg.workshop_role_id in f_omar["roles_added"]
    assert cfg.prospect_role_id in f_omar["roles_removed"]
    omar_tok_doc = fdb["zoom_access_tokens"].find_one({"discord_user_id": "7005"})
    assert omar_tok_doc is not None and omar_tok_doc["unique_zoom_url"] != sara_tok_doc["unique_zoom_url"]

    # 6. Unique Discord Invite minting + revocation test
    cfg_real_tok = _cfg(
        ceo_token="BotTokExample1234567890.AbCdEf.GhIjKlMnOpQrStUvWxYz12345678",
        discord_live_channel_id="900000000000000002",
    )
    svc_inv, fdb_inv = _fake_services(cfg_real_tok)
    revoked_codes = []

    class _FakeInviteResp:
        def __init__(self, status, data):
            self.status = status
            self._d = data

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def json(self, content_type=None):
            return self._d

    inv_counter = {"n": 0}

    def _inv_post(url, **kw):
        if "/channels/900000000000000002/invites" in url:
            inv_counter["n"] += 1
            return _FakeInviteResp(200, {"code": f"liveCode{inv_counter['n']}"})
        return _FakeInviteResp(404, {})

    def _inv_del(url, **kw):
        revoked_codes.append(url.rsplit("/", 1)[-1])
        return _FakeInviteResp(200, {})

    svc_inv.get_http = AsyncMock(return_value=SimpleNamespace(post=_inv_post, delete=_inv_del))  # type: ignore[method-assign]
    r_inv1 = await svc_inv.create_unique_zoom_link_for_payer(discord_user_id="8101", email_addr="inv1@test.com", full_name="Inv1")
    r_inv2 = await svc_inv.create_unique_zoom_link_for_payer(discord_user_id="8102", email_addr="inv2@test.com", full_name="Inv2")
    assert r_inv1["discord_live_url"] == "https://discord.gg/liveCode1"
    assert r_inv2["discord_live_url"] == "https://discord.gg/liveCode2"
    rev_inv = await svc_inv.revoke_zoom_access_for_user(discord_user_id="8101", reason="ceo_revoke_test")
    assert rev_inv["invites_revoked"] == 1 and revoked_codes == ["liveCode1"]

    await svc_inv.close()
    await svc.close()


@pytest.mark.asyncio
async def test_zoom_config_update_roundtrip_and_email_zoom_gating():
    cfg = _cfg()
    svc, _ = _fake_services(cfg)

    updated = await svc.update_live_session_zoom(
        zoom_url="https://us06web.zoom.us/j/999888777",
        zoom_id="999 888 777",
        zoom_pass="NEWPASS99",
        session_time="غداً الساعة 9:00 مساءً",
    )
    assert updated["zoom_url"] == "https://us06web.zoom.us/j/999888777"
    assert updated["zoom_id"] == "999 888 777"
    assert updated["zoom_pass"] == "NEWPASS99"
    assert updated["session_time"] == "غداً الساعة 9:00 مساءً"

    captured_emails = []

    class _FakeSMTP:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, u, p):
            pass

        def send_message(self, msg):
            captured_emails.append(msg.as_string())

    cfg_smtp = _cfg(gmail_user="trader@example.com", gmail_app_password="app-password-123456")
    svc_smtp, _ = _fake_services(cfg_smtp)
    await svc_smtp.update_live_session_zoom(zoom_url="https://us06web.zoom.us/j/777", zoom_pass="HIDDENPASS")

    with patch("smtplib.SMTP_SSL", _FakeSMTP):
        # Default include_zoom=False MUST hide Zoom link and passcode!
        res_unpaid = await svc_smtp.send_email_secure(
            to_email="lead@example.com",
            subject="Welcome",
            body_text="Please checkout",
        )
        assert res_unpaid["ok"] is True
        assert "HIDDENPASS" not in captured_emails[0]
        assert "us06web.zoom.us/j/777" not in captured_emails[0]

        # Explicit include_zoom=True after payment issues a UNIQUE personal link per recipient (NEVER generic Zoom URL/pass)
        res_paid_1 = await svc_smtp.send_email_secure(
            to_email="paid1@example.com",
            subject="Confirmed 1",
            body_text="Here is your workshop",
            include_zoom=True,
        )
        res_paid_2 = await svc_smtp.send_email_secure(
            to_email="paid2@example.com",
            subject="Confirmed 2",
            body_text="Here is your workshop",
            include_zoom=True,
        )
        assert res_paid_1["ok"] is True and res_paid_2["ok"] is True
        import email as _email_lib
        decoded_1 = "".join(
            p.get_payload(decode=True).decode("utf-8", "ignore")
            for p in _email_lib.message_from_string(captured_emails[1]).walk()
            if p.get_content_type() in ("text/plain", "text/html")
        )
        decoded_2 = "".join(
            p.get_payload(decode=True).decode("utf-8", "ignore")
            for p in _email_lib.message_from_string(captured_emails[2]).walk()
            if p.get_content_type() in ("text/plain", "text/html")
        )
        assert "/api/zoom-join?t=" in decoded_1
        assert "/api/zoom-join?t=" in decoded_2
        assert "us06web.zoom.us/j/777" not in decoded_1
        assert "HIDDENPASS" not in decoded_1
        assert decoded_1 != decoded_2

        # CRLF injection address safely stripped / invalid rejected
        res_bad = await svc_smtp.send_email_secure(
            to_email="notanemail",
            subject="X",
            body_text="Y",
        )
        assert res_bad["ok"] is False
    await svc.close()
    await svc_smtp.close()


@pytest.mark.asyncio
async def test_legacy_academy_kept_aside_and_tradesejon_onboarding_prompt():
    cfg = _cfg()
    svc, _ = _fake_services(cfg)
    bot = TraderBotClient(cfg, svc)
    bot._connection = SimpleNamespace(user=SimpleNamespace(id=900000000000000017))  # type: ignore[attr-defined]

    # 1. Message inside #nile-academy-bot without mention must be ignored completely
    nile_ch, guild = _channel(cid=900000000000000018, name="nile-academy-bot", private_from_everyone=False)
    msg_nile = SimpleNamespace(
        id=101, author=SimpleNamespace(id=5001, name="student", bot=False),
        channel=nile_ch, guild=guild, content="السلام عليكم", mentions=[], attachments=[],
        reply=AsyncMock(),
    )
    await bot.on_message(msg_nile)
    msg_nile.reply.assert_not_called()

    # 2. Entering DM as new unvalidated user sends Tradesejon Form card immediately
    dm_ch, _ = _channel(cid=88801, name="dm", private_from_everyone=True, is_dm=True)
    msg_dm = SimpleNamespace(
        id=102, author=SimpleNamespace(id=5002, name="new_trader", bot=False),
        channel=dm_ch, guild=None, content="hello", mentions=[], attachments=[],
        reply=AsyncMock(),
    )
    await bot.on_message(msg_dm)
    msg_dm.reply.assert_awaited_once()
    emb = msg_dm.reply.call_args.kwargs["embed"]
    assert "tradesejon" in (emb.title or "").lower() and "marhaban" in (emb.title or "").lower() and "workshop" in emb.description.lower()
    assert "بريدك" in emb.description and "prospect" in emb.description.lower()
    assert "SECRETZOOM" not in emb.description
    await svc.close()


@pytest.mark.asyncio
async def test_pr_desk_buttons_modal_and_rbac_enforcement():
    cfg = _cfg()
    svc, fdb = _fake_services(cfg)
    delivered = []

    async def deliver(ticket_code: str, txt: str, fbytes=None, fn: str = "") -> bool:
        delivered.append((ticket_code, txt, bool(fbytes), fn))
        return True

    await svc.create_or_update_discord_ticket(
        discord_user_id="777",
        discord_username="trader777",
        channel_id="9003",
        guild_id="900000000000000001",
        question="How does trial work?",
        reply="Fill form first.",
    )
    t_row = fdb["conversations"].find_one({"discord_user_id": "777"})
    tcode = t_row["ticketCode"]

    pr_pub, guild = _channel(cid=9002, name="pr-desk", private_from_everyone=False)
    pr_priv, _ = _channel(cid=9002, name="pr-desk", private_from_everyone=True)

    # 1. Unauthorized trader in private #pr-desk blocked
    ix_attacker = _interaction(user_id=999999, username="attacker", channel=pr_priv, guild=guild, custom_id=f"pr:resolve:{tcode}")
    handled = await handle_pr_button_interaction(ix_attacker, services=svc, cfg=cfg, deliver_fn=deliver)
    assert handled is True
    assert "⛔" in ix_attacker.response.send_message.call_args.args[0]
    assert fdb["security_audit_logs"].count_documents({"verdict": "DENY"}) == 1

    # 2. PR staff in PUBLIC #pr-desk also blocked
    ix_pub = _interaction(user_id=222222, username="pr_staff", channel=pr_pub, guild=guild, custom_id=f"pr:resolve:{tcode}")
    await handle_pr_button_interaction(ix_pub, services=svc, cfg=cfg, deliver_fn=deliver)
    assert "⛔" in ix_pub.response.send_message.call_args.args[0]

    # 3. PR staff in PRIVATE #pr-desk can trigger sendqr & resolve & reply modal
    ix_qr = _interaction(user_id=222222, username="pr_staff", channel=pr_priv, guild=guild, custom_id=f"pr:sendqr:{tcode}")
    await handle_pr_button_interaction(ix_qr, services=svc, cfg=cfg, deliver_fn=deliver)
    assert len(delivered) == 1 and delivered[0][2] is True
    assert "SECRETZOOM" not in delivered[0][1]

    ix_res = _interaction(user_id=222222, username="pr_staff", channel=pr_priv, guild=guild, custom_id=f"pr:resolve:{tcode}")
    await handle_pr_button_interaction(ix_res, services=svc, cfg=cfg, deliver_fn=deliver)
    assert fdb["conversations"].find_one({"ticketCode": tcode})["status"] == "resolved"

    modal = PRReplyModal(ticket_code=tcode, ai_mode=False, services=svc, cfg=cfg, deliver_fn=deliver)
    modal.reply_input._value = "Direct reply from PR desk"
    ix_sub = _interaction(user_id=222222, username="pr_staff", channel=pr_priv, guild=guild)
    await modal.on_submit(ix_sub)
    assert delivered[-1][1].endswith("Direct reply from PR desk")


@pytest.mark.asyncio
async def test_ceo_admin_panel_buttons_and_strict_rbac():
    cfg = _cfg()
    svc, fdb = _fake_services(cfg)
    svc.get_market_prices = AsyncMock(return_value={"XAUUSD": {"symbol": "XAU/USD", "unit": "$", "price": 3940.5}})  # type: ignore[method-assign]
    svc.check_ecosystem_health = AsyncMock(return_value={"AIOS 8088": "ONLINE (200)", "n8n 5678": "ONLINE (200)", "n8n Nod 4310": "ONLINE (200)"})  # type: ignore[method-assign]
    svc.ask_ceo_agent = AsyncMock(return_value={"ok": True, "reply": "Executed CEO directive cleanly."})  # type: ignore[method-assign]

    async def fake_bcast(msg: str, fbytes=None, fn: str = "") -> int:
        return 2

    panel = CEOAdminPanelView(svc, cfg, fake_bcast)
    ceo_priv, guild = _channel(cid=9001, name="ceo-command-center", private_from_everyone=True)
    ceo_pub, _ = _channel(cid=9001, name="ceo-command-center", private_from_everyone=False)

    # Attacker blocked on CEO KPI button
    ix_atk = _interaction(user_id=888888, username="evil", channel=ceo_priv, guild=guild)
    await panel.kpis_btn.callback(ix_atk)
    assert "⛔" in ix_atk.response.send_message.call_args.args[0]

    # CEO blocked inside public channel
    ix_ceo_pub = _interaction(user_id=900000000000000003, username="support_user", channel=ceo_pub, guild=guild)
    await panel.kpis_btn.callback(ix_ceo_pub)
    assert "⛔" in ix_ceo_pub.response.send_message.call_args.args[0]

    # CEO inside private channel succeeds across all buttons
    for btn_fn, expect in [
        (panel.kpis_btn.callback, "Tradesejon CEO Live CRM"),
        (panel.prices_btn.callback, "3940.5"),
        (panel.leads_btn.callback, "Verified Paid VIPs"),
        (panel.tickets_btn.callback, "Open Tickets"),
        (panel.zoom_btn.callback, cfg.default_zoom_id),
        (panel.health_btn.callback, "n8n Nod 4310"),
    ]:
        ix_ok = _interaction(user_id=900000000000000003, username="support_user", channel=ceo_priv, guild=guild)
        await btn_fn(ix_ok)
        sent = ix_ok.followup.send.call_args.args[0]
        assert expect in sent

    # CEO Prompt Modal denies attacker and allows CEO
    p_modal = CEOPromptModal(svc, cfg)
    p_modal.prompt_input._value = "Check status"
    ix_m_atk = _interaction(user_id=888888, username="evil", channel=ceo_priv, guild=guild)
    await p_modal.on_submit(ix_m_atk)
    assert "⛔" in ix_m_atk.response.send_message.call_args.args[0]

    ix_m_ok = _interaction(user_id=900000000000000003, username="support_user", channel=ceo_priv, guild=guild)
    await p_modal.on_submit(ix_m_ok)
    assert "Executed CEO directive cleanly." in ix_m_ok.followup.send.call_args.args[0]
    await svc.close()


@pytest.mark.asyncio
async def test_bridge_media_broadcast_and_vercel_discord_gateway_matrix():
    cfg = _cfg()
    captured_bcast = []

    async def bcast(text, b, fn):
        captured_bcast.append((text, b, fn))
        return 4

    app = create_bridge_app(
        cfg,
        SimpleNamespace(),
        status_fn=lambda: {"trader": {"ready": True}, "ceo": {"ready": True}},
        notify_ceo_fn=AsyncMock(return_value=True),
        notify_pr_fn=AsyncMock(),
        reply_trader_fn=AsyncMock(return_value=True),
        broadcast_fn=bcast,
        deliver_zoom_fn=AsyncMock(return_value={"ok": True, "zoom_delivered": True}),
    )
    async with TestClient(TestServer(app)) as client:
        hdrs = {"Authorization": f"Bearer {cfg.bridge_secret}"}
        # Bad base64 rejected 400 (with or without filename)
        r_b64 = await client.post(
            "/api/discord/broadcast",
            headers=hdrs,
            json={"message": "Hi", "filename": "x.png", "file_base64": "%%%NOT_B64%%%"},
        )
        assert r_b64.status == 400
        r_b64_nofn = await client.post(
            "/api/discord/broadcast",
            headers=hdrs,
            json={"message": "Hi", "file_base64": "!!!"},
        )
        assert r_b64_nofn.status == 400

        # Valid media broadcast decodes PNG bytes & strips @everyone
        sample = b"\x89PNG\r\n\x1a\nTEST"
        r_ok = await client.post(
            "/api/discord/broadcast",
            headers=hdrs,
            json={
                "message": "Update @everyone",
                "filename": "promo.png",
                "file_base64": base64.b64encode(sample).decode("ascii"),
            },
        )
        assert r_ok.status == 200
        assert (await r_ok.json())["delivered_count"] == 4
        assert "@everyone" not in captured_bcast[0][0]
        assert captured_bcast[0][1] == sample
        assert captured_bcast[0][2] == "promo.png"

    # Also test Vercel api/discord.js full HTTP handler behavior via Node (PING, /session locked, /trial form, invalid sig 401)
    js_path = Path(r"./data/workspace")
    if js_path.exists():
        sk = SigningKey.generate()
        pub_hex = sk.verify_key.encode().hex()

        def signed_req(payload_obj):
            ts = str(int(time.time()))
            raw = json.dumps(payload_obj).encode("utf-8")
            sig = sk.sign(ts.encode("utf-8") + raw).signature.hex()
            return ts, sig, raw.decode("utf-8")

        p1 = signed_req({"type": 1})
        p2 = signed_req({"type": 2, "data": {"name": "session"}})
        p3 = signed_req({"type": 2, "data": {"name": "trial"}})
        node_script = f"""
process.env.DISCORD_PUBLIC_KEY = {json.dumps(pub_hex)};
const handler = require({json.dumps(str(js_path))});
async function runReq(method, headers, bodyStr) {{
  let code = 200, out = null;
  const req = Object.assign([Buffer.from(bodyStr)], {{ method, headers, body: bodyStr }});
  const res = {{ status(c) {{ code = c; return this; }}, json(j) {{ out = j; return this; }} }};
  await handler(req, res);
  return {{ code, out }};
}}
(async () => {{
  const r_get = await runReq('GET', {{}}, '');
  const r_bad = await runReq('POST', {{ 'x-signature-ed25519': '00'.repeat(64), 'x-signature-timestamp': {json.dumps(p1[0])} }}, {json.dumps(p1[2])});
  const r_ping = await runReq('POST', {{ 'x-signature-ed25519': {json.dumps(p1[1])}, 'x-signature-timestamp': {json.dumps(p1[0])} }}, {json.dumps(p1[2])});
  const r_sess = await runReq('POST', {{ 'x-signature-ed25519': {json.dumps(p2[1])}, 'x-signature-timestamp': {json.dumps(p2[0])} }}, {json.dumps(p2[2])});
  const r_trial = await runReq('POST', {{ 'x-signature-ed25519': {json.dumps(p3[1])}, 'x-signature-timestamp': {json.dumps(p3[0])} }}, {json.dumps(p3[2])});
  console.log(JSON.stringify({{ r_get, r_bad, r_ping, r_sess, r_trial }}));
}})();
"""
        proc = subprocess.run([r"C:\Program Files\nodejs\node.exe", "-e", node_script], capture_output=True, text=True, timeout=12)
        assert proc.returncode == 0, proc.stderr
        parsed = json.loads(proc.stdout.strip())
        assert parsed["r_get"]["code"] == 405
        assert parsed["r_bad"]["code"] == 401
        assert parsed["r_ping"]["code"] == 200 and parsed["r_ping"]["out"] == {"type": 1}
        assert parsed["r_sess"]["out"]["data"]["flags"] == 64
        assert "zoom.us" not in parsed["r_sess"]["out"]["data"]["content"].lower()
        assert parsed["r_trial"]["out"]["data"]["flags"] == 64
        assert "/form" in parsed["r_trial"]["out"]["data"]["content"]


@pytest.mark.asyncio
async def test_unique_zoom_hmac_revocation_live_whop_and_oauth_registrant():
    from tradesejon_discord_bots.security import sign_zoom_access_token, verify_zoom_access_signature

    secret = "super-secret-signing-key-2026"
    t1, s1 = sign_zoom_access_token(
        discord_user_id="1001", email_addr="u1@test.com", checkout_ref="ref1", secret=secret
    )
    t2, s2 = sign_zoom_access_token(
        discord_user_id="1002", email_addr="u2@test.com", checkout_ref="ref2", secret=secret
    )
    assert t1 != t2 and s1 != s2
    assert verify_zoom_access_signature(
        token=t1, signature=s1, discord_user_id="1001", email_addr="u1@test.com", checkout_ref="ref1", secret=secret
    )
    assert not verify_zoom_access_signature(
        token=t1, signature=s1, discord_user_id="1002", email_addr="u1@test.com", checkout_ref="ref1", secret=secret
    )
    assert not verify_zoom_access_signature(
        token=t1, signature="0" * 32, discord_user_id="1001", email_addr="u1@test.com", checkout_ref="ref1", secret=secret
    )

    cfg = _cfg(whop_api_key="whop_live_test_key")
    svc, fdb = _fake_services(cfg)

    # Validate User A & B get distinct tokens and revoking A does not impact B
    res_a = await svc.create_unique_zoom_link_for_payer(discord_user_id="1001", email_addr="u1@test.com", full_name="User A")
    res_b = await svc.create_unique_zoom_link_for_payer(discord_user_id="1002", email_addr="u2@test.com", full_name="User B")
    assert res_a["unique_zoom_url"] != res_b["unique_zoom_url"]

    rev = await svc.revoke_zoom_access_for_user(discord_user_id="1001", reason="shared_link")
    assert rev["modified"] == 1
    assert fdb["zoom_access_tokens"].find_one({"token": res_a["token"]})["revoked"] is True
    assert fdb["zoom_access_tokens"].find_one({"token": res_b["token"]})["revoked"] is False

    # Issuing after revocation issues a fresh token, never returning the revoked token
    res_a2 = await svc.create_unique_zoom_link_for_payer(discord_user_id="1001", email_addr="u1@test.com", full_name="User A")
    assert res_a2["token"] != res_a["token"]
    assert res_a2["unique_zoom_url"] != res_a["unique_zoom_url"]

    # Updating session Zoom via /setzoom propagates new upstream Zoom URL to existing active gateway tokens
    await svc.update_live_session_zoom(zoom_url="https://us06web.zoom.us/j/1122334455?pwd=REALPASS")
    assert fdb["zoom_access_tokens"].find_one({"token": res_b["token"]})["upstream_zoom_url"] == "https://us06web.zoom.us/j/1122334455?pwd=REALPASS"

    # Live Whop API fallback unlocks paid user cleanly on /session tap without exposing generic Zoom
    fdb["trading_leads"].insert_one({
        "discord_user_id": "2002",
        "email": "livewhop@test.com",
        "full_name": "Whop Buyer",
        "watched_videos": True,
        "form_validated": True,
        "paid": False,
    })
    svc.verify_whop_payment_live_for_email = AsyncMock(  # type: ignore[method-assign]
        return_value={"paid": True, "whop_payment_id": "mem_live_99"}
    )
    bot = TraderBotClient(cfg, svc)
    pub_ch, guild = _channel(cid=9003, name="tradesejon-vip", private_from_everyone=False)
    ix = _interaction(user_id=2002, username="whop_buyer", channel=pub_ch, guild=guild)
    await bot.session_btn_logic(ix)
    emb = ix.followup.send.call_args.kwargs["embed"]
    assert "/api/zoom-join?t=" in emb.description
    assert cfg.default_zoom_url not in emb.description
    assert "SECRETZOOM" not in emb.description

    # Verify Vercel api/zoom-join.js blocks missing token 403 safely
    z_js = Path(r"./data/workspace")
    if z_js.exists():
        node_chk = f"""
const h = require({json.dumps(str(z_js))});
let code = 0, body = '';
const req = {{ query: {{}}, headers: {{}} }};
const res = {{ setHeader(){{}}, status(c){{ code=c; return this; }}, send(b){{ body=b; return this; }} }};
(async () => {{ await h(req, res); console.log(JSON.stringify({{ code, body }})); }})();
"""
        proc = subprocess.run([r"C:\Program Files\nodejs\node.exe", "-e", node_chk], capture_output=True, text=True, timeout=10)
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout.strip())["code"] == 403

    # Verify CEO slash /broadcast include_zoom=True & /revoke_zoom never leak generic Zoom
    bcast_captured = []

    async def fake_ceo_bcast(msg: str, fbytes=None, fn: str = "") -> int:
        bcast_captured.append(msg)
        return 3

    ceo_bot = CEOBotClient(cfg, svc, fake_ceo_bcast)
    ceo_priv, guild = _channel(cid=9001, name="ceo-command-center", private_from_everyone=True)
    bcast_cmd = ceo_bot.tree.get_command("broadcast")
    revoke_cmd = ceo_bot.tree.get_command("revoke_zoom")
    assert bcast_cmd is not None and revoke_cmd is not None
    ix_bc = _interaction(user_id=900000000000000003, username="support_user", channel=ceo_priv, guild=guild)
    await bcast_cmd.callback(ix_bc, "Live session starts soon!", include_zoom=True, include_whop=True)
    assert len(bcast_captured) == 1
    assert cfg.default_zoom_url not in bcast_captured[0]
    assert "SECRETZOOM" not in bcast_captured[0]
    assert "/session" in bcast_captured[0]

    ix_rv = _interaction(user_id=900000000000000003, username="support_user", channel=ceo_priv, guild=guild)
    await revoke_cmd.callback(ix_rv, "1002")
    assert fdb["zoom_access_tokens"].find_one({"token": res_b["token"]})["revoked"] is True

    await svc.close()


@pytest.mark.asyncio
async def test_zoom_oauth_registrant_and_whop_rest_api_parsing():
    cfg = _cfg(
        whop_api_key="whop_test_999",
        zoom_account_id="z_acct_1",
        zoom_client_id="z_cli_1",
        zoom_client_secret="z_sec_1",
        default_zoom_url="https://us06web.zoom.us/j/88392145021?pwd=SECRETZOOM",
        zoom_gateway_url="https://example.trycloudflare.com/webhook/zoom-join",
    )
    svc, fdb = _fake_services(cfg)

    class _FakeResp:
        def __init__(self, status: int, data: dict):
            self.status = status
            self._data = data

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def json(self, content_type=None):
            return self._data

    def _fake_post(url, **kw):
        if "zoom.us/oauth/token" in url:
            return _FakeResp(200, {"access_token": "z_tok_abc"})
        if "api.zoom.us/v2/meetings/" in url:
            return _FakeResp(201, {"join_url": "https://us06web.zoom.us/w/88392145021?tk=unique_tk_for_ali"})
        return _FakeResp(404, {})

    def _fake_get(url, **kw):
        if "api.whop.com" in url:
            return _FakeResp(200, {"data": [{"id": "mem_77", "email": "ali@vip.com", "valid": True, "status": "active"}]})
        return _FakeResp(404, {})

    fake_sess = SimpleNamespace(post=_fake_post, get=_fake_get)
    svc.get_http = AsyncMock(return_value=fake_sess)  # type: ignore[method-assign]

    whop_res = await svc.verify_whop_payment_live_for_email("ali@vip.com")
    assert whop_res["paid"] is True and whop_res["whop_payment_id"] == "mem_77"
    whop_miss = await svc.verify_whop_payment_live_for_email("nobody@vip.com")
    assert whop_miss["paid"] is False

    zres = await svc.create_unique_zoom_link_for_payer(
        discord_user_id="3003", email_addr="ali@vip.com", full_name="Ali Trader"
    )
    assert zres["zoom_source"] == "zoom_registrant_oauth_plus_gateway"
    assert zres["unique_zoom_url"].startswith("https://example.trycloudflare.com/webhook/zoom-join?t=")
    saved_doc = fdb["zoom_access_tokens"].find_one({"token": zres["token"]})
    assert saved_doc["upstream_zoom_url"] == "https://us06web.zoom.us/w/88392145021?tk=unique_tk_for_ali"
    await svc.close()


@pytest.mark.asyncio
async def test_every_slash_command_on_trader_and_ceo_bots():
    """Execute EVERY registered slash command across TraderBotClient & CEOBotClient to ensure zero AttributeError or hangs."""
    cfg = _cfg(gmail_user="trader@example.com", gmail_app_password="app-pass-16chars!")
    svc, fdb = _fake_services(cfg)
    svc.get_market_prices = AsyncMock(return_value={"XAUUSD": {"symbol": "XAU/USD", "unit": "$", "price": 3950.25}})  # type: ignore[method-assign]
    svc.ask_trader_support = AsyncMock(return_value="أهلاً بك في Tradesejon!")  # type: ignore[method-assign]
    svc.ask_ceo_agent = AsyncMock(return_value={"ok": True, "reply": "CEO Done"})  # type: ignore[method-assign]
    svc.check_ecosystem_health = AsyncMock(return_value={"AIOS 8088": "ONLINE (200)", "n8n Nod 4310": "ONLINE (200)"})  # type: ignore[method-assign]

    sent_emails = []

    async def fake_email(**kw):
        sent_emails.append(kw)
        return {"ok": True, "provider": "gmail_smtp", "from_addr": cfg.gmail_user, "to": kw.get("to_email"), "message_id": "<test@gmail.com>"}

    svc.send_email_secure = fake_email  # type: ignore[method-assign]

    bot_t = TraderBotClient(cfg, svc)
    bot_c = CEOBotClient(cfg, svc, AsyncMock(return_value=1), deliver_zoom_fn=AsyncMock(return_value=True))
    pub_ch, guild = _channel(cid=9003, name="tradesejon-vip", private_from_everyone=False)
    priv_ceo, _ = _channel(cid=9001, name="ceo-command-center", private_from_everyone=True)

    # 1. All Trader modal openers (/start, /form, /register, /trial) MUST succeed CLEANLY + /marhaban sends welcome banner
    marhaban_cmd = bot_t.tree.get_command("marhaban")
    assert marhaban_cmd is not None
    ix_m = _interaction(user_id=7100, username="trader1", channel=pub_ch, guild=guild)
    await marhaban_cmd.callback(ix_m)
    ix_m.response.send_message.assert_awaited_once()

    for cname in ("start", "form", "register", "trial"):
        cmd = bot_t.tree.get_command(cname)
        assert cmd is not None, f"Missing /{cname}"
        ix = _interaction(user_id=7100, username="trader1", channel=pub_ch, guild=guild)
        await cmd.callback(ix)
        ix.response.send_modal.assert_awaited_once()
        assert isinstance(ix.response.send_modal.call_args.args[0], TraderRegisterModal)

    # 2. Form submission saves Prospect and emails checkout links (then /verify_pay promotes & emails Workshop Live link!)
    modal = ix.response.send_modal.call_args.args[0]
    modal.watched_videos._value = "yes"
    modal.country._value = "مصر"
    modal.trading_level._value = "advanced"
    modal.phone._value = "+201012345678"
    modal.email._value = "tarek@example.com"
    ix_sub = _interaction(user_id=7100, username="trader1", channel=pub_ch, guild=guild)
    await modal.on_submit(ix_sub)
    assert any(e["to_email"] == "tarek@example.com" and e.get("include_zoom") is False for e in sent_emails)
    assert any(e["to_email"] == cfg.gmail_user and e.get("include_zoom") is False for e in sent_emails)

    # 3. /prices, /session, /ask, /support
    for cname, extra in (("prices", ()), ("session", ()), ("ask", ("السعر كام؟",)), ("support", ("محتاج مساعدة",))):
        cmd = bot_t.tree.get_command(cname)
        assert cmd is not None
        ix = _interaction(user_id=7100, username="trader1", channel=pub_ch, guild=guild)
        await cmd.callback(ix, *extra)
        assert ix.response.defer.await_count >= 1
        assert ix.followup.send.await_count >= 1

    # 4. PR commands attached to TraderBot (/vip_email, /tickets, /reply) + CEO commands (/panel, /crm, /ceo, /setzoom, /verify_pay, /revoke_zoom, /broadcast, /email, /health, /research)
    for cname, extra in (
        ("vip_email", ("vip@example.com", "Tarek", "Welcome")),
        ("tickets", ()),
        ("reply", ("TRD-1001", "Reply text")),
    ):
        cmd = bot_t.tree.get_command(cname)
        assert cmd is not None
        ix = _interaction(user_id=900000000000000003, username="support_user", channel=priv_ceo, guild=guild)
        await cmd.callback(ix, *extra)
        assert ix.followup.send.await_count >= 1

    for cname, extra in (
        ("panel", ()),
        ("crm", ("",)),
        ("ceo", ("status?",)),
        ("setzoom", ("", "", "", "")),
        ("verify_pay", ("tarek@example.com",)),
        ("revoke_zoom", ("tarek@example.com",)),
        ("broadcast", ("Hi traders", True, False)),
        ("email", ("vip@example.com", "Subj", "Body")),
        ("health", ()),
        ("research", ("XAUUSD gold analysis",)),
    ):
        cmd = bot_c.tree.get_command(cname)
        assert cmd is not None
        ix = _interaction(user_id=900000000000000003, username="support_user", channel=priv_ceo, guild=guild)
        await cmd.callback(ix, *extra)
        assert (ix.response.send_message.await_count + ix.followup.send.await_count) >= 1

    # verify_pay auto-emailed Tarek his unique personal Workshop link!
    assert any(e["to_email"] == "tarek@example.com" and e.get("include_zoom") is True and e.get("unique_zoom_url") for e in sent_emails)
    await svc.close()


@pytest.mark.asyncio
async def test_marhaban_prospect_workshop_viproom_progression_and_dual_response(tmp_path):
    """End-to-end test: external $60 vs community $45, pre-paid direct to Workshop, #prospects AI+Human response, $75 V tradesejon dual roles, CSV sync, and Agent-Reach."""
    csv_file = tmp_path / "crm_master.csv"
    cfg = _cfg(
        tradesajon_crm_csv_path=str(csv_file),
        whop_api_key="whop_live_test_key",
        whop_plan_workshop_45="plan_TEMPLATE_13",
        whop_checkout_workshop_45="https://whop.com/checkout/plan_TEMPLATE_13",
        whop_plan_external_60="plan_TEMPLATE_14",
        whop_checkout_external_60="https://whop.com/checkout/plan_TEMPLATE_14",
        whop_plan_viproom_75="plan_TEMPLATE_15",
        whop_checkout_viproom_75="https://whop.com/checkout/plan_TEMPLATE_15",
    )
    svc, fdb = _fake_services(cfg)
    svc.agent_reach.smart_research = AsyncMock(return_value={"ok": True, "source": "agent-reach:web", "summary": "Gold at $3950"})  # type: ignore[method-assign]
    svc.call_groq_chat = AsyncMock(return_value="رد AI Prospect Bot الفوري حول الورشة.")  # type: ignore[method-assign]

    role_calls = []
    persona_msgs = []

    async def fake_apply_roles(uid, *, add_role_ids=(), remove_role_ids=(), reason=""):
        role_calls.append({"uid": uid, "add": list(add_role_ids), "remove": list(remove_role_ids), "reason": reason})
        return {"ok": True, "added": list(add_role_ids), "removed": list(remove_role_ids), "errors": []}

    async def fake_persona(persona, *, content="", embeds=None, channel_id="", recipient_user_id=""):
        persona_msgs.append({"persona": persona, "content": content})
        return {"ok": True, "via": f"webhook:{persona}"}

    svc.discord_roles.apply_funnel_roles = fake_apply_roles  # type: ignore[method-assign]
    svc.discord_roles.send_persona_message = fake_persona  # type: ignore[method-assign]

    # 1. External (NOT in Discord community) gets official Offline Non-Member 6,000 EGP ($111.59) URL + pending_whop_otp until Whop OTP confirms
    ext_saved = await svc.save_discord_lead(
        discord_user_id="8801",
        discord_username="ext_user",
        full_name="External Lead",
        email_addr="ext@tradesejon.test",
        phone_e164="+201012345678",
        challenge="Join workshop",
        channel_id=cfg.marhaban_channel_id,
        in_community=False,
    )
    assert ext_saved["tier"] == "external_60" and ext_saved["price_usd"] == 111.59
    assert "plan_TEMPLATE_04" in ext_saved["whop_checkout_url"]
    assert "plan_TEMPLATE_01" in ext_saved["free_otp_url"]
    assert ext_saved["status"] == "pending_whop_otp" and cfg.prospect_role_id not in ext_saved["roles"]

    # 2. Pre-paid $45 user from beginning skips Prospect and goes straight to Workshop Role!
    class _FakeWhopResp:
        def __init__(self, payload):
            self.status = 200
            self._p = payload
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def json(self, content_type=None):
            return self._p

    svc.get_http = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(
            get=lambda url, **kw: _FakeWhopResp({
                "data": [{"id": "pay_pre_45", "customer_email": "prepaid45@tradesejon.test", "variant_id": cfg.whop_plan_workshop_45, "status": "paid", "total": {"amount": 45}}]
            }),
            post=lambda url, **kw: _FakeWhopResp({"id": "ch_test", "purchase_url": "https://whop.com/checkout/plan_TEMPLATE_13?d=test"}),
        )
    )
    pre_saved = await svc.save_discord_lead(
        discord_user_id="8802",
        discord_username="prepaid_user",
        full_name="Prepaid Trader",
        email_addr="prepaid45@tradesejon.test",
        phone_e164="+201012345678",
        challenge="Already paid",
        channel_id=cfg.marhaban_channel_id,
        in_community=True,
    )
    assert pre_saved["already_paid"] is True
    assert pre_saved["status"] == "prospect_paid_once"
    assert cfg.workshop_role_id in pre_saved["roles"]
    assert any(cfg.prospect_role_id in rc["remove"] and cfg.workshop_role_id in rc["add"] for rc in role_calls if rc["uid"] == "8802")

    # 3. Prospect renewal updates status from 'prospect_paid_once' -> 'vip_recurring'
    ren = await svc.fulfill_whop_payment_for_discord(
        email_addr="prepaid45@tradesejon.test",
        discord_user_id="8802",
        whop_payment_id="pay_pre_45_month2",
        tier="workshop_45",
        amount_usd=45,
    )
    assert ren["status"] == "vip_recurring"

    # 4. CSV master snapshot saved cleanly with both users and statuses
    assert csv_file.exists()
    csv_txt = csv_file.read_text(encoding="utf-8-sig")
    assert "ext@tradesejon.test" in csv_txt and "prepaid45@tradesejon.test" in csv_txt and "vip_recurring" in csv_txt

    # 5. #prospects AI Agent + Human Agent dual response AND no AI loop when Human Agent replies!
    bot = TraderBotClient(cfg, svc)
    pr_tickets = []
    bot.notify_pr_privately = AsyncMock(side_effect=lambda e, v=None: pr_tickets.append(e))  # type: ignore[method-assign]
    pros_ch, pros_guild = _channel(cid=int(cfg.prospects_channel_id), name="prospects", private_from_everyone=True)

    p_msg = SimpleNamespace(
        id=99001,
        author=SimpleNamespace(id=8801, name="ext_user", display_name="Ext", bot=False, roles=[], send=AsyncMock()),
        channel=pros_ch,
        guild=pros_guild,
        content="ما الفرق بين باقة 45 و 75؟",
        mentions=[],
        attachments=[],
        reply=AsyncMock(),
    )
    await bot.on_message(p_msg)
    p_msg.reply.assert_not_awaited()
    p_msg.author.send.assert_awaited_once()
    assert len(pr_tickets) == 1
    assert any("AI + Human Agent" in f"{f.name} {f.value}" for f in pr_tickets[0].fields) or "AI + Human Agent" in (pr_tickets[0].footer.text or "")

    # Human Agent replies in #prospects -> MUST NOT trigger AI auto-reply loop!
    h_msg = SimpleNamespace(
        id=99002,
        author=SimpleNamespace(id=4444, name="human_rep", display_name="Human", bot=False, roles=[SimpleNamespace(id=int(cfg.human_agent_role_id))], send=AsyncMock()),
        channel=pros_ch,
        guild=pros_guild,
        content="أهلاً بك، أنا من فريق الدعم البشري وأقدر أساعدك الآن!",
        mentions=[],
        attachments=[],
        reply=AsyncMock(),
    )
    await bot.on_message(h_msg)
    h_msg.reply.assert_not_awaited()
    csv_file.unlink(missing_ok=True)
    await svc.close()


@pytest.mark.asyncio
async def test_tradesejon_17_faq_master_guide_and_mentor_offline_online_plans():
    """Verify all 17-point Tradesejon FAQ details, Online Mentor Whop plans ($100), Offline plans (4000/6000 EGP), and /faq /mentors /brokers commands."""
    from tradesejon_discord_bots.crm_sync import resolve_tier_from_payment
    from tradesejon_discord_bots.shared_services import SharedServices

    cfg = _cfg()
    svc, _ = _fake_services(cfg)

    # 1. Verify Offline Workshop Plans (4000 EGP = $74.39 & 6000 EGP = $111.59)
    off_mem = resolve_tier_from_payment({"plan_id": "plan_TEMPLATE_03"}, cfg)
    assert off_mem["price_usd"] == 74.39 and off_mem["price_egp"] == 4000 and off_mem["workshop_mode"] == "offline_egypt"
    off_non = resolve_tier_from_payment({"plan_id": "plan_TEMPLATE_04"}, cfg)
    assert off_non["price_usd"] == 111.59 and off_non["price_egp"] == 6000 and off_non["workshop_mode"] == "offline_egypt"

    # 2. Verify all 5 Online Workshop Mentor Plans ($100 / 4 Hours)
    for pid, mentor_sub in (
        ("plan_TEMPLATE_05", "MentorAlpha"),
        ("plan_TEMPLATE_06", "MentorBeta"),
        ("plan_TEMPLATE_07", "MentorGamma"),
        ("plan_TEMPLATE_08", "MentorDelta"),
        ("plan_TEMPLATE_09", "Epsilon"),
    ):
        spec = resolve_tier_from_payment({"plan_id": pid}, cfg)
        assert spec["price_usd"] == 100
        assert spec["workshop_mode"] == "online_100usd_4h"
        assert mentor_sub in spec["mentor"]

    # 3. Verify FAQ context matcher covers Beginner Videos, Offline/Online Workshops, Mentors, and Brokers
    faq_beg = SharedServices.match_official_faq_context("أنا مبتدئ زيرو بورصة ومش عارف أبدأ منين")
    assert "YOUR_TRAINING_PLAYLIST" in faq_beg and "VIDEO_TEMPLATE_01" in faq_beg
    faq_off = SharedServices.match_official_faq_context("تفاصيل الوورك شوب الاوفلاين يوم Upcoming Scheduled Workshop")
    assert "74.39" in faq_off and "111.59" in faq_off and "plan_TEMPLATE_03" in faq_off
    faq_on = SharedServices.match_official_faq_context("مواعيد المحاضرين والوورك شوب الاونلاين")
    assert "plan_TEMPLATE_05" in faq_on and "plan_TEMPLATE_09" in faq_on
    faq_brk = SharedServices.match_official_faq_context("إيه المنصة وطريقة الإيداع على IBKR و DirectBroker و LicensedBroker")
    assert "your-broker.example.com" in faq_brk and "your-platform.example.com" in faq_brk

    # 4. Verify /faq, /mentors, /brokers slash commands on TraderBotClient
    bot_t = TraderBotClient(cfg, svc)
    pub_ch, guild = _channel(cid=9003, name="tradesejon-vip", private_from_everyone=False)
    for cname in ("faq", "mentors", "brokers"):
        cmd = bot_t.tree.get_command(cname)
        assert cmd is not None, f"Missing /{cname}"
        ix = _interaction(user_id=7100, username="trader1", channel=pub_ch, guild=guild)
        await cmd.callback(ix)
        ix.response.send_message.assert_awaited_once()

    # 5. Verify hard firewall blocks any automatic real action / scrape / test on client's live business links
    from tradesejon_discord_bots.security import is_protected_client_live_target

    for live_link in (
        "https://whop.com/Tradesejon/exp_TEMPLATE_VIP",
        "https://whop.com/checkout/plan_TEMPLATE_03",
        "https://whop.com/checkout/plan_TEMPLATE_04",
        "https://whop.com/checkout/plan_TEMPLATE_05",
        "https://whop.com/checkout/plan_TEMPLATE_06",
        "https://whop.com/checkout/plan_TEMPLATE_07",
        "https://whop.com/checkout/plan_TEMPLATE_08",
        "https://whop.com/checkout/plan_TEMPLATE_09",
        "https://your-broker.example.com/",
        "https://youtube.com/YOUR_TRAINING_PLAYLIST",
    ):
        assert is_protected_client_live_target(live_link) is True
        reach_blk = await svc.agent_reach.smart_research(live_link)
        assert reach_blk.get("blocked") is True and reach_blk.get("ok") is False

    await svc.close()


@pytest.mark.asyncio
async def test_whop_free_email_only_otp_gate_before_prospects_bot():
    """Verify email-only Free Whop ($0.00) flow: NO Prospects Bot before Whop OTP, immediate promotion AFTER Whop OTP confirmation, and exact official prices."""
    from tradesejon_discord_bots.bot_trader import TraderBotClient, WhopTierButtonsView, build_marhaban_welcome_embed, handle_verify_payment_click

    cfg = _cfg(whop_api_key="whop_live_otp_test_key")
    svc, fdb = _fake_services(cfg)
    role_events = []
    persona_events = []

    async def fake_roles(uid, *, add_role_ids=(), remove_role_ids=(), reason=""):
        role_events.append({"uid": uid, "add": list(add_role_ids), "remove": list(remove_role_ids)})
        return {"ok": True, "added": list(add_role_ids), "removed": list(remove_role_ids), "errors": []}

    async def fake_persona(persona, *, content="", embeds=None, channel_id="", recipient_user_id=""):
        persona_events.append({"persona": persona, "content": content})
        return {"ok": True}

    svc.discord_roles.apply_funnel_roles = fake_roles  # type: ignore[method-assign]
    svc.discord_roles.send_persona_message = fake_persona  # type: ignore[method-assign]

    # Pre-OTP: Whop has NO confirmed OTP / only a 'pending' checkout session
    class _FakeResp:
        def __init__(self, payload):
            self.status = 200
            self._p = payload
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def json(self, content_type=None):
            return self._p

    whop_state = {
        "data": [
            {
                "id": "mem_unverified_1",
                "email": "emailonly@tradesejon.test",
                "plan_id": "plan_TEMPLATE_01",
                "status": "pending",
                "valid": False,
                "amount": 0,
            }
        ]
    }
    svc.get_http = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(
            get=lambda url, **kw: _FakeResp(whop_state),
            post=lambda url, **kw: _FakeResp({"id": "ch_otp", "purchase_url": "https://whop.com/checkout/plan_TEMPLATE_01?email=emailonly%40tradesejon.test"}),
        )
    )

    bot = TraderBotClient(cfg, svc)
    bot._connection = SimpleNamespace(user=SimpleNamespace(id=900000000000000017))  # type: ignore[attr-defined]
    marhaban_ch, guild = _channel(cid=int(cfg.marhaban_channel_id), name="marhaban", private_from_everyone=False)

    # 1. Verify Marhaban welcome & buttons only display official file prices (NO invented $60 / $75)
    wel = build_marhaban_welcome_embed(cfg)
    assert "$45" in wel.description and "74.39" in wel.description and "111.59" in wel.description and "$100" in wel.description
    assert "$60" not in wel.description and "$75" not in wel.description

    # 2. User puts ONLY his email in #marhaban -> held at pending_whop_otp (NO Prospect Role before OTP!)
    msg_mail_only = SimpleNamespace(
        id=44001,
        author=SimpleNamespace(id=8901, name="otp_tester", display_name="OTP Tester", bot=False, roles=[], send=AsyncMock()),
        channel=marhaban_ch,
        guild=guild,
        content="emailonly@tradesejon.test",
        mentions=[],
        attachments=[],
        reply=AsyncMock(),
    )
    await bot.on_message(msg_mail_only)
    msg_mail_only.reply.assert_not_awaited()
    msg_mail_only.author.send.assert_awaited_once()
    reply_emb = msg_mail_only.author.send.call_args.kwargs["embed"]
    assert "plan_TEMPLATE_01" in reply_emb.description and "OTP" in reply_emb.description
    assert not any(cfg.prospect_role_id in ev["add"] for ev in role_events if ev["uid"] == "8901")
    lead_before = fdb["trading_leads"].find_one({"email": "emailonly@tradesejon.test"})
    assert lead_before["status"] == "pending_whop_otp" and lead_before["whop_otp_verified"] is False

    # Clicking Verify BEFORE Whop OTP confirmation returns bright RED error (0xEF4444) and still blocks #prospects!
    ix_pre = _interaction(user_id=8901, username="otp_tester", channel=marhaban_ch, guild=guild)
    await handle_verify_payment_click(ix_pre, svc, cfg, AsyncMock())
    pre_emb = ix_pre.followup.send.call_args.kwargs["embed"]
    assert pre_emb.color.value == 0xEF4444
    assert not any(cfg.prospect_role_id in ev["add"] for ev in role_events if ev["uid"] == "8901")

    # 3. User confirms Whop OTP -> Whop marks $0.00 membership valid/active -> reconciler immediately moves user to #prospects!
    whop_state["data"] = [
        {
            "id": "mem_otp_verified_99",
            "customer_email": "emailonly@tradesejon.test",
            "plan_id": "plan_TEMPLATE_01",
            "status": "active",
            "valid": True,
            "total": {"amount": 0},
        }
    ]
    rec = await svc.reconcile_recent_whop_payments()
    assert rec["fulfilled"] == 1 and "emailonly@tradesejon.test" in rec["users"]
    lead_after = fdb["trading_leads"].find_one({"email": "emailonly@tradesejon.test"})
    assert lead_after["status"] == "prospect" and lead_after["whop_otp_verified"] is True and lead_after["paid"] is False
    assert any(cfg.prospect_role_id in ev["add"] and cfg.workshop_role_id not in ev["add"] for ev in role_events if ev["uid"] == "8901")
    assert any(pe["persona"] == "prospect" and "OTP" in pe["content"] for pe in persona_events)
    await svc.close()


@pytest.mark.asyncio
async def test_modal_and_chat_reject_invalid_mail_number_and_missing_watched_with_red_errors():
    """Reject bogus mail/number/dummy phones & missing Yes/No select with bright red (0xEF4444) per-field errors."""
    cfg = _cfg()
    svc, fdb = _fake_services(cfg)
    bot = TraderBotClient(cfg, svc)
    bot._connection = SimpleNamespace(user=SimpleNamespace(id=900000000000000017))  # type: ignore[attr-defined]
    marhaban_ch, guild = _channel(cid=int(cfg.marhaban_channel_id), name="marhaban", private_from_everyone=False)

    # 1. Modal submitted with exact bogus inputs + unselected dropdowns -> must emit red 0xEF4444 embed for ALL 5 fields & RetryFormView
    modal = TraderRegisterModal(svc, cfg, AsyncMock())
    modal.email._value = "hgjgyugiuhi"
    modal.phone._value = "jkbkjijioiooi"
    ix = _interaction(user_id=8955, username="bad_input_user", channel=marhaban_ch, guild=guild)
    await modal.on_submit(ix)
    ix.followup.send.assert_awaited_once()
    err_emb = ix.followup.send.call_args.kwargs["embed"]
    assert err_emb.color.value == 0xEF4444
    field_text = " ".join(f"{f.name} {f.value}" for f in err_emb.fields)
    assert "Mail" in field_text and "Number" in field_text and "البلد" in field_text and "مستواك" in field_text and "Lead Strategist" in field_text
    assert fdb["trading_leads"].count_documents({"discord_user_id": "8955"}) == 0

    # 2. Repeated/dummy numbers are also rejected
    from tradesejon_discord_bots.security import collect_lead_errors
    ok_rep, errs_rep, _ = collect_lead_errors("Member", "valid@gmail.com", "0000000000")
    assert ok_rep is False and any("الهاتف" in e for e in errs_rep)
    ok_seq, errs_seq, _ = collect_lead_errors("Member", "valid@gmail.com", "123456789")
    assert ok_seq is False and any("الهاتف" in e for e in errs_seq)

    # 3. Direct #marhaban message with valid mail + bogus short/alpha phone -> red error reply, never saved
    bad_msg = SimpleNamespace(
        id=44099,
        author=SimpleNamespace(id=8956, name="bad_chat", display_name="Bad Chat", bot=False, roles=[], send=AsyncMock()),
        channel=marhaban_ch,
        guild=guild,
        content="valid@gmail.com 1234",
        mentions=[],
        attachments=[],
        reply=AsyncMock(),
    )
    await bot.on_message(bad_msg)
    bad_msg.reply.assert_not_awaited()
    bad_msg.author.send.assert_awaited_once()
    chat_emb = bad_msg.author.send.call_args.kwargs["embed"]
    assert chat_emb.color.value == 0xEF4444
    assert fdb["trading_leads"].count_documents({"discord_user_id": "8956"}) == 0

    # 4. Retry modal highlights red labels + invalid placeholders instead of re-seeding bogus text
    retry_modal = TraderRegisterModal(
        svc,
        cfg,
        AsyncMock(),
        prev_email="hgjgyugiuhi",
        prev_phone="jkbkjijioiooi",
        email_error=True,
        phone_error=True,
        watched_error=True,
    )
    assert "خطأ" in retry_modal.email.text and "🔴" in retry_modal.email.description and retry_modal.email.component.default is None
    assert "خطأ" in retry_modal.phone.text and "🔴" in retry_modal.phone.description and retry_modal.phone.component.default is None
    assert "🔴" in retry_modal.watched_videos.text
    assert retry_modal.email.component.required is True and retry_modal.email.component.min_length is None
    assert retry_modal.phone.component.required is True and retry_modal.phone.component.min_length is None
    assert retry_modal.country.component.required is True and retry_modal.trading_level.component.required is True
    await svc.close()


@pytest.mark.asyncio
async def test_master_file_and_unified_client_file_instant_autosync_across_all_events(tmp_path):
    """Verify full Master Guide (.md & .json) live reload + instant per-client dossier auto-update across all lifecycle events + firewall."""
    import json
    from pathlib import Path
    from tradesejon_discord_bots.security import is_protected_client_live_target

    csv_file = tmp_path / "pytest_crm_sync.csv"
    cfg = _cfg(tradesajon_crm_csv_path=str(csv_file), aios_base_url="http://127.0.0.1:8088")
    svc, fdb = _fake_services(cfg)
    aios_posts = []

    class _FakeAiosResp:
        status = 200
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False

    def _fake_post(url, **kw):
        aios_posts.append({"url": url, "json": kw.get("json")})
        return _FakeAiosResp()

    svc.get_http = AsyncMock(return_value=SimpleNamespace(post=_fake_post))  # type: ignore[method-assign]

    rag_dir = Path(__file__).resolve().parent.parent.parent / "rag-knowledge-base"
    faqs_path = rag_dir / "17_faqs_official.json"
    pricing_path = rag_dir / "pricing_and_mentors.json"
    assert faqs_path.exists() and pricing_path.exists()
    assert len(json.loads(faqs_path.read_text(encoding="utf-8"))) == 17
    assert json.loads(pricing_path.read_text(encoding="utf-8"))["offline_workshop_member_usd"] == 74.39

    # 2. Form submit -> updates client_files, aios_super_memory, trading_leads, and disk JSON immediately
    await svc.save_discord_lead(
        discord_user_id="9123",
        discord_username="auto_client",
        full_name="Auto Client",
        email_addr="autoclient@tradesejon.test",
        phone_e164="+201012345678",
        challenge="Masterclass",
        channel_id=cfg.marhaban_channel_id,
        watched_videos=False,
    )
    cf1 = fdb["client_files"].find_one({"discord_user_id": "9123"})
    assert cf1 is not None and cf1["watched_videos"] is False and cf1["status"] == "pending_whop_otp"
    assert "لا" in cf1["organized_natural_language"]
    mem1 = fdb["aios_super_memory"].find_one({"key": "client_file_uid_9123"})
    assert mem1 is not None and mem1["structured_json"]["email"] == "autoclient@tradesejon.test"

    # 3. Clicking Yes ('نعم') button -> instant auto-update across client_files, memory & AIOS!
    assert await svc.update_discord_lead_watched_videos(discord_user_id="9123", watched=True) is True
    cf2 = fdb["client_files"].find_one({"discord_user_id": "9123"})
    assert cf2["watched_videos"] is True and "نعم" in cf2["watched_videos_label"]
    mem2 = fdb["aios_super_memory"].find_one({"key": "client_file_uid_9123"})
    assert mem2["structured_json"]["watched_videos"] is True and "نعم" in mem2["ai_reply"]

    # 4. Confirming Whop $0 OTP -> instant auto-update to status='prospect' (#prospects)
    await svc.fulfill_whop_payment_for_discord(
        email_addr="autoclient@tradesejon.test",
        discord_user_id="9123",
        whop_payment_id="mem_otp_9123",
        tier="free_otp_test",
        plan_id="plan_TEMPLATE_01",
        amount_usd=0.0,
    )
    cf3 = fdb["client_files"].find_one({"discord_user_id": "9123"})
    assert cf3["whop_otp_verified"] is True and cf3["status"] == "prospect" and cf3["paid"] is False

    # 5. Paid Online Mentor ($100) + Ticket -> instant auto-update of mentor, ticket, paid=True & disk file
    await svc.fulfill_whop_payment_for_discord(
        email_addr="autoclient@tradesejon.test",
        discord_user_id="9123",
        whop_payment_id="pay_9123_MentorAlpha",
        plan_id="plan_TEMPLATE_05",
        amount_usd=100.0,
    )
    tcode = await svc.create_or_update_discord_ticket(
        discord_user_id="9123",
        discord_username="auto_client",
        channel_id=cfg.workshop_channel_id,
        guild_id=cfg.guild_id,
        question="تم الحجز مع MentorAlpha",
        reply="تم التأكيد",
    )
    cf4 = fdb["client_files"].find_one({"discord_user_id": "9123"})
    assert cf4["paid"] is True and "MentorAlpha" in cf4["mentor"] and cf4["ticket_code"] == tcode
    disk_file = svc._resolve_client_files_dir() / "uid_9123.json"
    assert disk_file.exists() and "autoclient@tradesejon.test" in disk_file.read_text(encoding="utf-8")
    assert any(p["json"].get("event") == "watched_videos_updated" for p in aios_posts)

    # 6. Firewall check: real client production links are strictly blocked from automated action
    assert is_protected_client_live_target("https://whop.com/checkout/plan_TEMPLATE_05") is True
    assert is_protected_client_live_target("https://your-broker.example.com/") is True
    await svc.close()


@pytest.mark.asyncio
async def test_support_user_commands_restricted_to_pr_desk_and_ceo_admin_only():
    from tradesejon_discord_bots.security import is_ceo_authorized

    cfg = _cfg()
    ceo_admin = SimpleNamespace(id=int(next(iter(cfg.ceo_user_ids))), name="CEO_Admin", guild_permissions=SimpleNamespace(administrator=True))
    pr_admin = MagicMock(spec=discord.Member)
    pr_admin.id = 888777666
    pr_admin.name = "PR_Admin"
    pr_admin.guild_permissions = SimpleNamespace(administrator=True)
    prospect_user = SimpleNamespace(id=999111222, name="ProspectUser", guild_permissions=SimpleNamespace(administrator=False))

    ch_ceo, g_ceo = _channel(cid=int(next(iter(cfg.ceo_channel_ids))), name="ceo-command-center", private_from_everyone=True)
    ch_pr, g_pr = _channel(cid=int(next(iter(cfg.pr_channel_ids))), name="pr-desk", private_from_everyone=True)
    ch_marhaban, g_marhaban = _channel(cid=int(cfg.marhaban_channel_id), name="marhaban", private_from_everyone=False)
    ch_prospects, g_prospects = _channel(cid=int(cfg.prospects_channel_id), name="prospects", private_from_everyone=True)
    ch_workshop, g_workshop = _channel(cid=int(cfg.workshop_channel_id), name="workshop-channel", private_from_everyone=True)
    ch_viproom, g_viproom = _channel(cid=int(cfg.viproom_channel_id), name="vip-room", private_from_everyone=True)
    ch_public, g_public = _channel(cid=999000111, name="tradesejon", private_from_everyone=False)

    # Allowed in private #ceo-command-center and #pr-desk for CEO / Guild Admin
    assert is_ceo_authorized(ceo_admin, ch_ceo, g_ceo, cfg)[0] is True
    assert is_ceo_authorized(ceo_admin, ch_pr, g_pr, cfg)[0] is True
    assert is_ceo_authorized(pr_admin, ch_pr, g_pr, cfg)[0] is True

    # Blocked in trader/prospect/marhaban channels even for CEO/Admin
    for blocked_ch, blocked_g in (
        (ch_marhaban, g_marhaban),
        (ch_prospects, g_prospects),
        (ch_workshop, g_workshop),
        (ch_viproom, g_viproom),
        (ch_public, g_public),
    ):
        ok, reason = is_ceo_authorized(ceo_admin, blocked_ch, blocked_g, cfg)
        assert ok is False, f"Expected CEO blocked in {blocked_ch.name}, got {reason}"

    # Non-admin user blocked everywhere including #pr-desk and #ceo-command-center
    for ch, g in ((ch_ceo, g_ceo), (ch_pr, g_pr), (ch_prospects, g_prospects), (ch_marhaban, g_marhaban)):
        ok, _ = is_ceo_authorized(prospect_user, ch, g, cfg)
        assert ok is False

    # Every Tradesejon Systems slash command must require administrator=True
    svc, _ = _fake_services(cfg)
    bot = CEOBotClient(cfg, svc, AsyncMock(return_value=0), AsyncMock(return_value=True))
    cmds = bot.tree.get_commands()
    assert len(cmds) == 11
    for cmd in cmds:
        assert cmd.default_permissions is not None and cmd.default_permissions.administrator is True, (
            f"Command /{cmd.name} must require administrator=True"
        )
    await svc.close()


@pytest.mark.asyncio
async def test_google_sheet_live_autosync_uses_configured_sheet_and_skips_temp_paths(tmp_path, monkeypatch):
    import asyncio
    import shutil
    import tradesejon_discord_bots.crm_sync as crm_sync

    cfg = _cfg()
    assert cfg.tradesajon_gsheet_id == "YOUR_GOOGLE_ID"

    engine = crm_sync.OmnichannelSyncEngine(cfg)
    calls = []

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        calls.append(cmd)
        proc = SimpleNamespace(
            returncode=0,
            communicate=AsyncMock(return_value=(b"ok", b"")),
        )
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(shutil, "which", lambda name: "C:\\bin\\gdrive.exe" if name == "gdrive" else None)

    # Temp / pytest paths must NEVER mutate the live Google Sheet
    temp_csv = tmp_path / "pytest_crm_temp.csv"
    temp_csv.write_text("a,b\n1,2\n", encoding="utf-8")
    res_temp = await engine.sync_google_sheet_live(temp_csv)
    assert res_temp["skipped"] is True and res_temp["reason"] == "test_temp_csv"
    assert calls == []

    # Production CSV path must invoke gdrive mutate update with the exact target sheet ID
    prod_csv = Path("./data/workspace")
    orig_exists = Path.exists
    monkeypatch.setattr(Path, "exists", lambda self: True if self == prod_csv else orig_exists(self))
    res_prod = await engine.sync_google_sheet_live(prod_csv)
    assert res_prod["ok"] is True
    assert len(calls) == 1
    assert list(calls[0][-4:]) == ["mutate", "update", "YOUR_GOOGLE_ID", str(prod_csv)]


@pytest.mark.asyncio
async def test_first_bot_hides_member_joins_and_deletes_system_join_messages():
    cfg = _cfg()
    svc, _ = _fake_services(cfg)
    svc.discord_roles.send_persona_message = AsyncMock()
    bot = TraderBotClient(cfg, svc)

    # 1. on_member_join must DM the user privately and NEVER post publicly in #marhaban
    new_member = SimpleNamespace(
        id=777888999,
        name="secret_joiner",
        mention="<@777888999>",
        bot=False,
        send=AsyncMock(),
        guild=SimpleNamespace(system_channel=None),
    )
    await bot.on_member_join(new_member)
    new_member.send.assert_awaited_once()
    svc.discord_roles.send_persona_message.assert_not_awaited()

    # 2. Discord system join message (MessageType.new_member) must be deleted immediately
    sys_join_msg = SimpleNamespace(
        id=555666,
        type=discord.MessageType.new_member,
        author=new_member,
        channel=SimpleNamespace(id=int(cfg.marhaban_channel_id), name="tradesejon"),
        delete=AsyncMock(),
        reply=AsyncMock(),
    )
    await bot.on_message(sys_join_msg)
    sys_join_msg.delete.assert_awaited_once()
    sys_join_msg.reply.assert_not_awaited()
    await svc.close()


@pytest.mark.asyncio
async def test_5_question_egyptian_modal_country_level_videos_and_autofill():
    from tradesejon_discord_bots.bot_trader import build_level_videos_embed

    cfg = _cfg()
    svc, fdb = _fake_services(cfg)
    modal = TraderRegisterModal(svc, cfg, AsyncMock())
    assert len(modal.children) == 5

    # Entry / Beginner -> receives Entry videos
    emb_entry = build_level_videos_embed("entry", cfg.videos_url)
    assert "Entry" in emb_entry.title and "VIDEO_TEMPLATE_01" in emb_entry.description

    # Intermediate & Advanced -> receives Lead Strategist Intermediate/Advanced videos
    emb_inter = build_level_videos_embed("intermediate", cfg.videos_url)
    assert "Intermediate" in emb_inter.title and "VIDEO_TEMPLATE_06" in emb_inter.description
    emb_adv = build_level_videos_embed("advanced", cfg.videos_url)
    assert "Advanced" in emb_adv.title and "VIDEO_TEMPLATE_07" in emb_adv.description

    # Submitting Intermediate level via modal sends Intermediate videos privately & saves country + level
    modal.email._value = "  USER.NAME+test@GMAIL.COM "
    modal.phone._value = "+966501234567"
    modal.country._value = "السعودية"
    modal.trading_level._value = "intermediate"
    modal.watched_videos._value = "yes"
    marhaban_ch, guild = _channel(cid=int(cfg.marhaban_channel_id), name="marhaban", private_from_everyone=False)
    ix = _interaction(user_id=9555, username="inter_user", channel=marhaban_ch, guild=guild)
    await modal.on_submit(ix)
    embeds = ix.followup.send.call_args.kwargs["embeds"]
    assert len(embeds) == 2 and "VIDEO_TEMPLATE_06" in embeds[1].description
    saved_lead = fdb["trading_leads"].find_one({"discord_user_id": "9555"})
    assert saved_lead["email"] == "user.name+test@gmail.com"
    assert saved_lead["country"] == "السعودية" and saved_lead["trading_level"] == "intermediate"
    await svc.close()


@pytest.mark.asyncio
async def test_strict_pay_plus_whop_otp_gate_only_way_to_get_live_workshop_link():
    """Verify the ONLY way to receive the live Discord workshop link is: Paid + Whop OTP confirmed by Whop."""
    from tradesejon_discord_bots.bot_trader import send_unique_session_response

    cfg = _cfg()
    svc, fdb = _fake_services(cfg)
    svc.verify_whop_payment_live_for_email = AsyncMock(return_value={"paid": False, "otp_verified": False})  # type: ignore[method-assign]
    svc.reconcile_recent_whop_payments = AsyncMock(return_value={"fulfilled": 0, "users": []})  # type: ignore[method-assign]
    svc.discord_roles.apply_roles = AsyncMock(return_value={"ok": True, "added": [], "removed": []})  # type: ignore[method-assign]
    svc.discord_roles.send_persona_message = AsyncMock(return_value=True)  # type: ignore[method-assign]
    svc.send_email_secure = AsyncMock(return_value={"ok": True})  # type: ignore[method-assign]

    marhaban_ch, guild = _channel(cid=int(cfg.marhaban_channel_id), name="marhaban", private_from_everyone=False)
    uid = 9666
    email = "gateonly@tradesejon.test"

    def _has_live_link(text: str) -> bool:
        return "discord.gg/" in text or "discord.com/channels/" in text or "/api/zoom-join" in text

    # Stage 1: Unregistered -> blocked, no live link
    ix1 = _interaction(user_id=uid, username="gate_user", channel=marhaban_ch, guild=guild)
    await send_unique_session_response(ix1, svc, cfg, AsyncMock())
    assert not _has_live_link(str(ix1.followup.send.call_args))

    # Stage 2: Form submitted (pending_whop_otp) -> blocked, no live link
    await svc.save_discord_lead(
        discord_user_id=str(uid),
        discord_username="gate_user",
        full_name="Gate User",
        email_addr=email,
        phone_e164="+201012345678",
        challenge="test",
        channel_id=cfg.marhaban_channel_id,
        watched_videos=True,
    )
    ix2 = _interaction(user_id=uid, username="gate_user", channel=marhaban_ch, guild=guild)
    await send_unique_session_response(ix2, svc, cfg, AsyncMock())
    assert not _has_live_link(str(ix2.followup.send.call_args))

    # Stage 3: Free $0 Whop OTP confirmed (prospect, NOT paid) -> enters #prospects, STILL NO live workshop link
    res_otp = await svc.fulfill_whop_payment_for_discord(
        email_addr=email,
        discord_user_id=str(uid),
        whop_payment_id="mem_gate_otp",
        tier="free_otp_test",
        plan_id="plan_TEMPLATE_01",
        amount_usd=0.0,
    )
    assert res_otp["status"] == "prospect" and res_otp["discord_live_url"] == ""
    ix3 = _interaction(user_id=uid, username="gate_user", channel=marhaban_ch, guild=guild)
    await send_unique_session_response(ix3, svc, cfg, AsyncMock())
    assert not _has_live_link(str(ix3.followup.send.call_args))

    # Stage 4: Paid flag without Whop OTP confirmation -> STILL BLOCKED from live link
    fdb["trading_leads"].update_one({"discord_user_id": str(uid)}, {"$set": {"paid": True, "whop_otp_verified": False}})
    ix4 = _interaction(user_id=uid, username="gate_user", channel=marhaban_ch, guild=guild)
    await send_unique_session_response(ix4, svc, cfg, AsyncMock())
    assert not _has_live_link(str(ix4.followup.send.call_args))

    # Stage 5: Whop confirms paid payment + OTP -> NOW and ONLY NOW gets the live Discord workshop link!
    res_paid = await svc.fulfill_whop_payment_for_discord(
        email_addr=email,
        discord_user_id=str(uid),
        whop_payment_id="pay_gate_45",
        tier="workshop_45",
        amount_usd=45.0,
    )
    assert res_paid["whop_otp_verified"] is True and res_paid["discord_live_url"] != ""
    ix5 = _interaction(user_id=uid, username="gate_user", channel=marhaban_ch, guild=guild)
    await send_unique_session_response(ix5, svc, cfg, AsyncMock())
    emb5 = ix5.followup.send.call_args.kwargs["embed"]
    assert _has_live_link(emb5.description)
    await svc.close()


@pytest.mark.asyncio
async def test_roles_by_payment_tier_and_viproom_gets_lower_workshop_fee():
    cfg = _cfg()
    svc, fdb = _fake_services(cfg)
    svc.verify_whop_payment_live_for_email = AsyncMock(return_value={"paid": False, "otp_verified": False})  # type: ignore[method-assign]
    svc.send_email_secure = AsyncMock(return_value={"ok": True})  # type: ignore[method-assign]
    role_events: list[dict] = []

    async def _fake_apply(uid, add=(), remove=(), **kw):
        role_events.append({"uid": str(uid), "add": list(add), "remove": list(remove)})
        return {"ok": True}

    svc.discord_roles.apply_funnel_roles = _fake_apply  # type: ignore[method-assign]
    svc.discord_roles.send_persona_message = AsyncMock(return_value=True)  # type: ignore[method-assign]

    # Free bundle -> Prospect Role only (no workshop / viproom roles)
    r_free = await svc.fulfill_whop_payment_for_discord(
        email_addr="tier_free@tradesejon.test", discord_user_id="9801", whop_payment_id="mem_f",
        tier="free_otp_test", plan_id="plan_TEMPLATE_01", amount_usd=0.0,
    )
    assert r_free["status"] == "prospect"
    assert cfg.prospect_role_id in r_free["roles_added"]
    assert cfg.workshop_role_id not in r_free["roles_added"] and cfg.viproom_role_id not in r_free["roles_added"]

    # Workshop paid -> Workshop Role (Prospect removed)
    r_ws = await svc.fulfill_whop_payment_for_discord(
        email_addr="tier_ws@tradesejon.test", discord_user_id="9802", whop_payment_id="pay_w",
        tier="workshop_45", amount_usd=45,
    )
    assert cfg.workshop_role_id in r_ws["roles_added"] and cfg.prospect_role_id in r_ws["roles_removed"]
    assert cfg.viproom_role_id not in r_ws["roles_added"]

    # V tradesejon paid -> V tradesejon + Workshop roles (everything)
    r_vc = await svc.fulfill_whop_payment_for_discord(
        email_addr="tier_vc@tradesejon.test", discord_user_id="9803", whop_payment_id="pay_v",
        tier="viproom_75", amount_usd=100,
    )
    assert cfg.viproom_role_id in r_vc["roles_added"] and cfg.workshop_role_id in r_vc["roles_added"]

    # V tradesejon payer who is not detected as guild member still gets the lower member offline fee tier
    saved = await svc.save_discord_lead(
        discord_user_id="9803", discord_username="vc", full_name="VC User", email_addr="tier_vc@tradesejon.test",
        phone_e164="+201012345679", challenge="x", channel_id=cfg.marhaban_channel_id, in_community=False,
    )
    assert saved["in_community"] is True
    await svc.close()


@pytest.mark.asyncio
async def test_alignment_with_Tradesejon_guide_and_client_firewall():
    """Every price, plan and link in the official Tradesejon guide matches the code; real client assets are never touched."""
    import re as _re

    from tradesejon_discord_bots.bot_trader import build_level_videos_embed
    from tradesejon_discord_bots.crm_sync import ONLINE_MENTOR_PLANS, OFFLINE_WORKSHOP_PLANS, build_tier_defs
    from tradesejon_discord_bots.security import is_protected_client_discord_guild, is_protected_client_live_target

    rag_dir = Path(__file__).resolve().parent.parent.parent / "rag-knowledge-base"
    guide = (rag_dir / "17_faqs_official.json").read_text(encoding="utf-8")
    cfg = _cfg()
    assert "74.39" in guide and "111.59" in guide
    tiers = build_tier_defs(cfg)
    assert tiers["workshop_45"]["price_usd"] == 45 and tiers["viproom_75"]["price_usd"] == 100
    assert OFFLINE_WORKSHOP_PLANS["plan_TEMPLATE_03"]["price_egp"] == 4000
    assert OFFLINE_WORKSHOP_PLANS["plan_TEMPLATE_04"]["price_egp"] == 6000

    assert is_protected_client_discord_guild(cfg.guild_id) is False
    assert len(ONLINE_MENTOR_PLANS) == 5

    # Real Tradesejon guild: no role mutations, no invite minting
    prod_cfg = _cfg(guild_id="900000000000000013")
    svc, _ = _fake_services(prod_cfg)
    res = await svc.discord_roles.apply_funnel_roles("123456789012345678", add_role_ids=["1"], remove_role_ids=["2"])
    assert res.get("blocked") is True and res["added"] == [] and res["removed"] == []
    assert await svc._create_unique_discord_live_invite("900000000000000016") == ("", "")
    await svc.close()

    # Video guidance follows the guide: playlist for everyone, attribution accurate, abroad -> online workshop
    emb_adv = build_level_videos_embed("advanced", cfg.videos_url, "السعودية")
    assert "YOUR_TRAINING_PLAYLIST" in emb_adv.description
    assert "MentorGamma فتحي" in emb_adv.description and "حسام" in emb_adv.description
    assert "$100" in emb_adv.description
    assert "$100" not in build_level_videos_embed("entry", cfg.videos_url, "مصر").description


@pytest.mark.asyncio
async def test_live_discord_invite_is_one_time_15_minutes_and_rotates_after_expiry_or_use():
    from datetime import timedelta

    cfg = _cfg(ceo_token="bot_tok." + ("x" * 30), discord_live_channel_id="900000000000000002")
    svc, fdb = _fake_services(cfg)
    invite_calls = []
    counter = {"n": 0}

    class _FakeResp:
        def __init__(self, code_val: str):
            self.status = 201
            self._code = code_val

        async def json(self, content_type=None):
            return {"code": self._code}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class _FakeHttp:
        def post(self, url, **kwargs):
            invite_calls.append(kwargs.get("json"))
            counter["n"] += 1
            return _FakeResp(f"oneTimeCode{counter['n']}")

    svc.get_http = AsyncMock(return_value=_FakeHttp())  # type: ignore[method-assign]

    # 1. First link minted -> max_age=900 (15m), max_uses=1, unique=True
    r1 = await svc.create_unique_zoom_link_for_payer(discord_user_id="9777", email_addr="rot@tradesejon.test", full_name="Rot User")
    assert r1["discord_live_url"] == "https://discord.gg/oneTimeCode1"
    assert invite_calls[-1] == {"max_age": 900, "max_uses": 1, "temporary": False, "unique": True}
    tok_doc1 = fdb["zoom_access_tokens"].find_one({"token": r1["token"]})
    assert tok_doc1["max_uses"] == 1 and tok_doc1["max_age_sec"] == 900 and tok_doc1["one_time"] is True

    # 2. After 15 minutes expire -> link changes automatically to a brand-new unique code
    fdb["zoom_access_tokens"].update_one(
        {"token": r1["token"]},
        {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}},
    )
    r2 = await svc.create_unique_zoom_link_for_payer(discord_user_id="9777", email_addr="rot@tradesejon.test", full_name="Rot User")
    assert r2["discord_live_url"] == "https://discord.gg/oneTimeCode2"
    assert r2["discord_live_url"] != r1["discord_live_url"]

    # 3. After 1 use (use_count=1) -> link changes automatically again
    fdb["zoom_access_tokens"].update_one({"token": r2["token"]}, {"$set": {"use_count": 1}})
    r3 = await svc.create_unique_zoom_link_for_payer(discord_user_id="9777", email_addr="rot@tradesejon.test", full_name="Rot User")
    assert r3["discord_live_url"] == "https://discord.gg/oneTimeCode3"
    assert r3["discord_live_url"] != r2["discord_live_url"]
    await svc.close()


@pytest.mark.asyncio
async def test_discord_form_rejects_every_invalid_mail_format_in_loop():
    """Retest loop: EVERY invalid email format must be rejected in-form (red 0xEF4444 + RetryFormView) & chat without saving."""
    cfg = _cfg()
    svc, fdb = _fake_services(cfg)
    bot = TraderBotClient(cfg, svc)
    bot._connection = SimpleNamespace(user=SimpleNamespace(id=900000000000000017))  # type: ignore[attr-defined]
    marhaban_ch, guild = _channel(cid=int(cfg.marhaban_channel_id), name="marhaban", private_from_everyone=False)

    invalid_emails = [
        "admin_user@gmail.com@gmail.com",
        "hgjgyugiuhi",
        "user@gmail.comm",
        "user@gmail.con",
        "user@gmai.com",
        "user@gmial.com",
        "user.@gmail.com",
        ".user@gmail.com",
        "user..name@gmail.com",
        "foo bar@gmail.com",
        "user@domain",
        "@gmail.com",
        "user@",
        "user@-gmail.com",
        "user@gmail-.com",
        "a@b.c",
        "user@gmail.c",
        "<script>@gmail.com",
    ]

    for idx, bad_email in enumerate(invalid_emails):
        uid = 9900 + idx
        modal = TraderRegisterModal(svc, cfg, AsyncMock())
        modal.email._value = bad_email
        modal.phone._value = "+201012345678"
        modal.country._value = "مصر"
        modal.trading_level._value = "entry"
        modal.watched_videos._value = "yes"
        ix = _interaction(user_id=uid, username=f"bad_mail_{idx}", channel=marhaban_ch, guild=guild)
        await modal.on_submit(ix)
        ix.followup.send.assert_awaited_once()
        kw = ix.followup.send.call_args.kwargs
        assert kw["ephemeral"] is True, f"Error for {bad_email!r} must be ephemeral=True"
        assert kw["embed"].color.value == 0xEF4444, f"Error embed for {bad_email!r} must be red"
        assert kw["view"].email_error is True, f"RetryFormView for {bad_email!r} must flag email_error=True"
        assert fdb["trading_leads"].count_documents({"discord_user_id": str(uid)}) == 0, f"Invalid mail {bad_email!r} saved!"
        assert fdb["leads"].count_documents({"discord_user_id": str(uid)}) == 0
        assert fdb["contacts"].count_documents({"discord_user_id": str(uid)}) == 0
        assert fdb["client_files"].count_documents({"discord_user_id": str(uid)}) == 0

        # Direct #marhaban chat message with exact bad email (no spaces acting as name, or spaced invalid email)
        # Note: messages with a space before a valid domain like "foo bar@gmail.com" are handled as non-question invalid attempts in #marhaban
        chat_uid = 9950 + idx
        msg = SimpleNamespace(
            id=88000 + idx,
            type=discord.MessageType.default,
            author=SimpleNamespace(id=chat_uid, name=f"chat_bad_{idx}", display_name="Chat Bad", bot=False, roles=[], send=AsyncMock()),
            channel=marhaban_ch,
            guild=guild,
            content=f"mail: {bad_email}" if " " in bad_email else f"{bad_email} +201012345678",
            mentions=[],
            attachments=[],
            delete=AsyncMock(),
            reply=AsyncMock(),
        )
        await bot.on_message(msg)
        msg.reply.assert_not_awaited()
        msg.author.send.assert_awaited_once()
        chat_kw = msg.author.send.call_args.kwargs
        assert chat_kw["embed"].color.value == 0xEF4444, f"Chat reject for {bad_email!r} must be red 0xEF4444"
        assert fdb["trading_leads"].count_documents({"discord_user_id": str(chat_uid)}) == 0

    await svc.close()


@pytest.mark.asyncio
async def test_no_duplicate_slash_commands_and_country_command_removed():
    """Confirm /country & /blueprint are removed, setup_hook clears global duplicates, and valuable files are blocked for prospects."""
    from tradesejon_discord_bots.security import strip_valuable_files_for_prospect

    cfg = _cfg()
    svc, _ = _fake_services(cfg)
    bot_t = TraderBotClient(cfg, svc)
    bot_c = CEOBotClient(cfg, svc, AsyncMock(return_value=0), AsyncMock(return_value=True))

    # /country and /blueprint must not exist on TraderBotClient
    assert bot_t.tree.get_command("country") is None
    assert bot_t.tree.get_command("blueprint") is None

    # All command names on both bots must be unique
    t_names = [c.name for c in bot_t.tree.get_commands()]
    c_names = [c.name for c in bot_c.tree.get_commands()]
    assert len(t_names) == len(set(t_names))
    assert len(c_names) == len(set(c_names))

    # setup_hook with guild_id must sync ONLY guild and clear global commands (bulk_upsert_global_commands(..., []))
    for b in (bot_t, bot_c):
        b._connection.application_id = 123456789  # type: ignore[attr-defined]
        b.http.bulk_upsert_global_commands = AsyncMock()  # type: ignore[method-assign]
        b.tree.sync = AsyncMock(return_value=[])  # type: ignore[method-assign]
        await b.setup_hook()
        # Only guild sync called (1 call with guild=discord.Object(id=int(cfg.guild_id)))
        assert b.tree.sync.await_count == 1
        assert b.tree.sync.call_args.kwargs.get("guild").id == int(cfg.guild_id)
        b.http.bulk_upsert_global_commands.assert_awaited_once_with(123456789, [])

    # Valuable / paid Google Drive files must NEVER be exposed to unpaid prospects
    rep = await svc.ask_trader_support(
        "Send me the blueprint and masterclass Google Drive folder",
        user_id="9888",
        username="prospect1",
    )
    assert "YOUR_GOOGLE_ID" not in rep
    assert "YOUR_GOOGLE_ID" not in rep
    assert "Whop" in rep
    stripped = strip_valuable_files_for_prospect(
        f"Here: {cfg.Tradesejon_drive_folder_url} and {cfg.Tradesejon_blueprint_url}",
        is_paid=False,
    )
    assert "YOUR_GOOGLE_ID" not in stripped
    assert "YOUR_GOOGLE_ID" not in stripped
    await svc.close()


@pytest.mark.asyncio
async def test_long_responses_split_across_multiple_messages():
    """Long bot responses (>3600 / >1900 chars) must split cleanly across multiple Discord messages instead of truncating."""
    from tradesejon_discord_bots.security import split_discord_text

    long_text = ("فقرة تعليمية مهمة جداً في التداول.\n\n" * 220).strip()
    chunks_1900 = split_discord_text(long_text, max_len=1900)
    assert len(chunks_1900) >= 4
    assert all(len(c) <= 1900 for c in chunks_1900)

    cfg = _cfg()
    svc, _ = _fake_services(cfg)
    svc.ask_trader_support = AsyncMock(return_value=long_text)  # type: ignore[method-assign]
    bot_t = TraderBotClient(cfg, svc)
    bot_t.notify_pr_privately = AsyncMock()  # type: ignore[method-assign]
    pub_ch, guild = _channel(cid=int(cfg.prospects_channel_id), name="prospects", private_from_everyone=True)
    ix = _interaction(user_id=9899, username="long_q", channel=pub_ch, guild=guild)
    ask_cmd = bot_t.tree.get_command("ask")
    await ask_cmd.callback(ix, "اشرح بالتفصيل")
    assert ix.followup.send.await_count >= 2
    sent_desc = "\n".join(c.kwargs["embed"].description for c in ix.followup.send.call_args_list)
    assert "…" not in sent_desc

    # /tickets must be hidden from non-admins and denied for non-PR/non-CEO users
    t_cmd = bot_t.tree.get_command("tickets")
    assert t_cmd is not None and t_cmd.default_permissions is not None and t_cmd.default_permissions.administrator is True
    ix_non_pr = _interaction(user_id=9899, username="regular_prospect", channel=pub_ch, guild=guild)
    await t_cmd.callback(ix_non_pr)
    ix_non_pr.response.send_message.assert_awaited_once()
    assert "⛔" in ix_non_pr.response.send_message.call_args.args[0]
    await svc.close()


@pytest.mark.asyncio
async def test_admin_send_to_users_command_and_all_file_types_supported():
    from tradesejon_discord_bots.security import is_allowed_attachment

    # All safe images, videos, audio, docs, spreadsheets, archives allowed up to 25MB; executables still blocked
    for ct, fn in (
        ("video/mp4", "clip.mp4"),
        ("video/quicktime", "clip.mov"),
        ("image/png", "chart.png"),
        ("audio/ogg", "voice.ogg"),
        ("application/pdf", "guide.pdf"),
        ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "notes.docx"),
        ("application/zip", "bundle.zip"),
        ("text/csv", "crm.csv"),
    ):
        assert is_allowed_attachment(ct, 1024 * 1024, fn) is True
    assert is_allowed_attachment("application/x-msdownload", 1024, "bad.exe") is False

    cfg = _cfg()
    svc, _ = _fake_services(cfg)
    delivered: list[tuple[str, str, int]] = []

    async def fake_dm(uid: str, msg: str, files: list[tuple[bytes, str]]) -> bool:
        delivered.append((uid, msg, len(files)))
        return True

    bot_c = CEOBotClient(cfg, svc, AsyncMock(return_value=0), AsyncMock(return_value=True), deliver_user_dm_fn=fake_dm)
    send_cmd = bot_c.tree.get_command("send_to_users")
    assert send_cmd is not None and send_cmd.default_permissions.administrator is True

    # Non-admin blocked
    pub_ch, guild = _channel(cid=int(cfg.marhaban_channel_id), name="marhaban", private_from_everyone=False)
    ix_bad = _interaction(user_id=9899, username="prospect", channel=pub_ch, guild=guild)
    await send_cmd.callback(ix_bad, "111222333444555666", "hi")
    assert "⛔" in ix_bad.response.send_message.call_args.args[0]
    assert delivered == []

    # Admin in #pr-desk sends text + video attachment to two selected users
    svc.safe_download_discord_attachment = AsyncMock(return_value=(b"VIDEO_BYTES", "video/mp4", "ok"))  # type: ignore[method-assign]
    pr_ch, _ = _channel(cid=int(next(iter(cfg.pr_channel_ids))), name="pr-desk", private_from_everyone=True)
    ix_admin = _interaction(user_id=int(next(iter(cfg.ceo_user_ids))), username="ceo", channel=pr_ch, guild=guild)
    fake_att = SimpleNamespace(filename="session.mp4", content_type="video/mp4", size=11)
    await send_cmd.callback(ix_admin, "<@111222333444555666>, 222333444555666777", "مرحباً! إليك الفيديو", fake_att)
    assert len(delivered) == 2
    assert delivered[0] == ("111222333444555666", "مرحباً! إليك الفيديو", 1)
    assert delivered[1] == ("222333444555666777", "مرحباً! إليك الفيديو", 1)
    await svc.close()


def test_vps_n8n_workflows_valid_zero_secrets_and_no_laptop_deps() -> None:
    import json
    import re
    from pathlib import Path

    vps_dir = Path(__file__).resolve().parent.parent.parent / "n8n-workflows"
    workflow_files = sorted(vps_dir.glob("[0-9][0-9]_Tradesejon_*.json"))
    assert len(workflow_files) == 21

    combined_text_parts: list[str] = []
    forbidden_local_urls = ("127.0.0.1", "localhost", "host.docker.internal")
    secret_patterns = (
        re.compile(r"gsk_[A-Za-z0-9]{10,}"),
        re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
        re.compile(r"re_[A-Za-z0-9]{16,}"),
        re.compile(r"AIza[A-Za-z0-9_-]{20,}"),
        re.compile(r"\bal-[A-Za-z0-9_-]{20,}"),
        re.compile(r"mongodb\+srv://[^\s\"']+@"),
        re.compile(r"[MN][A-Za-z0-9_-]{23,26}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}"),
    )

    for wf_path in workflow_files:
        raw = wf_path.read_text(encoding="utf-8")
        combined_text_parts.append(raw)
        data = json.loads(raw)

        assert data.get("active") is True, f"{wf_path.name} must be active"
        nodes = data.get("nodes")
        connections = data.get("connections")
        assert isinstance(nodes, list) and len(nodes) > 0, f"{wf_path.name} missing nodes"
        assert isinstance(connections, dict) and len(connections) > 0, f"{wf_path.name} missing connections"

        node_names = {n.get("name") for n in nodes if isinstance(n, dict) and n.get("name")}
        for src, outputs in connections.items():
            assert src in node_names, f"{wf_path.name} connection source {src!r} missing from nodes"
            for branch_group in outputs.values():
                for branch in branch_group:
                    for edge in branch:
                        target = edge.get("node")
                        assert target in node_names, f"{wf_path.name} connection target {target!r} missing from nodes"

        lower_raw = raw.lower()
        for local_host in forbidden_local_urls:
            assert local_host not in lower_raw, f"{wf_path.name} contains local dependency {local_host!r}"

        for pat in secret_patterns:
            assert not pat.search(raw), f"{wf_path.name} matched hardcoded secret pattern {pat.pattern}"

    combined = "\n".join(combined_text_parts)
    assert "WORKSHOP_LAUNCH_TOKEN" in combined
    assert "WHOP_WEBHOOK_SECRET" in combined
    assert "YOUR_GOOGLE_ID" in combined
    assert "max_age: 900" in combined or '"max_age": 900' in combined
    assert "max_uses: 1" in combined or '"max_uses": 1' in combined
    assert "prospect" in combined.lower() and ("drive.google.com" in combined or "strip" in combined.lower() or "valuable" in combined.lower())


def test_detailed_email_protocol_every_error_distinct() -> None:
    from tradesejon_discord_bots.security import validate_email_detailed

    cases = [
        ("", "مطلوب"),
        ("abc def@gmail.com", "مسافات"),
        ("abcdef", "@"),
        ("a@@b.com", "أكثر من علامة @"),
        ("@gmail.com", "قبل علامة @ مفقود"),
        ("user..name@gmail.com", "نقطتين متتاليتين"),
        ("user@gmail", "نقطة وامتداد"),
        ("user@gmail.1", "امتداد النطاق"),
        ("user@gmial.com", "خطأ إملائي"),
        ("user@mailinator.com", "وهمي"),
        ("user@a..com", "صيغة النطاق"),
    ]
    seen_errors: set[str] = set()
    for raw_email, expected_fragment in cases:
        ok, err, normalized = validate_email_detailed(raw_email)
        assert ok is False and normalized == "", f"Expected rejection for {raw_email!r}"
        assert expected_fragment in err, f"Expected {expected_fragment!r} in error {err!r} for {raw_email!r}"
        assert "Must be 5" not in err
        seen_errors.add(err)
    assert len(seen_errors) == len(cases)

    for good in ("trader@gmail.com", "Member.Epsilon@Tradesejon.net"):
        ok, err, normalized = validate_email_detailed(good)
        assert ok is True and err == "ok" and normalized == good.lower()


def test_all_countries_search_and_country_matched_phone_validation() -> None:
    from tradesejon_discord_bots.country_phone import (
        ALL_COUNTRIES,
        resolve_country_from_text,
        validate_phone_for_country,
    )

    assert len(ALL_COUNTRIES) >= 190
    assert resolve_country_from_text("مصر").iso2 == "EG"
    assert resolve_country_from_text("Egypt").iso2 == "EG"
    assert resolve_country_from_text("السعودية").iso2 == "SA"
    assert resolve_country_from_text("Saudi").iso2 == "SA"
    assert resolve_country_from_text("UAE").iso2 == "AE"
    assert resolve_country_from_text("المانيا").iso2 == "DE"
    assert resolve_country_from_text("اليابان").iso2 == "JP"
    assert resolve_country_from_text("Brazil").iso2 == "BR"
    assert resolve_country_from_text("zzzz") is None
    assert resolve_country_from_text("12345") is None

    # Egypt short phone -> exact reasoned error
    ok, err, _ = validate_phone_for_country("+201021", "مصر")
    assert ok is False and "قصير جداً" in err and "Must be 8" not in err

    # Egypt wrong prefix -> exact reasoned error
    ok, err, _ = validate_phone_for_country("+201912345678", "Egypt")
    assert ok is False and "010" in err

    # Egypt chosen, Saudi number entered -> country mismatch error
    ok, err, _ = validate_phone_for_country("+966501234567", "Egypt")
    assert ok is False and "لا يطابق البلد المختار" in err

    # Valid Egypt, Saudi, UAE, Germany
    ok, _, norm = validate_phone_for_country("+201012345678", "مصر")
    assert ok is True and norm == "+201012345678"
    ok, _, norm = validate_phone_for_country("01012345678", "Egypt")
    assert ok is True and norm == "+201012345678"
    ok, _, norm = validate_phone_for_country("+966501234567", "السعودية")
    assert ok is True and norm == "+966501234567"
    ok, err, _ = validate_phone_for_country("+201012345678", "Saudi Arabia")
    assert ok is False and "لا يطابق البلد المختار" in err
    ok, _, norm = validate_phone_for_country("+971501234567", "UAE")
    assert ok is True and norm == "+971501234567"
    ok, _, norm = validate_phone_for_country("+4915123456789", "Germany")
    assert ok is True and norm == "+4915123456789"


@pytest.mark.asyncio
async def test_welcome_messages_sent_privately_via_dm_never_public_channel(monkeypatch) -> None:
    from tradesejon_discord_bots.crm_sync import DiscordRoleAndPersonaClient

    cfg = _cfg()
    mgr = DiscordRoleAndPersonaClient(cfg)
    posted_urls: list[str] = []

    class _FakeResp:
        def __init__(self, status: int, data: dict):
            self.status = status
            self._data = data

        async def json(self):
            return self._data

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class _FakeSession:
        def __init__(self, *args, **kwargs):
            pass

        def post(self, url, **kwargs):
            posted_urls.append(str(url))
            if "/users/@me/channels" in str(url):
                return _FakeResp(200, {"id": "999888777666555444"})
            return _FakeResp(200, {"id": "111"})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    import aiohttp
    monkeypatch.setattr(aiohttp, "ClientSession", _FakeSession)

    res = await mgr.send_persona_message(
        "workshop",
        recipient_user_id="123456789012345678",
        content="🚀 مرحباً بك في Workshop Channel!",
    )
    assert res["ok"] is True and res["private"] is True and res["via"] == "private_dm:workshop"
    assert any("/users/@me/channels" in u for u in posted_urls)
    assert any("/channels/999888777666555444/messages" in u for u in posted_urls)
    assert not any("webhooks" in u for u in posted_urls)


@pytest.mark.asyncio
async def test_all_emails_and_discord_chats_with_media_saved_instantly_to_dual_mongo() -> None:
    cfg = _cfg()
    svc, fdb = _fake_services(cfg)

    # 1. Discord message with image, video, audio, PDF, and zip attachments -> archived once (no duplicates on replay)
    atts = [
        SimpleNamespace(filename="chart.png", content_type="image/png", size=12345, url="https://cdn.discordapp.com/chart.png"),
        SimpleNamespace(filename="session.mp4", content_type="video/mp4", size=99999, url="https://cdn.discordapp.com/session.mp4"),
        SimpleNamespace(filename="voice.ogg", content_type="audio/ogg", size=4500, url="https://cdn.discordapp.com/voice.ogg"),
        SimpleNamespace(filename="plan.pdf", content_type="application/pdf", size=8100, url="https://cdn.discordapp.com/plan.pdf"),
        SimpleNamespace(filename="archive.zip", content_type="application/zip", size=2200, url="https://cdn.discordapp.com/archive.zip"),
    ]
    for _ in range(2):
        await svc.log_discord_chat_message(
            bot_role="trader",
            direction="inbound",
            message_id="msg_media_9001",
            discord_user_id="555111222333444555",
            discord_username="vip_trader",
            channel_id=cfg.workshop_channel_id,
            channel_name="workshop-channel",
            guild_id=cfg.guild_id,
            content="شوف الشارت والفيديو والملفات",
            attachments=atts,
        )
    docs = list(fdb["discord_messages"].find({"message_id": "msg_media_9001"}))
    assert len(docs) == 1
    saved_msg = docs[0]
    assert saved_msg["has_media"] is True
    assert set(saved_msg["media_kinds"]) == {"image", "video", "audio", "document", "file"}
    assert len(saved_msg["attachments"]) == 5

    # 2. Email send attempt -> logged immediately to BOTH outbound_emails AND email_logs
    await svc.send_email_secure(to_email="trader@gmail.com", subject="Welcome", body_text="Hello Tradesejon trader")
    assert fdb["outbound_emails"].count_documents({"to": "trader@gmail.com"}) == 1
    assert fdb["email_logs"].count_documents({"to": "trader@gmail.com"}) == 1
    await svc.close()





