"""Omnichannel CRM sync, Discord role/persona dispatch, tier resolution, and Agent-Reach client."""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp

from .config import DiscordConfig
from .security import (
    is_protected_client_discord_guild,
    is_protected_client_live_target,
    redact_secrets,
    safe_discord_text,
    sanitize_untrusted_text,
)

logger = logging.getLogger("tradesejon.discord.crm_sync")

DISCORD_API_BASE = "https://discord.com/api/v10"
FREE_OTP_TEST_PLAN_ID = "plan_TEMPLATE_01"
FREE_OTP_TEST_URL = "https://whop.com/checkout/plan_TEMPLATE_01"
DISCORD_VIP_45_URL = "https://whop.com/Tradesejon/exp_TEMPLATE_VIP"

STATUS_LABELS: dict[str, str] = {
    "pending_whop_otp": "Pending Whop OTP (Email Verification Required)",
    "prospect": "Prospect (Whop OTP Verified — In #prospects)",
    "prospect_paid_once": "Prospect Paid Once (Workshop Active)",
    "vip_recurring": "VIP Recurring ($45/mo Active)",
    "viproom_workshop_vip": "Online Workshop ($100) + V tradesejon VIP (Both Roles)",
    "churned": "Churned / Access Revoked",
}


ONLINE_MENTOR_PLANS: dict[str, dict[str, Any]] = {
    "plan_TEMPLATE_05": {"mentor": "MentorAlpha (MentorAlpha)", "checkout_url": "https://whop.com/checkout/plan_TEMPLATE_05"},
    "plan_TEMPLATE_06": {"mentor": "MentorBeta (MentorBeta ⚡)", "checkout_url": "https://whop.com/checkout/plan_TEMPLATE_06"},
    "plan_TEMPLATE_07": {"mentor": "MentorGamma (MentorGamma)", "checkout_url": "https://whop.com/checkout/plan_TEMPLATE_07"},
    "plan_TEMPLATE_08": {"mentor": "MentorDelta (MentorDelta)", "checkout_url": "https://whop.com/checkout/plan_TEMPLATE_08"},
    "plan_TEMPLATE_09": {"mentor": "Mentor Epsilon (Mentor Epsilon)", "checkout_url": "https://whop.com/checkout/plan_TEMPLATE_09"},
}

OFFLINE_WORKSHOP_PLANS: dict[str, dict[str, Any]] = {
    "plan_TEMPLATE_03": {
        "title": "Tradesejon Offline Workshop (Discord Member - 4,000 EGP / $74.39)",
        "price_usd": 74.39,
        "price_egp": 4000,
        "checkout_url": "https://whop.com/checkout/plan_TEMPLATE_03",
    },
    "plan_TEMPLATE_04": {
        "title": "Tradesejon Offline Workshop (Non-Member - 6,000 EGP / $111.59)",
        "price_usd": 111.59,
        "price_egp": 6000,
        "checkout_url": "https://whop.com/checkout/plan_TEMPLATE_04",
    },
}


