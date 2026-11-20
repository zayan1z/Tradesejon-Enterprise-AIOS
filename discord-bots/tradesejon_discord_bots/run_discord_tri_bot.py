"""Single-instance supervisor running Trader, PR, and CEO Discord bots + 127.0.0.1 bridge."""

from __future__ import annotations

import asyncio
import io
import logging
import socket
import sys
from pathlib import Path

from aiohttp import web

# Support running as `python run_discord_tri_bot.py` from any working directory
PKG_PARENT = str(Path(__file__).resolve().parent.parent)
if PKG_PARENT not in sys.path:
    sys.path.insert(0, PKG_PARENT)

import discord  # noqa: E402
from tradesejon_discord_bots.bot_ceo import CEOBotClient  # noqa: E402
from tradesejon_discord_bots.bot_pr import PRDeskBotClient, PRTicketActionView  # noqa: E402
from tradesejon_discord_bots.bot_trader import TraderBotClient  # noqa: E402
from tradesejon_discord_bots.bridge_server import create_bridge_app  # noqa: E402
from tradesejon_discord_bots.config import DiscordConfig, load_config  # noqa: E402
from tradesejon_discord_bots.security import (  # noqa: E402
    is_channel_private_from_everyone,
    redact_secrets,
    safe_allowed_mentions,
    safe_discord_text,
    split_discord_text,
)
from tradesejon_discord_bots.shared_services import SharedServices, setup_secure_logging  # noqa: E402

logger = logging.getLogger("tradesejon.discord.runner")
LOCK_PORT = 45680


