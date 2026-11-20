"""Tradesejon Trader Support & Community Bot for Discord."""

from __future__ import annotations

import asyncio
import io
import logging
from typing import Awaitable, Callable

import discord
from discord import app_commands

from .bot_pr import (
    PRTicketActionView,
    build_pr_ticket_embed,
    handle_pr_button_interaction,
    handle_pr_desk_voice_reply,
    register_pr_commands,
)
from .config import DiscordConfig
from .country_phone import resolve_country_from_text, validate_phone_for_country
from .crm_sync import build_tier_defs
from .security import (
    RateLimiter,
    collect_lead_errors,
    detect_invalid_contact_attempt,
    extract_email_and_phone_from_text,
    extract_email_only_from_text,
    force_format_email,
    is_ceo_authorized,
    is_channel_private_from_everyone,
    is_pr_authorized,
    redact_secrets,
    safe_allowed_mentions,
    safe_discord_text,
    sanitize_untrusted_text,
    split_discord_text,
    validate_email_detailed,
    validate_lead_input,
    validate_watched_videos,
)
from .shared_services import SharedServices

logger = logging.getLogger("tradesejon.discord.trader")


def build_marhaban_welcome_embed(cfg: DiscordConfig, member_mention: str = "أهلاً بك") -> discord.Embed:
    """Build flagship Marhaban welcome & official Tradesejon pricing embed matching master files."""
    tier_summary = (
        "• 🎁 **اختبار الدفع وتأكيد الحساب عبر Whop (مجاناً $0.00 — بدون بطاقة):** إدخال الإيميل وتأكيد كود **Whop OTP** فقط\n"
        "• 💎 **عضوية Discord VIP (اللايف اليومي + الـ Scanner):** **`$45 / شهر`** (https://whop.com/Tradesejon/exp_TEMPLATE_VIP"
        "• 🏛️ **Offline Workshop (4 ساعات حضورية — Upcoming Scheduled Workshop):**\n"
        "  - لأعضاء الديسكورد: **`4,000 EGP ($74.39)`** | لغير الأعضاء: **`6,000 EGP ($111.59)`**\n"
        "• 💻 **Online Workshop الخاصة (4 ساعات مع المحاضر — الاثنين–الخميس):** **`$100`** (MentorAlpha / MentorBeta / MentorGamma / MentorDelta / Mentor Epsilon)"
    )
    return discord.Embed(
        title="👋 Marhaban — أهلاً بك في مجتمع Tradesejon Official!",
        color=0x10B981,
        description=(
            f"مرحباً {member_mention} في **Tradesejon Official Trading Ecosystem**.\n\n"
            f"📌 **الباقات والأسعار الرسمية الموثقة:**\n{tier_summary}\n\n"
            "🚀 **خطوات الدخول السريع إلى Prospects Bot (`#prospects`):**\n"
            "1️⃣ اضغط **«ابدأ نموذج Marhaban 📋»** أدناه (أو أرسل **بريدك الإلكتروني فقط** هنا لتجربة Whop المجانية `$0.00`).\n"
            "2️⃣ افتح رابط **Whop** وأدخل بريدك الإلكتروني ليصلك كود التحقق (**Whop OTP**).\n"
            "3️⃣ **فقط بعد تأكيد كود الـ OTP على Whop** يتم إدخالك فوراً وتلقائياً إلى **Prospects Bot (`#prospects`)** للرد عليك من الـ AI والفريق البشري واختيار باقتك!"
        ),
    )


class MarhabanWelcomeView(discord.ui.View):
    """Persistent Marhaban entry view in #marhaban opening the intake form or checking status."""

    def __init__(self, services: SharedServices, cfg: DiscordConfig, pr_notify_fn: Callable[[discord.Embed, PRTicketActionView], Awaitable[None]]):
        super().__init__(timeout=None)
        self.services = services
        self.cfg = cfg
        self.pr_notify_fn = pr_notify_fn

    @discord.ui.button(label="ابدأ نموذج Marhaban 📋", style=discord.ButtonStyle.danger, custom_id="mr:start_form")
    async def marhaban_form_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await open_trader_form_with_autofill(interaction, self.services, self.cfg, self.pr_notify_fn)

    @discord.ui.button(label="أسعار 12 سوق مباشر 📊", style=discord.ButtonStyle.secondary, custom_id="mr:prices", row=0)
    async def marhaban_prices_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        prices = await self.services.get_market_prices()
        await interaction.followup.send(embed=format_prices_embed(prices), ephemeral=True)


class WhopTierButtonsView(discord.ui.View):
    """Dynamic Whop OTP test ($0.00) + official packages ($45 VIP, 4000/6000 EGP Offline, $100 Online) + Verify button."""

    def __init__(
        self,
        services: SharedServices,
        cfg: DiscordConfig,
        pr_notify_fn: Callable[[discord.Embed, PRTicketActionView], Awaitable[None]],
        *,
        workshop_url: str = "",
        viproom_url: str = "",
        free_otp_url: str = "",
        in_community: bool = True,
        otp_verified: bool = False,
    ):
        super().__init__(timeout=None)
        self.services = services
        self.cfg = cfg
        self.pr_notify_fn = pr_notify_fn
        otp_link = free_otp_url or cfg.whop_checkout_url or "https://whop.com/checkout/plan_TEMPLATE_01"
        if otp_link.startswith("https://"):
            self.add_item(discord.ui.Button(label="🎁 Whop OTP مجاناً ($0.00 — بالإيميل فقط)", style=discord.ButtonStyle.link, url=otp_link, row=1))
        if otp_verified:
            vip_url = "https://whop.com/Tradesejon/exp_TEMPLATE_VIP"
            off_url = cfg.whop_checkout_offline_member_4000egp if in_community else cfg.whop_checkout_offline_nonmember_6000egp
            off_label = "🏛️ Offline (4,000 ج / $74.39)" if in_community else "🏛️ Offline (6,000 ج / $111.59)"
            on_url = viproom_url or cfg.whop_checkout_online_MentorAlpha
            self.add_item(discord.ui.Button(label="💎 Discord VIP ($45/mo)", style=discord.ButtonStyle.link, url=vip_url, row=1))
            if off_url.startswith("https://"):
                self.add_item(discord.ui.Button(label=off_label, style=discord.ButtonStyle.link, url=off_url, row=1))
            if on_url.startswith("https://"):
                self.add_item(discord.ui.Button(label="💻 Online Workshop ($100)", style=discord.ButtonStyle.link, url=on_url, row=1))
            for child in self.children:
                if getattr(child, "custom_id", "") == "tr:verify_whop_btn":
                    child.label = "✅ تم الدفع — افتح Workshop الآن"
        else:
            for child in self.children:
                if getattr(child, "custom_id", "") == "tr:verify_whop_btn":
                    child.label = "✅ أكدت كود Whop OTP — ادخل Prospects الآن"

    @discord.ui.button(label="✅ نعم", style=discord.ButtonStyle.success, custom_id="wv:yes", row=0)
    async def watched_yes_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = str(interaction.user.id)
        await self.services.update_discord_lead_watched_videos(discord_user_id=uid, watched=True)
        for child in self.children:
            if getattr(child, "custom_id", "") == "wv:yes":
                child.label = "✅ تم تسجيل إجابتك: نعم"
                child.disabled = True
            elif getattr(child, "custom_id", "") == "wv:no":
                child.disabled = True
        next_steps_emb = discord.Embed(
            title="✅ ممتاز! أنت جاهز للخطوة التالية في رحلتك مع Tradesejon",
            color=0x10B981,
            description=(
                "بما إنك شوفت فيديوهات Lead Strategist التعليمية، دي خطواتك الجاية عشان تطبق صح:\n\n"
                "1️⃣ **متابعة الـ Live Trading اليومي في الديسكورد ($45/شهر شامل الـ Scanner):**\n"
                "• Lead Strategist: كل يوم في الـ After Hours\n"
                "• البشمهندس MentorAlpha: لايف سيكولوجية التداول كل يوم أحد\n"
                "• MentorGamma: كل ثلاثاء وخميس | MentorDelta: كل اثنين وأربعاء | Mentor Epsilon: كل سبت وخميس\n\n"
                "2️⃣ **حضور الـ Workshop (4 ساعات: 2.5 نظري + 1.5 عملي):**\n"
                "• **Offline Workshop (Upcoming Scheduled Workshop):**\n"
                "  - لأعضاء الديسكورد (4,000 ج / $74.39): https://whop.com/checkout/plan_TEMPLATE_03\n"
                "  - لغير الأعضاء (6,000 ج / $111.59): https://whop.com/checkout/plan_TEMPLATE_04\n"
                "• **Online Workshop ($100 — 4 ساعات مع المحاضر أيام الاثنين–الخميس):**\n"
                "  - MentorAlpha: https://whop.com/checkout/plan_TEMPLATE_05\n"
                "  - MentorBeta ⚡: https://whop.com/checkout/plan_TEMPLATE_06\n"
                "  - MentorGamma: https://whop.com/checkout/plan_TEMPLATE_07\n"
                "  - MentorDelta: https://whop.com/checkout/plan_TEMPLATE_08\n"
                "  - Mentor Epsilon: https://whop.com/checkout/plan_TEMPLATE_09"
            ),
        )
        try:
            await interaction.response.edit_message(view=self)
            if hasattr(interaction, "followup") and hasattr(interaction.followup, "send"):
                await interaction.followup.send(embed=next_steps_emb, ephemeral=True)
        except Exception:
            await interaction.response.send_message(
                "✅ تم تسجيل إجابتك بنجاح: نعم، شاهدت الفيديوهات! 🚀",
                embed=next_steps_emb,
                ephemeral=True,
            )

    @discord.ui.button(label="❌ لا", style=discord.ButtonStyle.secondary, custom_id="wv:no", row=0)
    async def watched_no_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = str(interaction.user.id)
        await self.services.update_discord_lead_watched_videos(discord_user_id=uid, watched=False)
        for child in self.children:
            if getattr(child, "custom_id", "") == "wv:no":
                child.label = "❌ تم تسجيل إجابتك: لا"
                child.disabled = True
            elif getattr(child, "custom_id", "") == "wv:yes":
                child.disabled = True
        videos_emb = discord.Embed(
            title="🎥 خطة البداية الكاملة — فيديوهات التأسيس وبلاي ليست Lead Strategist",
            color=0x3B82F6,
            description=(
                "ولا يهمك! الخطوة الأولى والأهم قبل أي حاجة هي إنك تبدأ بالفيديوهات بالترتيب ده وتجمّع كل أسئلتك في ورقة:\n\n"
                "📌 **أولاً (لو مبتدئ خالص / زيرو بورصة — شوف الـ 5 فيديوهات دي بالترتيب الأول):**\n"
                "1️⃣ **البورصة | Market Basics:** https://youtu.be/VIDEO_TEMPLATE_01\n"
                "2️⃣ **عالم الاستثمار | Investment Series:** https://youtu.be/VIDEO_TEMPLATE_02\n"
                "3️⃣ **أهم قاعدة في الاستثمار | Market Basics:** https://youtu.be/VIDEO_TEMPLATE_03\n"
                "4️⃣ **مفاهيم أساسية | Market Instructor:** https://youtu.be/VIDEO_TEMPLATE_04\n"
                "5️⃣ **سوق الأسهم الأمريكي باختصار | Market Overview:** https://youtu.be/VIDEO_TEMPLATE_05\n\n"
                "🚀 **ثانياً (الأساس — بلاي ليست Lead Strategist التعليمية على يوتيوب):**\n"
                "🔗 https://youtube.com/YOUR_TRAINING_PLAYLIST"
                "💡 **نصيحة مهمة:** اتفرج على أكبر عدد من فيديوهات Member واكتب أي سؤال يقف معاك في ورقة عشان نناقشه بالتفصيل في الـ Workshop الجاية (Upcoming Scheduled Workshop)!"
            ),
        )
        try:
            await interaction.response.edit_message(view=self)
            if hasattr(interaction, "followup") and hasattr(interaction.followup, "send"):
                await interaction.followup.send(embed=videos_emb, ephemeral=True)
        except Exception:
            await interaction.response.send_message(
                "✅ تم تسجيل إجابتك بنجاح: لا، لم أشاهدها بعد. إليك روابط الفيديوهات للبدء فوراً:",
                embed=videos_emb,
                ephemeral=True,
            )

    @discord.ui.button(label="✅ تم التأكيد على Whop — افتح Workshop الآن", style=discord.ButtonStyle.primary, custom_id="tr:verify_whop_btn", row=2)
    async def verify_whop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_verify_payment_click(interaction, self.services, self.cfg, self.pr_notify_fn)