def build_tier_defs(cfg: DiscordConfig) -> dict[str, dict[str, Any]]:
    """Return canonical tier table matching official Tradesejon file prices ($0 OTP Test, $45/mo VIP, 4000/6000 EGP Offline, $100 Online)."""
    p_free = FREE_OTP_TEST_PLAN_ID
    p45 = cfg.whop_plan_workshop_45 if cfg.whop_plan_workshop_45 not in ("", FREE_OTP_TEST_PLAN_ID) else "exp_TEMPLATE_12"
    url_45 = (
        cfg.whop_checkout_workshop_45
        if cfg.whop_checkout_workshop_45 and FREE_OTP_TEST_PLAN_ID not in cfg.whop_checkout_workshop_45
        else DISCORD_VIP_45_URL
    )
    p_non_member = "plan_TEMPLATE_04"
    url_non_member = OFFLINE_WORKSHOP_PLANS[p_non_member]["checkout_url"]
    p_online = "plan_TEMPLATE_05"
    url_online = ONLINE_MENTOR_PLANS[p_online]["checkout_url"]

    return {
        "free_otp_test": {
            "tier": "free_otp_test",
            "title": "Whop Free Pass ($0.00 — Email OTP Verification Test)",
            "price_usd": 0.0,
            "billing": "free_otp_gate",
            "plan_id": p_free,
            "alt_plan_ids": (p_free,),
            "checkout_url": FREE_OTP_TEST_URL,
            "add_roles": [r for r in (cfg.prospect_role_id,) if r],
            "remove_roles": [],
            "welcome_channels": [c for c in (cfg.prospects_channel_id,) if c],
            "default_status": "prospect",
        },
        "workshop_45": {
            "tier": "workshop_45",
            "title": "Discord Live Trading + Scanner VIP ($45/month)",
            "price_usd": 45,
            "billing": "monthly",
            "plan_id": p45,
            "alt_plan_ids": tuple(dict.fromkeys((
                p45,
                "exp_TEMPLATE_12",
                "plan_TEMPLATE_13",
                "plan_TEMPLATE_03",
                cfg.whop_plan_legacy_45,
            ))),
            "checkout_url": url_45,
            "add_roles": [r for r in (cfg.workshop_role_id,) if r],
            "remove_roles": [r for r in (cfg.prospect_role_id,) if r],
            "welcome_channels": [c for c in (cfg.workshop_channel_id,) if c],
            "default_status": "prospect_paid_once",
        },
        "external_60": {
            "tier": "external_60",
            "title": "Tradesejon Offline Workshop (Non-Member - 6,000 EGP / $111.59)",
            "price_usd": 111.59,
            "price_egp": 6000,
            "billing": "one_time",
            "plan_id": p_non_member,
            "alt_plan_ids": tuple(dict.fromkeys((p_non_member, cfg.whop_plan_external_60, "plan_TEMPLATE_14"))),
            "checkout_url": url_non_member,
            "add_roles": [r for r in (cfg.workshop_role_id,) if r],
            "remove_roles": [r for r in (cfg.prospect_role_id,) if r],
            "welcome_channels": [c for c in (cfg.workshop_channel_id,) if c],
            "default_status": "prospect_paid_once",
        },
        "viproom_75": {
            "tier": "viproom_75",
            "title": "Online Workshop 4H Private ($100) + V tradesejon VIP",
            "price_usd": 100,
            "billing": "one_time_vip",
            "plan_id": p_online,
            "alt_plan_ids": tuple(dict.fromkeys((*ONLINE_MENTOR_PLANS.keys(), cfg.whop_plan_viproom_75, "plan_TEMPLATE_15"))),
            "checkout_url": url_online,
            "add_roles": [r for r in (cfg.viproom_role_id, cfg.workshop_role_id) if r],
            "remove_roles": [r for r in (cfg.prospect_role_id,) if r],
            "welcome_channels": [c for c in (cfg.viproom_channel_id, cfg.workshop_channel_id) if c],
            "default_status": "viproom_workshop_vip",
        },
    }


def _extract_whop_amount_usd(payment_obj: dict[str, Any]) -> float:
    """Parse monetary amount in USD from Whop v1/v2/v5 payment or membership payloads."""
    raw_total = payment_obj.get("total")
    amount_val: float = 0.0
    if isinstance(raw_total, dict):
        try:
            amount_val = float(raw_total.get("amount") or raw_total.get("final_amount") or 0)
        except Exception:
            amount_val = 0.0
    elif raw_total is not None:
        try:
            amount_val = float(raw_total)
        except Exception:
            amount_val = 0.0
    if not amount_val:
        for k in ("final_amount", "amount", "subtotal", "amount_usd", "usd_total", "amount_charged", "initial_price"):
            if payment_obj.get(k) is not None:
                try:
                    amount_val = float(payment_obj[k])
                    break
                except Exception:
                    pass
    if amount_val > 500:
        amount_val = amount_val / 100.0
    return amount_val