def acquire_singleton_socket(port: int = LOCK_PORT) -> socket.socket | None:
    """Bind exclusive loopback socket to prevent duplicate Discord bot runners."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        sock.listen(1)
        return sock
    except OSError:
        sock.close()
        return None


async def run_bot_with_backoff(name: str, client: discord.Client, token: str, cfg: DiscordConfig) -> None:
    """Keep a Discord Gateway bot alive cleanly with bounded exponential backoff."""
    delay = 5
    while True:
        try:
            logger.info("Starting %s Discord Gateway client...", name)
            await client.start(token)
        except asyncio.CancelledError:
            await client.close()
            raise
        except Exception as exc:
            logger.error("%s exited with error: %s", name, redact_secrets(str(exc), cfg.known_secrets))
        await asyncio.sleep(delay)
        delay = min(60, delay * 2)


async def orchestrate(cfg: DiscordConfig) -> None:
    """Boot all Discord bots and loopback bridge concurrently."""
    services = SharedServices(cfg)
    trader_bot = TraderBotClient(cfg, services) if cfg.trader_token else None
    pr_bot = None
    ceo_bot = None

    async def broadcast_handler(text: str, file_bytes: bytes | None, filename: str) -> int:
        client = trader_bot or ceo_bot
        if client is None:
            return 0
        users, channel_ids = await services.list_discord_broadcast_targets()
        sent = 0
        safe_txt = safe_discord_text(text)

        async def _dispatch(target) -> bool:
            if file_bytes and filename:
                await target.send(
                    safe_txt,
                    file=discord.File(io.BytesIO(file_bytes), filename=filename),
                    allowed_mentions=safe_allowed_mentions(),
                )
            else:
                await target.send(safe_txt, allowed_mentions=safe_allowed_mentions())
            return True

        # Broadcast to allowlisted public/trader channels first, then known Discord DMs
        sent_channels = set()
        for cid_str in channel_ids:
            try:
                ch = client.get_channel(int(cid_str)) or await client.fetch_channel(int(cid_str))
                if ch and str(ch.id) not in sent_channels:
                    await _dispatch(ch)
                    sent_channels.add(str(ch.id))
                    sent += 1
            except Exception:
                pass
        for uid_str in users:
            try:
                u = client.get_user(int(uid_str)) or await client.fetch_user(int(uid_str))
                if u and await _dispatch(u):
                    sent += 1
                    await asyncio.sleep(0.25)
            except Exception:
                pass
        return sent

    async def deliver_zoom_to_user(
        discord_user_id: str,
        full_name: str = "Tradesejon VIP",
        unique_zoom_url: str = "",
    ) -> bool:
        if trader_bot and discord_user_id:
            return await trader_bot.deliver_zoom_to_user(
                discord_user_id,
                full_name,
                unique_zoom_url=unique_zoom_url,
            )
        return False

    async def deliver_user_dm_from_admin(
        discord_user_id: str,
        text: str,
        raw_files: list[tuple[bytes, str]] | None = None,
    ) -> bool:
        uid = str(discord_user_id or "").strip()
        if not uid.isdigit():
            return False
        chunks = split_discord_text(text, max_len=1900) if (text or "").strip() else [""]
        for bot_client in (trader_bot, ceo_bot):
            if not bot_client:
                continue
            try:
                user = bot_client.get_user(int(uid)) or await bot_client.fetch_user(int(uid))
                if user:
                    for idx, ch_txt in enumerate(chunks):
                        d_files = (
                            [discord.File(io.BytesIO(b), filename=fn) for b, fn in (raw_files or [])]
                            if idx == len(chunks) - 1
                            else []
                        )
                        await user.send(
                            content=ch_txt or None,
                            files=d_files,
                            allowed_mentions=safe_allowed_mentions(),
                        )
                    return True
            except Exception:
                continue
        return False

    ceo_bot = (
        CEOBotClient(
            cfg,
            services,
            broadcast_handler,
            deliver_zoom_fn=deliver_zoom_to_user,
            deliver_user_dm_fn=deliver_user_dm_from_admin,
        )
        if cfg.ceo_token
        else None
    )

    async def deliver_to_trader(ticket_code: str, text: str, file_bytes: bytes | None = None, filename: str = "") -> bool:
        if trader_bot:
            return await trader_bot.deliver_to_trader(ticket_code, text, file_bytes, filename)
        return False

    if cfg.pr_token:
        pr_bot = PRDeskBotClient(cfg, services, deliver_to_trader)

    async def notify_pr(
        embed: discord.Embed,
        view: PRTicketActionView | None = None,
        file: discord.File | None = None,
        files: list[discord.File] | None = None,
    ) -> None:
        client = pr_bot or trader_bot
        if client:
            all_files: list[discord.File] = list(files or [])
            if file:
                all_files.append(file)
            if trader_bot and client is trader_bot:
                await trader_bot.notify_pr_privately(embed, view, files=all_files)
                return
            for ch_id_str in cfg.pr_channel_ids:
                try:
                    ch = client.get_channel(int(ch_id_str)) or await client.fetch_channel(int(ch_id_str))
                    if ch and is_channel_private_from_everyone(ch, getattr(ch, "guild", None)):
                        await ch.send(embed=embed, view=view, files=all_files or None, allowed_mentions=safe_allowed_mentions())
                        return
                except Exception:
                    pass
            for uid_str in cfg.ceo_user_ids:
                try:
                    u = client.get_user(int(uid_str)) or await client.fetch_user(int(uid_str))
                    if u:
                        await u.send(embed=embed, view=view, files=all_files or None, allowed_mentions=safe_allowed_mentions())
                        return
                except Exception:
                    pass

    async def notify_ceo(message: str) -> bool:
        if ceo_bot and await ceo_bot.notify_ceo(message):
            return True
        if trader_bot:
            for uid_str in cfg.ceo_user_ids:
                try:
                    u = trader_bot.get_user(int(uid_str)) or await trader_bot.fetch_user(int(uid_str))
                    if u:
                        await u.send(safe_discord_text(message), allowed_mentions=safe_allowed_mentions())
                        return True
                except Exception:
                    pass
        return False

    def bot_status() -> dict[str, dict[str, object]]:
        out = {}
        for key, cl in (("trader", trader_bot), ("pr", pr_bot), ("ceo", ceo_bot)):
            if cl is None:
                out[key] = {"configured": False, "ready": False, "fallback": "trader" if key == "pr" and trader_bot else None}
            else:
                out[key] = {
                    "configured": True,
                    "ready": cl.is_ready(),
                    "user": str(cl.user) if cl.user else "",
                    "user_id": str(getattr(cl.user, "id", "")) if cl.user else "",
                    "guilds": len(cl.guilds),
                }
        return out

    async def handle_deliver_zoom(payload: dict) -> dict:
        email_addr = str(payload.get("email") or "").strip().lower()
        phone = str(payload.get("phone") or "").strip()
        uid = str(payload.get("discord_user_id") or "").strip()
        payment_id = str(payload.get("whop_payment_id") or payload.get("payment_id") or "").strip()
        checkout_ref = str(payload.get("checkout_ref") or "").strip()
        tier = str(payload.get("tier") or "").strip().lower()
        plan_id = str(payload.get("plan_id") or payload.get("variant_id") or "").strip()
        try:
            amount_usd = float(payload.get("amount_usd") or payload.get("amount") or 0)
        except Exception:
            amount_usd = 0.0
        res = await services.fulfill_whop_payment_for_discord(
            email_addr=email_addr,
            phone=phone,
            discord_user_id=uid,
            whop_payment_id=payment_id,
            checkout_ref=checkout_ref,
            tier=tier,
            plan_id=plan_id,
            amount_usd=amount_usd,
        )
        is_free_otp = bool(res.get("is_free_otp") or res.get("tier") == "free_otp_test")
        delivered = False
        if is_free_otp:
            target_uid = str(res.get("discord_user_id") or "")
            if trader_bot and target_uid.isdigit():
                try:
                    u = trader_bot.get_user(int(target_uid)) or await trader_bot.fetch_user(int(target_uid))
                    if u:
                        await u.send(
                            f"🎉 تم تأكيد كود **Whop OTP** على بريدك بنجاح!\n"
                            f"✅ تم إدخالك فوراً إلى **Prospects Bot (<#{cfg.prospects_channel_id}>)** ومنحك **Prospect Role**.",
                            allowed_mentions=safe_allowed_mentions(),
                        )
                        delivered = True
                except Exception:
                    pass
            if ceo_bot:
                await ceo_bot.notify_ceo(
                    f"🔐 **تم تأكيد Whop Email OTP المجاني وإدخال العميل إلى #prospects فوراً:**\n"
                    f"• الاسم: `{res.get('full_name')}` | البريد: `{res.get('email') or email_addr or '-'}`\n"
                    f"• Discord ID: `{res.get('discord_user_id') or '-'}` | Roles Added: `{res.get('roles_added')}`"
                )
            return {**res, "zoom_delivered": False, "prospect_unlocked": True, "dm_notified": delivered, "email_delivered": False, "telegram_delivered": False}

        if res.get("discord_user_id"):
            delivered = await deliver_zoom_to_user(
                res["discord_user_id"],
                res.get("full_name", "Tradesejon VIP"),
                unique_zoom_url=res.get("unique_zoom_url", ""),
            )
        email_delivered = False
        tg_delivered = False
        target_email = (res.get("email") or email_addr or "").strip().lower()
        live_url = str(res.get("unique_zoom_url") or "")
        tier_title = str(res.get("tier_title") or "Tradesejon Workshop")
        if target_email and live_url and cfg.gmail_app_password:
            em = await services.send_email_secure(
                to_email=target_email,
                subject=f"Tradesejon VIP — رابط Workshop الشخصي ({tier_title})",
                body_text=(
                    f"تم تأكيد اشتراكك بنجاح ({tier_title}) وترقية حالتك إلى {res.get('status')}.\n"
                    f"تم فتح قناة Workshop"
                    + (" + قناة V tradesejon" if res.get("tier") == "viproom_75" else "")
                    + f" ومرفق رابط البث المباشر الخاص بحسابك فقط:\n{live_url}"
                ),
                recipient_name=res.get("full_name", "Tradesejon VIP"),
                include_qr=True,
                include_zoom=True,
                unique_zoom_url=live_url,
            )
            email_delivered = bool(em.get("ok"))
        if live_url:
            sess = await services.get_live_session_info()
            tok_tg = cfg.telegram_trading_token or cfg.telegram_ceo_token
            targets_tg = await services.find_telegram_chats_for_user(email_addr=target_email, phone_e164=phone)
            if cfg.telegram_ceo_chat_id and cfg.telegram_ceo_chat_id not in targets_tg:
                targets_tg.append(cfg.telegram_ceo_chat_id)
            for cid in targets_tg:
                if await services.send_telegram_live_link(
                    chat_id=cid,
                    token=tok_tg,
                    full_name=res.get("full_name", "Tradesejon VIP"),
                    live_url=live_url,
                    whop_url=sess["whop_url"],
                    session_name=sess["session_name"],
                    session_time=sess["session_time"],
                    include_qr=True,
                ):
                    tg_delivered = True
        if ceo_bot:
            await ceo_bot.notify_ceo(
                f"🎙️ **تم تأكيد دفع Whop وترقية العميل ({res.get('status')}) تلقائياً:**\n"
                f"• الاسم: `{res.get('full_name')}` | الباقة: `{res.get('tier')}` (`${res.get('amount_usd')}`)\n"
                f"• البريد: `{target_email or '-'}` (Email: `{'OK' if email_delivered else 'SKIP'}` | Telegram: `{'OK' if tg_delivered else 'SKIP'}`)\n"
                f"• Discord ID: `{res.get('discord_user_id') or '-'}` | Roles Added: `{res.get('roles_added')}`\n"
                f"• تسليم الرابط بالخاص: `{'DELIVERED ✅' if delivered else 'PENDING/DM_CLOSED ⚠️'}`"
            )
        return {**res, "zoom_delivered": delivered, "email_delivered": email_delivered, "telegram_delivered": tg_delivered}

    app = create_bridge_app(
        cfg,
        services,
        status_fn=bot_status,
        notify_ceo_fn=notify_ceo,
        notify_pr_fn=notify_pr,
        reply_trader_fn=deliver_to_trader,
        broadcast_fn=broadcast_handler,
        deliver_zoom_fn=handle_deliver_zoom,
        dm_user_fn=deliver_user_dm_from_admin,
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, cfg.bridge_host, cfg.bridge_port)
    await site.start()
    logger.info("Discord Loopback Bridge listening strictly on http://%s:%d", cfg.bridge_host, cfg.bridge_port)

    async def whop_reconcile_loop() -> None:
        """Background Whop v1/v5 poll promoting OTP-verified users -> Prospects Bot and paid users -> Workshop every 10s."""
        while True:
            try:
                await asyncio.sleep(10)
                res = await services.reconcile_recent_whop_payments()
                if res.get("fulfilled"):
                    logger.info("Whop reconciler auto-promoted %s OTP/paid user(s): %s", res["fulfilled"], res.get("users"))
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Whop reconciler cycle error: %s", redact_secrets(str(exc), cfg.known_secrets))

    tasks = []
    if trader_bot and cfg.trader_token:
        tasks.append(asyncio.create_task(run_bot_with_backoff("TraderSupportBot", trader_bot, cfg.trader_token, cfg)))
    if pr_bot and cfg.pr_token:
        tasks.append(asyncio.create_task(run_bot_with_backoff("PRDeskBot", pr_bot, cfg.pr_token, cfg)))
    if ceo_bot and cfg.ceo_token:
        tasks.append(asyncio.create_task(run_bot_with_backoff("CEOBot", ceo_bot, cfg.ceo_token, cfg)))
    for p_name, p_tok in (
        ("MarhabanBot", cfg.marhaban_token),
        ("ProspectBot", cfg.prospect_token),
        ("WorkshopBot", cfg.workshop_token),
        ("VipRoomBot", cfg.viproom_token),
    ):
        if p_tok and p_tok not in (cfg.trader_token, cfg.pr_token, cfg.ceo_token):
            tasks.append(asyncio.create_task(run_bot_with_backoff(p_name, TraderBotClient(cfg, services), p_tok, cfg)))
    tasks.append(asyncio.create_task(whop_reconcile_loop()))

    try:
        if tasks:
            await asyncio.gather(*tasks)
        else:
            logger.error("No Discord bot tokens configured in .env; holding bridge open cleanly.")
            await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        await services.close()


def main() -> int:
    cfg = load_config()
    setup_secure_logging(cfg)
    lock_sock = acquire_singleton_socket(LOCK_PORT)
    if lock_sock is None:
        logger.info("Another Discord Tri-Bot runner holds lock port %d; exiting cleanly.", LOCK_PORT)
        return 0
    try:
        asyncio.run(orchestrate(cfg))
    except KeyboardInterrupt:
        pass
    finally:
        lock_sock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
