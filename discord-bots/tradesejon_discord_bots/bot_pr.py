"""Tradesejon PR Desk Bot for Discord (staff/private-channel gated)."""

from __future__ import annotations

import io
import logging
from typing import Awaitable, Callable

import discord
from discord import app_commands

from .config import DiscordConfig
from .security import (
    is_pr_authorized,
    redact_secrets,
    safe_allowed_mentions,
    safe_discord_text,
    sanitize_untrusted_text,
)
from .shared_services import SharedServices

logger = logging.getLogger("tradesejon.discord.pr")


def build_pr_ticket_embed(
    *,
    ticket_code: str,
    trader_label: str,
    user_id: str,
    platform_source: str,
    question: str,
    suggested_reply: str = "",
    email: str = "",
    phone: str = "",
    lead_score: str = "",
) -> discord.Embed:
    """Build private PR Desk ticket notification embed (must ONLY be sent to private #pr-desk)."""
    embed = discord.Embed(
        title=f"🎫 تذكرة متداول جديدة [{ticket_code}]",
        color=0x38BDF8,
        description=safe_discord_text(question, max_len=1200),
    )
    embed.add_field(name="👤 المتداول", value=safe_discord_text(trader_label, 120), inline=True)
    embed.add_field(name="🆔 Discord / Ref", value=f"`{sanitize_untrusted_text(str(user_id), 64)}`", inline=True)
    embed.add_field(name="📡 المصدر", value=safe_discord_text(platform_source, 64), inline=True)
    if email:
        embed.add_field(name="📧 البريد", value=f"`{sanitize_untrusted_text(email, 100)}`", inline=True)
    if phone:
        embed.add_field(name="📱 الهاتف", value=f"`{sanitize_untrusted_text(phone, 40)}`", inline=True)
    if lead_score:
        embed.add_field(name="💎 التصنيف", value=f"`{sanitize_untrusted_text(lead_score, 32)}`", inline=True)
    if suggested_reply:
        embed.add_field(name="🤖 رد البوت التلقائي", value=safe_discord_text(suggested_reply, 900), inline=False)
    embed.set_footer(text=f"Tradesejon PR Desk • Ticket {ticket_code}")
    return embed


class PRTicketActionView(discord.ui.View):
    """Stateless-friendly interactive ticket triage buttons using dynamic custom_ids."""

    def __init__(self, ticket_code: str):
        super().__init__(timeout=None)
        code = sanitize_untrusted_text(ticket_code, 24).upper()
        self.add_item(discord.ui.Button(label="رد مباشر 💬", style=discord.ButtonStyle.primary, custom_id=f"pr:reply:{code}"))
        self.add_item(discord.ui.Button(label="رد صوتي 🎙️", style=discord.ButtonStyle.primary, custom_id=f"pr:voicereply:{code}"))
        self.add_item(discord.ui.Button(label="صياغة AI 🤖", style=discord.ButtonStyle.secondary, custom_id=f"pr:aidraft:{code}"))
        self.add_item(discord.ui.Button(label="إرسال QR & Whop 🔗", style=discord.ButtonStyle.success, custom_id=f"pr:sendqr:{code}"))
        self.add_item(discord.ui.Button(label="إيميل VIP 📧", style=discord.ButtonStyle.secondary, custom_id=f"pr:email:{code}"))
        self.add_item(discord.ui.Button(label="تم الحل ✅", style=discord.ButtonStyle.danger, custom_id=f"pr:resolve:{code}"))