def resolve_tier_from_payment(payment_obj: dict[str, Any], cfg: DiscordConfig) -> dict[str, Any]:
    """Identify paid/OTP tier uniformly from Whop plan/variant id, metadata, or payment amount."""
    tiers = build_tier_defs(cfg)
    meta = payment_obj.get("metadata") if isinstance(payment_obj.get("metadata"), dict) else {}
    raw_plan = payment_obj.get("plan")
    plan_from_obj = raw_plan.get("id") if isinstance(raw_plan, dict) else (raw_plan if isinstance(raw_plan, str) else "")
    plan_id = str(
        payment_obj.get("variant_id")
        or payment_obj.get("plan_id")
        or plan_from_obj
        or meta.get("plan_id")
        or ""
    ).strip()
    meta_tier = str(meta.get("tier") or payment_obj.get("tier") or "").strip().lower()
    has_explicit_amount = any(payment_obj.get(k) is not None for k in ("total", "final_amount", "amount", "subtotal", "amount_usd"))
    amount_val = _extract_whop_amount_usd(payment_obj)

    if meta_tier == "free_otp_test":
        return tiers["free_otp_test"]
    if plan_id == FREE_OTP_TEST_PLAN_ID and amount_val <= 0 and meta_tier not in ("workshop_45", "external_60", "viproom_75"):
        return tiers["free_otp_test"]
    if has_explicit_amount and amount_val <= 0 and not plan_id and not meta_tier:
        return tiers["free_otp_test"]
    if plan_id in ONLINE_MENTOR_PLANS:
        m_info = ONLINE_MENTOR_PLANS[plan_id]
        base = dict(tiers["viproom_75"])
        base["plan_id"] = plan_id
        base["price_usd"] = 100
        base["mentor"] = m_info["mentor"]
        base["workshop_mode"] = "online_100usd_4h"
        base["title"] = f"Online Workshop 4H ($100) — Mentor: {m_info['mentor']}"
        base["checkout_url"] = m_info["checkout_url"]
        return base
    if plan_id in OFFLINE_WORKSHOP_PLANS:
        off_info = OFFLINE_WORKSHOP_PLANS[plan_id]
        base = dict(tiers["workshop_45"] if plan_id == "plan_TEMPLATE_03" else tiers["external_60"])
        base["plan_id"] = plan_id
        base["price_usd"] = off_info["price_usd"]
        base["price_egp"] = off_info["price_egp"]
        base["workshop_mode"] = "offline_egypt"
        base["title"] = off_info["title"]
        base["checkout_url"] = off_info["checkout_url"]
        return base
    if meta_tier in tiers:
        return tiers[meta_tier]
    if plan_id in ("plan_TEMPLATE_04", "plan_TEMPLATE_14"):
        return tiers["external_60"]
    if plan_id == "plan_TEMPLATE_15":
        return tiers["viproom_75"]
    if plan_id in ("exp_TEMPLATE_12", "plan_TEMPLATE_13", cfg.whop_plan_legacy_45):
        return tiers["workshop_45"]
    for spec in tiers.values():
        if plan_id and plan_id in spec["alt_plan_ids"]:
            return spec

    if amount_val >= 105:
        return tiers["external_60"]
    if amount_val >= 70:
        return tiers["viproom_75"]
    return tiers["workshop_45"]


def compute_next_status(
    *,
    event_type: str,
    current_status: str = "",
    tier: str = "",
    paid_count: int = 0,
) -> str:
    """Compute canonical funnel status across OTP verification, prospects, paid workshops, and refunds."""
    ev = (event_type or "").lower().strip()
    if ev in ("refund", "churned", "revoked", "payment.refunded", "membership.went_invalid"):
        return "churned"
    if ev in ("pending_whop_otp", "pending_otp"):
        if current_status in ("prospect", "prospect_paid_once", "vip_recurring", "viproom_workshop_vip"):
            return current_status
        return "pending_whop_otp"
    if ev in ("prospect", "lead", "form_submitted") or tier == "free_otp_test":
        if current_status in ("prospect_paid_once", "vip_recurring", "viproom_workshop_vip"):
            return current_status
        return "prospect"
    if tier == "viproom_75" or current_status == "viproom_workshop_vip":
        return "viproom_workshop_vip"
    if paid_count >= 1 or current_status in ("prospect_paid_once", "vip_recurring"):
        return "vip_recurring" if paid_count >= 2 or current_status == "prospect_paid_once" else "prospect_paid_once"
    return "prospect_paid_once"