async def handle_verify_payment_click(
    interaction: discord.Interaction,
    services: SharedServices,
    cfg: DiscordConfig,
    pr_notify_fn: Callable[[discord.Embed, PRTicketActionView | None], Awaitable[None]],
) -> None:
    """Verify Whop payment live, transition roles (Prospect -> Workshop / V tradesejon), and deliver direct link."""
    await interaction.response.defer(ephemeral=True)
    uid = str(interaction.user.id)
    funnel = await services.get_discord_user_funnel_state(uid)
    if not funnel.get("form_validated"):
        err_emb = discord.Embed(
            title="❌ لم يتم العثور على بيانات تسجيلك",
            description="🔴 يرجى الضغط على زر **«ابدأ نموذج Marhaban 📋»** وأدخل إيميلك ورقم هاتفك أولاً.",
            color=0xEF4444,
        )
        await interaction.followup.send(
            embed=err_emb,
            ephemeral=True,
            view=MarhabanWelcomeView(services, cfg, pr_notify_fn),
        )
        return

    email_val = funnel.get("email", "")
    if not funnel.get("paid") and (email_val or uid):
        live = await services.verify_whop_payment_live_for_email(email_val, discord_user_id=uid)
        if live.get("otp_verified") or live.get("paid"):
            is_free_otp = bool(live.get("is_free_otp") or live.get("tier") == "free_otp_test")
            fulfilled = await services.fulfill_whop_payment_for_discord(
                email_addr=email_val,
                phone=funnel.get("phone", ""),
                discord_user_id=uid,
                whop_payment_id=str(live.get("whop_payment_id") or ""),
                tier=str(live.get("tier") or ("free_otp_test" if is_free_otp else "workshop_45")),
                plan_id=str(live.get("plan_id") or ("plan_TEMPLATE_01" if is_free_otp else "")),
                amount_usd=float(live.get("amount_usd") or 0),
            )
            if fulfilled.get("is_free_otp") or fulfilled.get("tier") == "free_otp_test":
                emb = discord.Embed(
                    title="🎉 تم تأكيد كود Whop OTP بنجاح — أهلاً بك في Prospects Bot!",
                    color=0x10B981,
                    description=(
                        f"✅ تم التحقق من بريدك (`{email_val}`) عبر **Whop OTP ($0.00)**.\n"
                        f"🚀 تم إدخالك فوراً إلى **Prospects Bot (<#{cfg.prospects_channel_id}>)** وتفعيل **Prospect Role**!\n\n"
                        "💳 **الباقات الرسمية للترقية إلى `#workshop-channel`:**\n"
                        "• لايف الديسكورد اليومي + الـ Scanner: **`$45 / شهر`** (https://whop.com/Tradesejon/exp_TEMPLATE_VIP"
                        "• Offline Workshop (Upcoming Scheduled Workshop): **`4,000 EGP ($74.39)`** للأعضاء | **`6,000 EGP ($111.59)`** لغير الأعضاء\n"
                        "• Online Workshop الخاصة (4 ساعات مع المحاضر): **`$100`**"
                    ),
                )
                await interaction.followup.send(
                    embed=emb,
                    view=WhopTierButtonsView(
                        services,
                        cfg,
                        pr_notify_fn,
                        free_otp_url=funnel.get("free_otp_url", ""),
                        workshop_url=funnel.get("checkout_url", ""),
                        viproom_url=funnel.get("viproom_checkout_url", ""),
                        in_community=bool(funnel.get("in_community", True)),
                        otp_verified=True,
                    ),
                    ephemeral=True,
                )
                return

            delivery = await services.deliver_live_link_everywhere(
                email_addr=email_val,
                phone_e164=funnel.get("phone", ""),
                full_name=funnel.get("full_name") or getattr(interaction.user, "display_name", "Tradesejon VIP"),
                discord_user_id=uid,
                tier=fulfilled.get("tier", "workshop_45"),
                paid=True,
            )
            live_url = delivery.get("discord_live_url") or fulfilled.get("discord_live_url") or cfg.default_zoom_url
            emb = discord.Embed(
                title=f"🎉 تم تأكيد الدفع ({fulfilled.get('status')}) — مرحباً بك في Workshop!",
                color=0x10B981,
                description=(
                    f"✅ تم نقلك تلقائياً من Prospect Bot إلى <#{cfg.workshop_channel_id}>"
                    + (f" و <#{cfg.viproom_channel_id}>" if fulfilled.get("tier") == "viproom_75" else "")
                    + f"\n🔗 **رابط البث المباشر للورشة:** {live_url}\n"
                    f"📧 وتم إرسال التأكيد إلى بريدك (`{email_val}`) وتيليجرام."
                ),
            )
            await interaction.followup.send(embed=emb, ephemeral=True)
            return
        await services.reconcile_recent_whop_payments()
        funnel = await services.get_discord_user_funnel_state(uid)

    if funnel.get("paid") and funnel.get("whop_otp_verified"):
        await send_unique_session_response(interaction, services, cfg, pr_notify_fn, already_deferred=True)
        return

    in_comm = bool(funnel.get("in_community", True))
    otp_done = bool(funnel.get("whop_otp_verified"))
    off_label = "4,000 EGP ($74.39) — عضو مجتمع" if in_comm else "6,000 EGP ($111.59) — غير عضو"
    if not otp_done:
        emb = discord.Embed(
            title="❌ لم يتم تأكيد كود Whop OTP على بريدك بعد",
            color=0xEF4444,
            description=(
                f"🔴 **الدخول إلى Prospects Bot (`#prospects`) يتطلب تأكيد Whop OTP أولاً!**\n\n"
                f"📌 **البريد المسجل:** `{email_val}`\n"
                "1️⃣ اضغط زر **«🎁 Whop OTP مجاناً ($0.00 — بالإيميل فقط)»** أدناه.\n"
                "2️⃣ أدخل بريدك على Whop ليصلك كود التحقق (**OTP**).\n"
                "3️⃣ **فقط بعد تأكيد الـ OTP في Whop** اضغط **«✅ أكدت كود Whop OTP — ادخل Prospects الآن»** (أو انتظر لحظات وسينقلك البوت تلقائياً إلى `#prospects`)."
            ),
        )
    else:
        emb = discord.Embed(
            title="❌ لم يتم تأكيد دفع الباقة المدفوعة على Whop بعد",
            color=0xEF4444,
            description=(
                f"🔴 **أنت الآن في Prospects Bot (`#prospects`) — فتح Workshop يحتاج تأكيد الدفع على Whop.**\n\n"
                f"📌 **البريد المسجل:** `{email_val}`\n"
                "• **Discord Live VIP + Scanner:** **`$45 / شهر`**\n"
                f"• **Offline Workshop:** **`{off_label}`**\n"
                "• **Online Workshop (4 ساعات خاصة):** **`$100`**\n\n"
                "أتمم الاشتراك بالرابط أدناه بنفس البريد، ثم اضغط **«✅ تم الدفع — افتح Workshop الآن»**."
            ),
        )
    emb.set_footer(text="Tradesejon Zero-Trust OTP & Payment Gate")
    await interaction.followup.send(
        embed=emb,
        view=WhopTierButtonsView(
            services,
            cfg,
            pr_notify_fn,
            free_otp_url=funnel.get("free_otp_url", ""),
            workshop_url=funnel.get("checkout_url", ""),
            viproom_url=funnel.get("viproom_checkout_url", ""),
            in_community=in_comm,
            otp_verified=otp_done,
        ),
        ephemeral=True,
    )


async def complete_free_live_registration(
    *,
    services: SharedServices,
    cfg: DiscordConfig,
    pr_notify_fn: Callable[[discord.Embed, PRTicketActionView], Awaitable[None]],
    discord_user: discord.abc.User,
    channel_id: str,
    guild_id: str,
    raw_full_name: str,
    raw_phone: str,
    raw_email: str,
    raw_challenge: str = "",
    raw_watched_videos: str = "",
    raw_country: str = "",
    raw_level: str = "",
    platform_label: str = "Discord Marhaban Form",
    in_community: bool | None = None,
    require_watched_videos: bool = False,
    require_country_and_level: bool = False,
) -> tuple[bool, str, discord.Embed | None, bytes | None, dict[str, Any]]:
    """Validate Mail+Phone(+Country+Level+Watched), sync CRM/n8n/AIOS/Sheets, enforce Whop OTP gate -> Prospects Bot or Paid Workshop."""
    _, watched_choice = validate_watched_videos(raw_watched_videos)
    watched_bool = watched_choice == "yes"
    country_val = sanitize_untrusted_text(raw_country, 80).strip()
    level_val = str(raw_level or "").strip().lower()
    if level_val not in ("entry", "intermediate", "advanced"):
        level_val = ""
    resolved_name = sanitize_untrusted_text(
        raw_full_name or getattr(discord_user, "display_name", "") or getattr(discord_user, "name", "") or raw_email.split("@")[0],
        80,
    )
    if len(resolved_name) < 2:
        resolved_name = "Tradesejon VIP"

    country_is_other = country_val in ("خارج مصر", "Other", "other")
    country_spec = None if country_is_other else (resolve_country_from_text(country_val) if country_val else None)
    resolved_country_name = country_spec.en if country_spec else ""

    ok, errors_list, phone_e164 = collect_lead_errors(
        resolved_name, raw_email, raw_phone, country=resolved_country_name
    )
    if require_country_and_level and not country_val:
        errors_list.append("البلد (Country) مطلوب — اختار بلدك من القائمة.")
        ok = False
    elif country_val and not country_is_other and country_spec is None:
        errors_list.append(
            f"البلد ({country_val}) غير معروف — اختار بلدك من القائمة."
        )
        ok = False
    if require_country_and_level and not level_val:
        errors_list.append("مستواك في التداول — من فضلك اختار مستواك (مبتدئ / متوسط / متقدم).")
        ok = False
    if require_watched_videos and not str(raw_watched_videos or "").strip():
        errors_list.append("هل شاهدت فيديوهاتنا ؟ — يرجى اختيار ✅ أيوة أو ❌ لسه.")
        ok = False
    if not ok:
        formatted_err = "\n".join(f"🔴 {e}" for e in errors_list)
        return False, formatted_err, None, None, {}

    email_addr = force_format_email(raw_email)
    challenge_txt = sanitize_untrusted_text(
        raw_challenge or f"البلد: {country_val or 'غير محدد'} | المستوى: {level_val or 'غير محدد'}",
        400,
    )
    uid = str(discord_user.id)
    uname = str(getattr(discord_user, "name", resolved_name))

    saved = await services.save_discord_lead(
        discord_user_id=uid,
        discord_username=uname,
        full_name=resolved_name,
        email_addr=email_addr,
        phone_e164=phone_e164,
        challenge=challenge_txt,
        channel_id=str(channel_id or ""),
        watched_videos=watched_bool,
        in_community=in_community,
        country=country_val,
        trading_level=level_val,
    )
    free_otp_url = saved.get("free_otp_url") or cfg.whop_checkout_url or "https://whop.com/checkout/plan_TEMPLATE_01"
    secure_whop_url = saved["whop_checkout_url"]
    viproom_whop_url = saved.get("viproom_checkout_url") or cfg.whop_checkout_viproom_75
    checkout_ref = saved["checkout_ref"]
    already_paid = bool(saved.get("already_paid"))
    otp_verified = bool(saved.get("whop_otp_verified") or already_paid)
    is_comm = bool(saved.get("in_community", True))
    tier_val = str(saved.get("tier") or ("workshop_45" if is_comm else "external_60"))
    status_val = str(saved.get("status") or ("prospect_paid_once" if already_paid else ("prospect" if otp_verified else "pending_whop_otp")))

    delivery = await services.deliver_live_link_everywhere(
        email_addr=email_addr,
        phone_e164=phone_e164,
        full_name=resolved_name,
        discord_user_id=uid,
        checkout_ref=checkout_ref,
        whop_checkout_url=free_otp_url if not otp_verified else secure_whop_url,
        tier=tier_val,
        paid=already_paid,
    )
    live_url = delivery.get("discord_live_url") or ""
    tg_deep = delivery["telegram_deep_link"]
    sess = delivery["session"]

    ticket_code = await services.create_or_update_discord_ticket(
        discord_user_id=uid,
        discord_username=uname,
        channel_id=str(channel_id or ""),
        guild_id=str(guild_id or ""),
        question=f"[Status: {status_val} | Tier: {tier_val} | OTP: {otp_verified} | Ref: {checkout_ref}] {challenge_txt}",
        reply=(
            f"تم التحقق من الدفع المسبق وفتح Workshop مباشرة: {live_url}"
            if already_paid
            else (
                f"تم تأكيد Whop OTP وفتح Prospects Bot: {secure_whop_url}"
                if otp_verified
                else f"بانتظار تأكيد Whop OTP ($0.00 بالإيميل) قبل دخول Prospects Bot: {free_otp_url}"
            )
        ),
    )

    pr_embed = build_pr_ticket_embed(
        ticket_code=ticket_code,
        trader_label=f"{resolved_name} (@{uname})",
        user_id=uid,
        platform_source=f"{platform_label} ({status_val})",
        question=(
            f"الحالة: **{status_val}** | عضو سيرفر: {'نعم ✅' if is_comm else 'لا'} | تأكيد Whop OTP: {'✅ تم' if otp_verified else '⏳ بانتظار OTP'}\n"
            + (
                f"رابط Workshop المباشر: {live_url}\n"
                if live_url
                else f"رابط Whop OTP المجاني ($0.00): {free_otp_url}\nالباقات الرسمية ($45 VIP | Offline 4,000/6,000 EGP | Online $100): {secure_whop_url}\n"
            )
            + f"الهدف: {challenge_txt}\nGoogle Sheet: {cfg.tradesajon_gsheet_url}"
        ),
        email=email_addr,
        phone=phone_e164,
        lead_score="VIP_ALREADY_PAID" if already_paid else ("PROSPECT_OTP_VERIFIED" if otp_verified else "PENDING_WHOP_OTP"),
    )
    await pr_notify_fn(pr_embed, PRTicketActionView(ticket_code))

    if already_paid:
        qr_target = live_url or secure_whop_url
        qr_bytes = services.generate_qr_png(qr_target)
        confirm_embed = discord.Embed(
            title=f"✅ أهلاً بك يا {resolved_name} — اشتراكك مدفوع مسبقاً وتم فتح Workshop فوراً!",
            color=0x10B981,
            description=(
                f"• **حالتك الآن:** `{status_val}` (`{tier_val}`)\n"
                f"• **قناتك المفتوحة:** <#{cfg.workshop_channel_id}>"
                + (f" + <#{cfg.viproom_channel_id}> (**V tradesejon Role + Workshop Role**)" if tier_val == "viproom_75" else "")
                + f"\n• **رابط Workshop المباشر الحصري بك:**\n🔗 {live_url}\n\n"
                f"📧 **البريد (`{email_addr}`):** {'✅ تم الإرسال' if delivery.get('email_sent') else '⚠️ قيد المعالجة'}\n"
                f"📲 **تيليجرام:** {tg_deep}"
            ),
        )
        return True, "ok", confirm_embed, qr_bytes, saved

    if otp_verified:
        mention = f"<@{uid}>" if uid.isdigit() else resolved_name
        if uid.isdigit():
            await services.discord_roles.send_persona_message(
                "prospect",
                recipient_user_id=uid,
                content=(
                    f"🎉 أهلاً بك يا {mention} في **Prospects Bot** (`#prospects`)!\n"
                    f"✅ تم تأكيد كود **Whop OTP** عبر بريدك وتم نقلك فوراً إلى **Prospects Bot** (`Prospect Role`).\n"
                    "💬 اسأل هنا أي سؤال وسيرد عليك **الـ AI Agent وفريقنا البشري معاً**!\n"
                    "💳 **الباقات الرسمية للترقية إلى `#workshop-channel`:**\n"
                    "• لايف الديسكورد اليومي + الـ Scanner: **`$45 / شهر`** (https://whop.com/Tradesejon/exp_TEMPLATE_VIP"
                    "• Offline Workshop (Upcoming Scheduled Workshop): **`4,000 EGP ($74.39)`** للأعضاء | **`6,000 EGP ($111.59)`** لغير الأعضاء\n"
                    "• Online Workshop الخاصة (4 ساعات مع المحاضر): **`$100`**"
                ),
            )
        qr_bytes = services.generate_qr_png(secure_whop_url)
        confirm_embed = discord.Embed(
            title=f"👋 Marhaban {resolved_name} — تم تأكيد Whop OTP ودخولك Prospects Bot بنجاح! [{checkout_ref}]",
            color=0x10B981,
            description=(
                f"• **حالتك الحالية:** `prospect` | تم منحك **Prospect Role** في <#{cfg.prospects_channel_id}>\n"
                f"• **تفاصيل الجلسة المباشرة:** {sess['session_name']} ({sess['session_time']})\n\n"
                "🎬 **هل شاهدت فيديوهاتنا وشروحات Tradesejon السابقة؟**\n"
                "👇 اضغط على زر [✅ نعم] أو [❌ لا] أدناه لتأكيد إجابتك:\n\n"
                "💳 **الباقات الرسمية على Whop:**\n"
                "1️⃣ **Discord VIP Live + Scanner:** **`$45 / شهر`** — https://whop.com/Tradesejon/exp_TEMPLATE_VIP"
                f"2️⃣ **Offline Workshop (4 ساعات):** **`4,000 EGP ($74.39)`** للأعضاء | **`6,000 EGP ($111.59)`** لغير الأعضاء\n"
                "3️⃣ **Online Workshop الخاصة (4 ساعات مع المحاضر):** **`$100`**"
            ),
        )
        return True, "ok", confirm_embed, qr_bytes, saved

    # Strictly Pending Whop OTP: DO NOT grant #prospects / Prospect Role before Whop confirms the OTP!
    qr_bytes = services.generate_qr_png(free_otp_url)
    confirm_embed = discord.Embed(
        title=f"🔐 الخطوة الأخيرة قبل الدخول إلى Prospects Bot — تأكيد Whop OTP [{checkout_ref}]",
        color=0xF59E0B,
        description=(
            f"• **تم حفظ بياناتك بنجاح** (`{email_addr}`)\n"
            f"• **الحالة الحالية:** `pending_whop_otp` *(لن يتم فتح <#{cfg.prospects_channel_id}> إلا بعد تأكيد كود الـ OTP على Whop)*\n\n"
            "📩 **افتح رابط Whop المجاني ($0.00 — بالإيميل فقط بدون بطاقة بنكية):**\n"
            f"🔗 {free_otp_url}\n\n"
            "1️⃣ أدخل بريدك فقط على صفحة Whop (`plan_TEMPLATE_01`).\n"
            "2️⃣ ستستلم فوراً كود تحقق (**Whop OTP**) على إيميلك.\n"
            "3️⃣ **بمجرد تأكيد الـ OTP في Whop** (أو بالضغط على زر التحقق أدناه) يتم إدخالك فوراً وتلقائياً إلى **Prospects Bot**!"
        ),
    )
    return True, "ok", confirm_embed, qr_bytes, saved