class PRReplyModal(discord.ui.Modal):
    """Modal for direct human PR reply or AI-guided draft reply."""

    reply_input = discord.ui.TextInput(
        label="رسالتك أو توجيهك للرد على المتداول",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1500,
    )

    def __init__(
        self,
        *,
        ticket_code: str,
        ai_mode: bool,
        services: SharedServices,
        cfg: DiscordConfig,
        deliver_fn: Callable[[str, str, bytes | None, str], Awaitable[bool]],
        voice_mode: bool = False,
    ):
        title_prefix = "🎙️ رد صوتي" if voice_mode else ("🤖 صياغة AI" if ai_mode else "💬 رد مباشر")
        super().__init__(title=f"{title_prefix} [{ticket_code}]")
        self.ticket_code = ticket_code
        self.ai_mode = ai_mode
        self.voice_mode = voice_mode
        self.services = services
        self.cfg = cfg
        self.deliver_fn = deliver_fn

    async def on_submit(self, interaction: discord.Interaction) -> None:
        ok, reason = is_pr_authorized(interaction.user, interaction.channel, interaction.guild, self.cfg)
        if not ok:
            await interaction.response.send_message("⛔ غير مصرح لك بهذا الإجراء في هذه القناة.", ephemeral=True)
            await self.services.log_security_audit(
                bot_role="pr", action="modal_reply", verdict="DENY",
                actor_id=str(interaction.user.id), actor_name=interaction.user.name,
                guild_id=str(interaction.guild_id or ""), channel_id=str(interaction.channel_id or ""),
                reason=reason,
            )
            return

        await interaction.response.defer(ephemeral=True)
        raw_text = sanitize_untrusted_text(self.reply_input.value, max_len=1500)
        ticket = await self.services.get_ticket(self.ticket_code)
        final_text = raw_text
        if self.ai_mode:
            ctx = (ticket or {}).get("last_message", "")
            final_text = await self.services.ask_pr_ai_draft(raw_text, trader_context=ctx)

        audio_bytes: bytes | None = None
        audio_filename = ""
        if self.voice_mode:
            is_en = not any("\u0600" <= ch <= "\u06FF" for ch in final_text)
            audio_bytes = await self.services.synthesize_masri_voice(final_text, is_english=is_en)
            if audio_bytes:
                audio_filename = "tradesejon_pr_voice_reply.mp3"

        delivered = await self.deliver_fn(
            self.ticket_code,
            f"👨‍💼 **رد فريق Tradesejon بخصوص تذكرتك [{self.ticket_code}]:**\n\n{final_text}",
            audio_bytes,
            audio_filename,
        )
        await self.services.log_security_audit(
            bot_role="pr", action="ai_reply" if self.ai_mode else "direct_reply", verdict="ALLOW",
            actor_id=str(interaction.user.id), actor_name=interaction.user.name,
            guild_id=str(interaction.guild_id or ""), channel_id=str(interaction.channel_id or ""),
            summary=f"{self.ticket_code} delivered={delivered}",
        )
        status_note = "تم إرسال الرد بنجاح إلى المتداول ✅" if delivered else "تم حفظ الرد، لكن تعذر التسليم التلقائي لعدم توفر قناة المتداول."
        await interaction.followup.send(safe_discord_text(f"{status_note}\n\n{final_text}"), ephemeral=True)