class AgentReachClient:
    """Asynchronous wrapper around Panniantong/agent-reach CLI for real-time internet & market lookup."""

    URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
    TICKER_RE = re.compile(r"\$([A-Z]{2,6})\b")

    def __init__(self, cfg: DiscordConfig):
        self.cfg = cfg

    def available(self) -> bool:
        """Check whether the agent-reach executable is installed on disk."""
        exe = str(self.cfg.agent_reach_exe or "").strip()
        return bool(exe and Path(exe).exists())

    async def run(self, args: list[str], *, timeout: float = 18.0) -> dict[str, Any]:
        """Run agent-reach CLI command safely without shell interpolation and parse JSON output."""
        if any(is_protected_client_live_target(a) for a in args):
            return {"ok": False, "blocked": True, "error": "protected_client_live_business_link", "results": []}
        if not self.available():
            return {"ok": False, "error": "agent_reach_not_installed", "results": []}
        try:
            proc = await asyncio.create_subprocess_exec(
                self.cfg.agent_reach_exe,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            raw_out = stdout_b.decode("utf-8", errors="replace").strip()
            raw_err = stderr_b.decode("utf-8", errors="replace").strip()
            if raw_out:
                try:
                    parsed = json.loads(raw_out)
                    if isinstance(parsed, dict):
                        return parsed
                    return {"ok": True, "data": parsed}
                except Exception:
                    return {"ok": proc.returncode == 0, "text": raw_out[:3500], "stderr": raw_err[:400]}
            return {"ok": proc.returncode == 0, "stderr": raw_err[:400], "results": []}
        except asyncio.TimeoutError:
            return {"ok": False, "error": "timeout", "results": []}
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:240], "results": []}

    async def smart_research(self, query_or_url: str, *, limit: int = 3) -> dict[str, Any]:
        """Fetch live context via Agent-Reach when URL, ticker, or live web lookup is requested."""
        text = sanitize_untrusted_text(query_or_url, 500)
        if not text:
            return {"ok": False, "summary": "", "source": "none"}
        if is_protected_client_live_target(text):
            return {"ok": False, "blocked": True, "reason": "protected_client_live_business_link", "summary": "", "source": "client_link_firewall"}

        url_match = self.URL_RE.search(text)
        if url_match:
            target_url = url_match.group(0).rstrip(".,);]")
            res = await self.run(["read", target_url, "--timeout", "15"], timeout=17.0)
            snippet = self._extract_text(res)
            return {"ok": bool(snippet), "source": f"agent-reach:read({target_url})", "summary": snippet[:1800], "raw": res}

        lower = text.lower()
        ticker_match = self.TICKER_RE.search(text)
        if ticker_match:
            sym = ticker_match.group(1)
            res = await self.run(["xueqiu", "quote", sym], timeout=14.0)
            snippet = self._extract_text(res)
            if snippet:
                return {"ok": True, "source": f"agent-reach:xueqiu({sym})", "summary": snippet[:1500], "raw": res}

        if any(k in lower for k in ("github", "repo", "agent-reach", "open source")):
            res = await self.run(["github", "search", text[:120], "--limit", str(limit)], timeout=14.0)
            snippet = self._extract_text(res)
            if snippet:
                return {"ok": True, "source": "agent-reach:github", "summary": snippet[:1500], "raw": res}

        if any(k in lower for k in ("youtube", "فيديو", "شرح يوتيوب", "تحليل")):
            res = await self.run(["youtube", "search", text[:120], "--limit", str(limit)], timeout=14.0)
            snippet = self._extract_text(res)
            if snippet:
                return {"ok": True, "source": "agent-reach:youtube", "summary": snippet[:1500], "raw": res}

        res = await self.run(["web", "search", text[:160], "--limit", str(limit)], timeout=16.0)
        snippet = self._extract_text(res)
        return {"ok": bool(snippet), "source": "agent-reach:web", "summary": snippet[:1600], "raw": res}

    @staticmethod
    def _extract_text(res: dict[str, Any]) -> str:
        if not isinstance(res, dict):
            return ""
        if res.get("text"):
            return str(res["text"])
        items = res.get("results") or res.get("items") or res.get("data")
        if isinstance(items, list) and items:
            parts: list[str] = []
            for idx, item in enumerate(items[:4], start=1):
                if isinstance(item, dict):
                    title = str(item.get("title") or item.get("name") or item.get("full_name") or "").strip()
                    url = str(item.get("url") or item.get("html_url") or item.get("link") or "").strip()
                    desc = str(item.get("snippet") or item.get("text") or item.get("description") or item.get("content") or "").strip()
                    line = f"{idx}. {title}".strip()
                    if url:
                        line += f" — {url}"
                    if desc:
                        line += f"\n   {desc[:260]}"
                    parts.append(line)
                else:
                    parts.append(f"{idx}. {str(item)[:260]}")
            return "\n".join(parts)
        for key in ("content", "markdown", "title", "description", "summary"):
            if res.get(key):
                return str(res[key])[:1600]
        return ""