COUNTRY_SELECT_OPTIONS = [
    ("🇪🇬 مصر (Egypt)", "مصر"),
    ("🇸🇦 السعودية", "السعودية"),
    ("🇦🇪 الإمارات", "الإمارات"),
    ("🇰🇼 الكويت", "الكويت"),
    ("🇶🇦 قطر", "قطر"),
    ("🇧🇭 البحرين", "البحرين"),
    ("🇴🇲 عُمان", "عمان"),
    ("🇯🇴 الأردن", "الأردن"),
    ("🇱🇧 لبنان", "لبنان"),
    ("🇮🇶 العراق", "العراق"),
    ("🇵🇸 فلسطين", "فلسطين"),
    ("🇲🇦 المغرب", "المغرب"),
    ("🇹🇳 تونس", "تونس"),
    ("🇩🇿 الجزائر", "الجزائر"),
    ("🇱🇾 ليبيا", "ليبيا"),
    ("🇸🇩 السودان", "السودان"),
    ("🇾🇪 اليمن", "اليمن"),
    ("🇸🇾 سوريا", "سوريا"),
    ("🇬🇧 بريطانيا (UK)", "UK"),
    ("🇺🇸 أمريكا (USA)", "USA"),
    ("🇩🇪 ألمانيا (Germany)", "Germany"),
    ("🇫🇷 فرنسا (France)", "France"),
    ("🇨🇦 كندا (Canada)", "Canada"),
    ("🇹🇷 تركيا (Turkey)", "Turkey"),
    ("🌍 دولة تانية (خارج مصر)", "خارج مصر"),
]


def build_level_videos_embed(level: str, videos_url: str = "", country: str = "") -> discord.Embed:
    """Egyptian Arabic video guidance aligned with the official Tradesejon 17-FAQ guide (Q1, Q5, Q11, Q17)."""
    playlist = videos_url or "https://youtube.com/YOUR_TRAINING_PLAYLIST"
    lvl = (level or "").strip().lower()
    abroad_note = (
        "\n\n🌍 **ملاحظة:** الـ Workshop الأوفلاين في مصر بس. من خارج مصر الأنسب ليك الـ **Online Workshop (4 ساعات — `$100` موحد)** مع المحاضر اللي تختاره."
        if country and country.strip() not in ("مصر", "Egypt")
        else ""
    )
    if lvl in ("intermediate", "advanced"):
        lvl_label = "متوسط (Intermediate)" if lvl == "intermediate" else "متقدم (Advanced)"
        return discord.Embed(
            title=f"🚀 الخطوة الجاية ليك ({lvl_label}) — فيديوهات Lead Strategist + شرح المنصات",
            color=0x3B82F6,
            description=(
                f"1️⃣ **ابدأ ببلاي ليست Lead Strategist التعليمية (كل الفيديوهات الطويلة والشورتس):**\n{playlist}\n\n"
                "2️⃣ **بعدها شرح المنصات (بعد فيديوهات Member):**\n"
                "• سيت أب IBKR وتشغيل TWS (MentorGamma فتحي): https://youtu.be/VIDEO_TEMPLATE_06\n"
                "• فتح حساب DirectBroker والإيداع (حسام): https://youtu.be/VIDEO_TEMPLATE_07\n"
                "• Setup ذراع PlayStation كبديل Stream Deck للـ Hotkeys: https://youtu.be/VIDEO_TEMPLATE_08\n"
                "• لينكات إضافية: https://youtu.be/VIDEO_TEMPLATE_09 — https://youtu.be/VIDEO_TEMPLATE_10\n\n"
                "💡 *الـ 3 خطوات: فيديوهات Member ← لايف الديسكورد اليومي ← حضور الـ Workshop.*"
                + abroad_note
            ),
        )
    return discord.Embed(
        title="🎬 مبتدئ (Entry) — ابدأ بالترتيب ده قبل فيديوهات Lead Strategist",
        color=0x10B981,
        description=(
            "فيديوهات عامة عن البورصة بالترتيب (قبل فيديوهات Member):\n\n"
            "1️⃣ **البورصة | Market Basics:** https://youtu.be/VIDEO_TEMPLATE_01\n"
            "2️⃣ **لو نفسك تدخل عالم الاستثمار | Investment Series:** https://youtu.be/VIDEO_TEMPLATE_02\n"
            "3️⃣ **أهم قاعدة في الاستثمار | Market Basics:** https://youtu.be/VIDEO_TEMPLATE_03\n"
            "4️⃣ **المحاضرة الأولى: مفاهيم أساسية | Market Instructor:** https://youtu.be/VIDEO_TEMPLATE_04\n"
            "5️⃣ **سوق الأسهم الأمريكي باختصار | Market Overview:** https://youtu.be/VIDEO_TEMPLATE_05\n\n"
            f"6️⃣ **بعدها ابدأ بلاي ليست Lead Strategist التعليمية:**\n{playlist}"
            + abroad_note
        ),
    )


class RetryFormView(discord.ui.View):
    """Bright red retry button displayed immediately when form validation fails, pre-filling previous values."""

    def __init__(
        self,
        services: SharedServices,
        cfg: DiscordConfig,
        pr_notify_fn: Callable[[discord.Embed, PRTicketActionView], Awaitable[None]],
        *,
        prev_email: str = "",
        prev_phone: str = "",
        prev_name: str = "",
        prev_challenge: str = "",
        prev_country: str = "",
        prev_level: str = "",
        email_error: bool = False,
        email_error_reason: str = "",
        phone_error: bool = False,
        phone_error_reason: str = "",
        country_error: bool = False,
        country_error_reason: str = "",
        level_error: bool = False,
        watched_error: bool = False,
    ):
        super().__init__(timeout=300)
        self.services = services
        self.cfg = cfg
        self.pr_notify_fn = pr_notify_fn
        self.prev_email = prev_email
        self.prev_phone = prev_phone
        self.prev_name = prev_name
        self.prev_challenge = prev_challenge
        self.prev_country = prev_country
        self.prev_level = prev_level
        self.email_error = email_error
        self.email_error_reason = email_error_reason
        self.phone_error = phone_error
        self.phone_error_reason = phone_error_reason
        self.country_error = country_error
        self.country_error_reason = country_error_reason
        self.level_error = level_error
        self.watched_error = watched_error

    @discord.ui.button(label="🔴 تصحيح الأخطاء وإعادة فتح النموذج", style=discord.ButtonStyle.danger, custom_id="mr:retry_form")
    async def retry_form_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            TraderRegisterModal(
                self.services,
                self.cfg,
                self.pr_notify_fn,
                prev_email=self.prev_email,
                prev_phone=self.prev_phone,
                prev_name=self.prev_name,
                prev_challenge=self.prev_challenge,
                prev_country=self.prev_country,
                prev_level=self.prev_level,
                email_error=self.email_error,
                email_error_reason=self.email_error_reason,
                phone_error=self.phone_error,
                phone_error_reason=self.phone_error_reason,
                country_error=self.country_error,
                country_error_reason=self.country_error_reason,
                level_error=self.level_error,
                watched_error=self.watched_error,
            )
        )


async def open_trader_form_with_autofill(
    interaction: discord.Interaction,
    services: SharedServices,
    cfg: DiscordConfig,
    pr_notify_fn: Callable[[discord.Embed, PRTicketActionView], Awaitable[None]],
) -> None:
    """Open TraderRegisterModal with any known Discord user email/phone/name auto-filled and force-formatted."""
    uid = str(getattr(interaction.user, "id", "") or "")
    prefill_email = ""
    prefill_phone = ""
    prefill_name = str(getattr(interaction.user, "display_name", "") or getattr(interaction.user, "name", "") or "")[:80]
    if uid:
        try:
            funnel = await asyncio.wait_for(services.get_discord_user_funnel_state(uid), timeout=0.8)
            raw_em = str(funnel.get("email") or "")
            prefill_email = force_format_email(raw_em)
            prefill_phone = str(funnel.get("phone") or "")
            if funnel.get("full_name"):
                prefill_name = str(funnel.get("full_name"))[:80]
        except Exception:
            pass
    await interaction.response.send_modal(
        TraderRegisterModal(
            services,
            cfg,
            pr_notify_fn,
            prev_email=prefill_email,
            prev_phone=prefill_phone,
            prev_name=prefill_name,
        )
    )