async def handle_pr_button_interaction(
    interaction: discord.Interaction,
    *,
    services: SharedServices,
    cfg: DiscordConfig,
    deliver_fn: Callable[[str, str, bytes | None, str], Awaitable[bool]],
) -> bool:
    """Process persistent `pr:<action>:<ticket_code>` component button presses."""
    if interaction.type != discord.InteractionType.component:
        return False
    cid = str((interaction.data or {}).get("custom_id", ""))
    if not cid.startswith("pr:"):
        return False

    ok, reason = is_pr_authorized(interaction.user, interaction.channel, interaction.guild, cfg)
    if not ok:
        await services.log_security_audit(
            bot_role="pr", action="button_click", verdict="DENY",
            actor_id=str(interaction.user.id), actor_name=interaction.user.name,
            guild_id=str(interaction.guild_id or ""), channel_id=str(interaction.channel_id or ""),
            reason=reason, summary=cid,
        )
        await interaction.response.send_message(
            "⛔ هذه الأزرار متاحة لفريق العلاقات العامة/الإدارة فقط داخل قناة #pr-desk الخاصة.",
            ephemeral=True,
        )
        return True

    parts = cid.split(":", 2)
    if len(parts) != 3:
        return False
    _, action, ticket_code = parts

    if action in ("reply", "voicetext", "aidraft"):
        await interaction.response.send_modal(
            PRReplyModal(
                ticket_code=ticket_code,
                ai_mode=(action == "aidraft"),
                voice_mode=(action == "voicetext"),
                services=services,
                cfg=cfg,
                deliver_fn=deliver_fn,
            )
        )
        return True

    if action == "voicereply":
        mic_url = f"{cfg.aios_base_url.rstrip('/')}/pr-voice?ticket={ticket_code}"
        voice_view = discord.ui.View(timeout=300)
        voice_view.add_item(discord.ui.Button(label="🎙️ فتح المايك للتسجيل بصوتك الآن", style=discord.ButtonStyle.link, url=mic_url))
        text_tts_btn = discord.ui.Button(label="⌨️ كتابة نص وتحويله لصوت", style=discord.ButtonStyle.secondary, custom_id=f"pr:voicetext:{ticket_code}")
        voice_view.add_item(text_tts_btn)
        voice_emb = discord.Embed(
            title=f"🎙️ تسجيل رد صوتي بشري للتذكرة [{ticket_code}]",
            color=0x3B82F6,
            description=(
                "تطبيق Discord Desktop لا يعرض أيقونة مايكروفون في قنوات النص، لذلك وفرنا لك **3 طرق فورية:**\n\n"
                "1️⃣ **التسجيل بالمايك مباشرة (الأسرع):** اضغط زر **«🎙️ فتح المايك للتسجيل بصوتك الآن»** أدناه ليفتح الاستوديو بالمايك فوراً ويرسل صوتك للمتداول بضغطة واحدة.\n"
                "2️⃣ **رفع ملف صوتي هنا:** اعمل Reply على رسالة التذكرة بأي ملف صوتي (`.ogg` / `.mp3` / `.wav`) وسنوصله فوراً.\n"
                "3️⃣ **تحويل نص لصوت مصري:** اضغط **«⌨️ كتابة نص وتحويله لصوت»** لكتابة ردك وسننطقه صوتياً باللهجة المصرية."
            ),
        )
        await interaction.response.send_message(embed=voice_emb, view=voice_view, ephemeral=True)
        return True

    await interaction.response.defer(ephemeral=True)
    if action == "sendqr":
        sess = await services.get_live_session_info()
        qr_bytes = services.generate_qr_png(sess["whop_url"])
        caption = (
            f"🎯 **رمز QR ورابط تفعيل VIP لمدة 7 أيام مجاناً ($0 اليوم) [{ticket_code}]**\n"
            f"🔗 {sess['whop_url']}"
        )
        delivered = await deliver_fn(ticket_code, caption, qr_bytes, "tradesejon_vip_qr.png")
        await interaction.followup.send(
            f"✅ تم إرسال QR ورابط التفعيل للتذكرة [{ticket_code}]." if delivered else "⚠️ تعذر التسليم لقناة المتداول.",
            ephemeral=True,
        )
        return True

    if action == "email":
        ticket = await services.get_ticket(ticket_code)
        target_email = (ticket or {}).get("email", "")
        if not target_email:
            await interaction.followup.send("⚠️ لا يوجد بريد إلكتروني مسجل لهذه التذكرة بعد.", ephemeral=True)
            return True
        res = await services.send_email_secure(
            to_email=target_email,
            subject="دعوتك الرسمية لحضور جلسة التداول VIP — Tradesejon",
            body_text="أهلاً بك في مجتمع Tradesejon! يسعدنا تأكيد دعوتك الخاصة للجلسة المباشرة.",
            recipient_name=(ticket or {}).get("fullName", "Trader"),
            include_qr=True,
        )
        msg = f"📧 تم إرسال إيميل VIP بنجاح إلى `{target_email}`." if res.get("ok") else f"⚠️ تعذر الإرسال: {res.get('error')}"
        await interaction.followup.send(safe_discord_text(msg), ephemeral=True)
        return True

    if action == "resolve":
        done = await services.resolve_ticket(ticket_code, str(interaction.user.id))
        await interaction.followup.send(f"✅ تم إغلاق التذكرة [{ticket_code}] بنجاح." if done else "⚠️ لم يتم العثور على التذكرة.", ephemeral=True)
        return True

    return False