class DiscordRoleAndPersonaClient:
    """REST manager for guild membership check, role transitions, and persona webhooks."""

    def __init__(self, cfg: DiscordConfig):
        self.cfg = cfg

    def _bot_token_for(self, persona: str = "trader") -> str:
        mapping = {
            "marhaban": self.cfg.marhaban_token or self.cfg.trader_token,
            "prospect": self.cfg.prospect_token or self.cfg.trader_token,
            "workshop": self.cfg.workshop_token or self.cfg.trader_token,
            "viproom": self.cfg.viproom_token or self.cfg.trader_token,
            "pr": self.cfg.pr_token or self.cfg.trader_token,
            "ceo": self.cfg.ceo_token or self.cfg.trader_token,
        }
        return mapping.get(persona, self.cfg.trader_token) or ""

    def _webhook_for(self, persona: str) -> str:
        return {
            "marhaban": self.cfg.marhaban_webhook_url,
            "prospect": self.cfg.prospect_webhook_url,
            "workshop": self.cfg.workshop_webhook_url,
            "viproom": self.cfg.viproom_webhook_url,
        }.get(persona, "")

    def _channel_for(self, persona: str) -> str:
        return {
            "marhaban": self.cfg.marhaban_channel_id,
            "prospect": self.cfg.prospects_channel_id,
            "workshop": self.cfg.workshop_channel_id,
            "viproom": self.cfg.viproom_channel_id,
        }.get(persona, self.cfg.marhaban_channel_id)

    async def check_is_community_member(self, discord_user_id: str) -> dict[str, Any]:
        """Verify guild membership & roles via REST (works cleanly without Members Intent)."""
        uid = str(discord_user_id or "").strip()
        if not uid.isdigit() or not self.cfg.guild_id:
            return {"in_community": False, "roles": [], "already_workshop": False, "already_viproom": False, "is_prospect": False}
        token = self._bot_token_for("ceo") or self._bot_token_for("trader")
        if not token:
            return {"in_community": True, "roles": [], "already_workshop": False, "already_viproom": False, "is_prospect": False}
        url = f"{DISCORD_API_BASE}/guilds/{self.cfg.guild_id}/members/{uid}"
        headers = {"Authorization": f"Bot {token}"}
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        member = await resp.json()
                        roles = [str(r) for r in (member.get("roles") or [])]
                        return {
                            "in_community": True,
                            "roles": roles,
                            "already_workshop": self.cfg.workshop_role_id in roles,
                            "already_viproom": self.cfg.viproom_role_id in roles,
                            "is_prospect": self.cfg.prospect_role_id in roles,
                        }
                    if resp.status == 404:
                        return {"in_community": False, "roles": [], "already_workshop": False, "already_viproom": False, "is_prospect": False}
        except Exception as exc:
            logger.debug("check_is_community_member skipped: %s", exc)
        return {"in_community": True, "roles": [], "already_workshop": False, "already_viproom": False, "is_prospect": False}

    async def apply_funnel_roles(
        self,
        discord_user_id: str,
        *,
        add_role_ids: list[str] | tuple[str, ...] = (),
        remove_role_ids: list[str] | tuple[str, ...] = (),
        reason: str = "Tradesejon funnel transition",
    ) -> dict[str, Any]:
        """Assign and remove Discord roles via REST safely and idempotently."""
        uid = str(discord_user_id or "").strip()
        if is_protected_client_discord_guild(self.cfg.guild_id):
            return {"ok": False, "blocked": True, "reason": "protected_client_discord_guild", "added": [], "removed": [], "errors": []}
        if not uid.isdigit() or not self.cfg.guild_id:
            return {"ok": False, "skipped": True, "reason": "non_numeric_discord_id", "added": [], "removed": [], "errors": []}
        token = self._bot_token_for("ceo") or self._bot_token_for("trader")
        if not token:
            return {"ok": False, "skipped": True, "reason": "missing_bot_token", "added": [], "removed": [], "errors": []}

        headers = {"Authorization": f"Bot {token}", "X-Audit-Log-Reason": sanitize_untrusted_text(reason, 80)}
        added: list[str] = []
        removed: list[str] = []
        errors: list[str] = []
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            for rid in dict.fromkeys(str(r).strip() for r in add_role_ids if r and str(r).strip().isdigit()):
                url = f"{DISCORD_API_BASE}/guilds/{self.cfg.guild_id}/members/{uid}/roles/{rid}"
                try:
                    async with session.put(url, headers=headers) as resp:
                        if resp.status in (200, 204):
                            added.append(rid)
                        else:
                            errors.append(f"add:{rid}:{resp.status}")
                except Exception as exc:
                    errors.append(f"add:{rid}:{type(exc).__name__}")
            for rid in dict.fromkeys(str(r).strip() for r in remove_role_ids if r and str(r).strip().isdigit()):
                url = f"{DISCORD_API_BASE}/guilds/{self.cfg.guild_id}/members/{uid}/roles/{rid}"
                try:
                    async with session.delete(url, headers=headers) as resp:
                        if resp.status in (200, 204, 404):
                            removed.append(rid)
                        else:
                            errors.append(f"del:{rid}:{resp.status}")
                except Exception as exc:
                    errors.append(f"del:{rid}:{type(exc).__name__}")
        return {"ok": not errors, "added": added, "removed": removed, "errors": errors}

    async def send_persona_message(
        self,
        persona: str,
        *,
        content: str = "",
        embeds: list[dict[str, Any]] | None = None,
        channel_id: str = "",
        recipient_user_id: str = "",
    ) -> dict[str, Any]:
        """Deliver persona message privately via DM when recipient_user_id is set (never publicly to channels)."""
        safe_content = redact_secrets(safe_discord_text(content, 1900), self.cfg.known_secrets) if content else ""
        payload: dict[str, Any] = {
            "allowed_mentions": {"parse": []},
        }
        if safe_content:
            payload["content"] = safe_content
        if embeds:
            payload["embeds"] = embeds[:1]

        uid = str(recipient_user_id or "").strip()
        if uid:
            if not uid.isdigit():
                return {"ok": False, "skipped": True, "reason": "invalid_recipient_user_id"}
            token = self._bot_token_for(persona)
            if not token:
                return {"ok": False, "error": "missing_bot_token"}
            headers = {"Authorization": f"Bot {token}"}
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                    async with session.post(
                        f"{DISCORD_API_BASE}/users/@me/channels",
                        headers=headers,
                        json={"recipient_id": uid},
                    ) as dm_resp:
                        if not (200 <= dm_resp.status < 300):
                            return {"ok": False, "via": f"private_dm:{persona}", "status": dm_resp.status}
                        dm_data = await dm_resp.json()
                        dm_ch_id = str(dm_data.get("id") or "")
                    if not dm_ch_id:
                        return {"ok": False, "via": f"private_dm:{persona}", "error": "missing_dm_channel_id"}
                    async with session.post(
                        f"{DISCORD_API_BASE}/channels/{dm_ch_id}/messages",
                        headers=headers,
                        json=payload,
                    ) as msg_resp:
                        return {
                            "ok": 200 <= msg_resp.status < 300,
                            "via": f"private_dm:{persona}",
                            "private": True,
                            "status": msg_resp.status,
                        }
            except Exception as exc:
                return {"ok": False, "via": f"private_dm:{persona}", "error": str(exc)[:180]}

        wh_url = self._webhook_for(persona)
        if wh_url and not channel_id:
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                    async with session.post(f"{wh_url}?wait=true", json=payload) as resp:
                        if 200 <= resp.status < 300:
                            return {"ok": True, "via": f"webhook:{persona}", "status": resp.status}
            except Exception as exc:
                logger.warning("Webhook persona %s failed: %s", persona, exc)

        target_ch = channel_id or self._channel_for(persona)
        token = self._bot_token_for(persona)
        if target_ch and target_ch.isdigit() and token:
            url = f"{DISCORD_API_BASE}/channels/{target_ch}/messages"
            headers = {"Authorization": f"Bot {token}"}
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                    async with session.post(url, headers=headers, json=payload) as resp:
                        return {"ok": 200 <= resp.status < 300, "via": f"bot_rest:{persona}", "status": resp.status}
            except Exception as exc:
                return {"ok": False, "error": str(exc)[:180]}
        return {"ok": False, "error": "no_channel_or_webhook"}