class TraderRegisterModal(discord.ui.Modal, title="نموذج ورشة Tradesejon | Marhaban"):
    """5-question Egyptian Arabic intake modal (Discord max 5 components): Mail, Number, Country, Level, Watched Videos."""

    email = discord.ui.Label(
        text="1️⃣ الإيميل بتاعك (Mail) *",
        description="اكتب إيميلك الصحيح بصيغة حقيقية مثل: name@gmail.com أو hotmail أو outlook",
        component=discord.ui.TextInput(
            placeholder="name@gmail.com أو hotmail أو outlook",
            required=True,
            max_length=120,
        ),
    )
    phone = discord.ui.Label(
        text="2️⃣ رقم الموبايل / الواتساب (Number) *",
        description="اكتب رقم موبايلك الصحيح مطابق لبلدك مثل: +201012345678",
        component=discord.ui.TextInput(
            placeholder="+201012345678",
            required=True,
            max_length=25,
        ),
    )
    country = discord.ui.Label(
        text="3️⃣ بلدك إيه حالياً؟ *",
        description="اختار بلدك من القائمة",
        component=discord.ui.Select(
            placeholder="ابحث أو اختار بلدك...",
            min_values=1,
            max_values=1,
            required=True,
            options=[discord.SelectOption(label=lbl, value=val) for lbl, val in COUNTRY_SELECT_OPTIONS],
        ),
    )
    trading_level = discord.ui.Label(
        text="4️⃣ مستواك في التداول إيه دلوقتي؟ *",
        description="اختار مستواك عشان نبعتلك فيديوهات Lead Strategist المناسبة ليك",
        component=discord.ui.Select(
            placeholder="اختار مستواك...",
            min_values=1,
            max_values=1,
            required=True,
            options=[
                discord.SelectOption(label="مبتدئ تماماً (من الصفر — Entry)", value="entry", emoji="🟢"),
                discord.SelectOption(label="متوسط (أعرف الأساسيات بس — Intermediate)", value="intermediate", emoji="🟡"),
                discord.SelectOption(label="متقدم (محتاج إشارات وإدارة مخاطر — Advanced)", value="advanced", emoji="🔵"),
            ],
        ),
    )
    watched_videos = discord.ui.Label(
        text="5️⃣ هل اتفرجت على فيديوهات Lead Strategist؟ *",
        description="اختار أيوة أو لسه",
        component=discord.ui.Select(
            placeholder="اختار: أيوة أو لسه",
            min_values=1,
            max_values=1,
            required=True,
            options=[
                discord.SelectOption(label="أيوة اتفرجت عليها", value="yes", emoji="✅"),
                discord.SelectOption(label="لسه ماتفرجتش", value="no", emoji="❌"),
            ],
        ),
    )

    def __init__(
        self,
        services: SharedServices,
        cfg: DiscordConfig,
        pr_notify_fn: Callable[[discord.Embed, PRTicketActionView], Awaitable[None]],
        *,
        prev_email: str = "",
        prev_phone: str = "",
        prev_name: str = "",
        prev_challenge: str = "",
        prev_country: str = "",
        prev_level: str = "",
        email_error: bool = False,
        email_error_reason: str = "",
        phone_error: bool = False,
        phone_error_reason: str = "",
        country_error: bool = False,
        country_error_reason: str = "",
        level_error: bool = False,
        watched_error: bool = False,
    ):
        super().__init__()
        self.services = services
        self.cfg = cfg
        self.pr_notify_fn = pr_notify_fn
        self._prev_name = prev_name
        self._prev_challenge = prev_challenge
        em_comp = self.email.component
        ph_comp = self.phone.component
        ct_comp = self.country.component
        if email_error:
            self.title = "🔴 صلح الإيميل أولاً | نموذج Tradesejon"
            self.email.text = "🔴 خطأ! اكتب إيميل صحيح (Gmail / Hotmail / Outlook) *"
            reason_short = (email_error_reason or f"الإيميل ({prev_email[:30]}) مش صحيح — اكتب صيغة مثل name@gmail.com")[:95]
            self.email.description = f"🔴 {reason_short}"
            em_comp.placeholder = f"❌ ({prev_email[:24]}) غلط — الصيغة: name@gmail.com أو hotmail أو outlook"
        elif prev_email:
            em_comp.default = prev_email[:120]
        if phone_error:
            self.phone.text = "🔴 خطأ! اكتب رقم موبايل صحيح مطابق لبلدك *"
            ph_reason = (phone_error_reason or f"الرقم ({prev_phone[:25]}) مش صحيح — أرقام صحيحة فقط مثل +201012345678")[:95]
            self.phone.description = f"🔴 {ph_reason}"
            ph_comp.placeholder = f"❌ ({prev_phone[:22]}) غلط — أرقام فقط مثل +201012345678"
        elif prev_phone:
            ph_comp.default = prev_phone[:25]
        if country_error:
            self.country.text = "🔴 اختار بلدك من القائمة *"
            ct_reason = (country_error_reason or "لازم تختار بلدك عشان نقدر نكمل")[:95]
            self.country.description = f"🔴 {ct_reason}"
        elif prev_country:
            for opt in self.country.component.options:
                opt.default = opt.value == prev_country
        if level_error:
            self.trading_level.text = "🔴 اختار مستواك في التداول (مبتدئ / متوسط / متقدم) *"
            self.trading_level.description = "🔴 لازم تختار مستواك عشان نبعتلك الفيديوهات المناسبة"
        elif prev_level:
            for opt in self.trading_level.component.options:
                opt.default = opt.value == prev_level
        if watched_error:
            self.watched_videos.text = "🔴 اختار إجابة: هل اتفرجت على فيديوهات Lead Strategist؟ *"
            self.watched_videos.description = "🔴 لازم تختار أيوة أو لسه"

    @staticmethod
    def _read_text_value(lbl: Any) -> str:
        compat_val = getattr(lbl, "_value", None)
        if compat_val is not None:
            return str(compat_val)
        comp = getattr(lbl, "component", None)
        if comp is not None:
            return str(getattr(comp, "value", None) or getattr(comp, "_value", "") or "")
        return str(getattr(lbl, "value", "") or "")

    @staticmethod
    def _read_select_value(lbl: Any) -> str:
        compat_val = getattr(lbl, "_value", "")
        if compat_val:
            return str(compat_val)
        comp = getattr(lbl, "component", None)
        vals = getattr(comp, "values", None) or []
        return str(vals[0]) if vals else ""

    def _read_watched_value(self) -> str:
        return self._read_select_value(self.watched_videos)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        raw_wv = self._read_watched_value()
        raw_country = self._read_select_value(self.country) or self._read_text_value(self.country)
        raw_level = self._read_select_value(self.trading_level)
        typed_em = self._read_text_value(self.email)
        raw_em = force_format_email(typed_em) or typed_em.strip()
        raw_ph = self._read_text_value(self.phone)
        raw_nm = self._prev_name or str(getattr(interaction.user, "display_name", "") or getattr(interaction.user, "name", "") or "")
        raw_ch = self._prev_challenge or ""
        ok, msg, confirm_embed, qr_bytes, saved = await complete_free_live_registration(
            services=self.services,
            cfg=self.cfg,
            pr_notify_fn=self.pr_notify_fn,
            discord_user=interaction.user,
            channel_id=str(interaction.channel_id or ""),
            guild_id=str(interaction.guild_id or ""),
            raw_full_name=raw_nm,
            raw_phone=raw_ph,
            raw_email=raw_em,
            raw_challenge=raw_ch,
            raw_watched_videos=raw_wv,
            raw_country=raw_country,
            raw_level=raw_level,
            require_watched_videos=True,
            require_country_and_level=True,
        )
        if not ok or confirm_embed is None or qr_bytes is None:
            mail_ok, mail_specific_err, _ = validate_email_detailed(raw_em)
            ct_is_other = raw_country.strip() in ("خارج مصر", "Other", "other")
            ct_spec = None if ct_is_other else (resolve_country_from_text(raw_country) if raw_country.strip() else None)
            phone_ok, phone_specific_err, _ = validate_phone_for_country(raw_ph, ct_spec.en if ct_spec else "")
            em_bad = (not mail_ok) or ("البريد" in msg or "Mail" in msg)
            ph_bad = (not phone_ok) or ("الهاتف" in msg or "الموبايل" in msg or "Number" in msg)
            ct_bad = (not raw_country.strip()) or (not ct_is_other and ct_spec is None) or ("البلد" in msg)
            ct_specific_err = (
                "البلد (Country) مطلوب — اختار بلدك من القائمة."
                if not raw_country.strip()
                else (
                    f"البلد ({sanitize_untrusted_text(raw_country, 40)}) غير معروف — اختار بلدك من القائمة."
                    if (not ct_is_other and ct_spec is None)
                    else ""
                )
            )
            lv_bad = "مستواك" in msg
            wv_bad = "فيديوهاتنا" in msg or "فيديوهات" in msg
            err_embed = discord.Embed(
                title="❌ البيانات فيها غلط — من فضلك صلحها الأول",
                description="🔴 ماينفعش نحفظ النموذج غير بإجابات صحيحة في الخانات دي:",
                color=0xEF4444,
            )
            if em_bad:
                exact_mail_err = mail_specific_err if not mail_ok else "البريد الإلكتروني (Mail) غير صحيح — اكتب صيغة صحيحة مثل name@gmail.com أو name@hotmail.com أو name@outlook.com."
                err_embed.add_field(
                    name="🔴 الإيميل (Mail) مش صحيح",
                    value=f"```diff\n- اللي كتبته: {sanitize_untrusted_text(raw_em, 80) or '(فاضي)'}\n- السبب: {exact_mail_err}\n+ الصح: name@gmail.com أو name@hotmail.com أو name@outlook.com\n```",
                    inline=False,
                )
            if ph_bad:
                exact_ph_err = phone_specific_err if not phone_ok else "رقم الموبايل غير صحيح."
                ph_ex = ct_spec.example if ct_spec else "+201012345678"
                err_embed.add_field(
                    name="🔴 رقم الموبايل / الواتساب (Number) مش صحيح",
                    value=f"```diff\n- اللي كتبته: {sanitize_untrusted_text(raw_ph, 40) or '(فاضي)'}\n- السبب: {exact_ph_err}\n+ الصح: {ph_ex}\n```",
                    inline=False,
                )
            if ct_bad:
                err_embed.add_field(
                    name="🔴 البلد (Country) مش صحيح",
                    value=f"```diff\n- اللي كتبته: {sanitize_untrusted_text(raw_country, 40) or '(فاضي)'}\n- السبب: {ct_specific_err or 'اكتب اسم بلدك الحقيقي بالعربي أو الإنجليزي'}\n+ الصح: مصر / Egypt / السعودية / Saudi / UAE / Germany\n```",
                    inline=False,
                )
            if lv_bad:
                err_embed.add_field(
                    name="🔴 مستواك في التداول",
                    value="```diff\n- ما اخترتش مستواك\n+ اختار: مبتدئ / متوسط / متقدم\n```",
                    inline=False,
                )
            if wv_bad:
                err_embed.add_field(
                    name="🔴 فيديوهات Lead Strategist",
                    value="```diff\n- ما اخترتش إجابة\n+ اختار (أيوة) أو (لسه) من القائمة\n```",
                    inline=False,
                )
            if not any((em_bad, ph_bad, ct_bad, lv_bad, wv_bad)):
                err_embed.add_field(name="🔴 سبب الخطأ", value=f"```diff\n- {msg}\n```", inline=False)
            err_embed.set_footer(text="اضغط الزر الأحمر تحت عشان تفتح النموذج تاني وتصلح الغلط فوراً (أنت بس اللي شايف الرسالة دي)")
            retry_view = RetryFormView(
                self.services,
                self.cfg,
                self.pr_notify_fn,
                prev_email=raw_em,
                prev_phone=raw_ph,
                prev_name=raw_nm,
                prev_challenge=raw_ch,
                prev_country=raw_country,
                prev_level=raw_level,
                email_error=em_bad,
                email_error_reason=(mail_specific_err if not mail_ok else ""),
                phone_error=ph_bad,
                phone_error_reason=(phone_specific_err if not phone_ok else ""),
                country_error=ct_bad,
                country_error_reason=ct_specific_err,
                level_error=lv_bad,
                watched_error=wv_bad,
            )
            await interaction.followup.send(
                f"🔴 **في غلط في البيانات — صلح الخانات دي الأول:**\n{msg}",
                embed=err_embed,
                view=retry_view,
                ephemeral=True,
            )
            return

        verify_view: discord.ui.View = (
            TraderActionView(self.services, self.cfg, self.pr_notify_fn)
            if saved.get("already_paid")
            else WhopTierButtonsView(
                self.services,
                self.cfg,
                self.pr_notify_fn,
                free_otp_url=saved.get("free_otp_url", ""),
                workshop_url=saved.get("whop_checkout_url", ""),
                viproom_url=saved.get("viproom_checkout_url", ""),
                in_community=bool(saved.get("in_community", True)),
                otp_verified=bool(saved.get("whop_otp_verified") or saved.get("already_paid")),
            )
        )
        qr_fname = "tradesejon_workshop_qr.png" if saved.get("already_paid") else "tradesejon_whop_qr.png"
        videos_embed = build_level_videos_embed(raw_level, self.cfg.videos_url, raw_country)
        await interaction.followup.send(
            embeds=[confirm_embed, videos_embed],
            file=discord.File(io.BytesIO(qr_bytes), filename=qr_fname),
            view=verify_view,
            ephemeral=True,
        )
        try:
            await interaction.user.send(
                embeds=[confirm_embed, videos_embed],
                file=discord.File(io.BytesIO(qr_bytes), filename=qr_fname),
                view=verify_view,
                allowed_mentions=safe_allowed_mentions(),
            )
        except Exception:
            pass

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.error("Register modal error: %s", redact_secrets(str(error), self.cfg.known_secrets))
        try:
            err_embed = discord.Embed(
                title="❌ خطأ غير متوقع في معالجة النموذج",
                description="🔴 حدث خطأ تقني مؤقت أثناء إرسال النموذج. يرجى المحاولة مرة أخرى الآن بالضغط على زر النموذج.",
                color=0xEF4444,
            )
            err_embed.set_footer(text="Tradesejon System Alert")
            if interaction.response.is_done():
                await interaction.followup.send(embed=err_embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=err_embed, ephemeral=True)
        except Exception:
            pass


class TraderActionView(discord.ui.View):
    """Persistent quick-action buttons attached to Tradesejon Trader Support answers."""

    def __init__(self, services: SharedServices, cfg: DiscordConfig, pr_notify_fn: Callable[[discord.Embed, PRTicketActionView], Awaitable[None]]):
        super().__init__(timeout=None)
        self.services = services
        self.cfg = cfg
        self.pr_notify_fn = pr_notify_fn

    @discord.ui.button(label="سجل بالإيميل والرقم 📝", style=discord.ButtonStyle.danger, custom_id="tr:register")
    async def register_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await open_trader_form_with_autofill(interaction, self.services, self.cfg, self.pr_notify_fn)

    @discord.ui.button(label="رابط Discord Live المباشر 🎙️", style=discord.ButtonStyle.success, custom_id="tr:session")
    async def session_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await send_unique_session_response(interaction, self.services, self.cfg, self.pr_notify_fn)

    @discord.ui.button(label="باقات وأسعار Whop الرسمية 💳", style=discord.ButtonStyle.primary, custom_id="tr:trial")
    async def trial_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = str(interaction.user.id)
        funnel = await self.services.get_discord_user_funnel_state(uid)
        if not funnel.get("form_validated"):
            await open_trader_form_with_autofill(interaction, self.services, self.cfg, self.pr_notify_fn)
            return
        await interaction.response.defer(ephemeral=True)
        in_comm = bool(funnel.get("in_community", True))
        otp_done = bool(funnel.get("whop_otp_verified"))
        free_otp_url = funnel.get("free_otp_url") or self.cfg.whop_checkout_url or "https://whop.com/checkout/plan_TEMPLATE_01"
        checkout_url = funnel.get("checkout_url") or (self.cfg.whop_checkout_offline_member_4000egp if in_comm else self.cfg.whop_checkout_offline_nonmember_6000egp)
        viproom_url = funnel.get("viproom_checkout_url") or self.cfg.whop_checkout_online_MentorAlpha
        qr_bytes = self.services.generate_qr_png(free_otp_url if not otp_done else checkout_url)
        embed = discord.Embed(
            title="💳 الباقات الرسمية لـ Tradesejon على Whop",
            color=0x10B981 if otp_done else 0xF59E0B,
            description=(
                f"🎁 **اختبار Whop المجاني ($0.00 — بالإيميل + كود OTP):**\n{free_otp_url}\n\n"
                "💎 **Discord VIP Live + Scanner:** **`$45 / شهر`** — https://whop.com/Tradesejon/exp_TEMPLATE_VIP"
                f"🏛️ **Offline Workshop (Upcoming Scheduled Workshop):** **`4,000 EGP ($74.39)`** للأعضاء | **`6,000 EGP ($111.59)`** لغير الأعضاء\n"
                f"💻 **Online Workshop الخاصة (4 ساعات مع المحاضر):** **`$100`** — {viproom_url}\n\n"
                + (
                    "*(أدخل إيميلك في رابط Whop المجاني أعلاه وأكد الـ OTP ثم اضغط زر التحقق للدخول فوراً لـ `#prospects`)*"
                    if not otp_done
                    else "*(أنت الآن في `#prospects` — اضغط «✅ تم الدفع» فور إتمام باقة الورشة لفتح `#workshop-channel`)*"
                )
            ),
        )
        await interaction.followup.send(
            embed=embed,
            file=discord.File(io.BytesIO(qr_bytes), filename="tradesejon_whop_qr.png"),
            view=WhopTierButtonsView(
                self.services,
                self.cfg,
                self.pr_notify_fn,
                free_otp_url=free_otp_url,
                workshop_url=checkout_url,
                viproom_url=viproom_url,
                in_community=in_comm,
                otp_verified=otp_done,
            ),
            ephemeral=True,
        )

    @discord.ui.button(label="أسعار 12 سوق مباشر 📊", style=discord.ButtonStyle.secondary, custom_id="tr:prices")
    async def prices_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        prices = await self.services.get_market_prices()
        embed = format_prices_embed(prices)
        await interaction.followup.send(embed=embed, ephemeral=True)


