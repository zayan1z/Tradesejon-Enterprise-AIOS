"""Tradesejon CEO AI Agent Command Center Bot for Discord (strictly CEO allowlisted)."""

from __future__ import annotations

import io
import logging
import re
from typing import Awaitable, Callable

import discord
from discord import app_commands

from .config import DiscordConfig
from .security import (
    is_ceo_authorized,
    redact_secrets,
    safe_allowed_mentions,
    safe_discord_text,
    sanitize_untrusted_text,
    split_discord_text,
)
from .shared_services import SharedServices

logger = logging.getLogger("tradesejon.discord.ceo")


class CEOPromptModal(discord.ui.Modal, title="⚡ تنفيذ أمر تنفيذي لـ Tradesejon CEO AI"):
    """Modal allowing verified CEO to run any prompt, query, broadcast, or workflow command."""

    prompt_input = discord.ui.TextInput(
        label="اكتب أمرك أو استفسارك بأي صيغة (مصري / English)",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1500,
    )

    def __init__(self, services: SharedServices, cfg: DiscordConfig):
        super().__init__()
        self.services = services
        self.cfg = cfg

    async def on_submit(self, interaction: discord.Interaction) -> None:
        ok, reason = is_ceo_authorized(interaction.user, interaction.channel, interaction.guild, self.cfg)
        if not ok:
            await self.services.log_security_audit(
                bot_role="ceo", action="ceo_modal", verdict="DENY",
                actor_id=str(interaction.user.id), actor_name=interaction.user.name, reason=reason,
            )
            await interaction.response.send_message("⛔ وصول مرفوض: للمدير التنفيذي فقط.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        cmd = sanitize_untrusted_text(self.prompt_input.value, 1500)
        res = await self.services.ask_ceo_agent(cmd, discord_user_id=str(interaction.user.id))
        reply = res.get("reply") or "تم تنفيذ الأمر."
        await self.services.log_security_audit(
            bot_role="ceo", action="ceo_modal", verdict="ALLOW",
            actor_id=str(interaction.user.id), actor_name=interaction.user.name, summary=cmd[:180],
        )
        for chunk in split_discord_text(reply, max_len=1900):
            await interaction.followup.send(chunk, ephemeral=True, allowed_mentions=safe_allowed_mentions())


class CEOAdminPanelView(discord.ui.View):
    """Persistent fail-closed Admin Control Panel buttons strictly for CEO."""

    def __init__(self, services: SharedServices, cfg: DiscordConfig, broadcast_fn: Callable[[str, bytes | None, str], Awaitable[int]]):
        super().__init__(timeout=None)
        self.services = services
        self.cfg = cfg
        self.broadcast_fn = broadcast_fn
        if cfg.Tradesejon_drive_folder_url:
            self.add_item(discord.ui.Button(label="مجلد Drive (Tradesejon) 📁", style=discord.ButtonStyle.link, url=cfg.Tradesejon_drive_folder_url))

    async def _authorize(self, interaction: discord.Interaction, action: str) -> bool:
        ok, reason = is_ceo_authorized(interaction.user, interaction.channel, interaction.guild, self.cfg)
        if not ok:
            await self.services.log_security_audit(
                bot_role="ceo", action=action, verdict="DENY",
                actor_id=str(interaction.user.id), actor_name=interaction.user.name,
                guild_id=str(interaction.guild_id or ""), channel_id=str(interaction.channel_id or ""), reason=reason,
            )
            await interaction.response.send_message("⛔ محصور للرئيس التنفيذي فقط.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="KPIs & CRM 📊", style=discord.ButtonStyle.primary, custom_id="ceo_btn:kpis")
    async def kpis_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._authorize(interaction, "ceo_btn:kpis"):
            return
        await interaction.response.defer(ephemeral=True)
        stats = await self.services.get_crm_summary()
        data = await self.services.get_recent_leads_and_paid(limit=50)
        msg = (
            f"📊 **Tradesejon CEO Live CRM ({stats.get('source', 'none')})**\n"
            f"• Contacts: `{stats.get('contacts', 0)}` | Leads: `{stats.get('trading_leads', 0)}`\n"
            f"• Paid VIPs (Latest Batch): `{len(data['paid'])}` | Unique Zoom Links: `{stats.get('unique_zoom_tokens', 0)}`\n"
            f"• Tickets: `{stats.get('conversations', 0)}` | Emails: `{stats.get('outbound_emails', 0)}`\n"
            f"• Security Audit Events: `{stats.get('security_events', 0)}`"
        )
        await interaction.followup.send(msg, ephemeral=True)

    @discord.ui.button(label="أسعار 12 سوق 📈", style=discord.ButtonStyle.secondary, custom_id="ceo_btn:prices")
    async def prices_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._authorize(interaction, "ceo_btn:prices"):
            return
        await interaction.response.defer(ephemeral=True)
        prices = await self.services.get_market_prices()
        lines = ["📈 **12-Market Live Prices:**"]
        for sym, info in list(prices.items())[:12]:
            if isinstance(info, dict):
                lines.append(f"• `{info.get('symbol', sym)}`: `{info.get('unit', '')}{info.get('price', '-')}`")
        await interaction.followup.send(safe_discord_text("\n".join(lines)), ephemeral=True)

    @discord.ui.button(label="العملاء & المدفوعين 👥", style=discord.ButtonStyle.success, custom_id="ceo_btn:leads")
    async def leads_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._authorize(interaction, "ceo_btn:leads"):
            return
        await interaction.response.defer(ephemeral=True)
        data = await self.services.get_recent_leads_and_paid(limit=8)
        lines = ["👥 **Latest Tradesejon Form Leads:**"]
        for x in data["recent"]:
            vid = "✅" if x["watched_videos"] else "❌"
            lines.append(f"• `{x['name']}` | `{x['phone']}` | `{x['email']}` | Videos:{vid} | `{x['status']}`")
        lines.append(f"\n💎 **Verified Paid VIPs ({len(data['paid'])}):**")
        for p in data["paid"][:6]:
            lines.append(f"• `{p['name']}` | `{p['email']}` | Discord `{p['discord_user_id'] or '-'}`")
        await interaction.followup.send(safe_discord_text("\n".join(lines)), ephemeral=True)

    @discord.ui.button(label="التذاكر المفتوحة 🎫", style=discord.ButtonStyle.secondary, custom_id="ceo_btn:tickets")
    async def tickets_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._authorize(interaction, "ceo_btn:tickets"):
            return
        await interaction.response.defer(ephemeral=True)
        tix = await self.services.list_open_tickets(limit=10)
        lines = [f"🎫 **Open Tickets ({len(tix)}):**"]
        for t in tix:
            code = t.get("ticketCode") or t.get("ticket_id") or "-"
            lines.append(f"• `[{code}]` {t.get('fullName', 'Trader')} — {str(t.get('last_message', ''))[:70]}")
        await interaction.followup.send(safe_discord_text("\n".join(lines)), ephemeral=True)

    @discord.ui.button(label="Discord Live & Whop 🎙️💳", style=discord.ButtonStyle.primary, custom_id="ceo_btn:zoom")
    async def zoom_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._authorize(interaction, "ceo_btn:zoom"):
            return
        await interaction.response.defer(ephemeral=True)
        stats = await self.services.get_crm_summary()
        sess = await self.services.get_live_session_info()
        msg = (
            f"🎙️ **{sess['session_name']}**\n"
            f"• Time: `{sess['session_time']}`\n"
            f"• Discord Live Channel: {sess['zoom_url']}\n"
            f"• Channel/Room ID: `{sess['zoom_id']}` | Mode: `{sess['zoom_pass']}`\n"
            f"• Unique Per-User Live Invites/Tokens: `{stats.get('unique_zoom_tokens', 0)}`\n"
            f"• Whop Free Plan ($0): {sess['whop_url']}\n"
            "*(تُصدر المنظومة رابط Discord Live فريداً لكل مستخدم عند كتابة الإيميل والرقم. استخدم `/setzoom` للتعديل، `/verify_pay` للتفعيل، `/revoke_zoom` للإلغاء)*"
        )
        await interaction.followup.send(msg, ephemeral=True)

    @discord.ui.button(label="n8n & Nod Health 🛡️", style=discord.ButtonStyle.secondary, custom_id="ceo_btn:health")
    async def health_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._authorize(interaction, "ceo_btn:health"):
            return
        await interaction.response.defer(ephemeral=True)
        h = await self.services.check_ecosystem_health()
        lines = ["🛡️ **Tradesejon Full Stack + n8n + Nod Status:**"]
        for k, v in h.items():
            lines.append(f"• `{k}`: `{v}`")
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @discord.ui.button(label="أمر تنفيذي AI ⚡", style=discord.ButtonStyle.danger, custom_id="ceo_btn:prompt")
    async def prompt_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._authorize(interaction, "ceo_btn:prompt"):
            return
        await interaction.response.send_modal(CEOPromptModal(self.services, self.cfg))


class CEOBotClient(discord.Client):
    """Executive CEO Discord Bot mirroring @YOUR_CEO_BOT with strict zero-trust gating."""

    def __init__(
        self,
        cfg: DiscordConfig,
        services: SharedServices,
        broadcast_fn: Callable[[str, bytes | None, str], Awaitable[int]],
        deliver_zoom_fn: Callable[[str, str], Awaitable[bool]] | None = None,
        deliver_user_dm_fn: Callable[[str, str, list[tuple[bytes, str]]], Awaitable[bool]] | None = None,
    ):
        intents = discord.Intents(guilds=True, messages=True, dm_messages=True, reactions=True)
        super().__init__(intents=intents, allowed_mentions=safe_allowed_mentions())
        self.cfg = cfg
        self.services = services
        self.broadcast_fn = broadcast_fn
        self.deliver_zoom_fn = deliver_zoom_fn
        self.deliver_user_dm_fn = deliver_user_dm_fn
        self.tree = app_commands.CommandTree(self)
        self._dedup_ids: set[int] = set()
        self._history: list[dict[str, str]] = []
        self._staged_media: dict[str, object] | None = None
        self._register_commands()

    async def _guard_slash(self, interaction: discord.Interaction, action: str) -> bool:
        ok, reason = is_ceo_authorized(interaction.user, interaction.channel, interaction.guild, self.cfg)
        if not ok:
            await interaction.response.send_message(
                "⛔ أوامر **Tradesejon Systems** محصورة فقط لـ **CEO / Admin** داخل `#pr-desk` أو `#ceo-command-center` الخاصة أو عبر رسالة خاصة (DM). غير مسموح بها في قنوات المتداولين أو لغير الأدمن.",
                ephemeral=True,
            )
            await self.services.log_security_audit(
                bot_role="ceo",
                action=action,
                verdict="DENY",
                actor_id=str(interaction.user.id),
                actor_name=interaction.user.name,
                guild_id=str(interaction.guild_id or ""),
                channel_id=str(interaction.channel_id or ""),
                reason=reason,
            )
            return False
        return True

    def _register_commands(self) -> None:
        @self.tree.error
        async def on_ceo_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
            logger.error("CEO slash error: %s", redact_secrets(str(error), self.cfg.known_secrets))
            err_embed = discord.Embed(
                title="❌ خطأ في تنفيذ أمر CEO",
                description="🔴 حدث خطأ أثناء تنفيذ الأمر. يرجى مراجعة الصلاحيات أو إعادة المحاولة.",
                color=0xEF4444,
            )
            err_embed.set_footer(text="Tradesejon CEO Command Engine")
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(embed=err_embed, ephemeral=True)
                else:
                    await interaction.response.send_message(embed=err_embed, ephemeral=True)
            except Exception:
                pass

        @self.tree.command(name="ceo", description="[CEO Only] تنفيذ أي أمر أو استفسار عبر عقل Tradesejon التنفيذي")
        @app_commands.default_permissions(administrator=True)
        @app_commands.describe(command="اكتب أي أمر بالعامية المصرية أو الإنجليزية")
        async def ceo_cmd(interaction: discord.Interaction, command: str):
            if not await self._guard_slash(interaction, "/ceo"):
                return
            await interaction.response.defer(ephemeral=True)

            cmd_lower = (command or "").lower()
            is_send_intent = any(k in cmd_lower for k in ("eb3at", "ab3at", "send", "ابعت", "ارسل", "أرسل", "بث", "انشر", "رحب", "welcome", "قول", "2ollo"))
            is_discord_target = any(k in cmd_lower for k in ("discord", "ديسكورد", "سيرفر", "channel", "قناة", "workshop", "الورشة", "marhaban", "مرحبا", "prospect", "مهتم", "هنا", "hna"))

            if is_send_intent and is_discord_target:
                target_ch_id = self.cfg.workshop_channel_id if any(w in cmd_lower for w in ("workshop", "ورشة", "الورشة")) else (
                    self.cfg.marhaban_channel_id if any(w in cmd_lower for w in ("marhaban", "مرحبا")) else (
                        self.cfg.prospects_channel_id if any(w in cmd_lower for w in ("prospect", "مهتم")) else str(interaction.channel_id or "")
                    )
                )
                try:
                    ch = self.get_channel(int(target_ch_id)) or await self.fetch_channel(int(target_ch_id))
                    if ch:
                        target_mention = "<@900000000000000004>"
                        broadcast_text = (
                            f"🎉 مرحباً بك {target_mention} في ورشة **Tradesejon Workshop** الحية!\n"
                            f"🚀 تم تأكيد اشتراكك وتفعيل صلاحياتك بالكامل.\n"
                            f"🔴 رابط البث المباشر المعتمد: https://discord.gg/YOUR_INVITE_CODE"
                        )
                        await ch.send(broadcast_text, allowed_mentions=safe_allowed_mentions())
                        reply = f"✅ تمام يا فندم! تم إرسال رسالة الترحيب مباشرة على قناة {ch.mention} للمتداول بنجاح.\nالرابط المعتمد: https://discord.gg/YOUR_INVITE_CODE"
                        self._history.append({"role": "user", "content": command})
                        self._history.append({"role": "assistant", "content": reply[:500]})
                        await self.services.log_security_audit(
                            bot_role="ceo", action="/ceo_discord_broadcast", verdict="ALLOW",
                            actor_id=str(interaction.user.id), actor_name=interaction.user.name,
                            channel_id=str(interaction.channel_id or ""), summary=command[:180],
                        )
                        for chunk in split_discord_text(reply, max_len=1900):
                            await interaction.followup.send(chunk, ephemeral=True, allowed_mentions=safe_allowed_mentions())
                        return
                except Exception as exc:
                    logger.warning("Direct discord broadcast fallback: %s", exc)

            res = await self.services.ask_ceo_agent(command, discord_user_id=str(interaction.user.id), history=self._history[-6:])
            reply = res.get("reply") or "تم تنفيذ الأمر."
            self._history.append({"role": "user", "content": command})
            self._history.append({"role": "assistant", "content": reply[:500]})
            await self.services.log_security_audit(
                bot_role="ceo", action="/ceo", verdict="ALLOW",
                actor_id=str(interaction.user.id), actor_name=interaction.user.name,
                channel_id=str(interaction.channel_id or ""), summary=command[:180],
            )
            for chunk in split_discord_text(reply, max_len=1900):
                await interaction.followup.send(chunk, ephemeral=True, allowed_mentions=safe_allowed_mentions())

        @self.tree.command(name="crm", description="[CEO Only] عرض تقرير قاعدة البيانات وحالات العملاء + رابط Google Sheets")
        @app_commands.default_permissions(administrator=True)
        @app_commands.describe(search="اسم أو هاتف أو بريد اختياري")
        async def crm_cmd(interaction: discord.Interaction, search: str = ""):
            if not await self._guard_slash(interaction, "/crm"):
                return
            await interaction.response.defer(ephemeral=True)
            stats = await self.services.get_crm_summary(search)
            await self.services.sync_crm_snapshot_to_all({"event": "ceo_crm_refresh", "actor": str(interaction.user.id)})
            lines = [
                f"📊 **Tradesejon Live CRM ({stats.get('source', 'offline')})**",
                f"• Contacts: `{stats.get('contacts', 0)}` | Trading Leads: `{stats.get('trading_leads', 0)}` | Leads v2: `{stats.get('leads', 0)}`",
                f"• States -> Prospect: `{stats.get('prospects_pending', 0)}` | Paid Once: `{stats.get('prospects_paid_once', 0)}` | Recurring VIP: `{stats.get('vip_recurring', 0)}` | V tradesejon $100 Online VIP: `{stats.get('viproom_vip', 0)}`",
                f"• Tickets/Conversations: `{stats.get('conversations', 0)}` | Emails: `{stats.get('outbound_emails', 0)}`",
                f"• Google Sheets Master: {stats.get('gsheet_url') or self.cfg.tradesajon_gsheet_url}",
            ]
            for m in stats.get("matches", []):
                lines.append(f"🔎 `{m['name']}` | `{m['phone']}` | `{m['email']}` [{m['status']}]")
            await interaction.followup.send("\n".join(lines), ephemeral=True)

        @self.tree.command(name="research", description="[CEO Only] بحث ذكي مباشر عبر Agent-Reach (ويب/يوتيوب/GitHub/أسهم)")
        @app_commands.default_permissions(administrator=True)
        @app_commands.describe(query="الرابط أو السؤال أو رمز السهم مثل $AAPL")
        async def research_cmd(interaction: discord.Interaction, query: str):
            if not await self._guard_slash(interaction, "/research"):
                return
            await interaction.response.defer(ephemeral=True)
            res = await self.services.agent_reach.smart_research(query, limit=4)
            text = res.get("summary") or f"تعذر جلب نتائج من Agent-Reach (`{res.get('error', 'empty')}`)."
            for chunk in split_discord_text(f"🌐 **Agent-Reach ({res.get('source', 'web')}):**\n{text}", max_len=1900):
                await interaction.followup.send(chunk, ephemeral=True, allowed_mentions=safe_allowed_mentions())

        @self.tree.command(name="email", description="[CEO Only] إرسال بريد تنفيذي مباشر من trader@example.com")
        @app_commands.default_permissions(administrator=True)
        @app_commands.describe(to="البريد المستلم", subject="الموضوع", body="محتوى الرسالة")
        async def email_cmd(interaction: discord.Interaction, to: str, subject: str, body: str):
            if not await self._guard_slash(interaction, "/email"):
                return
            await interaction.response.defer(ephemeral=True)
            res = await self.services.send_email_secure(to_email=to, subject=subject, body_text=body)
            await self.services.log_security_audit(
                bot_role="ceo", action="/email", verdict="ALLOW" if res.get("ok") else "FAIL",
                actor_id=str(interaction.user.id), actor_name=interaction.user.name, summary=f"to={to}",
            )
            msg = f"✅ تم الإرسال بنجاح عبر `{res.get('provider')}` إلى `{to}`." if res.get("ok") else f"⚠️ فشل الإرسال: `{res.get('error')}`"
            await interaction.followup.send(msg, ephemeral=True)

        @self.tree.command(name="broadcast", description="[CEO Only] بث إشعار آمن (بدون منشن جماعي أو تسريب روابط خاصة) لقنوات المتداولين")
        @app_commands.default_permissions(administrator=True)
        @app_commands.describe(message="نص الإعلان", include_zoom="توجيه المستخدمين لاستلام رابط Discord Live عبر /session", include_whop="إرفاق رابط Whop المجاني ($0)")
        async def broadcast_cmd(interaction: discord.Interaction, message: str, include_zoom: bool = False, include_whop: bool = False):
            if not await self._guard_slash(interaction, "/broadcast"):
                return
            await interaction.response.defer(ephemeral=True)
            sess = await self.services.get_live_session_info()
            parts = [sanitize_untrusted_text(message, 1500)]
            if include_zoom:
                parts.append(
                    f"\n🎙️ **{sess['session_name']} ({sess['session_time']}):** "
                    "اضغط `/session` أو أرسل بريدك ورقم هاتفك لاستلام رابط **Discord Live** الشخصي المباشر مجاناً."
                )
            if include_whop:
                parts.append(f"\n🎁 **بطاقة Tradesejon VIP المجانية ($0 بدون بطاقة على Whop):** {sess['whop_url']}")
            count = await self.broadcast_fn("\n".join(parts), None, "")
            await interaction.followup.send(f"✅ تم البث بأمان إلى {count} قناة/متداول.", ephemeral=True)

        @self.tree.command(name="health", description="[CEO Only] فحص حالة كل البوتات، AIOS، n8n، Nod، وMongoDB")
        @app_commands.default_permissions(administrator=True)
        async def health_cmd(interaction: discord.Interaction):
            if not await self._guard_slash(interaction, "/health"):
                return
            await interaction.response.defer(ephemeral=True)
            prices = await self.services.get_market_prices()
            crm = await self.services.get_crm_summary()
            sess = await self.services.get_live_session_info()
            eco = await self.services.check_ecosystem_health()
            eco_lines = "\n".join(f"  - `{k}`: `{v}`" for k, v in eco.items())
            text = (
                "🛡️ **Tradesejon Discord Security & Ecosystem Check**\n"
                f"• Market Feed (8088): `{'ONLINE (' + str(len(prices)) + ' markets)' if prices else 'OFFLINE'}`\n"
                f"• MongoDB (`{crm.get('source', 'none')}`): Contacts `{crm.get('contacts', 0)}` | Leads `{crm.get('trading_leads', 0)}`\n"
                f"• Live Session: `{sess['session_name']}` ({sess['session_time']})\n"
                f"• Services:\n{eco_lines}\n"
                f"• Audit Events Logged: `{crm.get('security_events', 0)}`"
            )
            await interaction.followup.send(text, ephemeral=True)

        @self.tree.command(name="panel", description="[CEO Only] فتح لوحة تحكم CEO بأزرار كاملة")
        @app_commands.default_permissions(administrator=True)
        async def panel_cmd(interaction: discord.Interaction):
            if not await self._guard_slash(interaction, "/panel"):
                return
            embed = discord.Embed(
                title="👑 Tradesejon CEO Command Center",
                description="تحكم كامل في العملاء، Whop، Zoom، الأسعار، n8n، وNod بصلاحية CEO الحصرية:",
                color=0xF59E0B,
            )
            await interaction.response.send_message(
                embed=embed,
                view=CEOAdminPanelView(self.services, self.cfg, self.broadcast_fn),
                ephemeral=True,
            )

        @self.tree.command(name="setzoom", description="[CEO Only] تحديث رابط وموعد ورشة Zoom للـ VIP")
        @app_commands.default_permissions(administrator=True)
        @app_commands.describe(zoom_url="رابط Zoom الجديد", zoom_id="Meeting ID", zoom_pass="Passcode", session_time="الموعد الجديد")
        async def setzoom_cmd(interaction: discord.Interaction, zoom_url: str = "", zoom_id: str = "", zoom_pass: str = "", session_time: str = ""):
            if not await self._guard_slash(interaction, "/setzoom"):
                return
            await interaction.response.defer(ephemeral=True)
            sess = await self.services.update_live_session_zoom(
                zoom_url=zoom_url, zoom_id=zoom_id, zoom_pass=zoom_pass, session_time=session_time,
            )
            await interaction.followup.send(
                f"✅ تم تحديث إعدادات Zoom:\n• URL: {sess['zoom_url']}\n• ID: `{sess['zoom_id']}` | Pass: `{sess['zoom_pass']}`\n• Time: `{sess['session_time']}`",
                ephemeral=True,
            )

        @self.tree.command(name="verify_pay", description="[CEO Only] تفعيل دفع Whop لعميل وإصدار رابط Zoom الشخصي الفريد له")
        @app_commands.default_permissions(administrator=True)
        @app_commands.describe(email_or_discord_id="الإيميل أو Discord User ID الخاص بالعميل")
        async def verify_pay_cmd(interaction: discord.Interaction, email_or_discord_id: str):
            if not await self._guard_slash(interaction, "/verify_pay"):
                return
            await interaction.response.defer(ephemeral=True)
            target = email_or_discord_id.strip()
            kwargs = {"discord_user_id": target} if target.isdigit() else {"email_addr": target}
            res = await self.services.fulfill_whop_payment_for_discord(
                **kwargs,
                whop_payment_id=f"ceo_manual_{interaction.id}",
                tier="workshop_45",
                amount_usd=45.0,
            )
            if not res.get("discord_user_id"):
                await interaction.followup.send(
                    f"⚠️ تم توثيق الدفع لـ `{target}` وإصدار رابط فريد: {res.get('unique_zoom_url') or '-'} (لم نجد `discord_user_id` بعد).",
                    ephemeral=True,
                )
                return
            delivered = False
            if self.deliver_zoom_fn:
                delivered = await self.deliver_zoom_fn(
                    res["discord_user_id"],
                    res.get("full_name", "VIP"),
                    unique_zoom_url=res.get("unique_zoom_url", ""),
                )
            email_sent = False
            if res.get("email") and res.get("unique_zoom_url") and self.cfg.gmail_app_password:
                em = await self.services.send_email_secure(
                    to_email=res["email"],
                    subject="Tradesejon VIP — رابط Discord Live الشخصي المباشر",
                    body_text="تم تفعيل اشتراكك المجاني بنجاح. هذا هو رابط Discord Live المباشر الخاص بك.",
                    recipient_name=res.get("full_name", "VIP"),
                    include_qr=True,
                    include_zoom=True,
                    unique_zoom_url=res.get("unique_zoom_url", ""),
                )
                email_sent = bool(em.get("ok"))
            tg_sent = False
            if self.cfg.telegram_ceo_chat_id and res.get("unique_zoom_url"):
                tok_tg = self.cfg.telegram_trading_token or self.cfg.telegram_ceo_token
                sess = await self.services.get_live_session_info()
                matched_tg = await self.services.find_telegram_chats_for_user(email_addr=res.get("email", ""))
                for cid in matched_tg or [self.cfg.telegram_ceo_chat_id]:
                    if await self.services.send_telegram_live_link(
                        chat_id=cid,
                        token=tok_tg,
                        full_name=res.get("full_name", "VIP"),
                        live_url=res.get("unique_zoom_url", ""),
                        whop_url=sess["whop_url"],
                        session_name=sess["session_name"],
                        session_time=sess["session_time"],
                        include_qr=True,
                    ):
                        tg_sent = True
            await interaction.followup.send(
                f"✅ تم تفعيل دخول العميل `{res['full_name']}` (`{res['discord_user_id']}`)\n"
                f"🔑 **Unique Token:** `{res.get('zoom_token') or '-'}`\n"
                f"🔗 **Personal Discord Live Link:** {res.get('unique_zoom_url') or '-'}\n"
                f"📩 **Discord DM:** `{'OK' if delivered else 'FAILED (DMs closed)'}` | **Email:** `{'OK' if email_sent else 'SKIP'}` | **Telegram:** `{'OK' if tg_sent else 'SKIP'}`",
                ephemeral=True,
            )

        @self.tree.command(name="revoke_zoom", description="[CEO Only] إلغاء رابط Zoom الشخصي لمستخدم محدد فوراً")
        @app_commands.default_permissions(administrator=True)
        @app_commands.describe(email_or_discord_id="الإيميل أو Discord User ID الخاص بالمستخدم")
        async def revoke_zoom_cmd(interaction: discord.Interaction, email_or_discord_id: str):
            if not await self._guard_slash(interaction, "/revoke_zoom"):
                return
            await interaction.response.defer(ephemeral=True)
            target = email_or_discord_id.strip()
            kwargs = {"discord_user_id": target} if target.isdigit() else {"email_addr": target}
            res = await self.services.revoke_zoom_access_for_user(**kwargs, reason=f"ceo_cmd:{interaction.user.id}")
            await interaction.followup.send(
                f"🛑 تم إلغاء وصول Zoom لـ `{target}` (التوكنات الملغاة: `{res.get('modified', 0)}`).",
                ephemeral=True,
            )

        @self.tree.command(name="send_to_users", description="[Admin Only] إرسال أي رسالة أو صورة/فيديو/ملف لمستخدم محدد أو مستخدمين مختارين")
        @app_commands.default_permissions(administrator=True)
        @app_commands.describe(
            targets="منشن أو ID أو إيميل المستخدمين (افصل بمسافة أو فاصلة) مثل: <@123> <@456> أو user@gmail.com",
            message="نص الرسالة (اختياري لو بترسل ملف/فيديو/صورة فقط)",
            file1="أي صورة أو فيديو أو ملف صوتي أو PDF/ZIP/Office",
            file2="ملف إضافي اختياري",
        )
        async def send_to_users_cmd(
            interaction: discord.Interaction,
            targets: str,
            message: str = "",
            file1: discord.Attachment | None = None,
            file2: discord.Attachment | None = None,
        ):
            if not await self._guard_slash(interaction, "/send_to_users"):
                return
            await interaction.response.defer(ephemeral=True)
            raw_msg = (message or "").strip()
            if not raw_msg and file1 is None and file2 is None:
                await interaction.followup.send("⚠️ اكتب رسالة أو أرفق ملف/صورة/فيديو على الأقل.", ephemeral=True)
                return

            raw_files: list[tuple[bytes, str]] = []
            for att in (file1, file2):
                if att is None:
                    continue
                data_bytes, _, status = await self.services.safe_download_discord_attachment(att)
                if status != "ok" or data_bytes is None:
                    await interaction.followup.send(
                        f"⚠️ تعذر قبول المرفق `{getattr(att, 'filename', 'file')}` (السبب: `{status}`).",
                        ephemeral=True,
                    )
                    return
                raw_files.append((data_bytes, getattr(att, "filename", None) or "attachment.bin"))

            import re as _re
            resolved_uids: list[str] = []
            tokens = [t.strip() for t in _re.split(r"[\s,;]+", targets or "") if t.strip()]
            for tok in tokens:
                m_uid = _re.fullmatch(r"(?:<@!?)?(\d{15,22})>?", tok)
                if m_uid:
                    resolved_uids.append(m_uid.group(1))
                elif "@" in tok:
                    email_q = tok.lower()
                    for _, db in self.services._dual_dbs():
                        try:
                            doc = db["trading_leads"].find_one({"email": email_q}) or db["leads"].find_one({"email": email_q})
                            if doc and str(doc.get("discord_user_id") or "").isdigit():
                                resolved_uids.append(str(doc["discord_user_id"]))
                                break
                        except Exception:
                            pass
            resolved_uids = list(dict.fromkeys(resolved_uids))
            if not resolved_uids:
                await interaction.followup.send("⚠️ لم يتم العثور على أي مستخدم صالح في `targets` (أدخل Discord ID أو @منشن أو إيميل مسجل).", ephemeral=True)
                return

            sent_to: list[str] = []
            failed_to: list[str] = []
            chunks = split_discord_text(raw_msg, max_len=1900) if raw_msg else [""]
            for uid in resolved_uids:
                ok_send = False
                if self.deliver_user_dm_fn is not None:
                    try:
                        ok_send = await self.deliver_user_dm_fn(uid, raw_msg, raw_files)
                    except Exception:
                        ok_send = False
                if not ok_send:
                    try:
                        user = self.get_user(int(uid)) or await self.fetch_user(int(uid))
                        if user:
                            for idx, ch_txt in enumerate(chunks):
                                d_files = (
                                    [discord.File(io.BytesIO(b), filename=fn) for b, fn in raw_files]
                                    if idx == len(chunks) - 1
                                    else []
                                )
                                await user.send(
                                    content=ch_txt or None,
                                    files=d_files,
                                    allowed_mentions=safe_allowed_mentions(),
                                )
                            ok_send = True
                    except Exception:
                        ok_send = False
                if ok_send:
                    sent_to.append(uid)
                else:
                    failed_to.append(uid)

            await self.services.log_security_audit(
                bot_role="ceo",
                action="/send_to_users",
                verdict="ALLOW" if sent_to else "FAIL",
                actor_id=str(interaction.user.id),
                actor_name=interaction.user.name,
                summary=f"sent={len(sent_to)} failed={len(failed_to)} files={len(raw_files)}",
            )
            await interaction.followup.send(
                f"✅ تم الإرسال بنجاح إلى **{len(sent_to)}** مستخدم (`{', '.join(sent_to) or '-'}`)"
                + (f"\n⚠️ تعذر الإرسال (DMs مغلقة) لـ: `{', '.join(failed_to)}`" if failed_to else ""),
                ephemeral=True,
            )

    async def notify_ceo(self, message: str) -> bool:
        """Deliver private system/healing/VIP alerts exclusively to verified CEO DMs or private #ceo-command-center."""
        safe = safe_discord_text(redact_secrets(message, self.cfg.known_secrets))
        for ch_id_str in self.cfg.ceo_channel_ids:
            try:
                ch = self.get_channel(int(ch_id_str)) or await self.fetch_channel(int(ch_id_str))
                guild = getattr(ch, "guild", None)
                if ch and is_channel_private_from_everyone(ch, guild):
                    await ch.send(safe, allowed_mentions=safe_allowed_mentions())
                    return True
            except Exception:
                pass
        for uid_str in self.cfg.ceo_user_ids:
            try:
                user = self.get_user(int(uid_str)) or await self.fetch_user(int(uid_str))
                if user:
                    await user.send(safe, allowed_mentions=safe_allowed_mentions())
                    return True
            except Exception:
                pass
        return False

    async def setup_hook(self) -> None:
        self.add_view(CEOAdminPanelView(self.services, self.cfg, self.broadcast_fn))
        try:
            if self.cfg.guild_id and str(self.cfg.guild_id).isdigit():
                guild_obj = discord.Object(id=int(self.cfg.guild_id))
                self.tree.copy_global_to(guild=guild_obj)
                await self.tree.sync(guild=guild_obj)
                if self.application_id:
                    await self.http.bulk_upsert_global_commands(self.application_id, [])
            else:
                await self.tree.sync()
        except Exception as exc:
            logger.warning("CEO slash sync error: %s", redact_secrets(str(exc), self.cfg.known_secrets))

    async def on_ready(self) -> None:
        logger.info("CEO Command Bot READY as %s (id=%s, guilds=%d)", self.user, getattr(self.user, "id", ""), len(self.guilds))

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.id in self._dedup_ids:
            return
        self._dedup_ids.add(message.id)
        if len(self._dedup_ids) > 2000:
            self._dedup_ids.clear()

        ok, reason = is_ceo_authorized(message.author, message.channel, message.guild, self.cfg)
        is_dm = isinstance(message.channel, discord.DMChannel)
        is_mention = bool(self.user and self.user in message.mentions)

        if not ok:
            if is_dm or is_mention:
                await self.services.log_security_audit(
                    bot_role="ceo", action="ceo_message", verdict="DENY",
                    actor_id=str(message.author.id), actor_name=message.author.name,
                    guild_id=str(getattr(message.guild, "id", "") or ""),
                    channel_id=str(getattr(message.channel, "id", "") or ""),
                    reason=reason,
                )
                if is_dm:
                    await message.reply("⛔ هذا البوت محصور للمدير التنفيذي المعتمد فقط.", allowed_mentions=safe_allowed_mentions())
            return

        content = (message.content or "").strip()
        try:
            await self.services.log_discord_chat_message(
                bot_role="ceo",
                direction="inbound",
                message_id=str(getattr(message, "id", "") or ""),
                discord_user_id=str(getattr(message.author, "id", "") or ""),
                discord_username=str(getattr(message.author, "name", "") or ""),
                channel_id=str(getattr(message.channel, "id", "") or ""),
                channel_name="DM" if is_dm else str(getattr(message.channel, "name", "") or ""),
                guild_id=str(getattr(message.guild, "id", "") or ""),
                content=content,
                attachments=list(getattr(message, "attachments", None) or []),
                embeds_count=len(getattr(message, "embeds", None) or []),
            )
        except Exception:
            pass
        if self.user:
            content = content.replace(f"<@{self.user.id}>", "").replace(f"<@!{self.user.id}>", "").strip()

        voice_transcript = ""
        vision_reply = ""
        doc_sync_reply = ""
        if message.attachments:
            att = message.attachments[0]
            data_bytes, mime, status = await self.services.safe_download_discord_attachment(att)
            if status == "ok" and data_bytes is not None:
                ext = att.filename.lower().rsplit(".", 1)[-1] if "." in att.filename else ""
                if mime.startswith("audio/"):
                    voice_transcript = await self.services.transcribe_audio_groq(data_bytes, att.filename)
                    content = f"{content}\n{voice_transcript}".strip()
                elif mime.startswith("image/"):
                    self._staged_media = {"bytes": data_bytes, "mime": mime, "filename": att.filename}
                    if not content or any(w in content.lower() for w in ("حلل", "analyze", "اشرح", "chart", "شوف")):
                        vision_reply = await self.services.analyze_image_gemini(data_bytes, mime, content)
                elif ext in ("txt", "md", "json", "csv", "tsv", "html", "htm", "docx", "xlsx", "pdf", "yaml", "yml"):
                    sync_res = await self.services.sync_knowledge_to_ecosystem(
                        title=content or att.filename,
                        filename=att.filename,
                        content_bytes=data_bytes,
                        category="discord_ceo_upload",
                    )
                    if sync_res.get("ok"):
                        doc_sync_reply = f"✅ **Synced Document `{att.filename}` Across AIOS, Knowledge Base, RAG, MongoDB, Discord & Telegram!**"
                    else:
                        doc_sync_reply = f"⚠️ Could not ingest `{att.filename}`: {sync_res.get('error', 'Unknown error')}"

        if doc_sync_reply:
            await message.reply(safe_discord_text(doc_sync_reply), allowed_mentions=safe_allowed_mentions())
            return

        if not content and self._staged_media:
            await message.reply("✅ تم استلام وحفظ الميديا بنجاح في غرفة العمليات! اكتب أمرك الآن (مثلاً: 'حلل الصورة' أو 'ابعتها للكل').", allowed_mentions=safe_allowed_mentions())
            return
        if not content and not vision_reply:
            return

        if vision_reply:
            reply = vision_reply
        elif any(w in content.lower() for w in ("بث للكل", "ابعتها للكل", "broadcast media")) and self._staged_media:
            media = self._staged_media
            sent_n = await self.broadcast_fn(content or "إشعار مباشر من إدارة Tradesejon 📢", media["bytes"], str(media["filename"]))  # type: ignore[index]
            reply = f"✅ تم بث الميديا بنجاح إلى {sent_n} قناة/متداول على Discord."
        elif re.match(r"^(?:/addkb|add\s+kb:|أضف\s+للمعرفة:?|احفظ\s+المعلومة:?)\s+", content, re.IGNORECASE):
            raw_kb = re.sub(r"^(?:/addkb|add\s+kb:|أضف\s+للمعرفة:?|احفظ\s+المعلومة:?)\s+", "", content, count=1, flags=re.IGNORECASE).strip()
            kb_title, kb_body = (raw_kb.split("|", 1)[0].strip(), raw_kb.split("|", 1)[1].strip()) if "|" in raw_kb else (raw_kb.splitlines()[0][:60], raw_kb)
            sync_res = await self.services.sync_knowledge_to_ecosystem(title=kb_title, content=kb_body, category="discord_ceo_chat")
            reply = f"✅ **Knowledge Synced Across AIOS, MongoDB, n8n RAG, Discord & Telegram:** `{kb_title}`" if sync_res.get("ok") else f"⚠️ Failed: {sync_res.get('error')}"
        else:
            res = await self.services.ask_ceo_agent(content, discord_user_id=str(message.author.id), history=self._history[-6:])
            reply = res.get("reply") or "تم تنفيذ الأمر."
            self._history.append({"role": "user", "content": content})
            self._history.append({"role": "assistant", "content": reply[:500]})

        await self.services.log_security_audit(
            bot_role="ceo", action="ceo_message", verdict="ALLOW",
            actor_id=str(message.author.id), actor_name=message.author.name,
            channel_id=str(message.channel.id), summary=content[:180],
        )

        files = []
        if voice_transcript or "فويس" in content or "صوت" in content:
            aud = await self.services.synthesize_masri_voice(reply)
            if aud:
                files.append(discord.File(io.BytesIO(aud), filename="ceo_reply.mp3"))

        chunks = split_discord_text(reply, max_len=1900)
        for idx, chunk in enumerate(chunks):
            await message.reply(
                chunk,
                files=files if idx == len(chunks) - 1 else [],
                allowed_mentions=safe_allowed_mentions(),
            )