class OmnichannelSyncEngine:
    """Instant synchronization across CSV, Google Sheets, n8n (local + VPS), and AIOS CRM."""

    CSV_HEADERS = [
        "updated_at",
        "discord_user_id",
        "discord_username",
        "full_name",
        "email",
        "phone",
        "tier",
        "status",
        "in_community",
        "plan_id",
        "amount_usd",
        "roles",
        "whop_payment_id",
        "source",
        "live_link",
        "notes",
    ]

    def __init__(self, cfg: DiscordConfig):
        self.cfg = cfg
        self._csv_lock = asyncio.Lock()
        self._gsheet_lock = asyncio.Lock()

    async def sync_google_sheet_live(self, csv_path: Path) -> dict[str, Any]:
        """Auto-update live Google Sheet (Tradesejon CRM & Forms) in-place via gdrive CLI."""
        sheet_id = str(self.cfg.tradesajon_gsheet_id or "").strip()
        if not sheet_id or not csv_path.exists():
            return {"ok": False, "skipped": True, "reason": "missing_sheet_id_or_csv"}
        low_path = str(csv_path).lower()
        if "pytest" in low_path or "\\temp\\" in low_path or "/temp/" in low_path:
            return {"ok": False, "skipped": True, "reason": "test_temp_csv"}
        gdrive_exe = shutil.which("gdrive")
        if not gdrive_exe:
            return {"ok": False, "skipped": True, "reason": "gdrive_cli_not_found"}
        async with self._gsheet_lock:
            try:
                proc = await asyncio.create_subprocess_exec(
                    gdrive_exe,
                    "mutate",
                    "update",
                    sheet_id,
                    str(csv_path),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=15.0)
                return {
                    "ok": proc.returncode == 0,
                    "sheet_id": sheet_id,
                    "stdout": stdout_b.decode("utf-8", errors="replace")[:200],
                    "stderr": stderr_b.decode("utf-8", errors="replace")[:200],
                }
            except Exception as exc:
                return {"ok": False, "sheet_id": sheet_id, "error": str(exc)[:180]}

    async def write_csv_snapshot(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Atomically persist all CRM/form records to local master CSV mirror for Google Sheets & audit."""
        csv_path = Path(self.cfg.tradesajon_crm_csv_path)
        async with self._csv_lock:
            def _write() -> int:
                csv_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = csv_path.with_suffix(".csv.tmp")
                written = 0
                with tmp.open("w", encoding="utf-8-sig", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=self.CSV_HEADERS)
                    writer.writeheader()
                    for doc in rows:
                        roles_raw = doc.get("roles") or doc.get("discord_roles") or []
                        if isinstance(roles_raw, (list, tuple, set)):
                            roles_str = "|".join(str(x) for x in roles_raw if x)
                        else:
                            roles_str = str(roles_raw or "")
                        writer.writerow({
                            "updated_at": str(doc.get("updated_at") or datetime.now(timezone.utc).isoformat()),
                            "discord_user_id": str(doc.get("discord_user_id") or doc.get("user_id") or ""),
                            "discord_username": sanitize_untrusted_text(str(doc.get("discord_username") or doc.get("trader_label") or ""), 80),
                            "full_name": sanitize_untrusted_text(str(doc.get("full_name") or doc.get("name") or ""), 120),
                            "email": str(doc.get("email") or "").strip().lower(),
                            "phone": str(doc.get("phone") or ""),
                            "tier": str(doc.get("tier") or ("workshop_45" if doc.get("paid") else "prospect")),
                            "status": str(doc.get("status") or "prospect"),
                            "in_community": str(bool(doc.get("in_community", True))).lower(),
                            "plan_id": str(doc.get("plan_id") or ""),
                            "amount_usd": str(doc.get("amount_usd") or ("0" if not doc.get("paid") else "45")),
                            "roles": roles_str,
                            "whop_payment_id": str(doc.get("whop_payment_id") or ""),
                            "source": sanitize_untrusted_text(str(doc.get("source") or "discord"), 60),
                            "live_link": str(doc.get("live_link") or doc.get("zoom_url") or ""),
                            "notes": sanitize_untrusted_text(str(doc.get("notes") or doc.get("message") or ""), 300),
                        })
                        written += 1
                tmp.replace(csv_path)
                return written

            count = await asyncio.to_thread(_write)
        gs_res = await self.sync_google_sheet_live(csv_path)
        return {"ok": True, "csv_path": str(csv_path), "rows": count, "sheet_url": self.cfg.tradesajon_gsheet_url, "gsheet_live_sync": gs_res}

    async def notify_n8n_funnel_sync(self, record: dict[str, Any], *, all_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Push instant event to local n8n webhook & Google Sheets webhook (and VPS n8n when reachable)."""
        payload = {
            "event": record.get("event") or record.get("status") or "crm_sync",
            "sheet_id": self.cfg.tradesajon_gsheet_id,
            "sheet_url": self.cfg.tradesajon_gsheet_url,
            "record": record,
            "total_rows": len(all_rows) if all_rows is not None else 0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        targets: list[tuple[str, str]] = []
        if self.cfg.n8n_base_url:
            targets.append(("n8n_local", f"{self.cfg.n8n_base_url}/webhook/tradesejon-funnel-sync"))
        if self.cfg.aios_base_url:
            targets.append(("aios_sync", f"{self.cfg.aios_base_url}/api/funnel-sync"))
        if self.cfg.gsheets_webhook_url:
            targets.append(("gsheets_webhook", self.cfg.gsheets_webhook_url))
        if self.cfg.vps_n8n_base_url and self.cfg.vps_n8n_api_key:
            targets.append(("n8n_vps", f"{self.cfg.vps_n8n_base_url}/webhook/tradesejon-funnel-sync"))

        headers = {"Content-Type": "application/json"}
        if self.cfg.aios_internal_token:
            headers["X-AIOS-Token"] = self.cfg.aios_internal_token

        results: dict[str, Any] = {}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6)) as session:
            for name, url in targets:
                try:
                    async with session.post(url, json=payload, headers=headers) as resp:
                        results[name] = {"ok": 200 <= resp.status < 300, "status": resp.status}
                except Exception as exc:
                    results[name] = {"ok": False, "error": str(exc)[:120]}
        return {"ok": any(v.get("ok") for v in results.values()), "targets": results}
