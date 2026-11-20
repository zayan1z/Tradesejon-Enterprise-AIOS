"""Loopback-only authenticated HTTP bridge (127.0.0.1:8095) for n8n & AIOS."""

from __future__ import annotations

import base64
import logging
from typing import Any, Awaitable, Callable

import discord
from aiohttp import web

from .bot_pr import PRTicketActionView, build_pr_ticket_embed
from .config import DiscordConfig
from .security import (
    constant_time_bearer_ok,
    redact_secrets,
    safe_allowed_mentions,
    safe_discord_text,
    sanitize_untrusted_text,
)
from .shared_services import SharedServices

logger = logging.getLogger("tradesejon.discord.bridge")

MAX_BRIDGE_BODY_BYTES = 5 * 1024 * 1024


def create_bridge_app(
    cfg: DiscordConfig,
    services: SharedServices,
    *,
    status_fn: Callable[[], dict[str, Any]],
    notify_ceo_fn: Callable[[str], Awaitable[bool]],
    notify_pr_fn: Callable[[discord.Embed, PRTicketActionView | None], Awaitable[None]],
    reply_trader_fn: Callable[[str, str, bytes | None, str], Awaitable[bool]],
    broadcast_fn: Callable[[str, bytes | None, str], Awaitable[int]],
    deliver_zoom_fn: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    dm_user_fn: Callable[[str, str, list[tuple[bytes, str]] | None], Awaitable[bool]] | None = None,
) -> web.Application:
    """Build aiohttp loopback application with constant-time Bearer authentication."""

    @web.middleware
    async def auth_and_ip_middleware(request: web.Request, handler):
        # Strictly permit loopback peer addresses only
        peer = request.remote or ""
        if peer not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            return web.json_response({"ok": False, "error": "forbidden_non_loopback"}, status=403)
        if request.path != "/health":
            if not constant_time_bearer_ok(request.headers.get("Authorization"), cfg.bridge_secret):
                return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
        return await handler(request)

    app = web.Application(middlewares=[auth_and_ip_middleware], client_max_size=MAX_BRIDGE_BODY_BYTES)

    async def health(request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "service": "tradesejon-discord-bridge", "bots": status_fn()})

    async def notify_ceo(request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid_json"}, status=400)
        msg = redact_secrets(sanitize_untrusted_text(str(data.get("message") or data.get("text") or ""), 1900), cfg.known_secrets)
        if not msg:
            return web.json_response({"ok": False, "error": "empty_message"}, status=400)
        sent = await notify_ceo_fn(msg)
        return web.json_response({"ok": True, "delivered": bool(sent)})

    async def notify_pr(request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid_json"}, status=400)
        tcode = sanitize_untrusted_text(str(data.get("ticket_code") or "TRD-NEW"), 24).upper()
        embed = build_pr_ticket_embed(
            ticket_code=tcode,
            trader_label=str(data.get("trader_label") or data.get("full_name") or "Lead"),
            user_id=str(data.get("user_id") or data.get("email") or "-"),
            platform_source=str(data.get("source") or "AIOS/n8n"),
            question=str(data.get("message") or data.get("question") or "-"),
            email=str(data.get("email") or ""),
            phone=str(data.get("phone") or ""),
            lead_score=str(data.get("lead_score") or ""),
        )
        await notify_pr_fn(embed, PRTicketActionView(tcode))
        return web.json_response({"ok": True, "ticket_code": tcode})

    def _decode_attachment(data: dict[str, Any]) -> tuple[bytes | None, str]:
        filename = sanitize_untrusted_text(str(data.get("filename") or ""), 80)
        raw_b64 = data.get("file_base64")
        if raw_b64 is not None and str(raw_b64).strip() != "":
            file_bytes = base64.b64decode(str(raw_b64), validate=True)
            return file_bytes, filename or "attachment.bin"
        return None, filename

    async def reply_trader(request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid_json"}, status=400)
        tcode = sanitize_untrusted_text(str(data.get("ticket_code") or ""), 24).upper()
        text = safe_discord_text(str(data.get("message") or ""))
        if not tcode or not text:
            return web.json_response({"ok": False, "error": "missing_ticket_or_message"}, status=400)
        try:
            file_bytes, filename = _decode_attachment(data)
        except Exception:
            return web.json_response({"ok": False, "error": "invalid_base64"}, status=400)
        ok = await reply_trader_fn(tcode, text, file_bytes, filename)
        return web.json_response({"ok": ok, "ticket_code": tcode})

    async def broadcast(request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid_json"}, status=400)
        text = safe_discord_text(str(data.get("message") or data.get("text") or ""))
        try:
            file_bytes, filename = _decode_attachment(data)
        except Exception:
            return web.json_response({"ok": False, "error": "invalid_base64"}, status=400)
        if not text and not file_bytes:
            return web.json_response({"ok": False, "error": "empty_message"}, status=400)
        count = await broadcast_fn(text or "📢 إشعار مباشر من Tradesejon", file_bytes, filename)
        return web.json_response({"ok": True, "delivered_count": count})

    async def deliver_zoom(request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid_json"}, status=400)
        if deliver_zoom_fn is None:
            return web.json_response({"ok": False, "error": "zoom_delivery_unavailable"}, status=503)
        res = await deliver_zoom_fn(data if isinstance(data, dict) else {})
        return web.json_response(res)

    app.router.add_get("/health", health)
    app.router.add_get("/api/discord/status", health)
    app.router.add_post("/api/discord/notify-ceo", notify_ceo)
    app.router.add_post("/api/discord/notify-pr", notify_pr)
    app.router.add_post("/api/discord/reply-trader", reply_trader)
    app.router.add_post("/api/discord/broadcast", broadcast)
    async def dm_user(request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid_json"}, status=400)
        target_uid = str(data.get("user_id") or data.get("discord_user_id") or "").strip()
        msg = safe_discord_text(str(data.get("message") or data.get("text") or ""))
        if not target_uid:
            return web.json_response({"ok": False, "error": "missing_user_id"}, status=400)
        if not msg and not data.get("file_base64"):
            return web.json_response({"ok": False, "error": "empty_message"}, status=400)
        try:
            file_bytes, filename = _decode_attachment(data)
            raw_files = [(file_bytes, filename)] if (file_bytes and filename) else None
        except Exception:
            return web.json_response({"ok": False, "error": "invalid_base64"}, status=400)
        if dm_user_fn is None:
            return web.json_response({"ok": False, "error": "dm_user_unavailable"}, status=503)
        try:
            delivered = await dm_user_fn(target_uid, msg, raw_files)
            return web.json_response({"ok": bool(delivered), "user_id": target_uid, "delivered": bool(delivered)})
        except Exception as e:
            logger.exception("Error during dm_user execution: %s", e)
            return web.json_response({"ok": False, "delivered": False, "error": f"{type(e).__name__}: {str(e)}"}, status=200)

    app.router.add_post("/api/discord/deliver-zoom", deliver_zoom)
    app.router.add_post("/api/discord/dm-user", dm_user)
    return app