def format_prices_embed(prices: dict) -> discord.Embed:
    embed = discord.Embed(title="📊 الأسعار المؤسسية اللحظية — Tradesejon 12 Markets", color=0xF59E0B)
    if not prices:
        embed.description = "⚠️ تعذر تحديث الأسعار اللحظية حالياً، جرب مرة أخرى خلال لحظات."
        return embed
    for sym, info in list(prices.items())[:12]:
        if not isinstance(info, dict):
            continue
        unit = info.get("unit", "")
        val = info.get("price", "-")
        chg = info.get("change_pct", "")
        chg_str = f" ({chg})" if chg != "" else ""
        embed.add_field(name=info.get("symbol", sym), value=f"`{unit}{val}`{chg_str}", inline=True)
    embed.set_footer(text="Tradesejon Live Institutional Feed")
    return embed


class TraderBotClient(discord.Client):
    """Trader Support Community Discord Bot (plus PR Desk fallback when 2 tokens are used)."""

    def __init__(self, cfg: DiscordConfig, services: SharedServices):
        intents = discord.Intents(guilds=True, messages=True, dm_messages=True, reactions=True)
        if cfg.enable_members_intent:
            intents.members = True
        super().__init__(intents=intents, allowed_mentions=safe_allowed_mentions())
        self.cfg = cfg
        self.services = services
        self.tree = app_commands.CommandTree(self)
        self.chat_limiter = RateLimiter(max_requests=6, window_seconds=60)
        self.media_limiter = RateLimiter(max_requests=3, window_seconds=60)
        self._dedup_ids: set[int] = set()
        self._register_commands()

    async def notify_pr_privately(
        self,
        embed: discord.Embed,
        view: PRTicketActionView | None = None,
        file: discord.File | None = None,
        files: list[discord.File] | None = None,
    ) -> None:
        """Send ticket alerts strictly to a private #pr-desk channel or verified CEO DMs (never public)."""
        delivered = False
        all_files: list[discord.File] = list(files or [])
        if file:
            all_files.append(file)
        for ch_id_str in self.cfg.pr_channel_ids:
            try:
                ch = self.get_channel(int(ch_id_str)) or await self.fetch_channel(int(ch_id_str))
                guild = getattr(ch, "guild", None)
                if ch and is_channel_private_from_everyone(ch, guild):
                    try:
                        await ch.send(embed=embed, view=view, files=all_files or None, allowed_mentions=safe_allowed_mentions())
                    except Exception:
                        await ch.send(embed=embed, view=view, allowed_mentions=safe_allowed_mentions())
                    delivered = True
                    break
            except Exception as exc:
                logger.warning("Failed delivery to PR channel %s: %s", ch_id_str, exc)

        if not delivered:
            for guild in self.guilds:
                for ch in getattr(guild, "text_channels", []):
                    if "pr-desk" in ch.name.lower() or "pr_desk" in ch.name.lower():
                        if is_channel_private_from_everyone(ch, guild):
                            try:
                                try:
                                    await ch.send(embed=embed, view=view, files=all_files or None, allowed_mentions=safe_allowed_mentions())
                                except Exception:
                                    await ch.send(embed=embed, view=view, allowed_mentions=safe_allowed_mentions())
                                delivered = True
                                break
                            except Exception as exc:
                                logger.warning("Failed delivery to fallback PR channel %s: %s", getattr(ch, "name", ""), exc)
                if delivered:
                    break

        if not delivered:
            for uid_str in self.cfg.ceo_user_ids:
                try:
                    u = self.get_user(int(uid_str)) or await self.fetch_user(int(uid_str))
                    if u:
                        await u.send(embed=embed, view=view, files=all_files or None, allowed_mentions=safe_allowed_mentions())
                        delivered = True
                        break
                except Exception:
                    pass

    async def deliver_to_trader(self, ticket_code: str, text: str, file_bytes: bytes | None = None, filename: str = "") -> bool:
        """Deliver a PR staff reply directly to the trader's Discord channel or DM."""
        ticket = await self.services.get_ticket(ticket_code)
        if not ticket:
            return False
        ch_id = ticket.get("discord_channel_id")
        uid = ticket.get("discord_user_id")
        safe_txt = safe_discord_text(text)

        async def _send_to(target) -> bool:
            if file_bytes and filename:
                await target.send(
                    safe_txt,
                    file=discord.File(io.BytesIO(file_bytes), filename=filename),
                    allowed_mentions=safe_allowed_mentions(),
                )
            else:
                await target.send(safe_txt, allowed_mentions=safe_allowed_mentions())
            return True

        # Deliver to originating channel when inside #prospects / #workshop-channel, and DM user for privacy
        posted_channel = False
        is_prospect_origin = str(ch_id) == str(self.cfg.prospects_channel_id)
        if ch_id and is_prospect_origin:
            try:
                ch = self.get_channel(int(ch_id)) or await self.fetch_channel(int(ch_id))
                if ch:
                    mention = f"<@{uid}> " if str(uid).isdigit() else ""
                    await ch.send(
                        safe_discord_text(f"👤 **Human Agent:** {mention}{safe_txt}"),
                        allowed_mentions=safe_allowed_mentions(),
                    )
                    posted_channel = True
            except Exception:
                pass
        if uid:
            try:
                user = self.get_user(int(uid)) or await self.fetch_user(int(uid))
                if user:
                    return await _send_to(user)
            except Exception:
                pass
        if ch_id and not posted_channel:
            try:
                ch = self.get_channel(int(ch_id)) or await self.fetch_channel(int(ch_id))
                if ch:
                    return await _send_to(ch)
            except Exception:
                pass
        return posted_channel

    async def deliver_zoom_to_user(
        self,
        discord_user_id: str,
        full_name: str = "Tradesejon VIP",
        unique_zoom_url: str = "",
    ) -> bool:
        """Deliver a strictly unique per-user Discord Live direct link via DM to the verified Discord user."""
        sess = await self.services.get_live_session_info()
        personal_live = (unique_zoom_url or "").strip()
        if not personal_live:
            zres = await self.services.create_unique_zoom_link_for_payer(
                discord_user_id=discord_user_id,
                full_name=full_name,
            )
            personal_live = str(zres.get("discord_live_url") or zres.get("unique_zoom_url") or "")
        if not personal_live:
            return False
        embed = discord.Embed(
            title=f"🎙️ تم تفعيل دخولك المجاني يا {sanitize_untrusted_text(full_name, 60)} — {sess['session_name']}!",
            color=0x10B981,
            description=(
                f"⏰ **موعد الجلسة:** {sess['session_time']}\n"
                f"🔗 **رابط Discord Live المباشر الخاص بك:**\n{personal_live}\n\n"
                f"💳 **بطاقة Whop المجانية ($0 بدون بطاقة):** {sess['whop_url']}\n\n"
                "🛡️ *(هذا الرابط شخصي ومرتبط بحسابك — لا تشاركه خارج حسابك)*"
            ),
        )
        try:
            u = self.get_user(int(discord_user_id)) or await self.fetch_user(int(discord_user_id))
            if u:
                await u.send(embed=embed, allowed_mentions=safe_allowed_mentions())
                return True
        except Exception as exc:
            logger.warning("Discord Live DM delivery failed for %s: %s", discord_user_id, redact_secrets(str(exc), self.cfg.known_secrets))
        return False

    def _register_commands(self) -> None:
        @self.tree.error
        async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
            logger.error("Trader slash error: %s", redact_secrets(str(error), self.cfg.known_secrets))
            err_embed = discord.Embed(
                title="❌ حدث خطأ مؤقت في تنفيذ الأمر",
                description="🔴 تعذر إتمام العملية في الوقت الحالي. يرجى إعادة المحاولة الآن أو استخدام الأزرار المتاحة.",
                color=0xEF4444,
            )
            err_embed.set_footer(text="Tradesejon Command Engine")
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(embed=err_embed, ephemeral=True)
                else:
                    await interaction.response.send_message(embed=err_embed, ephemeral=True)
            except Exception:
                pass

        @self.tree.command(name="marhaban", description="رسالة الترحيب وتفاصيل باقات Tradesejon ($0 OTP / $45 VIP / Offline & Online Workshop)")
        async def marhaban_cmd(interaction: discord.Interaction):
            await interaction.response.send_message(
                embed=build_marhaban_welcome_embed(self.cfg, getattr(interaction.user, "mention", "أهلاً بك")),
                view=MarhabanWelcomeView(self.services, self.cfg, self.notify_pr_privately),
                ephemeral=True,
                allowed_mentions=safe_allowed_mentions(),
            )

        @self.tree.command(name="start", description="ابدأ نموذج Marhaban للتسجيل في Tradesejon Workshop")
        async def start_cmd(interaction: discord.Interaction):
            await open_trader_form_with_autofill(interaction, self.services, self.cfg, self.notify_pr_privately)

        @self.tree.command(name="form", description="نموذج Marhaban (الإيميل + الرقم + الاسم + التحدي)")
        async def form_cmd(interaction: discord.Interaction):
            await open_trader_form_with_autofill(interaction, self.services, self.cfg, self.notify_pr_privately)

        @self.tree.command(name="ask", description="اسأل مساعد Tradesejon بالعامية المصرية أو الإنجليزية")
        @app_commands.describe(question="سؤالك عن التداول، الأسعار، الجلسات، أو التسجيل")
        async def ask_cmd(interaction: discord.Interaction, question: str):
            await self._handle_slash_support(interaction, question)

        @self.tree.command(name="support", description="تواصل مع دعم متداولي Tradesejon وافتح تذكرة متابعة فورية")
        @app_commands.describe(message="استفسارك")
        async def support_cmd(interaction: discord.Interaction, message: str):
            await self._handle_slash_support(interaction, message)

        @self.tree.command(name="prices", description="عرض الأسعار الحية لـ 12 سوق مالي (ذهب، بيتكوين، فوركس، مؤشرات)")
        async def prices_cmd(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)
            prices = await self.services.get_market_prices()
            await interaction.followup.send(
                embed=format_prices_embed(prices),
                view=TraderActionView(self.services, self.cfg, self.notify_pr_privately),
                ephemeral=True,
                allowed_mentions=safe_allowed_mentions(),
            )

        @self.tree.command(name="session", description="استلام رابط Discord Live المباشر الخاص بك (فوراً بعد كتابة الإيميل والرقم)")
        async def session_cmd(interaction: discord.Interaction):
            await self.session_btn_logic(interaction)

        @self.tree.command(name="trial", description="عرض رابط Whop المجاني ($0 بـ Email OTP) وباقات الورشة الرسمية")
        async def trial_cmd(interaction: discord.Interaction):
            uid = str(interaction.user.id)
            try:
                funnel = await asyncio.wait_for(
                    self.services.get_discord_user_funnel_state(uid),
                    timeout=1.2,
                )
            except Exception:
                funnel = {}
            if not funnel.get("form_validated"):
                await open_trader_form_with_autofill(interaction, self.services, self.cfg, self.notify_pr_privately)
                return
            await interaction.response.defer(ephemeral=True)
            in_comm = bool(funnel.get("in_community", True))
            otp_done = bool(funnel.get("whop_otp_verified"))
            free_otp_url = funnel.get("free_otp_url") or self.cfg.whop_checkout_url or "https://whop.com/checkout/plan_TEMPLATE_01"
            whop_url = funnel.get("checkout_url") or (self.cfg.whop_checkout_offline_member_4000egp if in_comm else self.cfg.whop_checkout_offline_nonmember_6000egp)
            viproom_url = funnel.get("viproom_checkout_url") or self.cfg.whop_checkout_online_MentorAlpha
            qr_bytes = self.services.generate_qr_png(free_otp_url if not otp_done else whop_url)
            embed = discord.Embed(
                title="🎁 بطاقة Whop المجانية ($0 — بـ Email OTP) وباقات Tradesejon الرسمية",
                color=0x10B981 if otp_done else 0xF59E0B,
                description=(
                    f"🎁 **رابط Whop المجاني ($0.00 بالإيميل + كود OTP):**\n{free_otp_url}\n\n"
                    "💎 **Discord VIP Live + Scanner:** **`$45 / شهر`** — https://whop.com/Tradesejon/exp_TEMPLATE_VIP"
                    f"🏛️ **Offline Workshop (Upcoming Scheduled Workshop):** **`4,000 EGP ($74.39)`** للأعضاء | **`6,000 EGP ($111.59)`** لغير الأعضاء\n"
                    f"💻 **Online Workshop الخاصة (4 ساعات):** **`$100`** — {viproom_url}\n\n"
                    + (
                        "*(فقط بعد تأكيد كود Whop OTP يتم إدخالك فوراً لـ `#prospects` Prospects Bot)*"
                        if not otp_done
                        else "*(أنت الآن في `#prospects` — اضغط «✅ تم الدفع» بعد الدفع لفتح `#workshop-channel`)*"
                    )
                ),
            )
            await interaction.followup.send(
                embed=embed,
                file=discord.File(io.BytesIO(qr_bytes), filename="tradesejon_whop_qr.png"),
                view=WhopTierButtonsView(
                    self.services,
                    self.cfg,
                    self.notify_pr_privately,
                    free_otp_url=free_otp_url,
                    workshop_url=whop_url,
                    viproom_url=viproom_url,
                    in_community=in_comm,
                    otp_verified=otp_done,
                ),
                ephemeral=True,
            )

        @self.tree.command(name="register", description="اكتب بريدك ورقمك واستلم رابط Discord Live على ديسكورد والميل والتيليجرام")
        async def register_cmd(interaction: discord.Interaction):
            await open_trader_form_with_autofill(interaction, self.services, self.cfg, self.notify_pr_privately)

        @self.tree.command(name="faq", description="الدليل الشامل والأسئلة الشائعة (الفيديوهات، الورشة، الاستراتيجية، والخطوات الـ 3)")
        async def faq_cmd(interaction: discord.Interaction):
            embed = discord.Embed(
                title="📚 الدليل الشامل لمنظومة Tradesejon — خطواتك من الصفر للاحتراف",
                color=0x10B981,
                description=(
                    "🎯 **الـ 3 خطوات الذهبية عشان تبدأ وتطبق صح:**\n"
                    "1️⃣ **فيديوهات Lead Strategist التعليمية:**\n"
                    "• البلاي ليست الرسمية: https://youtube.com/YOUR_TRAINING_PLAYLIST"
                    "• لو زيرو بورصة ومبتدئ خالص شوف الأول بالترتيب:\n"
                    "  - البورصة (Market Basics): https://youtu.be/VIDEO_TEMPLATE_01\n"
                    "  - عالم الاستثمار (Investment Series): https://youtu.be/VIDEO_TEMPLATE_02\n"
                    "  - أهم قاعدة في الاستثمار (Market Basics): https://youtu.be/VIDEO_TEMPLATE_03\n"
                    "  - مفاهيم أساسية (Market Instructor): https://youtu.be/VIDEO_TEMPLATE_04\n"
                    "  - سوق الأسهم الأمريكي باختصار (Market Overview): https://youtu.be/VIDEO_TEMPLATE_05\n\n"
                    "2️⃣ **اشتراك الديسكورد ($45/شهر شامل الـ Scanner):**\n"
                    "• Live Trading يومي مع الـ Mentors وLead Strategist + أداة الـ Scanner + قنوات Professional Alerts و Traders' Profits و Traders' Mistakes + لايف سيكولوجي كل أحد مع البشمهندس MentorAlpha.\n\n"
                    "3️⃣ **حضور الـ Workshop (4 ساعات: 2.5 نظري + 1.5 عملي):**\n"
                    "• استراتيجية الـ Scalping على الأسهم الأمريكية الصغيرة (4H, 15m, 1m, 5s + VWAP + EMA 9 + Break → Retest → Hold + Level 2 & Time & Sales).\n"
                    "• ابدأ دائماً بحساب **Demo تجريبي** الأول، ولما تنتقل للحقيقي ممكن تبدأ من 500$ بكميات صغيرة (1 إلى 5 أسهم)."
                ),
            )
            try:
                await interaction.response.send_message(embed=embed, ephemeral=True)
            except Exception:
                await interaction.followup.send(embed=embed, ephemeral=True)

        @self.tree.command(name="mentors", description="جدول مواعيد الـ Mentors الأسبوعي + روابط حجز الـ Workshop (Offline & Online)")
        async def mentors_cmd(interaction: discord.Interaction):
            embed = discord.Embed(
                title="🗓️ جدول الـ Mentors الأسبوعي وروابط حجز الـ Workshop",
                color=0x3B82F6,
                description=(
                    "🎙️ **جدول اللايف تريدينج الأسبوعي في الديسكورد:**\n"
                    "• **Lead Strategist (Member):** كل يوم في الـ After Hours\n"
                    "• **البشمهندس MentorAlpha (MentorAlpha):** سيكولوجية التداول كل يوم أحد\n"
                    "• **MentorGamma (MentorGamma):** كل ثلاثاء وخميس\n"
                    "• **MentorDelta (MentorDelta):** كل اثنين وأربعاء\n"
                    "• **Mentor Epsilon (Mentor Epsilon):** كل سبت وخميس\n\n"
                    "🎯 **الـ Offline Workshop (Upcoming Scheduled Workshop — مرتين في الشهر يوم جمعة):**\n"
                    "• لأعضاء الديسكورد (`4,000 ج = $74.39`): https://whop.com/checkout/plan_TEMPLATE_03\n"
                    "• لغير الأعضاء (`6,000 ج = $111.59`): https://whop.com/checkout/plan_TEMPLATE_04\n\n"
                    "💻 **الـ Online Workshop ($100 — 4 ساعات مع المحاضر أيام الاثنين–الخميس):**\n"
                    "1️⃣ **MentorAlpha (MentorAlpha):** https://whop.com/checkout/plan_TEMPLATE_05\n"
                    "2️⃣ **MentorBeta (MentorBeta ⚡):** https://whop.com/checkout/plan_TEMPLATE_06\n"
                    "3️⃣ **MentorGamma (MentorGamma):** https://whop.com/checkout/plan_TEMPLATE_07\n"
                    "4️⃣ **MentorDelta (MentorDelta):** https://whop.com/checkout/plan_TEMPLATE_08\n"
                    "5️⃣ **Mentor Epsilon (Mentor Epsilon):** https://whop.com/checkout/plan_TEMPLATE_09"
                ),
            )
            try:
                await interaction.response.send_message(embed=embed, ephemeral=True)
            except Exception:
                await interaction.followup.send(embed=embed, ephemeral=True)

        @self.tree.command(name="brokers", description="روابط المنصات وطريقة التسجيل والإيداع (Webull, IBKR, DirectBroker, LicensedBroker, Joystick)")
        async def brokers_cmd(interaction: discord.Interaction):
            embed = discord.Embed(
                title="💻 المنصات المعتمدة من مصر وروابط التسجيل والإيداع",
                color=0x0F766E,
                description=(
                    "📊 **التحليل والشارت:** `Webull` | **التنفيذ:** `Interactive Brokers (IBKR)` أو `DirectBroker` (زيرو عمولة)\n\n"
                    "🔗 **الروابط والشروحات الرسمية (مهم تشوف فيديوهات Member الأول):**\n"
                    "• **شرح التسجيل على IBKR خطوة بخطوة:**\n"
                    "  https://drive.google.com/YOUR_DRIVE_RESOURCE?usp=sharing\n"
                    "• **سيت أب IBKR وتشغيل منصة TWS (MentorGamma فتحي):**\n"
                    "  https://youtu.be/VIDEO_TEMPLATE_06\n"
                    "• **رابط التسجيل في بروكر DirectBroker من مصر (زيرو عمولة):**\n"
                    "  https://your-broker.example.com/\n"
                    "• **فيديو شرح فتح حساب DirectBroker والإيداع (حسام):**\n"
                    "  https://youtu.be/VIDEO_TEMPLATE_07\n"
                    "• **تحميل أبليكيشن LicensedBroker (يفتح Level 2 مجاناً بعد التسجيل):**\n"
                    "  https://your-platform.example.com/\n"
                    "• **تحويل ذراع PlayStation USB إلى Stream Deck للـ Hotkeys:**\n"
                    "  https://youtu.be/VIDEO_TEMPLATE_08\n"
                    "• **شروحات إضافية مهمة:**\n"
                    "  https://youtu.be/VIDEO_TEMPLATE_09\n"
                    "  https://youtu.be/VIDEO_TEMPLATE_10"
                ),
            )
            try:
                await interaction.response.send_message(embed=embed, ephemeral=True)
            except Exception:
                await interaction.followup.send(embed=embed, ephemeral=True)

        # Attach PR Desk slash commands on TraderBot seamlessly when no dedicated 3rd PR token is set
        if not self.cfg.pr_token:
            register_pr_commands(
                self.tree,
                services=self.services,
                cfg=self.cfg,
                deliver_fn=self.deliver_to_trader,
            )

    async def session_btn_logic(self, interaction: discord.Interaction) -> None:
        await send_unique_session_response(interaction, self.services, self.cfg, self.notify_pr_privately)

    async def _handle_slash_support(self, interaction: discord.Interaction, question: str) -> None:
        uid = str(interaction.user.id)
        allowed, wait_sec = self.chat_limiter.check(uid)
        if not allowed:
            await interaction.response.send_message(
                f"⏳ تم إرسال عدة رسائل بسرعة؛ جرب مرة أخرى خلال {wait_sec} ثانية.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        reply = await self.services.ask_trader_support(question, user_id=uid, username=interaction.user.name)
        ticket_code = await self.services.create_or_update_discord_ticket(
            discord_user_id=uid,
            discord_username=interaction.user.name,
            channel_id=str(interaction.channel_id or ""),
            guild_id=str(interaction.guild_id or ""),
            question=question,
            reply=reply,
        )
        chunks = split_discord_text(reply, max_len=3600)
        for idx, chunk in enumerate(chunks):
            title_suffix = f" ({idx + 1}/{len(chunks)})" if len(chunks) > 1 else ""
            embed = discord.Embed(
                title=f"🤖 Tradesejon Support | {ticket_code}{title_suffix}",
                description=chunk,
                color=0x10B981,
            )
            if idx == len(chunks) - 1:
                embed.set_footer(text="Tradesejon • للرد الخاص أو الحجز استخدم الأزرار أدناه")
            send_kw: dict[str, Any] = {
                "embed": embed,
                "ephemeral": True,
                "allowed_mentions": safe_allowed_mentions(),
            }
            if idx == len(chunks) - 1:
                send_kw["view"] = TraderActionView(self.services, self.cfg, self.notify_pr_privately)
            await interaction.followup.send(**send_kw)
        pr_embed = build_pr_ticket_embed(
            ticket_code=ticket_code,
            trader_label=interaction.user.name,
            user_id=uid,
            platform_source="Discord Trader Bot",
            question=question,
            suggested_reply=reply,
        )
        await self.notify_pr_privately(pr_embed, PRTicketActionView(ticket_code))

    async def setup_hook(self) -> None:
        self.add_view(TraderActionView(self.services, self.cfg, self.notify_pr_privately))
        self.add_view(MarhabanWelcomeView(self.services, self.cfg, self.notify_pr_privately))
        self.add_view(WhopTierButtonsView(self.services, self.cfg, self.notify_pr_privately))
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
            logger.warning("Trader slash sync error: %s", redact_secrets(str(exc), self.cfg.known_secrets))

    async def ensure_marhaban_banner(self) -> None:
        """Ensure #marhaban always has an up-to-date pinned welcome banner & form button ready."""
        ch_id = (self.cfg.marhaban_channel_id or "").strip()
        if not ch_id.isdigit():
            return
        try:
            ch = self.get_channel(int(ch_id)) or await self.fetch_channel(int(ch_id))
            if not ch:
                return
            fresh_emb = build_marhaban_welcome_embed(self.cfg)
            fresh_view = MarhabanWelcomeView(self.services, self.cfg, self.notify_pr_privately)
            recent = [m async for m in ch.history(limit=6)]
            for m in recent:
                if m.author and self.user and m.author.id == self.user.id and m.embeds:
                    try:
                        await m.edit(embed=fresh_emb, view=fresh_view)
                    except Exception:
                        pass
                    return
            sent = await ch.send(
                embed=fresh_emb,
                view=fresh_view,
                allowed_mentions=safe_allowed_mentions(),
            )
            try:
                await sent.pin()
            except Exception:
                pass
        except Exception as exc:
            logger.debug("ensure_marhaban_banner skipped: %s", exc)

    async def _suppress_public_join_notifications(self) -> None:
        """Disable Discord system join notifications and clean up public join announcements so nobody sees who joined."""
        for guild in getattr(self, "guilds", []) or []:
            try:
                flags = getattr(guild, "system_channel_flags", None)
                if flags is not None and getattr(flags, "join_notifications", False):
                    flags.join_notifications = False
                    await guild.edit(system_channel_flags=flags, reason="Privacy: hide join notifications")
            except Exception as exc:
                logger.debug("Could not disable system join_notifications on guild %s: %s", getattr(guild, "id", ""), exc)
            try:
                sys_ch = getattr(guild, "system_channel", None)
                if sys_ch is not None:
                    async for msg in sys_ch.history(limit=25):
                        if getattr(msg, "type", None) == discord.MessageType.new_member:
                            try:
                                await msg.delete()
                            except Exception:
                                pass
            except Exception:
                pass

    async def on_ready(self) -> None:
        logger.info("Trader Support Bot READY as %s (id=%s, guilds=%d)", self.user, getattr(self.user, "id", ""), len(self.guilds))
        await self._suppress_public_join_notifications()
        await self.ensure_marhaban_banner()

    async def on_member_join(self, member: discord.Member) -> None:
        """Greet new Discord joins strictly via private DM (never announce publicly who joined)."""
        if member.bot:
            return
        emb = build_marhaban_welcome_embed(self.cfg, getattr(member, "mention", member.name))
        view = MarhabanWelcomeView(self.services, self.cfg, self.notify_pr_privately)
        try:
            await member.send(embed=emb, view=view, allowed_mentions=safe_allowed_mentions())
        except Exception:
            pass
        try:
            sys_ch = getattr(member.guild, "system_channel", None)
            if sys_ch is not None:
                async for msg in sys_ch.history(limit=10):
                    if getattr(msg, "type", None) == discord.MessageType.new_member:
                        try:
                            await msg.delete()
                        except Exception:
                            pass
        except Exception:
            pass

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if not self.cfg.pr_token:
            try:
                handled = await handle_pr_button_interaction(
                    interaction,
                    services=self.services,
                    cfg=self.cfg,
                    deliver_fn=self.deliver_to_trader,
                )
                if handled:
                    return
            except Exception as exc:
                logger.exception("Fallback PR button error: %s", redact_secrets(str(exc), self.cfg.known_secrets))

    async def _send_private_user_reply(self, message: discord.Message, **kwargs) -> None:
        """Deliver user-specific bot replies strictly in private (DM when in shared channels) so only that user sees them."""
        if isinstance(message.channel, discord.DMChannel):
            await message.reply(**kwargs)
            return
        try:
            await message.delete()
        except Exception:
            pass
        try:
            await message.author.send(**kwargs)
        except Exception:
            try:
                await message.channel.send(
                    f"🔒 <@{message.author.id}> تم إرسال ردك الخاص — افتح الرسائل الخاصة (DMs) أو اضغط زر **«ابدأ نموذج Marhaban 📋»** (رسالة خاصة لك وحدك).",
                    delete_after=8,
                    allowed_mentions=safe_allowed_mentions(),
                )
            except Exception:
                pass

    async def on_message(self, message: discord.Message) -> None:
        if getattr(message, "type", None) == discord.MessageType.new_member:
            try:
                await message.delete()
            except Exception:
                pass
            return
        if message.author.bot:
            return
        if message.id in self._dedup_ids:
            return
        self._dedup_ids.add(message.id)
        if len(self._dedup_ids) > 2000:
            self._dedup_ids.clear()

        is_dm = isinstance(message.channel, discord.DMChannel)
        is_mention = bool(self.user and self.user in message.mentions)
        ch_id = str(getattr(message.channel, "id", ""))
        ch_name = str(getattr(message.channel, "name", "")).lower()
        # Keep Nile Academy channels aside; handle strictly Tradesejon channels, DMs, or explicit @mention
        if "nile-academy" in ch_name and not is_mention:
            return
        try:
            await self.services.log_discord_chat_message(
                bot_role="trader",
                direction="inbound",
                message_id=str(getattr(message, "id", "") or ""),
                discord_user_id=str(getattr(message.author, "id", "") or ""),
                discord_username=str(getattr(message.author, "name", "") or ""),
                channel_id=ch_id,
                channel_name="DM" if is_dm else ch_name,
                guild_id=str(getattr(message.guild, "id", "") or ""),
                content=str(getattr(message, "content", "") or ""),
                attachments=list(getattr(message, "attachments", None) or []),
                embeds_count=len(getattr(message, "embeds", None) or []),
            )
        except Exception:
            pass
        is_marhaban_ch = ch_id == str(self.cfg.marhaban_channel_id) or "marhaban" in ch_name
        is_prospect_ch = ch_id == str(self.cfg.prospects_channel_id) or "prospect" in ch_name
        is_workshop_ch = ch_id == str(self.cfg.workshop_channel_id) or "workshop" in ch_name
        is_viproom_ch = ch_id == str(self.cfg.viproom_channel_id) or "vip-room" in ch_name or "viproom" in ch_name
        is_support_channel = (
            ch_id in self.cfg.trader_channel_ids
            or is_marhaban_ch
            or is_prospect_ch
            or is_workshop_ch
            or is_viproom_ch
            or "tradesejon" in ch_name
            or "trader" in ch_name
            or "support" in ch_name
            or "welcome" in ch_name
        )

        # Forward human staff voice notes in #pr-desk directly to the referenced trader ticket
        if ("pr-desk" in ch_name or "pr_desk" in ch_name or ch_id in self.cfg.pr_channel_ids) and not self.cfg.pr_token:
            if await handle_pr_desk_voice_reply(
                message,
                services=self.services,
                cfg=self.cfg,
                deliver_fn=self.deliver_to_trader,
            ):
                return

        # Ignore private PR/CEO channels unless bot explicitly mentioned
        if ("pr-desk" in ch_name or "ceo" in ch_name) and not is_mention:
            return

        if not (is_dm or is_mention or is_support_channel):
            return

        # In #prospects, when a Human Agent / CEO / PR replies directly, avoid AI self-reply loop
        author_role_ids = {str(getattr(r, "id", "")) for r in getattr(message.author, "roles", [])}
        is_human_staff = (
            is_ceo_authorized(message.author, message.channel, message.guild, self.cfg)[0]
            or is_pr_authorized(message.author, message.channel, message.guild, self.cfg)[0]
            or (bool(self.cfg.human_agent_role_id) and self.cfg.human_agent_role_id in author_role_ids)
        )
        if is_prospect_ch and is_human_staff and not is_mention:
            return

        raw_text = (message.content or "").strip()
        if self.user:
            raw_text = raw_text.replace(f"<@{self.user.id}>", "").replace(f"<@!{self.user.id}>", "").strip()

        has_attachments = bool(message.attachments)
        if not raw_text and not has_attachments:
            return

        uid = str(message.author.id)
        allowed, wait_sec = self.chat_limiter.check(uid)
        if not allowed:
            await self._send_private_user_reply(message, content=f"⏳ يرجى الانتظار {wait_sec} ثانية قبل إرسال رسالة أخرى.", allowed_mentions=safe_allowed_mentions())
            return

        user_voice_bytes: bytes | None = None
        user_voice_filename: str = ""
        voice_transcript: str = ""
        vision_reply: str = ""

        if message.attachments:
            m_ok, m_wait = self.media_limiter.check(uid)
            if not m_ok:
                await self._send_private_user_reply(message, content=f"⏳ يرجى الانتظار {m_wait} ثانية قبل إرسال ملف آخر.", allowed_mentions=safe_allowed_mentions())
                return
            att = message.attachments[0]
            data_bytes, mime, status = await self.services.safe_download_discord_attachment(att)
            if status != "ok" or data_bytes is None:
                await self._send_private_user_reply(message, content="⚠️ تعذر قبول المرفق (الحد الأقصى 25MB — مسموح بكل الصور والفيديوهات والصوت والملفات ما عدا الملفات التنفيذية).", allowed_mentions=safe_allowed_mentions())
                return
            if mime.startswith("audio/"):
                voice_transcript = await self.services.transcribe_audio_groq(data_bytes, att.filename)
                user_voice_bytes = data_bytes
                user_voice_filename = att.filename or "trader_voice_note.ogg"
                if voice_transcript:
                    raw_text = f"{raw_text}\n{voice_transcript}".strip()
            elif mime.startswith("image/"):
                vision_reply = await self.services.analyze_image_gemini(data_bytes, mime, raw_text)
            else:
                att_note = f"[مرفق مستلم: {sanitize_untrusted_text(att.filename or 'file', 80)} ({mime})]"
                raw_text = f"{raw_text}\n{att_note}".strip()

        # Direct Mail + Phone detection in Discord DM, #marhaban, or support message -> Strict Whop OTP Gate / Paid Check + Omnichannel Sync!
        extracted_contact = extract_email_and_phone_from_text(raw_text) if raw_text else None
        if extracted_contact:
            mail_val, phone_val, derived_name = extracted_contact
            ok_reg, msg_reg, confirm_embed, qr_bytes, saved = await complete_free_live_registration(
                services=self.services,
                cfg=self.cfg,
                pr_notify_fn=self.notify_pr_privately,
                discord_user=message.author,
                channel_id=ch_id,
                guild_id=str(getattr(message.guild, "id", "") or ""),
                raw_full_name=derived_name or getattr(message.author, "display_name", "") or message.author.name,
                raw_phone=phone_val,
                raw_email=mail_val,
                raw_challenge=raw_text,
                platform_label="Discord Direct Chat (Mail+Number)",
            )
            if ok_reg and confirm_embed is not None and qr_bytes is not None:
                otp_ok = bool(saved.get("whop_otp_verified") or saved.get("already_paid"))
                verify_view: discord.ui.View = (
                    TraderActionView(self.services, self.cfg, self.notify_pr_privately)
                    if saved.get("already_paid")
                    else WhopTierButtonsView(
                        self.services,
                        self.cfg,
                        self.notify_pr_privately,
                        free_otp_url=saved.get("free_otp_url", ""),
                        workshop_url=saved.get("whop_checkout_url", ""),
                        viproom_url=saved.get("viproom_checkout_url", ""),
                        in_community=bool(saved.get("in_community", True)),
                        otp_verified=otp_ok,
                    )
                )
                qr_fname = "tradesejon_workshop_qr.png" if saved.get("already_paid") else "tradesejon_whop_qr.png"
                reg_files = [discord.File(io.BytesIO(qr_bytes), filename=qr_fname)]
                if voice_transcript:
                    v_msg = (
                        f"أهلاً بك يا {derived_name or 'بطل'}، تم تأكيد حسابك ودخولك Prospects Bot بنجاح."
                        if otp_ok
                        else f"أهلاً بك يا {derived_name or 'بطل'}، افتح رابط Whop وأدخل بريدك لتأكيد كود الـ OTP ودخول Prospects Bot فوراً."
                    )
                    reg_aud = await self.services.synthesize_masri_voice(v_msg)
                    if reg_aud:
                        reg_files.append(discord.File(io.BytesIO(reg_aud), filename="tradesejon_voice_confirm.mp3"))
                await self._send_private_user_reply(
                    message,
                    embed=confirm_embed,
                    files=reg_files,
                    view=verify_view,
                    allowed_mentions=safe_allowed_mentions(),
                )
                return

        # Reject invalid/malformed Mail or Number (or bogus non-question text in #marhaban) typed directly in chat with a bright red actionable error
        has_bad_contact, bad_errs, bad_em, bad_ph = detect_invalid_contact_attempt(raw_text) if (not extracted_contact and raw_text and (is_dm or is_marhaban_ch)) else (False, [], "", "")
        _lower_msg = raw_text.lower()
        _looks_like_question_or_greeting = (
            "?" in raw_text
            or "؟" in raw_text
            or _lower_msg in ("start", "/start", "hi", "hello", "marhaban", "مرحبا", "اهلا", "أهلا", "السلام عليكم")
            or any(k in _lower_msg for k in ("سعر", "بكام", "كام", "ورشة", "workshop", "whop", "zoom", "live", "mentor", "محاضر", "ibkr", "DirectBroker", "webull", "LicensedBroker", "form", "register", "سجل", "نموذج", "اشتراك", "فيديو", "فيديوهات", "مساعدة", "دعم", "start"))
        )
        if not has_bad_contact and is_marhaban_ch and not extracted_contact and not extract_email_only_from_text(raw_text) and raw_text and not _looks_like_question_or_greeting:
            has_bad_contact = True
            bad_errs = [
                "البريد الإلكتروني (Mail) غير صحيح — يجب إدخال بريد إلكتروني صحيح مثل name@gmail.com.",
                "رقم الهاتف / الواتساب (Number) غير صحيح — يجب إدخال رقم هاتف صحيح مثل +201012345678.",
            ]
        if has_bad_contact:
            bad_embed = discord.Embed(
                title="❌ البيانات المدخلة غير صحيحة — يرجى التصحيح",
                description="🔴 نقبل فقط **بريد إلكتروني صحيح (Mail)** و **رقم هاتف صحيح (Number)**:",
                color=0xEF4444,
            )
            if bad_em or any("البريد" in e for e in bad_errs):
                bad_embed.add_field(
                    name="🔴 البريد الإلكتروني (Mail) غير صحيح",
                    value=f"```diff\n- المدخل: {bad_em or sanitize_untrusted_text(raw_text, 60)}\n+ الصيغة الصحيحة: name@gmail.com\n```",
                    inline=False,
                )
            if bad_ph or any("الهاتف" in e for e in bad_errs):
                bad_embed.add_field(
                    name="🔴 رقم الهاتف / الواتساب (Number) غير صحيح",
                    value=f"```diff\n- المدخل: {bad_ph or sanitize_untrusted_text(raw_text, 40)}\n+ الصيغة الصحيحة: +201012345678 أو 01012345678\n```",
                    inline=False,
                )
            await self._send_private_user_reply(
                message,
                embed=bad_embed,
                view=RetryFormView(
                    self.services,
                    self.cfg,
                    self.notify_pr_privately,
                    prev_email=bad_em,
                    prev_phone=bad_ph,
                    email_error=bool(bad_em or any("البريد" in e for e in bad_errs)),
                    phone_error=bool(bad_ph or any("الهاتف" in e for e in bad_errs)),
                ),
                allowed_mentions=safe_allowed_mentions(),
            )
            return

        # Email-only Free Whop Test Flow: user enters ONLY their email -> receives Whop OTP link; only opens #prospects AFTER Whop OTP confirmation!
        email_only = extract_email_only_from_text(raw_text) if raw_text else None
        if email_only:
            mail_val, derived_name = email_only
            saved_otp = await self.services.save_email_for_whop_otp(
                discord_user_id=uid,
                discord_username=message.author.name,
                email_addr=mail_val,
                channel_id=ch_id,
                full_name=derived_name or getattr(message.author, "display_name", "") or message.author.name,
            )
            free_otp_url = saved_otp.get("free_otp_url") or self.cfg.whop_checkout_url or "https://whop.com/checkout/plan_TEMPLATE_01"
            otp_done = bool(saved_otp.get("whop_otp_verified") or saved_otp.get("already_paid"))
            qr_bytes_otp = self.services.generate_qr_png(free_otp_url if not otp_done else saved_otp.get("whop_checkout_url", free_otp_url))
            if otp_done:
                otp_emb = discord.Embed(
                    title="🎉 تم تأكيد Whop OTP بنجاح — أهلاً بك في Prospects Bot!",
                    color=0x10B981,
                    description=(
                        f"✅ تم التحقق من بريدك (`{mail_val}`) على Whop وإدخالك فوراً إلى **Prospects Bot (<#{self.cfg.prospects_channel_id}>)**!\n\n"
                        "💳 **الباقات الرسمية على Whop:**\n"
                        "• **Discord VIP Live + Scanner:** **`$45 / شهر`** — https://whop.com/Tradesejon/exp_TEMPLATE_VIP"
                        "• **Offline Workshop:** **`4,000 EGP ($74.39)`** للأعضاء | **`6,000 EGP ($111.59)`** لغير الأعضاء\n"
                        "• **Online Workshop الخاصة (4 ساعات):** **`$100`**"
                    ),
                )
            else:
                otp_emb = discord.Embed(
                    title="🔐 تم تسجيل بريدك لتجربة Whop المجانية ($0.00) — أكد كود Whop OTP أولاً!",
                    color=0xF59E0B,
                    description=(
                        f"📧 **البريد المسجل:** `{mail_val}`\n"
                        f"⏳ **الحالة الحالية:** `pending_whop_otp` *(لن تفتح قناة Prospects Bot <#{self.cfg.prospects_channel_id}> إلا بعد تأكيد كود الـ OTP في Whop)*\n\n"
                        f"🎁 **افتح رابط Whop أدناه ($0.00 — بدون بطاقة):**\n🔗 {free_otp_url}\n\n"
                        "1️⃣ أدخل بريدك الإلكتروني فقط على صفحة Whop.\n"
                        "2️⃣ سيصلك كود تحقق (**Whop OTP**) على إيميلك.\n"
                        "3️⃣ **فوراً بعد تأكيد كود الـ OTP على Whop** سيتم إدخالك مباشرة إلى **Prospects Bot (`#prospects`)**!"
                    ),
                )
            otp_view = WhopTierButtonsView(
                self.services,
                self.cfg,
                self.notify_pr_privately,
                free_otp_url=free_otp_url,
                workshop_url=saved_otp.get("whop_checkout_url", ""),
                viproom_url=saved_otp.get("viproom_checkout_url", ""),
                in_community=bool(saved_otp.get("in_community", True)),
                otp_verified=otp_done,
            )
            await self._send_private_user_reply(
                message,
                embed=otp_emb,
                file=discord.File(io.BytesIO(qr_bytes_otp), filename="tradesejon_whop_qr.png"),
                view=otp_view,
                allowed_mentions=safe_allowed_mentions(),
            )
            return

        # Automatically prompt Marhaban Entry Form (Mail + Number) on first interaction if no question asked
        funnel = await self.services.get_discord_user_funnel_state(uid)
        low_txt = raw_text.lower()
        if not funnel.get("form_validated") and not voice_transcript and (
            is_dm
            or is_marhaban_ch
            or low_txt in ("", "start", "/start", "hi", "hello", "marhaban", "مرحبا", "اهلا", "أهلا", "السلام عليكم")
            or any(k in low_txt for k in ("form", "register", "join", "whop", "zoom", "live", "discord", "سجل", "فورم", "نموذج", "اشتراك", "ورشة", "لايف"))
        ):
            emb_welcome = build_marhaban_welcome_embed(self.cfg, f"<@{uid}>" if uid.isdigit() else message.author.name)
            await self._send_private_user_reply(
                message,
                embed=emb_welcome,
                view=MarhabanWelcomeView(self.services, self.cfg, self.notify_pr_privately),
                allowed_mentions=safe_allowed_mentions(),
            )
            return

        effective_q = raw_text

        if vision_reply:
            reply_text = vision_reply
        else:
            reply_text = await self.services.ask_trader_support(effective_q or "مرحباً", user_id=uid, username=message.author.name)

        ticket_code = await self.services.create_or_update_discord_ticket(
            discord_user_id=uid,
            discord_username=message.author.name,
            channel_id=ch_id,
            guild_id=str(getattr(message.guild, "id", "") or ""),
            question=effective_q or "[Attachment]",
            reply=reply_text,
        )

        persona_title = (
            "🤖 Prospect Bot AI + Human Desk"
            if is_prospect_ch
            else ("🌟 V tradesejon VIP Concierge" if is_viproom_ch else ("🚀 Workshop Mentor Bot" if is_workshop_ch else "🤖 Tradesejon Support"))
        )
        files = []
        if voice_transcript or any(w in effective_q.lower() for w in ("فويس", "صوت", "voice")):
            aud = await self.services.synthesize_masri_voice(reply_text)
            if aud:
                files.append(discord.File(io.BytesIO(aud), filename="tradesejon_reply.mp3"))

        reply_view: discord.ui.View = (
            WhopTierButtonsView(
                self.services,
                self.cfg,
                self.notify_pr_privately,
                free_otp_url=funnel.get("free_otp_url", ""),
                workshop_url=funnel.get("checkout_url", ""),
                viproom_url=funnel.get("viproom_checkout_url", ""),
                in_community=bool(funnel.get("in_community", True)),
                otp_verified=bool(funnel.get("whop_otp_verified")),
            )
            if is_prospect_ch and not funnel.get("paid")
            else TraderActionView(self.services, self.cfg, self.notify_pr_privately)
        )
        chunks = split_discord_text(reply_text, max_len=3600)
        for idx, chunk in enumerate(chunks):
            title_suffix = f" ({idx + 1}/{len(chunks)})" if len(chunks) > 1 else ""
            embed = discord.Embed(
                title=f"{persona_title} | {ticket_code}{title_suffix}",
                description=chunk,
                color=0x3B82F6 if is_prospect_ch else 0x10B981,
            )
            if idx == 0 and voice_transcript:
                embed.add_field(name="🎙️ تفريغ رسالتك الصوتية", value=safe_discord_text(voice_transcript, 600), inline=False)
            if idx == len(chunks) - 1 and is_prospect_ch:
                embed.set_footer(text="AI Agent replied above • Human Agent team alerted simultaneously to assist you!")
            send_kw: dict[str, Any] = {
                "embed": embed,
                "allowed_mentions": safe_allowed_mentions(),
            }
            if idx == len(chunks) - 1:
                send_kw["view"] = reply_view
                send_kw["files"] = files
            await self._send_private_user_reply(message, **send_kw)
        platform_src = (
            f"#prospects (AI + Human Agent Dual Response | Status: {funnel.get('status', 'prospect')})"
            if is_prospect_ch
            else ("Discord Voice Message" if voice_transcript else ("Discord Trader Channel" if not is_dm else "Discord Trader DM"))
        )
        pr_embed = build_pr_ticket_embed(
            ticket_code=ticket_code,
            trader_label=f"{getattr(message.author, 'display_name', message.author.name)} (@{message.author.name})",
            user_id=uid,
            platform_source=platform_src,
            question=effective_q or "[Media Attachment]",
            suggested_reply=reply_text,
            email=funnel.get("email", ""),
            phone=funnel.get("phone", ""),
            lead_score="PROSPECT_VOICE_INQUIRY" if voice_transcript else ("PROSPECT_NEEDS_HUMAN_FOLLOWUP" if is_prospect_ch else "VIP_SUPPORT"),
        )
        pr_files: list[discord.File] = []
        if user_voice_bytes:
            pr_files.append(discord.File(io.BytesIO(user_voice_bytes), filename=user_voice_filename or "trader_voice_note.ogg"))
        if pr_files:
            try:
                await self.notify_pr_privately(pr_embed, PRTicketActionView(ticket_code), files=pr_files)
            except TypeError:
                await self.notify_pr_privately(pr_embed, PRTicketActionView(ticket_code))
        else:
            await self.notify_pr_privately(pr_embed, PRTicketActionView(ticket_code))


async def send_unique_session_response(
    interaction: discord.Interaction,
    services: SharedServices,
    cfg: DiscordConfig,
    pr_notify_fn: Callable[[discord.Embed, PRTicketActionView | None], Awaitable[None]],
    *,
    already_deferred: bool = False,
) -> None:
    """Issue a strictly unique Discord Live Workshop link for verified Whop payers (gating strangers & unpaid prospects)."""
    if not already_deferred:
        await interaction.response.defer(ephemeral=True)
    uid = str(interaction.user.id)
    is_ceo, _ = is_ceo_authorized(interaction.user, interaction.channel, interaction.guild, cfg)
    funnel = await services.get_discord_user_funnel_state(uid)
    if funnel.get("form_validated") and not funnel.get("paid") and funnel.get("email"):
        live = await services.verify_whop_payment_live_for_email(funnel["email"])
        if live.get("paid"):
            await services.fulfill_whop_payment_for_discord(
                email_addr=funnel["email"],
                phone=funnel.get("phone", ""),
                discord_user_id=uid,
                whop_payment_id=str(live.get("whop_payment_id") or ""),
                tier=str(live.get("tier") or "workshop_45"),
                plan_id=str(live.get("plan_id") or ""),
                amount_usd=float(live.get("amount_usd") or 45),
            )
            funnel = await services.get_discord_user_funnel_state(uid)
    sess = await services.get_live_session_info()

    if (funnel.get("paid") and funnel.get("whop_otp_verified")) or is_ceo:
        uname = funnel.get("full_name") or getattr(interaction.user, "display_name", None) or "Tradesejon VIP"
        uemail = funnel.get("email") or (f"ceo_{uid}@tradesejon.local" if is_ceo else "")
        zres = await services.create_unique_zoom_link_for_payer(
            discord_user_id=uid,
            email_addr=uemail,
            full_name=uname,
        )
        personal_live = str(zres.get("discord_live_url") or zres.get("unique_zoom_url") or "")
        if not personal_live:
            await interaction.followup.send(
                "⚠️ تعذر إصدار رابط Discord Live حالياً؛ تواصل مع الدعم.",
                ephemeral=True,
            )
            return
        tier_label = funnel.get("tier") or "workshop_45"
        embed = discord.Embed(
            title=f"🎙️ رابط Workshop Live الحصري بك — {sess['session_name']}",
            color=0x10B981,
            description=(
                f"⏰ **الموعد:** {sess['session_time']} | **الباقة:** `{tier_label}`\n"
                f"🔗 **رابط Workshop المباشر الحصري بك (لا يُشارك):**\n{personal_live}\n\n"
                f"💬 **قنواتك المفعلة:** <#{cfg.workshop_channel_id}>"
                + (f" + <#{cfg.viproom_channel_id}>" if tier_label == "viproom_75" else "")
                + "\n⏳ **صلاحية الرابط:** لمرة واحدة فقط (`1-Time Use`) ولمدة **15 دقيقة فقط** — بعد 15 دقيقة أو بعد الاستخدام يتغير الرابط تلقائياً.\n"
                + "🛡️ *(هذا الرابط شخصي ومرتبط بحسابك — لا تشاركه وإلا يُلغى تلقائياً)*"
            ),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    if funnel.get("form_validated"):
        in_comm = bool(funnel.get("in_community", True))
        otp_done = bool(funnel.get("whop_otp_verified"))
        err_emb = discord.Embed(
            title=(
                "❌ يتطلب تأكيد الدفع على Whop للوصول إلى Workshop"
                if otp_done
                else "❌ يتطلب تأكيد كود Whop OTP أولاً للدخول إلى Prospects Bot"
            ),
            color=0xEF4444,
            description=(
                (
                    f"🔴 **حالتك الحالية: Prospect (`#prospects`)**\n"
                    f"لا يمكن إصدار رابط الورشة المباشر أو دخول القنوات إلا بعد إتمام باقة على Whop:\n"
                    f"• **Discord VIP Live + Scanner:** **`$45 / شهر`**\n"
                    f"• **Offline Workshop (4 ساعات):** **`{'4,000 EGP ($74.39) لعضو السيرفر' if in_comm else '6,000 EGP ($111.59) لغير العضو'}`**\n"
                    f"• **Online Workshop الخاصة (4 ساعات):** **`$100`**\n\n"
                    "يرجى إتمام الاشتراك أدناه ثم الضغط على **«✅ تم الدفع — افتح Workshop الآن»**:"
                )
                if otp_done
                else (
                    "🔴 **حالتك الحالية: بانتظار تأكيد كود Whop OTP (`pending_whop_otp`)**\n"
                    "أدخل بريدك في رابط Whop المجاني (`$0.00`) بالأسفل وأكد كود الـ OTP ليتم إدخالك فوراً إلى **Prospects Bot (`#prospects`)**."
                )
            ),
        )
        err_emb.set_footer(text="Tradesejon Zero-Trust OTP & Payment Gate")
        err_msg_prospect = (
            "⏳ **حالتك الحالية: Prospect** — رابط الورشة المباشر يفتح تلقائياً فور إتمام اشتراك "
            "**Whop (`$45/شهر VIP` أو Offline `4,000/6,000 EGP` أو Online `$100`)**."
            if otp_done
            else "⏳ **بانتظار تأكيد كود Whop OTP ($0.00 بالإيميل فقط)** — أدخل بريدك في Whop وأكد الـ OTP ثم اضغط تحقق لدخول **Prospects Bot** فوراً:"
        )
        await interaction.followup.send(
            err_msg_prospect,
            embed=err_emb,
            ephemeral=True,
            view=WhopTierButtonsView(
                services,
                cfg,
                pr_notify_fn,
                free_otp_url=funnel.get("free_otp_url", ""),
                workshop_url=funnel.get("checkout_url", ""),
                viproom_url=funnel.get("viproom_checkout_url", ""),
                in_community=in_comm,
                otp_verified=otp_done,
            ),
        )
        return

    err_emb = discord.Embed(
        title="❌ مطلوب إدخال الإيميل وتأكيد Whop OTP أولاً",
        color=0xEF4444,
        description=(
            "🔴 **أدخل بريدك الإلكتروني أولاً** عبر **نموذج Marhaban** (أو أرسل إيميلك في الشات) للحصول على رابط **Whop ($0.00 Email + OTP)**.\n"
            "فوراً بعد تأكيد كود الـ OTP على Whop يتم إدخالك إلى **Prospects Bot (`#prospects`)**، وبعد إتمام باقتك تفتح قناة **Workshop Live** مباشرة."
        ),
    )
    err_emb.set_footer(text="Tradesejon Verification Engine")
    err_unreg = "🔒 **أدخل بريدك الإلكتروني أولاً** عبر **نموذج Marhaban** (أو اكتب بريدك في الشات) لاستلام كود Whop OTP ودخول **Prospects Bot** فوراً بعد التأكيد."
    await interaction.followup.send(
        err_unreg,
        embed=err_emb,
        ephemeral=True,
        view=MarhabanWelcomeView(services, cfg, pr_notify_fn),
    )