def register_pr_commands(
    tree: app_commands.CommandTree,
    *,
    services: SharedServices,
    cfg: DiscordConfig,
    deliver_fn: Callable[[str, str, bytes | None, str], Awaitable[bool]],
) -> None:
    """Attach PR Desk slash commands onto the chosen CommandTree."""

    @tree.command(name="tickets", description="[PR & CEO Only] عرض ومتابعة تذاكر الدعم المفتوحة")
    @app_commands.default_permissions(administrator=True)
    async def tickets_cmd(interaction: discord.Interaction):
        ok, reason = is_pr_authorized(interaction.user, interaction.channel, interaction.guild, cfg)
        if not ok:
            await interaction.response.send_message(
                "⛔ أمر `/tickets` محصور لفريق **PR Desk** والـ **CEO** فقط.",
                ephemeral=True,
            )
            await services.log_security_audit(
                bot_role="pr", action="/tickets", verdict="DENY",
                actor_id=str(interaction.user.id), actor_name=interaction.user.name,
                guild_id=str(interaction.guild_id or ""), channel_id=str(interaction.channel_id or ""),
                reason=reason,
            )
            return
        await interaction.response.defer(ephemeral=True)
        rows = await services.list_open_tickets(limit=10)
        if not rows:
            await interaction.followup.send("لا توجد تذاكر مفتوحة حالياً.", ephemeral=True)
            return
        lines = []
        for r in rows:
            code = r.get("ticketCode") or r.get("ticket_id") or "TRD"
            name = sanitize_untrusted_text(str(r.get("fullName") or r.get("user_name") or "Trader"), 40)
            plat = sanitize_untrusted_text(str(r.get("platform") or "-"), 24)
            last_m = sanitize_untrusted_text(str(r.get("last_message") or ""), 80)
            lines.append(f"• **`{code}`** ({plat}) — **{name}**: {last_m}")
        await interaction.followup.send(safe_discord_text("\n".join(lines)), ephemeral=True)

    @tree.command(name="reply", description="[PR Staff] الرد على تذكرة متداول محددة")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(ticket="رقم التذكرة مثل TRD-1234", message="نص الرد")
    async def reply_cmd(interaction: discord.Interaction, ticket: str, message: str):
        ok, reason = is_pr_authorized(interaction.user, interaction.channel, interaction.guild, cfg)
        if not ok:
            await interaction.response.send_message("⛔ غير مصرح.", ephemeral=True)
            await services.log_security_audit(
                bot_role="pr", action="/reply", verdict="DENY",
                actor_id=str(interaction.user.id), actor_name=interaction.user.name,
                guild_id=str(interaction.guild_id or ""), channel_id=str(interaction.channel_id or ""),
                reason=reason,
            )
            return
        await interaction.response.defer(ephemeral=True)
        code = sanitize_untrusted_text(ticket, 24).upper()
        body = sanitize_untrusted_text(message, 1500)
        delivered = await deliver_fn(code, f"👨‍💼 **رد فريق Tradesejon [{code}]:**\n\n{body}", None, "")
        await interaction.followup.send(
            f"✅ تم إرسال الرد لتذكرة `{code}`." if delivered else f"⚠️ لم يتم العثور على قناة ديسكورد نشطة للتذكرة `{code}`.",
            ephemeral=True,
        )

    @tree.command(name="vip_email", description="[PR Staff] إرسال بريد دعوة VIP لمتداول")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(email="البريد الإلكتروني المستلم", name="اسم المتداول", message="رسالة مخصصة اختيارية")
    async def vip_email_cmd(interaction: discord.Interaction, email: str, name: str = "Trader", message: str = ""):
        ok, reason = is_pr_authorized(interaction.user, interaction.channel, interaction.guild, cfg)
        if not ok:
            await interaction.response.send_message("⛔ غير مصرح.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        body = message.strip() or "يسعدنا دعوتك لإكمال التسجيل وتفعيل باقة Tradesejon Live VIP باستخدام رابط Whop الآمن ورمز QR المرفق."
        res = await services.send_email_secure(to_email=email, subject="Tradesejon VIP — دعوة التسجيل المباشر", body_text=body, recipient_name=name)
        ok_msg = (
            f"✅ تم الإرسال بنجاح عبر `{res.get('provider')}` من `{res.get('from_addr', cfg.gmail_user)}` إلى `{email}`\n"
            f"🆔 Message-ID: `{res.get('message_id', '-')}`\n"
            "*(ملاحظة: افحص صندوق الوارد أو Promotions / Spam عند المستلم)*"
        )
        await interaction.followup.send(
            safe_discord_text(ok_msg if res.get("ok") else f"⚠️ فشل الإرسال: {res.get('error')}"),
            ephemeral=True,
        )


async def handle_pr_desk_voice_reply(
    message: discord.Message,
    *,
    services: SharedServices,
    cfg: DiscordConfig,
    deliver_fn: Callable[[str, str, bytes | None, str], Awaitable[bool]],
) -> bool:
    """Forward a human staff voice note in #pr-desk directly to the trader for the referenced ticket."""
    if not getattr(message, "attachments", None):
        return False
    ok, _ = is_pr_authorized(message.author, message.channel, message.guild, cfg)
    if not ok:
        return False

    import re as _re

    ticket_code = ""
    m_txt = _re.search(r"\b(TRD-\d{3,6}|DC-\d{3,6})\b", (message.content or ""), _re.I)
    if m_txt:
        ticket_code = m_txt.group(1).upper()

    if not ticket_code and getattr(message, "reference", None) and getattr(message.reference, "message_id", None):
        try:
            ref_msg = message.reference.resolved
            if ref_msg is None and hasattr(message.channel, "fetch_message"):
                ref_msg = await message.channel.fetch_message(message.reference.message_id)
            if ref_msg is not None:
                blob_parts = [getattr(ref_msg, "content", "") or ""]
                for emb in getattr(ref_msg, "embeds", []) or []:
                    blob_parts.append(getattr(emb, "title", "") or "")
                    blob_parts.append(getattr(emb, "description", "") or "")
                    if getattr(emb, "footer", None):
                        blob_parts.append(getattr(emb.footer, "text", "") or "")
                    for fld in getattr(emb, "fields", []) or []:
                        blob_parts.append(f"{getattr(fld, 'name', '')} {getattr(fld, 'value', '')}")
                m_ref = _re.search(r"\b(TRD-\d{3,6}|DC-\d{3,6})\b", " ".join(blob_parts), _re.I)
                if m_ref:
                    ticket_code = m_ref.group(1).upper()
        except Exception:
            pass

    if not ticket_code:
        return False

    att = message.attachments[0]
    audio_bytes, mime, status = await services.safe_download_discord_attachment(att)
    if status != "ok" or not audio_bytes or not mime.startswith("audio/"):
        return False

    transcript = await services.transcribe_audio_groq(audio_bytes, getattr(att, "filename", "voice-message.ogg"))
    caption = (
        f"🎙️ **رسالة صوتية مباشرة من فريق Tradesejon بخصوص تذكرتك [{ticket_code}]:**"
        + (f"\n\n«{transcript}»" if transcript else "")
    )
    fname = getattr(att, "filename", None) or "tradesejon_human_voice_reply.ogg"
    delivered = await deliver_fn(ticket_code, caption, audio_bytes, fname)
    await services.log_security_audit(
        bot_role="pr", action="human_voice_reply", verdict="ALLOW" if delivered else "FAIL",
        actor_id=str(message.author.id), actor_name=message.author.name,
        guild_id=str(getattr(message.guild, "id", "") or ""),
        channel_id=str(getattr(message.channel, "id", "") or ""),
        summary=f"{ticket_code} delivered={delivered}",
    )
    status_note = f"✅ تم إرسال رسالتك الصوتية البشرية إلى المتداول للتذكرة `{ticket_code}`." if delivered else f"⚠️ تعذر تسليم الرسالة الصوتية للتذكرة `{ticket_code}`."
    await message.reply(status_note, allowed_mentions=safe_allowed_mentions())
    return True


class PRDeskBotClient(discord.Client):
    """Standalone Discord bot dedicated to PR Desk when DISCORD_PR_BOT_TOKEN is configured."""

    def __init__(
        self,
        cfg: DiscordConfig,
        services: SharedServices,
        deliver_to_trader_fn: Callable[[str, str, bytes | None, str], Awaitable[bool]],
    ):
        intents = discord.Intents(guilds=True, messages=True, dm_messages=True, reactions=True)
        super().__init__(intents=intents, allowed_mentions=safe_allowed_mentions())
        self.cfg = cfg
        self.services = services
        self.deliver_to_trader_fn = deliver_to_trader_fn
        self.tree = app_commands.CommandTree(self)
        register_pr_commands(self.tree, services=services, cfg=cfg, deliver_fn=deliver_to_trader_fn)

    async def setup_hook(self) -> None:
        try:
            await self.tree.sync()
        except Exception as exc:
            logger.warning("PR slash sync error: %s", redact_secrets(str(exc), self.cfg.known_secrets))

    async def on_ready(self) -> None:
        logger.info("PR Desk Bot READY as %s (id=%s)", self.user, getattr(self.user, "id", ""))

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        try:
            await handle_pr_button_interaction(
                interaction,
                services=self.services,
                cfg=self.cfg,
                deliver_fn=self.deliver_to_trader_fn,
            )
        except Exception as exc:
            logger.exception("PR interaction failure: %s", redact_secrets(str(exc), self.cfg.known_secrets))

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if await handle_pr_desk_voice_reply(
            message,
            services=self.services,
            cfg=self.cfg,
            deliver_fn=self.deliver_to_trader_fn,
        ):
            return
        content = (message.content or "").strip()
        if not content:
            return
        ok, _ = is_pr_authorized(message.author, message.channel, message.guild, self.cfg)
        if not ok:
            return
        if isinstance(message.channel, discord.DMChannel) or (self.user and self.user in message.mentions):
            cleaned = content.replace(f"<@{self.user.id}>", "").replace(f"<@!{self.user.id}>", "").strip() if self.user else content
            reply = await self.services.ask_pr_ai_draft(cleaned or "مرحباً، كيف أساعد فريق العلاقات العامة؟")
            await message.reply(safe_discord_text(reply), allowed_mentions=safe_allowed_mentions())
