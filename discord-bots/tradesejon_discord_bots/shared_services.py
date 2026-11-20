"""Shared async data, AI, multimodal, email, and security audit services."""

from __future__ import annotations

import asyncio
import base64
import email.utils
import io
import json
import logging
import re
import smtplib
import ssl
import tempfile
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from html import escape as html_escape
from pathlib import Path
from typing import Any

import aiohttp
import edge_tts
import psycopg2
import pymongo
import qrcode

from .config import DiscordConfig
from .crm_sync import (
    AgentReachClient,
    DiscordRoleAndPersonaClient,
    OmnichannelSyncEngine,
    build_tier_defs,
    compute_next_status,
    resolve_tier_from_payment,
)
from .security import (
    MAX_ATTACHMENT_BYTES,
    build_secure_whop_checkout_url,
    is_allowed_attachment,
    is_protected_client_live_target,
    is_protected_client_discord_guild,
    redact_secrets,
    sanitize_untrusted_text,
    sign_checkout_ref,
    strip_valuable_files_for_prospect,
    validate_discord_cdn_url,
    wrap_untrusted_prompt,
)

logger = logging.getLogger("tradesejon.discord.services")


class SecretRedactingFilter(logging.Filter):
    """Ensure no logger record ever leaks tokens, keys, or DB passwords."""

    def __init__(self, extra_secrets: tuple[str, ...] = ()):
        super().__init__()
        self.extra_secrets = extra_secrets

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_secrets(record.msg, self.extra_secrets)
        if record.args:
            record.args = tuple(
                redact_secrets(str(a), self.extra_secrets) if isinstance(a, str) else a
                for a in (record.args if isinstance(record.args, tuple) else (record.args,))
            )
        return True


def setup_secure_logging(cfg: DiscordConfig) -> None:
    """Attach redacting filter to root logging."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    redactor = SecretRedactingFilter(cfg.known_secrets)
    for handler in logging.getLogger().handlers:
        handler.addFilter(redactor)


class SharedServices:
    """Thread-safe / async service container shared across all Discord bots."""

    def __init__(self, cfg: DiscordConfig):
        self.cfg = cfg
        self._local_client: pymongo.MongoClient | None = None
        self._atlas_client: pymongo.MongoClient | None = None
        self._atlas_down_until: float = 0.0
        self._http: aiohttp.ClientSession | None = None
        self._ticket_lock = asyncio.Lock()
        self.agent_reach = AgentReachClient(cfg)
        self.discord_roles = DiscordRoleAndPersonaClient(cfg)
        self.omnichannel = OmnichannelSyncEngine(cfg)
        self._kb_cache_mtime: float = -1.0
        self._kb_cache_text: str = ""

    async def get_http(self) -> aiohttp.ClientSession:
        if self._http is None or self._http.closed:
            timeout = aiohttp.ClientTimeout(total=35)
            self._http = aiohttp.ClientSession(timeout=timeout, trust_env=False)
        return self._http

    async def close(self) -> None:
        if self._http and not self._http.closed:
            await self._http.close()

    def get_local_db(self):
        if not self.cfg.mongo_local_uri:
            return None
        try:
            if self._local_client is None:
                self._local_client = pymongo.MongoClient(
                    self.cfg.mongo_local_uri,
                    serverSelectionTimeoutMS=900,
                    connectTimeoutMS=900,
                    socketTimeoutMS=2500,
                )
            return self._local_client["tradesejon"]
        except Exception as exc:
            logger.warning("Local Mongo unavailable: %s", redact_secrets(str(exc), self.cfg.known_secrets))
            return None

    def get_atlas_db(self):
        if not self.cfg.mongo_atlas_uri:
            return None
        if time.monotonic() < getattr(self, "_atlas_down_until", 0.0):
            return None
        try:
            if self._atlas_client is None:
                self._atlas_client = pymongo.MongoClient(
                    self.cfg.mongo_atlas_uri,
                    serverSelectionTimeoutMS=5000,
                    connectTimeoutMS=5000,
                    socketTimeoutMS=8000,
                )
            return self._atlas_client["automation_hub_dev"]
        except Exception as exc:
            self._atlas_down_until = time.monotonic() + 30.0
            logger.warning("Atlas Mongo unavailable: %s", redact_secrets(str(exc), self.cfg.known_secrets))
            return None

    def _dual_dbs(self):
        dbs = []
        ldb = self.get_local_db()
        if ldb is not None:
            dbs.append(("local", ldb))
        adb = self.get_atlas_db()
        if adb is not None:
            dbs.append(("atlas", adb))
        return dbs

    async def log_security_audit(
        self,
        *,
        bot_role: str,
        action: str,
        verdict: str,
        actor_id: str,
        actor_name: str,
        guild_id: str = "",
        channel_id: str = "",
        reason: str = "",
        summary: str = "",
    ) -> None:
        """Persist redacted security audit events to MongoDB."""
        doc = {
            "platform": "discord",
            "bot_role": bot_role,
            "action": action,
            "verdict": verdict,
            "actor_id": str(actor_id),
            "actor_name": sanitize_untrusted_text(actor_name, max_len=80),
            "guild_id": str(guild_id),
            "channel_id": str(channel_id),
            "reason": reason,
            "summary": redact_secrets(sanitize_untrusted_text(summary, max_len=300), self.cfg.known_secrets),
            "created_at": datetime.now(timezone.utc),
        }

        def _write():
            for _, db in self._dual_dbs():
                try:
                    db["security_audit_logs"].insert_one(dict(doc))
                except Exception:
                    pass

        await asyncio.to_thread(_write)

    async def get_live_session_info(self) -> dict[str, str]:
        """Compute active Cairo VIP schedule merged with live MongoDB campaign overrides."""
        def _calc():
            try:
                from zoneinfo import ZoneInfo
                now_cairo = datetime.now(ZoneInfo("Africa/Cairo"))
            except Exception:
                now_cairo = datetime.now(timezone(timedelta(hours=2)))
            mins = now_cairo.hour * 60 + now_cairo.minute
            if mins < 11 * 60:
                slot_time = "اليوم الساعة 11:00 صباحاً (بتوقيت القاهرة)"
                slot_name = "جلسة التداول الصباحية — London Open"
            elif mins < 15 * 60 + 30:
                slot_time = "اليوم الساعة 03:30 عصراً (بتوقيت القاهرة)"
                slot_name = "جلسة التداول الحية — New York Open"
            elif mins < 20 * 60:
                slot_time = "اليوم الساعة 08:00 مساءً (بتوقيت القاهرة)"
                slot_name = "جلسة المساء VIP وتحليلات الإغلاق"
            else:
                slot_time = "غداً الساعة 11:00 صباحاً (بتوقيت القاهرة)"
                slot_name = "جلسة التداول الصباحية — London Open"

            zoom_url = self.cfg.default_zoom_url
            zoom_id = self.cfg.default_zoom_id
            zoom_pass = self.cfg.default_zoom_passcode
            whop_url = self.cfg.whop_checkout_url

            for _, db in self._dual_dbs():
                try:
                    camp = db["campaigns"].find_one({"_id": "workshop-poc"})
                    if camp:
                        cand_z = camp.get("zoomUrl") or camp.get("zoom_url") or ""
                        if cand_z and "88392145021" not in cand_z and "88392142026" not in cand_z:
                            zoom_url = cand_z
                        cand_id = camp.get("zoomMeetingId") or camp.get("meeting_id") or ""
                        if cand_id and "883 9214" not in str(cand_id):
                            zoom_id = cand_id
                        cand_p = camp.get("zoomPasscode") or camp.get("passcode") or ""
                        if cand_p and cand_p != "TradesejonVIP2026":
                            zoom_pass = cand_p
                        cand_w = camp.get("whopUrl") or ""
                        if cand_w and "plan_TEMPLATE_02" not in cand_w:
                            whop_url = cand_w
                        if camp.get("startAt"):
                            slot_time = str(camp.get("startAt"))
                        if camp.get("title"):
                            slot_name = str(camp.get("title"))
                    z_cfg = db["aios_config"].find_one({"key": "zoom_config"})
                    if isinstance(z_cfg, dict):
                        cand_z2 = z_cfg.get("zoom_url") or ""
                        if cand_z2 and "88392145021" not in cand_z2 and "88392142026" not in cand_z2:
                            zoom_url = cand_z2
                        cand_id2 = z_cfg.get("zoom_id") or ""
                        if cand_id2 and "883 9214" not in str(cand_id2):
                            zoom_id = cand_id2
                        cand_p2 = z_cfg.get("zoom_pass") or ""
                        if cand_p2 and cand_p2 != "TradesejonVIP2026":
                            zoom_pass = cand_p2
                        if z_cfg.get("whop_url") and "plan_TEMPLATE_02" not in str(z_cfg.get("whop_url")):
                            whop_url = str(z_cfg["whop_url"])
                        if z_cfg.get("session_time"):
                            slot_time = str(z_cfg["session_time"])
                    st_cfg = db["aios_config"].find_one({"key": "session_time"})
                    if isinstance(st_cfg, dict) and (st_cfg.get("value") or st_cfg.get("session_time")):
                        slot_time = str(st_cfg.get("value") or st_cfg.get("session_time"))
                    if camp or z_cfg or st_cfg:
                        break
                except Exception:
                    pass
            return {
                "session_name": slot_name,
                "session_time": slot_time,
                "zoom_url": zoom_url,
                "zoom_id": zoom_id,
                "zoom_pass": zoom_pass,
                "whop_url": whop_url,
                "discord_live_channel_id": self.cfg.discord_live_channel_id,
            }

        return await asyncio.to_thread(_calc)

    async def get_market_prices(self) -> dict[str, Any]:
        """Fetch live 12-market feed from AIOS local service with instant public spot fallback."""
        sess = await self.get_http()
        for attempt in range(2):
            try:
                async with sess.get(f"{self.cfg.aios_base_url}/api/market-prices", timeout=aiohttp.ClientTimeout(total=3.5)) as r:
                    if r.status == 200:
                        data = await r.json()
                        prices = data.get("prices", {})
                        if prices:
                            return prices
            except Exception as exc:
                logger.warning("Market prices fetch failed (attempt %d): %s", attempt + 1, exc)
                if attempt == 0:
                    await asyncio.sleep(0.35)
        # Direct resilient public spot fallback so /prices never stalls when AIOS reloads
        fallback: dict[str, Any] = {}
        try:
            async with sess.get(
                "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana,pax-gold&vs_currencies=usd",
                timeout=aiohttp.ClientTimeout(total=4),
            ) as r:
                if r.status == 200:
                    cg = await r.json()
                    if "pax-gold" in cg:
                        fallback["XAUUSD"] = {"symbol": "XAU/USD", "unit": "$", "price": cg["pax-gold"].get("usd")}
                    if "bitcoin" in cg:
                        fallback["BTCUSD"] = {"symbol": "BTC/USD", "unit": "$", "price": cg["bitcoin"].get("usd")}
                    if "ethereum" in cg:
                        fallback["ETHUSD"] = {"symbol": "ETH/USD", "unit": "$", "price": cg["ethereum"].get("usd")}
                    if "solana" in cg:
                        fallback["SOLUSD"] = {"symbol": "SOL/USD", "unit": "$", "price": cg["solana"].get("usd")}
        except Exception:
            pass
        return fallback

    def generate_qr_png(self, payload: str) -> bytes:
        qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=4)
        qr.add_data(payload)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    async def safe_download_discord_attachment(self, attachment) -> tuple[bytes | None, str, str]:
        """Download attachment only after SSRF URL, MIME, and max-byte validation."""
        url = getattr(attachment, "url", "")
        ct = getattr(attachment, "content_type", None) or "application/octet-stream"
        size = int(getattr(attachment, "size", 0) or 0)
        fname = sanitize_untrusted_text(getattr(attachment, "filename", "file.bin"), max_len=80)

        if not validate_discord_cdn_url(url):
            logger.warning("Blocked non-Discord-CDN attachment URL")
            return None, ct, "blocked_url_host"
        if not is_allowed_attachment(ct, size, fname):
            return None, ct, "disallowed_type_or_size"

        sess = await self.get_http()
        try:
            async with sess.get(url, allow_redirects=False, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status != 200:
                    return None, ct, f"http_{r.status}"
                chunks = []
                total = 0
                async for chunk in r.content.iter_chunked(65536):
                    total += len(chunk)
                    if total > MAX_ATTACHMENT_BYTES:
                        return None, ct, "exceeded_max_bytes"
                    chunks.append(chunk)
                clean_ct = ct.split(";")[0].strip().lower()
                if fname.lower().endswith((".ogg", ".opus")) or clean_ct in ("application/ogg", "application/x-ogg"):
                    clean_ct = "audio/ogg"
                elif fname.lower().endswith((".mp3",)):
                    clean_ct = "audio/mpeg"
                elif fname.lower().endswith((".wav",)):
                    clean_ct = "audio/wav"
                return b"".join(chunks), clean_ct, "ok"
        except Exception as exc:
            return None, ct, str(exc)

    async def search_postgres_kb(self, query: str, limit: int = 3) -> list[dict[str, str]]:
        """Query existing Postgres KB chunks safely using parameterized SQL."""
        if not self.cfg.pg_dsn or not query.strip():
            return []

        def _query():
            terms = [t for t in re.split(r"\s+", sanitize_untrusted_text(query, 200)) if len(t) >= 2][:5]
            if not terms:
                return []
            conn = None
            try:
                conn = psycopg2.connect(self.cfg.pg_dsn, connect_timeout=3)
                cur = conn.cursor()
                like_patterns = [f"%{t}%" for t in terms]
                sql = (
                    "SELECT ks.title, kc.content FROM kb_chunks kc "
                    "LEFT JOIN kb_sources ks ON kc.source_id = ks.id "
                    "WHERE " + " OR ".join(["kc.content ILIKE %s"] * len(like_patterns)) +
                    " LIMIT %s"
                )
                cur.execute(sql, (*like_patterns, limit))
                rows = cur.fetchall()
                return [{"title": str(r[0] or "KB"), "content": str(r[1] or "")[:500]} for r in rows]
            except Exception:
                return []
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

        return await asyncio.to_thread(_query)

    async def call_groq_chat(self, system_prompt: str, user_prompt: str, *, json_mode: bool = False) -> str:
        """Call active Groq models with graceful fallback."""
        if not self.cfg.groq_api_key:
            return ""
        sess = await self.get_http()
        models = ("openai/gpt-oss-120b", "llama-3.3-70b-versatile", "openai/gpt-oss-20b", "llama-3.1-8b-instant")
        for model in models:
            try:
                payload: dict[str, Any] = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.25,
                    "max_tokens": 1200,
                }
                if json_mode:
                    payload["response_format"] = {"type": "json_object"}
                async with sess.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.cfg.groq_api_key}", "Content-Type": "application/json"},
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=18),
                ) as r:
                    if r.status == 200:
                        data = await r.json()
                        txt = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
                        if txt:
                            return re.sub(r"<think>[\s\S]*?</think>", "", txt).strip()
            except Exception as exc:
                logger.warning("Groq %s failed: %s", model, exc)
        return ""

    async def transcribe_audio_groq(self, audio_bytes: bytes, filename: str = "voice.ogg") -> str:
        """Transcribe voice/audio via Groq Whisper Large v3 (with Egyptian Arabic context)."""
        if not self.cfg.groq_api_key or not audio_bytes:
            return ""
        sess = await self.get_http()
        form = aiohttp.FormData()
        form.add_field("file", audio_bytes, filename=filename or "voice.ogg", content_type="audio/ogg")
        form.add_field("model", "whisper-large-v3")
        form.add_field("language", "ar")
        form.add_field(
            "prompt",
            "تريدسيجون، Tradesejon، ورشة، تداول، Lead Strategist، إيه الدنيا، إيه الأخبار، الدهب، الذهب، الدولار، الفوركس، الأسهم، سكالنج، Whop، ديسكورد.",
        )
        try:
            async with sess.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {self.cfg.groq_api_key}"},
                data=form,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    return str(data.get("text", "")).strip()
        except Exception as exc:
            logger.warning("Whisper STT failed: %s", exc)
        return ""

    async def analyze_image_gemini(self, image_bytes: bytes, mime_type: str, question: str = "") -> str:
        """Analyze charts/screenshots securely using Gemini 2.5 Flash Vision."""
        if not self.cfg.gemini_api_key or not image_bytes:
            return ""
        b64 = base64.b64encode(image_bytes).decode("ascii")
        prompt = wrap_untrusted_prompt(
            "trader_image_question",
            question or "حلل هذه الصورة أو الرسم البياني باحترافية ووضوح باللغة المصرية مع ذكر المخاطر بدون تقديم نصيحة مالية مضمونة.",
        )
        sess = await self.get_http()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={self.cfg.gemini_api_key}"
        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": mime_type or "image/jpeg", "data": b64}},
                ]
            }]
        }
        try:
            async with sess.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=25)) as r:
                if r.status == 200:
                    data = await r.json()
                    parts = ((((data.get("candidates") or [{}])[0]).get("content") or {}).get("parts") or [])
                    return "".join(str(p.get("text", "")) for p in parts).strip()
        except Exception as exc:
            logger.warning("Gemini Vision failed: %s", exc)
        return ""

    async def synthesize_masri_voice(self, text: str, *, is_english: bool = False) -> bytes | None:
        """Synthesize natural Masri/English voice audio via ElevenLabs with Edge-TTS fallback."""
        clean = sanitize_untrusted_text(text, max_len=600)
        if not clean:
            return None
        if self.cfg.elevenlabs_api_key and not is_english:
            sess = await self.get_http()
            try:
                url = f"https://api.elevenlabs.io/v1/text-to-speech/{self.cfg.elevenlabs_voice_id}"
                async with sess.post(
                    url,
                    headers={"xi-api-key": self.cfg.elevenlabs_api_key, "Content-Type": "application/json"},
                    json={"text": clean, "model_id": "eleven_multilingual_v2"},
                    timeout=aiohttp.ClientTimeout(total=18),
                ) as r:
                    if r.status == 200:
                        return await r.read()
            except Exception as exc:
                logger.warning("ElevenLabs TTS fallback to Edge-TTS: %s", exc)

        voice = "en-US-AriaNeural" if is_english else "ar-EG-SalmaNeural"
        try:
            comm = edge_tts.Communicate(clean, voice)
            buf = io.BytesIO()
            async for chunk in comm.stream():
                if chunk.get("type") == "audio":
                    buf.write(chunk["data"])
            data = buf.getvalue()
            return data if data else None
        except Exception as exc:
            logger.warning("Edge-TTS failed: %s", exc)
            return None

    TRADESEJON_MASTER_KB_PROMPT = """
📚 [الدليل الشامل والأسئلة الشائعة الرسمية لمنظومة Tradesejon (17 نقطة معتمدة)]:
1) البداية والفيديوهات التعليمية (لو لسه مشفتش الفيديوهات وتايه):
   - الخطوة الأولى هي بلاي ليست Lead Strategist التعليمية على يوتيوب:
     https://youtube.com/YOUR_TRAINING_PLAYLIST
   - اتفرج على أكبر عدد من فيديوهات Member الطويلة والشورتس، وجمّع أسئلتك والنقاط اللي تقف معاك في ورقة قبل حضور الـ Workshop.
   - موعد الـ Workshop الأوفلاين القادمة: Upcoming Scheduled Workshop (بنعملها دورياً مرتين في الشهر دايماً يوم جمعة).

2) لو مبتدئ خالص (زيرو بورصة ومش فاهم أي حاجة):
   - قبل فيديوهات Member، شوف الـ 5 فيديوهات التأسيسية دي بالترتيب عشان تفهم الأساسيات ومتحسش إنك تايه:
     1. البورصة | Market Basics: https://youtu.be/VIDEO_TEMPLATE_01
     2. لو نفسك تدخل عالم الاستثمار | Investment Series: https://youtu.be/VIDEO_TEMPLATE_02
     3. أهم قاعدة في الاستثمار | Market Basics: https://youtu.be/VIDEO_TEMPLATE_03
     4. المحاضرة الأولى (مفاهيم أساسية) | Market Instructor: https://youtu.be/VIDEO_TEMPLATE_04
     5. كل ما تحتاج معرفته عن سوق الأسهم الأمريكي باختصار | Market Overview: https://youtu.be/VIDEO_TEMPLATE_05
     6. بعدها ابدأ فوراً بلاي ليست Lead Strategist التعليمية: https://youtube.com/YOUR_TRAINING_PLAYLIST

3) أهمية اشتراك الديسكورد (VIP Membership = 45$ شهرياً شامل الـ Scanner):
   - رابط الاشتراك: https://whop.com/Tradesejon/exp_TEMPLATE_VIP (أو باقة الورشة https://whop.com/checkout/plan_TEMPLATE_01).
   - المميزات:
     • Live Trading يومياً مع الـ Mentors وLead Strategist لايف على الشاشة (دخلوا إمتى، خرجوا إمتى، وليه اختاروا السهم وأسباب الدخول والخروج عملياً).
     • أداة الـ Scanner 🔍 لمتابعة الأسهم اللي عليها حركة قوية (Movement) وأخبار وفرص يومية.
     • قنوات متخصصة: Professional Alerts (تنبيهات وتحليلات يومية)، Traders' Profits (أرباح وتجارب الأعضاء)، Traders' Mistakes (التعلم من الأخطاء)، وقنوات شرح مخصصة لكل منصة.
     • لايف Trading Psychology 🧠 مع البشمهندس MentorAlpha كل يوم أحد للتحكم في المشاعر والأعصاب والخوف والطمع والالتزام بالخطة.

4) لو متداول قديم وخسرت قبل كده:
   - ربنا يعوض عليك. مجالنا (Scalping الأسهم الأمريكية الصغيرة) مختلف عن أي حاجة فاتت، فابدأ من الصفر على أساس صح، اتفرج على فيديوهات Member، وتدرب الأول على حساب Demo تجريبي (مبنجربش أبداً على حساب حقيقي) مع إدارة مخاطر صارمة.

5) تفاصيل الـ Workshop الـ Offline (في مصر):
   - النظام: One Full Workshop Session (عدد محدود للتركيز مع كل شخص). الموعد القادم: Upcoming Scheduled Workshop (مرتين في الشهر يوم جمعة).
   - السعر لأعضاء الديسكورد: 4,000 جنيه مصري = 74.39 دولار -> رابط الدفع: https://whop.com/checkout/plan_TEMPLATE_03
   - السعر لغير أعضاء الديسكورد: 6,000 جنيه مصري = 111.59 دولار -> رابط الدفع: https://whop.com/checkout/plan_TEMPLATE_04
   - بعد تأكيد الدفع 100% يتم إرسال رابط جروب الواتساب الخاص بالـ Workshop وتفعيل الدخول.

6) تفاصيل الـ Workshop الـ Online (أونلاين 4 ساعات برايفت - كل شخصين مع المحاضر):
   - السعر: 100$ (100 دولار) موحد للجميع (سواء عضو ديسكورد أو غير عضو)، مدة السيشن 4 ساعات.
   - المواعيد المتاحة: أيام الاثنين، الثلاثاء، الأربعاء، والخميس (بالتنسيق مع المحاضر).
   - المحاضرون الـ 5 وروابط الدفع المباشرة على Whop:
     1. MentorAlpha (MentorAlpha): https://whop.com/checkout/plan_TEMPLATE_05
     2. MentorBeta (MentorBeta ⚡): https://whop.com/checkout/plan_TEMPLATE_06
     3. MentorGamma (MentorGamma): https://whop.com/checkout/plan_TEMPLATE_07
     4. MentorDelta (MentorDelta): https://whop.com/checkout/plan_TEMPLATE_08
     5. Mentor Epsilon (Mentor Epsilon): https://whop.com/checkout/plan_TEMPLATE_09
   - بعد الدفع نحتاج: (الاسم + اسم المحاضر) لتنسيق الموعد.

7) منهج وتفاصيل الـ Workshop (4 ساعات: 2.5 ساعة نظري + 1.5 ساعة عملي):
   - استراتيجية Lead Strategist للـ Scalping على الأسهم الأمريكية الصغيرة (US Small Caps):
   - النظري (2.5 ساعة): (1) أساسيات Day Trading & Scalping، الـ Bid/Ask/Spread، أوامر Market & Limit، الشموع والـ Volume. (2) اختيار الأسهم بالـ Scanner والـ Float و RVOL و Volume والأخبار/Catalysts. (3) تحليل الشارت على فريمات (4H - 15m - 1m - 5s)، مؤشرات VWAP و EMA 9، الدعوم والمقاومات، واستراتيجية Break → Retest → Hold وكشف الـ False Breakout. (4) قراءة الـ Order Flow وشاشة Level 2 (Buy/Sell Walls & Floor) والـ Time & Sales. (5) إدارة المخاطر، حساب حجم الصفقة (Position Sizing)، Risk/Reward، الخروج على مراحل (Scale Out)، الـ Hotkeys، وتجنب الـ FOMO والـ Overtrading.
   - العملي (1.5 ساعة): تطبيق حي على أمثلة فعلية من السوق والـ Scanner والشارت و Level 2 و Time & Sales ومحاكاة صفقات ناجحة وفاشلة. (الورشة تعليمية وتدريبية على Demo وليست توصيات أو ضمان ربح).

8) شرح المصطلحات الأساسية باختصار:
   - Scalping: اقتناص حركات سعرية صغيرة وسريعة في وقت قصير بدل انتظار 5 أو 10 دولار.
   - Setup: السيناريو والشروط والإشارات اللي بنستناها على الشارت عشان ندخل الصفقة.
   - Entry: سعر نقطة الدخول وشراء السهم بناءً على إشارة وتحليل.
   - Exit: سعر الخروج وقفل الصفقة، وليه 3 أنواع: Take Profit (TP قفل على مكسب)، Stop Loss (SL إيقاف الخسارة)، Manual Exit (خروج يدوي لو تغيرت حركة السهم أو انتهى سبب الدخول).

9) المنصات المستخدمة من مصر وروابط الإيداع والتسجيل:
   - الشارت والتحليل: Webull. التنفيذ (البروكر): Interactive Brokers (IBKR) أو DirectBroker (زيرو عمولة).
   - شرح التسجيل على IBKR خطوة بخطوة: https://drive.google.com/YOUR_DRIVE_RESOURCE?usp=sharing
   - سيت أب IBKR وتشغيل منصة TWS: https://youtu.be/VIDEO_TEMPLATE_06
   - رابط التسجيل في بروكر DirectBroker من مصر (زيرو عمولة): https://your-broker.example.com/
   - فيديو شرح فتح حساب DirectBroker والإيداع: https://youtu.be/VIDEO_TEMPLATE_07
   - تطبيق LicensedBroker (يفتح Level 2 مجاناً بعد التسجيل): https://your-platform.example.com/
   - تحويل ذراع PlayStation USB Joystick إلى Stream Deck للـ Hotkeys: https://youtu.be/VIDEO_TEMPLATE_08
   - فيديوهات إضافية مفيدة: https://youtu.be/VIDEO_TEMPLATE_09 و https://youtu.be/VIDEO_TEMPLATE_10

10) جدول مواعيد الـ Mentors الأسبوعي في اللايف تريدينج:
    - Lead Strategist (Member): كل يوم في الـ After Hours.
    - البشمهندس MentorAlpha (MentorAlpha): سيكولوجي (Trading Psychology) كل يوم أحد.
    - MentorGamma (MentorGamma): كل ثلاثاء وخميس.
    - MentorDelta (MentorDelta): كل اثنين وأربعاء.
    - Mentor Epsilon (Mentor Epsilon): كل سبت وخميس.

11) رأس المال للبدء والـ 3 خطوات الذهبية:
    - ابدأ الأول على حساب Demo تجريبي بدون أي خسائر لحد ما تتقن الاستراتيجية.
    - لما تنتقل للحساب الحقيقي تقدر تبدأ من 500 دولار بكميات صغيرة جداً (1 إلى 5 أسهم فقط) مع إدارة مخاطر صارمة، وذلك بعد إتمام الـ 3 خطوات بالترتيب:
      (1) فيديوهات Member التعليمية -> (2) متابعة اللايف تريدينج اليومي باشتراك الديسكورد -> (3) حضور الـ Workshop.
"""

    def get_live_master_kb_prompt(self) -> str:
        """Reload client Master Guide (.md & .json) dynamically on change and sync to MongoDB/AIOS memory."""
        kb_root = Path(self.cfg.tradesejon_knowledge_base_dir or r"./data/workspace") / "root"
        md_path = kb_root / "Tradesejon_Official_17_FAQ_Master_Guide.md"
        json_path = kb_root / "Tradesejon_Official_17_FAQ_Master_Guide.json"
        if not md_path.exists():
            return self.TRADESEJON_MASTER_KB_PROMPT
        try:
            stat = md_path.stat()
            j_mtime = json_path.stat().st_mtime if json_path.exists() else 0.0
            latest_mtime = max(stat.st_mtime, j_mtime)
            if self._kb_cache_text and latest_mtime <= self._kb_cache_mtime:
                return self._kb_cache_text
            md_text = md_path.read_text(encoding="utf-8")
            j_text = json_path.read_text(encoding="utf-8") if json_path.exists() else "{}"
            j_data = json.loads(j_text) if j_text else {}
            now = datetime.now(timezone.utc)
            for _, db in self._dual_dbs():
                try:
                    db["kb_articles"].update_one(
                        {"slug": "tradesejon_official_17_faq_master_guide"},
                        {
                            "$set": {
                                "slug": "tradesejon_official_17_faq_master_guide",
                                "title": "Tradesejon Official 17 FAQ Master Guide",
                                "content": md_text,
                                "structured_json": j_data,
                                "mtime": latest_mtime,
                                "updated_at": now,
                            }
                        },
                        upsert=True,
                    )
                    db["aios_super_memory"].update_one(
                        {"key": "tradesejon_official_17_faq_master_guide"},
                        {
                            "$set": {
                                "key": "tradesejon_official_17_faq_master_guide",
                                "type": "client_master_guide",
                                "user_query": "Tradesejon Official 17 FAQ Master Guide",
                                "ai_reply": md_text,
                                "rule": md_text,
                                "structured_json": j_data,
                                "mtime": latest_mtime,
                                "updated_at": now,
                            }
                        },
                        upsert=True,
                    )
                except Exception:
                    pass
            self._kb_cache_mtime = latest_mtime
            self._kb_cache_text = md_text
            return md_text
        except Exception:
            return self.TRADESEJON_MASTER_KB_PROMPT

    @staticmethod
    def match_official_faq_context(query: str) -> str:
        """Return targeted high-precision excerpts from the 17-point Tradesejon FAQ when keywords match."""
        q = (query or "").lower()
        snippets: list[str] = []
        if any(k in q for k in ("مبتدئ", "زيرو", "صفر", "ابدا منين", "أبدأ منين", "مشفتش", "تايه", "فيديوهات", "بلاي ليست", "playlist", "beginner", "start")):
            snippets.append(
                "• الخطوة الأولى: بلاي ليست Lead Strategist التعليمية: https://youtube.com/YOUR_TRAINING_PLAYLIST"
                "• لو مبتدئ خالص (زيرو بورصة) شوف الـ 5 فيديوهات التأسيسية بالترتيب قبل فيديوهات Member:\n"
                "  1) البورصة | Market Basics: https://youtu.be/VIDEO_TEMPLATE_01\n"
                "  2) Investment Series (عالم الاستثمار): https://youtu.be/VIDEO_TEMPLATE_02\n"
                "  3) أهم قاعدة في الاستثمار | Market Basics: https://youtu.be/VIDEO_TEMPLATE_03\n"
                "  4) مفاهيم أساسية | Market Instructor: https://youtu.be/VIDEO_TEMPLATE_04\n"
                "  5) سوق الأسهم الأمريكي باختصار | Market Overview: https://youtu.be/VIDEO_TEMPLATE_05"
            )
        if any(k in q for k in ("اوفلاين", "أوفلاين", "offline", "4000", "6000", "74.39", "111.59", "Upcoming Scheduled Workshop", "جمعة", "جمعه", "ملحقتش", "هتكرروها", "تتكرر", "بتتكرر", "تكرار")):
            snippets.append(
                "• الـ Offline Workshop القادمة: يوم Upcoming Scheduled Workshop (وتُعقد دورياً مرتين في الشهر دايماً يوم جمعة).\n"
                "• السعر لأعضاء الديسكورد: 4,000 جنيه (74.39 دولار): https://whop.com/checkout/plan_TEMPLATE_03\n"
                "• السعر لغير الأعضاء: 6,000 جنيه (111.59 دولار): https://whop.com/checkout/plan_TEMPLATE_04"
            )
        if any(k in q for k in ("اونلاين", "أونلاين", "online", "100$", "100 دولار", "محاضر", "محاضرين", "MentorAlpha", "MentorBeta", "MentorGamma", "MentorDelta", "Mentor Epsilon", "جدول", "مواعيد", "mentors", "schedule")):
            snippets.append(
                "• الـ Online Workshop: مدتها 4 ساعات (شخصين مع المحاضر)، السعر 100$ موحد للجميع، أيام الاثنين والثلاثاء والأربعاء والخميس:\n"
                "  1) MentorAlpha (MentorAlpha): https://whop.com/checkout/plan_TEMPLATE_05\n"
                "  2) MentorBeta (MentorBeta ⚡): https://whop.com/checkout/plan_TEMPLATE_06\n"
                "  3) MentorGamma (MentorGamma): https://whop.com/checkout/plan_TEMPLATE_07\n"
                "  4) MentorDelta (MentorDelta): https://whop.com/checkout/plan_TEMPLATE_08\n"
                "  5) Mentor Epsilon (Mentor Epsilon): https://whop.com/checkout/plan_TEMPLATE_09\n"
                "• جدول اللايف تريدينج الأسبوعي في الديسكورد: Lead Strategist (يومياً After Hours)، MentorAlpha (سيكولوجي كل أحد)، MentorGamma (ثلاثاء وخميس)، MentorDelta (اثنين وأربعاء)، Mentor Epsilon (سبت وخميس)."
            )
        if any(k in q for k in ("منصه", "منصة", "بروكر", "ايداع", "إيداع", "ibkr", "interactive", "DirectBroker", "c pro", "webull", "LicensedBroker", "سهم", "joystick", "دراع", "stream deck", "tws")):
            snippets.append(
                "• المنصات من مصر: الشارت على Webull، والتنفيذ على Interactive Brokers (IBKR) أو DirectBroker (زيرو عمولة).\n"
                "  - شرح التسجيل في IBKR: https://drive.google.com/YOUR_DRIVE_RESOURCE?usp=sharing\n"
                "  - سيت أب IBKR وتشغيل TWS: https://youtu.be/VIDEO_TEMPLATE_06\n"
                "  - التسجيل في DirectBroker من مصر (زيرو عمولة): https://your-broker.example.com/\n"
                "  - شرح فتح حساب DirectBroker والإيداع: https://youtu.be/VIDEO_TEMPLATE_07\n"
                "  - تطبيق LicensedBroker (يفتح Level 2 مجاناً): https://your-platform.example.com/\n"
                "  - تحويل ذراع PlayStation USB إلى Stream Deck للـ Hotkeys: https://youtu.be/VIDEO_TEMPLATE_08"
            )
        if any(k in q for k in ("بكام", "راس مال", "رأس مال", "500", "demo", "ديمو", "تجريبي", "خسرت", "استراتيجي", "scalp", "setup", "entry", "exit", "level 2", "vwap")):
            snippets.append(
                "• الاستراتيجية: Scalping على الأسهم الأمريكية الصغيرة (Small Caps) باستخدام الفريمات (4H, 15m, 1m, 5s) و VWAP و EMA 9 واستراتيجية Break → Retest → Hold مع قراءة Level 2 و Time & Sales.\n"
                "• المصطلحات: Setup (شروط الدخول)، Entry (سعر الشراء)، Exit (3 أنواع: Take Profit، Stop Loss، Manual Exit).\n"
                "• رأس المال: ابدأ الأول على حساب Demo تجريبي بدون أي خسائر. عند الانتقال للحقيقي يمكنك البدء من 500$ بكميات صغيرة (1 إلى 5 أسهم فقط) بعد إتمام الـ 3 خطوات (فيديوهات Member + لايف الديسكورد اليومي + الـ Workshop)."
            )
        return "\n\n".join(snippets)

    async def ask_trader_support(self, question: str, *, user_id: str, username: str) -> str:
        """Answer trader questions using live AIOS + RAG KB + live session/prices context."""
        safe_q = sanitize_untrusted_text(question, max_len=1600)
        try:
            funnel = await self.get_discord_user_funnel_state(str(user_id or ""))
            is_paid = bool(funnel.get("paid"))
        except Exception:
            is_paid = False
        low_q = safe_q.lower()
        if not is_paid and any(
            kw in low_q
            for kw in (
                "blueprint",
                "masterclass",
                "business model canvas",
                "marketing plan",
                "visual identity",
                "1omga0wt",
                "1c7gz7rw",
                "المخطط الاستراتيجي",
                "دليل الماستركلاس",
                "خطة التسويق",
                "الهوية البصرية",
                "مجلد المستندات",
            )
        ):
            return "🔒 الملفات والمستندات الخاصة والمدفوعة (مثل محتوى الورشة المتقدم والمخططات الخاصة) متاحة فقط للمشتركين بعد تأكيد الاشتراك والدفع عبر Whop."
        session = await self.get_live_session_info()
        prices = await self.get_market_prices()
        kb_hits = await self.search_postgres_kb(safe_q, limit=2)
        faq_ctx = self.match_official_faq_context(safe_q)
        live_master_kb = await asyncio.to_thread(self.get_live_master_kb_prompt)
        reach_ctx = ""
        lower_q = safe_q.lower()
        if any(tok in lower_q for tok in ("http://", "https://", "youtube", "github", "ابحث", "search", "news", "أخبار", "تحليل")):
            reach_res = await self.agent_reach.smart_research(safe_q, limit=2)
            if reach_res.get("ok") and reach_res.get("summary"):
                reach_ctx = f"[{reach_res.get('source', 'agent-reach')}]\n{reach_res['summary'][:900]}"

        # Prefer local AIOS endpoint first
        sess = await self.get_http()
        headers = {"Content-Type": "application/json"}
        if self.cfg.aios_internal_token:
            headers["X-AIOS-Admin-Key"] = self.cfg.aios_internal_token
        try:
            async with sess.post(
                f"{self.cfg.aios_base_url}/api/trader-agent",
                headers=headers,
                json={"command": safe_q, "platform": "discord_trader", "sender": f"discord:{user_id}", "web_context": reach_ctx, "faq_context": faq_ctx},
                timeout=aiohttp.ClientTimeout(total=16),
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    rep = data.get("reply") or data.get("text")
                    if rep:
                        return strip_valuable_files_for_prospect(redact_secrets(str(rep), self.cfg.known_secrets), is_paid=is_paid)
        except Exception:
            pass

        price_snippet = ", ".join(
            f"{k}: {v.get('unit','')}{v.get('price')}"
            for k, v in list(prices.items())[:6]
            if isinstance(v, dict)
        )
        kb_snippet = "\n".join(f"- [{h['title']}] {h['content']}" for h in kb_hits)
        sys_prompt = (
            "أنت المساعد الذكي الرسمي والمشرف الآلي لمجتمع Tradesejon على Discord (بوت دعم المتداولين و Prospect Bot).\n"
            "قاعدة صارمة جداً (Strict Single-Source-of-Truth & Zero-Hallucination Rule):\n"
            "- أجب حصرياً وبنسبة 100% من الدليل الرسمي المكون من 17 نقطة أدناه فقط.\n"
            "- ممنوع منعاً باتاً اختراع أي أسعار أو باقات أو مواعيد أو محاضرين أو منصات أو مؤشرات غير مذكورة في هذا الدليل.\n"
            "- تكلم بالعامية المصرية المهذبة والواثقة والمشجعة لو السؤال عربي، وبالإنجليزي الاحترافي لو السؤال إنجليزي.\n"
            "- المؤسس والخبير الرئيسي: Lead Strategist (Lead Strategist — استراتيجية الـ Scalping على الأسهم الأمريكية الصغيرة US Small Caps).\n"
            "- سرية النظام: ممنوع تماماً ذكر أي أدوات داخلية أو بنية تحتية أو قواعد بيانات (ممنوع ذكر n8n أو AIOS أو localhost أو Google Sheets أو ملفات داخلية) أو تقديم توصيات شراء/بيع أو وعود بأرباح مضمونة.\n"
            "- قاعدة التحقق الصارم من الدفع (100% Payment Gate): لا يُمنح أي متداول رابط الورشة المباشر أو صلاحيات القنوات الخاصة إلا بعد التحقق والتأكيد من الدفع بنسبة 100% عبر Whop، وأي خطأ يظهر باللون الأحمر الواضح.\n"
            + (live_master_kb or self.TRADESEJON_MASTER_KB_PROMPT) + "\n"
            + (f"- تفاصيل مطابقة مباشرة لسؤال المتداول:\n{faq_ctx}\n" if faq_ctx else "")
        )
        reply = await self.call_groq_chat(sys_prompt, wrap_untrusted_prompt("trader_message", safe_q))
        fallback_reply = (
            f"أهلاً بك في مجتمع Tradesejon! 🚀\n\n{faq_ctx}"
            if faq_ctx
            else "أهلاً بك في مجتمع Tradesejon! ابدأ ببلاي ليست Lead Strategist التعليمية (https://youtube.com/YOUR_TRAINING_PLAYLIST أو املأ نموذج الانضمام في #marhaban للحصول على Prospect Role وروابط باقات الديسكورد ($45/شهر) والـ Workshop."
        )
        return strip_valuable_files_for_prospect(redact_secrets(reply or fallback_reply, self.cfg.known_secrets), is_paid=is_paid)

    async def ask_pr_ai_draft(self, instruction: str, trader_context: str = "") -> str:
        """Polish PR executive reply cleanly in Masri Arabic or English."""
        sys_prompt = (
            "أنت مسؤول العلاقات العامة في Tradesejon VIP. اكتب رداً تنفيذياً مهذباً وواضحاً "
            "ومباشراً للمتداول بناءً على التوجيه المرفق فقط دون ذكر أي بيانات حساسة أو أوامر تيليجرام مثل /sendmail."
        )
        prompt = wrap_untrusted_prompt("pr_instruction", f"التوجيه: {instruction}\nسياق التذكرة: {trader_context}")
        reply = await self.call_groq_chat(sys_prompt, prompt)
        clean_reply = re.sub(r"/sendmail\s+\S+", "", reply or instruction).strip()
        return redact_secrets(clean_reply or instruction, self.cfg.known_secrets)

    async def ask_ceo_agent(self, command: str, *, discord_user_id: str, history: list | None = None) -> dict[str, Any]:
        """Forward verified CEO commands to unified AIOS agent brain cleanly, enriched with Agent-Reach."""
        safe_cmd = sanitize_untrusted_text(command, max_len=2000)
        reach_ctx = ""
        lower_cmd = safe_cmd.lower()
        if any(tok in lower_cmd for tok in ("http://", "https://", "search", "ابحث", "research", "github", "youtube", "$")):
            reach_res = await self.agent_reach.smart_research(safe_cmd, limit=3)
            if reach_res.get("ok") and reach_res.get("summary"):
                reach_ctx = f"\n\n[{reach_res.get('source', 'agent-reach')}]\n{reach_res['summary'][:1200]}"
        sess = await self.get_http()
        headers = {"Content-Type": "application/json"}
        if self.cfg.aios_internal_token:
            headers["X-AIOS-Admin-Key"] = self.cfg.aios_internal_token
        try:
            async with sess.post(
                f"{self.cfg.aios_base_url}/api/n8n-agent",
                headers=headers,
                json={
                    "command": safe_cmd + reach_ctx,
                    "history": history or [],
                    "source": "discord_ceo",
                    "actor": f"discord:{discord_user_id}",
                },
                timeout=aiohttp.ClientTimeout(total=28),
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    raw = data.get("reply") or data.get("output") or data.get("text") or ""
                    data["reply"] = redact_secrets(str(raw), self.cfg.known_secrets)
                    return data
        except Exception as exc:
            logger.warning("CEO AIOS call failed, falling back to Groq: %s", redact_secrets(str(exc), self.cfg.known_secrets))

        ceo_sys_prompt = (
            "أنت المساعد التنفيذي لـ CEO Tradesejon ومسؤول العمليات الذكية على Discord.\n"
            "- تنفذ وتلبي توجيهات الرئيس التنفيذي فوراً وبدقة تامة.\n"
            "- تجيب دائماً بطاعة تنفيذية واثقة بالعامية المصرية الراقية («تمام يا فندم»، «من عيوني يا باشا»، «تحت أمر معاليك»)، أو بالإنجليزية التنفيذية إذا كان الأمر بالإنجليزية.\n"
            "- نظام ديسكورد وبوتات Tradesejon متصلة وجاهزة للبث وإرسال الرسائل لقنوات #workshop-channel و #prospects و #marhaban.\n"
            "- قاعدة العمل الأساسية: التحقق الصارم من الدفع 100% قبل منح أي متداول صلاحيات الورشة، مع إبراز أي أخطاء باللون الأحمر."
        )
        reply = await self.call_groq_chat(
            ceo_sys_prompt,
            wrap_untrusted_prompt("ceo_prompt", safe_cmd + reach_ctx),
        )
        return {"ok": bool(reply), "reply": redact_secrets(reply or "تعذر الوصول حالياً لخادم AIOS التنفيذي.", self.cfg.known_secrets)}

    async def send_email_secure(
        self,
        *,
        to_email: str,
        subject: str,
        body_text: str,
        recipient_name: str = "Trader",
        include_qr: bool = True,
        include_zoom: bool = False,
        unique_zoom_url: str = "",
    ) -> dict[str, Any]:
        """Send authenticated email via trader@example.com SMTP with Resend fallback (per-user unique Zoom only)."""
        # Reject any CRLF header injection
        clean_to = re.sub(r"[\r\n]", "", (to_email or "").strip())
        clean_subj = re.sub(r"[\r\n]", " ", sanitize_untrusted_text(subject, max_len=160))
        clean_name = re.sub(r"[\r\n]", " ", sanitize_untrusted_text(recipient_name, max_len=80))
        if not clean_to or "@" not in clean_to:
            return {"ok": False, "error": "invalid_recipient_email"}

        session = await self.get_live_session_info()
        personal_zoom_url = (unique_zoom_url or "").strip()
        if include_zoom and not personal_zoom_url:
            zres = await self.create_unique_zoom_link_for_payer(
                email_addr=clean_to,
                full_name=clean_name,
            )
            personal_zoom_url = str(zres.get("unique_zoom_url") or "")

        safe_body_html = html_escape(sanitize_untrusted_text(body_text, max_len=3500)).replace("\n", "<br>")
        zoom_html = (
            f"<p><b>رابط Discord Live الشخصي المباشر (حصري بك):</b> "
            f"<a style='color:#38bdf8' href='{html_escape(personal_zoom_url)}'>{html_escape(personal_zoom_url)}</a></p>"
            if (include_zoom and personal_zoom_url)
            else "<p><i>🔒 رابط Discord Live الشخصي المباشر يُرسل تلقائياً فور كتابة بريدك ورقم هاتفك.</i></p>"
        )
        zoom_txt = (
            f"\nYour Unique Discord Live Direct Link: {personal_zoom_url}"
            if (include_zoom and personal_zoom_url)
            else "\nDiscord Live: يُصدر رابطك المباشر تلقائياً فور كتابة بريدك ورقم هاتفك."
        )
        html_content = (
            f"<div dir='rtl' style='font-family:Arial,sans-serif;padding:24px;background:#ffffff;color:#0f172a;border:1px solid #e2e8f0;border-radius:12px'>"
            f"<h2 style='color:#0f766e;margin-top:0'>Tradesejon VIP — {html_escape(clean_subj)}</h2>"
            f"<p>أهلاً {html_escape(clean_name)}،</p>"
            f"<p>{safe_body_html}</p>"
            f"<hr style='border-color:#e2e8f0'>"
            f"<p><b>الجلسة الحية على Discord:</b> {html_escape(session['session_name'])} ({html_escape(session['session_time'])})</p>"
            f"{zoom_html}"
            f"<p><b>بطاقة Whop المجانية ($0 بدون بطاقة):</b> <a style='color:#059669' href='{html_escape(session['whop_url'])}'>{html_escape(session['whop_url'])}</a></p>"
            f"<p style='font-size:12px;color:#64748b'>Tradesejon Support &bull; {html_escape(self.cfg.gmail_user)}</p>"
            f"</div>"
        )

        msg_id = email.utils.make_msgid(domain="gmail.com")

        def _smtp_send() -> dict[str, Any]:
            if not (self.cfg.gmail_user and self.cfg.gmail_app_password):
                return {"ok": False, "error": "missing_gmail_credentials"}
            try:
                msg = EmailMessage()
                msg["From"] = email.utils.formataddr(("Tradesejon VIP", self.cfg.gmail_user))
                msg["To"] = clean_to
                msg["Reply-To"] = self.cfg.gmail_user
                msg["Date"] = email.utils.formatdate(localtime=True)
                msg["Message-ID"] = msg_id
                msg["Subject"] = clean_subj
                msg.set_content(f"{body_text}{zoom_txt}\nWhop Free Pass ($0): {session['whop_url']}")
                msg.add_alternative(html_content, subtype="html")
                if include_qr:
                    qr_target = personal_zoom_url if (include_zoom and personal_zoom_url) else session["whop_url"]
                    qr_name = "tradesejon_discord_live_qr.png" if (include_zoom and personal_zoom_url) else "tradesejon_vip_qr.png"
                    qr_bytes = self.generate_qr_png(qr_target)
                    msg.add_attachment(qr_bytes, maintype="image", subtype="png", filename=qr_name)
                with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15, context=ssl.create_default_context()) as srv:
                    srv.login(self.cfg.gmail_user, self.cfg.gmail_app_password)
                    srv.send_message(msg)
                return {
                    "ok": True,
                    "provider": "gmail_smtp",
                    "from_addr": self.cfg.gmail_user,
                    "to": clean_to,
                    "subject": clean_subj,
                    "message_id": msg_id,
                }
            except Exception as exc:
                return {"ok": False, "error": redact_secrets(str(exc), self.cfg.known_secrets)}

        res = await asyncio.to_thread(_smtp_send)
        if not res.get("ok") and self.cfg.resend_api_key:
            sess = await self.get_http()
            try:
                async with sess.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {self.cfg.resend_api_key}", "Content-Type": "application/json"},
                    json={
                        "from": f"Tradesejon VIP <{self.cfg.gmail_user}>",
                        "to": [clean_to],
                        "subject": clean_subj,
                        "html": html_content,
                    },
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as r:
                    if r.status in (200, 201):
                        rdata = await r.json()
                        res = {"ok": True, "provider": "resend", "from_addr": self.cfg.gmail_user, "to": clean_to, "id": rdata.get("id", "")}
            except Exception as exc:
                res = {"ok": False, "error": redact_secrets(str(exc), self.cfg.known_secrets)}

        def _log_email():
            now = datetime.now(timezone.utc)
            email_doc = {
                "to": clean_to,
                "subject": clean_subj,
                "body_preview": sanitize_untrusted_text(redact_secrets(body_text or "", self.cfg.known_secrets), max_len=600),
                "provider": res.get("provider", "none"),
                "message_id": res.get("message_id", ""),
                "error": res.get("error", ""),
                "ok": bool(res.get("ok")),
                "platform": "discord",
                "at": now,
                "created_at": now,
            }
            for _, db in self._dual_dbs():
                try:
                    db["outbound_emails"].insert_one(dict(email_doc))
                    db["email_logs"].insert_one(dict(email_doc))
                except Exception:
                    pass

        await asyncio.to_thread(_log_email)
        return res

    async def log_discord_chat_message(
        self,
        *,
        bot_role: str,
        direction: str = "inbound",
        message_id: str = "",
        discord_user_id: str = "",
        discord_username: str = "",
        channel_id: str = "",
        channel_name: str = "",
        guild_id: str = "",
        content: str = "",
        attachments: list[Any] | None = None,
        embeds_count: int = 0,
    ) -> dict[str, Any]:
        """Archive every Discord chat message (text + images/videos/audio/files) immediately to Local + Atlas MongoDB."""
        att_list: list[dict[str, Any]] = []
        for a in attachments or []:
            if isinstance(a, dict):
                fname = str(a.get("filename") or "")
                ctype = str(a.get("content_type") or "").lower()
                size = int(a.get("size") or 0)
                url = str(a.get("url") or "")
            else:
                fname = str(getattr(a, "filename", "") or "")
                ctype = str(getattr(a, "content_type", "") or "").lower()
                size = int(getattr(a, "size", 0) or 0)
                url = str(getattr(a, "url", "") or "")
            ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
            if ctype.startswith("image/") or ext in ("png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"):
                kind = "image"
            elif ctype.startswith("video/") or ext in ("mp4", "mov", "webm", "mkv", "avi"):
                kind = "video"
            elif ctype.startswith("audio/") or ext in ("mp3", "ogg", "wav", "m4a", "opus", "flac"):
                kind = "audio"
            elif ext in ("pdf", "doc", "docx", "xls", "xlsx", "csv", "txt", "ppt", "pptx"):
                kind = "document"
            else:
                kind = "file"
            att_list.append({
                "filename": sanitize_untrusted_text(fname, max_len=160),
                "content_type": ctype[:80],
                "size": size,
                "url": url[:500],
                "media_kind": kind,
            })

        now = datetime.now(timezone.utc)
        doc = {
            "platform": "discord",
            "bot_role": str(bot_role or "trader"),
            "direction": str(direction or "inbound"),
            "message_id": str(message_id or ""),
            "discord_user_id": str(discord_user_id or ""),
            "discord_username": sanitize_untrusted_text(discord_username, max_len=80),
            "discord_channel_id": str(channel_id or ""),
            "channel_name": sanitize_untrusted_text(channel_name, max_len=80),
            "discord_guild_id": str(guild_id or ""),
            "content": sanitize_untrusted_text(redact_secrets(content or "", self.cfg.known_secrets), max_len=2000),
            "attachments": att_list,
            "has_media": len(att_list) > 0,
            "media_kinds": sorted({x["media_kind"] for x in att_list}),
            "embeds_count": int(embeds_count or 0),
            "updated_at": now,
        }

        def _save() -> None:
            for _, db in self._dual_dbs():
                try:
                    if doc["message_id"]:
                        db["discord_messages"].update_one(
                            {"message_id": doc["message_id"], "bot_role": doc["bot_role"]},
                            {"$set": dict(doc), "$setOnInsert": {"created_at": now}},
                            upsert=True,
                        )
                    else:
                        d2 = dict(doc)
                        d2["created_at"] = now
                        db["discord_messages"].insert_one(d2)
                except Exception:
                    pass

        await asyncio.to_thread(_save)
        return doc

    async def create_or_update_discord_ticket(
        self,
        *,
        discord_user_id: str,
        discord_username: str,
        channel_id: str,
        guild_id: str,
        question: str,
        reply: str,
    ) -> str:
        """Create or update persistent [TRD-XXXX] ticket linked to Discord user/channel."""
        async with self._ticket_lock:
            def _upsert() -> str:
                now = datetime.now(timezone.utc)
                ticket_code = ""
                for _, db in self._dual_dbs():
                    try:
                        existing = db["conversations"].find_one({"discord_user_id": str(discord_user_id)})
                        if existing and existing.get("ticketCode"):
                            ticket_code = str(existing["ticketCode"])
                            break
                        if existing and existing.get("ticket_id"):
                            ticket_code = str(existing["ticket_id"])
                            break
                    except Exception:
                        pass
                if not ticket_code:
                    ticket_code = f"TRD-{int(time.time()) % 9000 + 1000}"

                msg_doc = {
                    "platform": "discord_trader",
                    "discord_user_id": str(discord_user_id),
                    "discord_username": sanitize_untrusted_text(discord_username, max_len=80),
                    "discord_channel_id": str(channel_id),
                    "discord_guild_id": str(guild_id),
                    "ticketCode": ticket_code,
                    "user_message": sanitize_untrusted_text(question, max_len=1500),
                    "bot_answer": sanitize_untrusted_text(reply, max_len=1500),
                    "created_at": now,
                }
                conv_set = {
                    "ticketCode": ticket_code,
                    "ticket_id": ticket_code,
                    "platform": "discord_trader",
                    "discord_user_id": str(discord_user_id),
                    "fullName": sanitize_untrusted_text(discord_username, max_len=80),
                    "discord_channel_id": str(channel_id),
                    "discord_guild_id": str(guild_id),
                    "last_message": sanitize_untrusted_text(question, max_len=500),
                    "last_bot_reply": sanitize_untrusted_text(reply, max_len=500),
                    "status": "open",
                    "updated_at": now,
                }
                for _, db in self._dual_dbs():
                    try:
                        db["discord_messages"].insert_one(dict(msg_doc))
                        db["conversations"].update_one(
                            {"discord_user_id": str(discord_user_id)},
                            {"$set": conv_set, "$setOnInsert": {"created_at": now}},
                            upsert=True,
                        )
                    except Exception:
                        pass
                return ticket_code

            tcode = await asyncio.to_thread(_upsert)
            await self.sync_unified_client_file(
                discord_user_id=str(discord_user_id),
                event="ticket_updated",
                extra={
                    "ticket_code": tcode,
                    "last_message": sanitize_untrusted_text(question, max_len=500),
                    "last_bot_reply": sanitize_untrusted_text(reply, max_len=500),
                    "discord_channel_id": str(channel_id),
                },
                sync_all=False,
            )
            return tcode

    async def get_ticket(self, ticket_code: str) -> dict[str, Any] | None:
        code = sanitize_untrusted_text(ticket_code, max_len=32).upper()

        def _find():
            for _, db in self._dual_dbs():
                try:
                    doc = db["conversations"].find_one({"$or": [{"ticketCode": code}, {"ticket_id": code}]})
                    if doc:
                        return doc
                except Exception:
                    pass
            return None

        return await asyncio.to_thread(_find)

    async def resolve_ticket(self, ticket_code: str, actor_id: str) -> bool:
        code = sanitize_untrusted_text(ticket_code, max_len=32).upper()

        def _upd() -> bool:
            updated = False
            for _, db in self._dual_dbs():
                try:
                    res = db["conversations"].update_many(
                        {"$or": [{"ticketCode": code}, {"ticket_id": code}]},
                        {"$set": {"status": "resolved", "resolved_by": str(actor_id), "updated_at": datetime.now(timezone.utc)}},
                    )
                    if res.modified_count or res.matched_count:
                        updated = True
                except Exception:
                    pass
            return updated

        return await asyncio.to_thread(_upd)

    async def list_open_tickets(self, limit: int = 10) -> list[dict[str, Any]]:
        def _list():
            for _, db in self._dual_dbs():
                try:
                    docs = list(db["conversations"].find({"status": {"$ne": "resolved"}}).sort("updated_at", -1).limit(limit))
                    if docs:
                        return docs
                except Exception:
                    pass
            return []

        return await asyncio.to_thread(_list)

    async def create_whop_tier_checkout(
        self,
        tier: str,
        *,
        discord_user_id: str = "",
        email_addr: str = "",
        full_name: str = "",
        in_community: bool = True,
        checkout_ref: str = "",
    ) -> dict[str, Any]:
        """Create a metadata-bound Whop v1 checkout session for a tier, with signed fallback URL."""
        tiers = build_tier_defs(self.cfg)
        spec = tiers.get(tier) or (tiers["workshop_45"] if in_community else tiers["external_60"])
        plan_id = spec["plan_id"]
        fallback_url = build_secure_whop_checkout_url(
            spec["checkout_url"],
            email_addr=email_addr,
            full_name=full_name,
            checkout_ref=checkout_ref or f"{spec['tier']}_{discord_user_id}",
        )
        if not self.cfg.whop_api_key or not plan_id or is_protected_client_live_target(plan_id) or is_protected_client_live_target(spec["checkout_url"]):
            return {"ok": True, "url": fallback_url, "plan_id": plan_id, "tier": spec["tier"], "price_usd": spec["price_usd"], "source": "direct_plan_fallback"}

        payload = {
            "variant_id": plan_id,
            "metadata": {
                "discord_user_id": str(discord_user_id or ""),
                "email": (email_addr or "").strip().lower(),
                "full_name": sanitize_untrusted_text(full_name or "", 80),
                "tier": spec["tier"],
                "in_community": str(bool(in_community)).lower(),
                "checkout_ref": str(checkout_ref or ""),
            },
        }
        try:
            sess = await self.get_http()
            headers = {
                "Authorization": f"Bearer {self.cfg.whop_api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            async with sess.post(
                "https://api.whop.com/api/v1/checkout_configurations",
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if 200 <= resp.status < 300:
                    data = await resp.json(content_type=None)
                    pur_url = str(data.get("purchase_url") or "")
                    if pur_url:
                        return {
                            "ok": True,
                            "url": pur_url,
                            "checkout_config_id": str(data.get("id") or ""),
                            "plan_id": plan_id,
                            "tier": spec["tier"],
                            "price_usd": spec["price_usd"],
                            "source": "whop_v1_checkout_config",
                        }
        except Exception as exc:
            logger.debug("Whop checkout_configurations fallback for %s: %s", spec["tier"], exc)
        return {"ok": True, "url": fallback_url, "plan_id": plan_id, "tier": spec["tier"], "price_usd": spec["price_usd"], "source": "direct_plan_fallback"}

    async def save_discord_lead(
        self,
        *,
        discord_user_id: str,
        discord_username: str,
        full_name: str,
        email_addr: str,
        phone_e164: str,
        challenge: str,
        channel_id: str,
        watched_videos: bool = True,
        in_community: bool | None = None,
        requested_tier: str = "",
        country: str = "",
        trading_level: str = "",
    ) -> dict[str, Any]:
        """Persist Tradesejon intake form, enforce Whop OTP verification before Prospect Role, and sync all systems."""
        uid_str = str(discord_user_id)
        if in_community is None:
            member_info = await self.discord_roles.check_is_community_member(uid_str)
            is_community = bool(member_info.get("in_community", True))
        else:
            member_info = {"already_workshop": False, "already_viproom": False, "roles": []}
            is_community = bool(in_community)

        existing_state = await self.get_discord_user_funnel_state(uid_str, email_addr=email_addr)
        # Paid V tradesejon / Workshop VIP customers always get the lower member workshop fee (4,000 EGP / $74.39).
        if existing_state.get("paid") and str(existing_state.get("tier") or "") in ("viproom_75", "workshop_45"):
            is_community = True
        if member_info.get("already_viproom"):
            is_community = True
        live_whop: dict[str, Any] = {"paid": False, "otp_verified": False}
        if not existing_state.get("paid") and (email_addr or uid_str):
            live_whop = await self.verify_whop_payment_live_for_email(email_addr, discord_user_id=uid_str)

        already_paid = bool(existing_state.get("paid") or live_whop.get("paid"))
        otp_verified = bool(existing_state.get("whop_otp_verified") or live_whop.get("otp_verified") or already_paid)
        paid_tier = str(
            (live_whop.get("tier") if live_whop.get("paid") else "")
            or existing_state.get("tier")
            or ("viproom_75" if member_info.get("already_viproom") else ("workshop_45" if member_info.get("already_workshop") else ""))
        )
        tiers = build_tier_defs(self.cfg)
        target_tier = (
            paid_tier
            if already_paid and paid_tier in tiers
            else (requested_tier if requested_tier in tiers else ("workshop_45" if is_community else "external_60"))
        )
        tier_spec = tiers[target_tier]
        paid_count = int(existing_state.get("paid_count") or (1 if already_paid else 0))

        checkout_ref, checkout_hmac = sign_checkout_ref(
            self.cfg.bridge_secret or self.cfg.aios_internal_token,
            uid_str,
            email_addr,
            phone_e164,
        )
        free_otp_checkout = await self.create_whop_tier_checkout(
            "free_otp_test",
            discord_user_id=uid_str,
            email_addr=email_addr,
            full_name=full_name,
            in_community=is_community,
            checkout_ref=f"{checkout_ref}_otp",
        )
        primary_checkout = await self.create_whop_tier_checkout(
            "workshop_45" if is_community else "external_60",
            discord_user_id=uid_str,
            email_addr=email_addr,
            full_name=full_name,
            in_community=is_community,
            checkout_ref=checkout_ref,
        )
        viproom_checkout = await self.create_whop_tier_checkout(
            "viproom_75",
            discord_user_id=uid_str,
            email_addr=email_addr,
            full_name=full_name,
            in_community=is_community,
            checkout_ref=f"{checkout_ref}_viproom",
        )
        free_otp_url = free_otp_checkout["url"]
        secure_whop_url = primary_checkout["url"]
        viproom_whop_url = viproom_checkout["url"]

        if already_paid:
            status_val = compute_next_status(
                event_type="payment.succeeded",
                current_status=str(existing_state.get("status") or ""),
                tier=target_tier,
                paid_count=paid_count,
            )
            role_ids_applied: list[str] = list(tier_spec["add_roles"])
            remove_ids: list[str] = list(tier_spec["remove_roles"])
        elif otp_verified:
            status_val = compute_next_status(
                event_type="prospect",
                current_status=str(existing_state.get("status") or ""),
                tier="free_otp_test",
                paid_count=paid_count,
            )
            role_ids_applied = [r for r in (self.cfg.prospect_role_id,) if r]
            remove_ids = []
        else:
            # Strict Whop OTP Gate: NEVER grant (or leave) Prospect Role before Whop OTP is confirmed!
            status_val = "pending_whop_otp"
            role_ids_applied = []
            remove_ids = [r for r in (self.cfg.prospect_role_id,) if r]

        def _save() -> dict[str, Any]:
            now = datetime.now(timezone.utc)
            c_ident = f"discord_{uid_str}"
            clean_email = email_addr.lower().strip()
            watched_label = "نعم (شاهد الفيديوهات)" if watched_videos else "لا (لم يشاهد الفيديوهات بعد)"
            structured_json = {
                "event": "discord_form_submitted",
                "submitted_at": now.isoformat(),
                "discord_user_id": uid_str,
                "discord_username": discord_username,
                "discord_channel_id": str(channel_id),
                "full_name": full_name,
                "email": clean_email,
                "phone_e164": phone_e164,
                "watched_videos": bool(watched_videos),
                "watched_videos_label": watched_label,
                "country": country,
                "trading_level": trading_level,
                "challenge": challenge,
                "in_community": is_community,
                "tier": target_tier,
                "plan_id": tier_spec["plan_id"],
                "amount_usd": tier_spec["price_usd"],
                "status": status_val,
                "already_paid": already_paid,
                "whop_otp_verified": otp_verified,
                "paid_count": paid_count,
                "roles": role_ids_applied,
                "checkout_ref": checkout_ref,
                "free_otp_url": free_otp_url,
                "whop_checkout_url": secure_whop_url,
                "viproom_checkout_url": viproom_whop_url,
            }
            organized_nl = (
                f"📋 استمارة متداول Tradesejon من Discord ({now.strftime('%Y-%m-%d %H:%M UTC')}):\n"
                f"• الاسم: {full_name} (@{discord_username} | Discord ID: {uid_str})\n"
                f"• البريد الإلكتروني: {clean_email}\n"
                f"• رقم الهاتف / الواتساب: {phone_e164 or 'لم يُدخل بعد (اختبار Whop OTP بالإيميل)'}\n"
                f"• البلد: {country or 'غير محدد'} | المستوى: {trading_level or 'غير محدد'}\n"
                f"• هل شاهد فيديوهاتنا؟: {watched_label}\n"
                f"• الهدف / التحدي: {challenge}\n"
                f"• عضو في السيرفر: {'نعم' if is_community else 'لا'} | الباقة: {tier_spec['title']} (${tier_spec['price_usd']})\n"
                f"• تأكيد Whop OTP: {'تم التأكيد ✅' if otp_verified else 'بانتظار تأكيد كود OTP على Whop ⏳'}\n"
                f"• الحالة في القمع: {status_val} | الدفع مؤكد 100%: {'نعم ✅' if already_paid else 'بانتظار التأكيد على Whop'}\n"
                f"• مرجع الطلب: {checkout_ref}"
            )
            lead_doc = {
                "full_name": full_name,
                "name": full_name,
                "email": clean_email,
                "phone": phone_e164,
                "phoneE164": phone_e164,
                "discord_user_id": uid_str,
                "discord_username": discord_username,
                "discord_channel_id": str(channel_id),
                "challenge": challenge,
                "country": country,
                "trading_level": trading_level,
                "watched_videos": bool(watched_videos),
                "watched_videos_label": watched_label,
                "structured_json": structured_json,
                "organized_natural_language": organized_nl,
                "form_validated": True,
                "whop_otp_verified": otp_verified,
                "free_otp_url": free_otp_url,
                "free_access": False,
                "in_community": is_community,
                "tier": target_tier,
                "plan_id": tier_spec["plan_id"],
                "amount_usd": tier_spec["price_usd"],
                "whop_plan_id": tier_spec["plan_id"],
                "checkout_ref": checkout_ref,
                "checkout_hmac": checkout_hmac,
                "whop_checkout_url": secure_whop_url,
                "viproom_checkout_url": viproom_whop_url,
                "paid": already_paid,
                "is_paid": already_paid,
                "paid_count": paid_count,
                "roles": role_ids_applied,
                "lead_score": "VIP_PAID" if already_paid else ("PROSPECT_OTP_VERIFIED" if otp_verified else "PENDING_WHOP_OTP"),
                "status": status_val,
                "platform": "discord",
                "source": "discord_marhaban_form",
                "consent": {"email": True},
                "updated_at": now,
            }
            for _, db in self._dual_dbs():
                try:
                    db["trading_leads"].update_one(
                        {"email": clean_email},
                        {"$set": dict(lead_doc), "$setOnInsert": {"created_at": now}},
                        upsert=True,
                    )
                    db["leads"].update_one(
                        {"email": clean_email},
                        {"$set": dict(lead_doc), "$setOnInsert": {"created_at": now}},
                        upsert=True,
                    )
                    db["discord_forms"].insert_one({**lead_doc, "submitted_at": now})
                    db["aios_super_memory"].update_one(
                        {"key": f"discord_form_{uid_str}"},
                        {
                            "$set": {
                                "key": f"discord_form_{uid_str}",
                                "type": "discord_form_submission",
                                "user_query": f"Discord Form {full_name} ({clean_email})",
                                "ai_reply": organized_nl,
                                "rule": organized_nl,
                                "structured_json": structured_json,
                                "created_at": now,
                            }
                        },
                        upsert=True,
                    )
                    db["funnel_status_events"].insert_one({
                        "discord_user_id": uid_str,
                        "email": clean_email,
                        "status": status_val,
                        "tier": target_tier,
                        "amount_usd": tier_spec["price_usd"],
                        "event": "form_submitted",
                        "at": now,
                    })
                    db["pending_whop_checkouts"].update_one(
                        {"discord_user_id": uid_str},
                        {
                            "$set": {
                                "discord_user_id": uid_str,
                                "discord_username": discord_username,
                                "discord_channel_id": str(channel_id),
                                "email": clean_email,
                                "phone": phone_e164,
                                "full_name": full_name,
                                "watched_videos": bool(watched_videos),
                                "in_community": is_community,
                                "tier": target_tier,
                                "paid": already_paid,
                                "whop_otp_verified": otp_verified,
                                "status": status_val,
                                "whop_plan_id": tier_spec["plan_id"],
                                "checkout_ref": checkout_ref,
                                "checkout_hmac": checkout_hmac,
                                "free_otp_url": free_otp_url,
                                "whop_checkout_url": secure_whop_url,
                                "viproom_checkout_url": viproom_whop_url,
                                "updated_at": now,
                            },
                            "$setOnInsert": {"created_at": now},
                        },
                        upsert=True,
                    )
                    db["contacts"].update_one(
                        {"contact_identifier": c_ident},
                        {
                            "$set": {
                                "contact_identifier": c_ident,
                                "name": full_name,
                                "full_name": full_name,
                                "number": phone_e164,
                                "phone_number": phone_e164,
                                "email": clean_email,
                                "discord_user_id": uid_str,
                                "discord_username": discord_username,
                                "discord_channel_id": str(channel_id),
                                "watched_videos": bool(watched_videos),
                                "in_community": is_community,
                                "status": status_val,
                                "tier": target_tier,
                                "whop_otp_verified": otp_verified,
                                "form_validated": True,
                                "platform": "discord",
                                "updated_at": now,
                            }
                        },
                        upsert=True,
                    )
                except Exception:
                    pass
            return {
                "contact_identifier": c_ident,
                "checkout_ref": checkout_ref,
                "free_otp_url": free_otp_url,
                "whop_checkout_url": secure_whop_url,
                "viproom_checkout_url": viproom_whop_url,
                "in_community": is_community,
                "tier": target_tier,
                "price_usd": tier_spec["price_usd"],
                "plan_id": tier_spec["plan_id"],
                "status": status_val,
                "already_paid": already_paid,
                "whop_otp_verified": otp_verified,
                "roles": role_ids_applied,
                "watched_videos": bool(watched_videos),
                "watched_videos_label": watched_label,
                "structured_json": structured_json,
                "organized_natural_language": organized_nl,
            }

        saved = await asyncio.to_thread(_save)
        role_res = await self.discord_roles.apply_funnel_roles(
            uid_str,
            add_role_ids=role_ids_applied,
            remove_role_ids=remove_ids,
            reason=f"Tradesejon form: status={status_val}",
        )
        saved["role_transition"] = role_res

        if already_paid and live_whop.get("paid") and not existing_state.get("paid"):
            await self.fulfill_whop_payment_for_discord(
                email_addr=email_addr,
                phone=phone_e164,
                discord_user_id=uid_str,
                whop_payment_id=str(live_whop.get("whop_payment_id") or ""),
                checkout_ref=checkout_ref,
                tier=target_tier,
                plan_id=tier_spec["plan_id"],
                amount_usd=float(tier_spec["price_usd"]),
            )
            saved["already_paid"] = True
        elif otp_verified and live_whop.get("otp_verified") and not existing_state.get("whop_otp_verified"):
            await self.fulfill_whop_payment_for_discord(
                email_addr=email_addr,
                phone=phone_e164,
                discord_user_id=uid_str,
                whop_payment_id=str(live_whop.get("whop_payment_id") or ""),
                checkout_ref=checkout_ref,
                tier="free_otp_test",
                plan_id="plan_TEMPLATE_01",
                amount_usd=0.0,
            )
            saved["whop_otp_verified"] = True
        else:
            await self.sync_unified_client_file(
                discord_user_id=uid_str,
                email_addr=email_addr.lower(),
                event="form_submitted",
                extra={
                    "full_name": full_name,
                    "email": email_addr.lower(),
                    "phone_e164": phone_e164,
                    "discord_username": discord_username,
                    "discord_channel_id": str(channel_id),
                    "watched_videos": bool(watched_videos),
                    "watched_videos_label": saved.get("watched_videos_label", ""),
                    "challenge": challenge,
                    "in_community": is_community,
                    "tier": target_tier,
                    "tier_title": tier_spec["title"],
                    "plan_id": tier_spec["plan_id"],
                    "amount_usd": tier_spec["price_usd"],
                    "status": status_val,
                    "whop_otp_verified": otp_verified,
                    "paid": already_paid,
                    "roles": role_ids_applied,
                    "checkout_ref": checkout_ref,
                    "free_otp_url": free_otp_url,
                    "whop_checkout_url": secure_whop_url,
                    "viproom_checkout_url": viproom_whop_url,
                },
                sync_all=True,
            )
        return saved

    async def save_email_for_whop_otp(
        self,
        *,
        discord_user_id: str,
        discord_username: str,
        email_addr: str,
        channel_id: str = "",
        full_name: str = "",
    ) -> dict[str, Any]:
        """Handle email-only Free Whop OTP flow: hold #prospects until Whop confirms OTP, then promote immediately."""
        return await self.save_discord_lead(
            discord_user_id=discord_user_id,
            discord_username=discord_username,
            full_name=full_name or discord_username or email_addr.split("@")[0],
            email_addr=email_addr,
            phone_e164="",
            challenge="Whop Free $0 Email + OTP Test",
            channel_id=channel_id,
            watched_videos=True,
            requested_tier="free_otp_test",
        )

    async def update_discord_lead_watched_videos(self, *, discord_user_id: str, watched: bool) -> bool:
        """Persist watched_videos answer across MongoDB, client_files, disk JSON, CSV, and AIOS."""
        uid_str = str(discord_user_id or "").strip()
        if not uid_str:
            return False
        watched_lbl = "نعم (شاهد الفيديوهات)" if watched else "لا (لم يشاهد الفيديوهات بعد)"

        def _update():
            now = datetime.now(timezone.utc)
            upd = {"watched_videos": bool(watched), "watched_videos_label": watched_lbl, "updated_at": now}
            for _, db in self._dual_dbs():
                try:
                    for col_name in ("trading_leads", "leads", "contacts", "discord_forms", "pending_whop_checkouts"):
                        db[col_name].update_many({"discord_user_id": uid_str}, {"$set": dict(upd)})
                except Exception as ex:
                    logger.debug("update_discord_lead_watched_videos db error: %s", ex)

        await asyncio.to_thread(_update)
        await self.sync_unified_client_file(
            discord_user_id=uid_str,
            event="watched_videos_updated",
            extra={"watched_videos": bool(watched), "watched_videos_label": watched_lbl},
            sync_all=True,
        )
        return True

    def _resolve_client_files_dir(self) -> Path:
        """Return safe disk folder for client dossier JSON files (isolated in temp during tests)."""
        csv_hint = str(self.cfg.tradesajon_crm_csv_path or "").lower()
        if "pytest" in csv_hint or "\\temp\\" in csv_hint or "/temp/" in csv_hint:
            d = Path(tempfile.gettempdir()) / "tradesejon_pytest_clients"
        else:
            d = Path(self.cfg.tradesejon_knowledge_base_dir or r"./data/workspace") / "clients"
        d.mkdir(parents=True, exist_ok=True)
        return d

    async def sync_unified_client_file(
        self,
        *,
        discord_user_id: str = "",
        email_addr: str = "",
        event: str = "client_file_updated",
        extra: dict[str, Any] | None = None,
        sync_all: bool = True,
    ) -> dict[str, Any]:
        """Aggregate all client fields and auto-update MongoDB client_files, AIOS memory, disk JSON, CSV & n8n."""
        uid_str = str(discord_user_id or "").strip()
        clean_mail = str(email_addr or "").strip().lower()
        extra_map = dict(extra or {})

        def _build_and_store() -> dict[str, Any]:
            now = datetime.now(timezone.utc)
            now_iso = now.isoformat()
            q_parts: list[dict[str, Any]] = []
            if uid_str:
                q_parts.append({"discord_user_id": uid_str})
            if clean_mail:
                q_parts.append({"email": clean_mail})
            merged: dict[str, Any] = {}
            for _, db in self._dual_dbs():
                if not q_parts:
                    break
                query = q_parts[0] if len(q_parts) == 1 else {"$or": q_parts}
                for col in ("trading_leads", "pending_whop_checkouts", "whop_payments", "conversations", "zoom_access_tokens", "client_files"):
                    try:
                        doc = db[col].find_one(query)
                        if isinstance(doc, dict):
                            for k, v in doc.items():
                                if k == "_id":
                                    continue
                                if v not in (None, "", [], {}) or k not in merged:
                                    merged[k] = v
                    except Exception:
                        pass
            for k, v in extra_map.items():
                if v not in (None, "") or k not in merged:
                    merged[k] = v

            eff_uid = str(merged.get("discord_user_id") or uid_str or "").strip()
            eff_mail = str(merged.get("email") or clean_mail or "").strip().lower()
            eff_phone = str(merged.get("phone_e164") or merged.get("phone") or merged.get("phoneNumber") or "").strip()
            eff_name = str(merged.get("full_name") or merged.get("name") or merged.get("fullName") or (eff_mail.split("@")[0] if eff_mail else "Trader")).strip()
            eff_watched = bool(merged.get("watched_videos", False))
            eff_watched_lbl = str(merged.get("watched_videos_label") or ("نعم (شاهد الفيديوهات)" if eff_watched else "لا (لم يشاهد الفيديوهات بعد)"))
            eff_status = str(merged.get("status") or "pending_whop_otp")
            eff_tier = str(merged.get("tier") or "")
            eff_tier_title = str(merged.get("tier_title") or eff_tier or "-")
            eff_plan_id = str(merged.get("plan_id") or merged.get("whop_plan_id") or "")
            try:
                eff_amt = float(merged.get("amount_usd") or 0.0)
            except Exception:
                eff_amt = 0.0
            eff_otp = bool(merged.get("whop_otp_verified") or merged.get("paid") or eff_status in ("prospect", "prospect_paid_once", "vip_recurring", "viproom_workshop_vip"))
            eff_paid = bool(merged.get("paid") or merged.get("is_paid") or eff_status in ("prospect_paid_once", "vip_recurring", "viproom_workshop_vip"))

            client_key = f"uid_{eff_uid}" if eff_uid else f"mail_{re.sub(r'[^a-zA-Z0-9._-]+', '_', eff_mail or 'unknown')}"
            structured_json = {
                "client_key": client_key,
                "last_event": event,
                "updated_at": now_iso,
                "discord_user_id": eff_uid,
                "discord_username": str(merged.get("discord_username") or ""),
                "discord_channel_id": str(merged.get("discord_channel_id") or ""),
                "full_name": eff_name,
                "email": eff_mail,
                "phone_e164": eff_phone,
                "watched_videos": eff_watched,
                "watched_videos_label": eff_watched_lbl,
                "challenge": str(merged.get("challenge") or ""),
                "in_community": bool(merged.get("in_community", True)),
                "status": eff_status,
                "whop_otp_verified": eff_otp,
                "paid": eff_paid,
                "paid_count": int(merged.get("paid_count") or (1 if eff_paid else 0)),
                "tier": eff_tier,
                "tier_title": eff_tier_title,
                "plan_id": eff_plan_id,
                "amount_usd": eff_amt,
                "mentor": str(merged.get("mentor") or ""),
                "workshop_mode": str(merged.get("workshop_mode") or ""),
                "checkout_ref": str(merged.get("checkout_ref") or ""),
                "free_otp_url": str(merged.get("free_otp_url") or ""),
                "whop_checkout_url": str(merged.get("whop_checkout_url") or ""),
                "viproom_checkout_url": str(merged.get("viproom_checkout_url") or ""),
                "unique_zoom_url": str(merged.get("unique_zoom_url") or merged.get("live_link") or ""),
                "whop_payment_id": str(merged.get("whop_payment_id") or merged.get("whopPaymentId") or ""),
                "roles": list(merged.get("roles") or merged.get("roles_added") or []),
                "ticket_code": str(merged.get("ticket_code") or merged.get("ticketCode") or ""),
                "last_message": str(merged.get("last_message") or ""),
                "last_bot_reply": str(merged.get("last_bot_reply") or ""),
            }
            organized_nl = (
                f"📂 ملف العميل الشامل Tradesejon ({now.strftime('%Y-%m-%d %H:%M UTC')} | آخر حدث: {event}):\n"
                f"• الاسم: {eff_name} (@{structured_json['discord_username']} | Discord ID: {eff_uid or '-'})\n"
                f"• البريد الإلكتروني: {eff_mail or '-'} | الهاتف: {eff_phone or '-'}\n"
                f"• هل شاهد فيديوهاتنا؟: {eff_watched_lbl}\n"
                f"• التحدي / الهدف: {structured_json['challenge'] or '-'}\n"
                f"• تأكيد Whop OTP: {'تم ✅' if eff_otp else 'بانتظار OTP ⏳'} | الحالة: {eff_status}\n"
                f"• الباقة: {eff_tier_title} (${eff_amt}) | خطة Whop: {eff_plan_id or '-'}\n"
                + (f"• المحاضر: {structured_json['mentor']}\n" if structured_json["mentor"] else "")
                + f"• التذكرة: {structured_json['ticket_code'] or '-'} | آخر رسالة: {structured_json['last_message'][:180] or '-'}"
            )
            dossier_doc = {
                **structured_json,
                "phone": eff_phone,
                "organized_natural_language": organized_nl,
                "structured_json": structured_json,
                "updated_at": now,
            }
            for _, db in self._dual_dbs():
                try:
                    db["client_files"].update_one({"client_key": client_key}, {"$set": dict(dossier_doc)}, upsert=True)
                    upd_target = {"discord_user_id": eff_uid} if eff_uid else {"email": eff_mail}
                    for col in ("trading_leads", "leads", "contacts"):
                        db[col].update_many(
                            upd_target,
                            {"$set": {"structured_json": structured_json, "organized_natural_language": organized_nl, "watched_videos": eff_watched, "watched_videos_label": eff_watched_lbl, "updated_at": now}},
                        )
                    if eff_uid:
                        db["aios_super_memory"].update_one(
                            {"key": f"discord_form_{eff_uid}"},
                            {"$set": {"key": f"discord_form_{eff_uid}", "type": "discord_form_submission", "user_query": f"Discord Form {eff_name} ({eff_mail})", "ai_reply": organized_nl, "rule": organized_nl, "structured_json": structured_json, "updated_at": now}},
                            upsert=True,
                        )
                    db["aios_super_memory"].update_one(
                        {"key": f"client_file_{client_key}"},
                        {"$set": {"key": f"client_file_{client_key}", "type": "client_file_dossier", "user_query": f"Client File {eff_name} ({eff_mail})", "ai_reply": organized_nl, "rule": organized_nl, "structured_json": structured_json, "updated_at": now}},
                        upsert=True,
                    )
                except Exception:
                    pass

            try:
                clients_dir = self._resolve_client_files_dir()
                dossier_json_path = clients_dir / f"{client_key}.json"
                payload_on_disk = {**structured_json, "organized_natural_language": organized_nl}
                dossier_json_path.write_text(json.dumps(payload_on_disk, ensure_ascii=False, indent=2), encoding="utf-8")
                idx_path = clients_dir / "master_clients_index.json"
                idx_data: dict[str, Any] = {}
                if idx_path.exists():
                    try:
                        idx_data = json.loads(idx_path.read_text(encoding="utf-8"))
                    except Exception:
                        idx_data = {}
                idx_data[client_key] = {
                    "client_key": client_key,
                    "full_name": eff_name,
                    "email": eff_mail,
                    "phone_e164": eff_phone,
                    "status": eff_status,
                    "whop_otp_verified": eff_otp,
                    "paid": eff_paid,
                    "tier": eff_tier,
                    "watched_videos": eff_watched,
                    "updated_at": now_iso,
                    "file": str(dossier_json_path),
                }
                idx_path.write_text(json.dumps(idx_data, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
            return {**structured_json, "phone": eff_phone, "structured_json": structured_json, "organized_natural_language": organized_nl}

        dossier = await asyncio.to_thread(_build_and_store)
        if sync_all:
            await self.sync_crm_snapshot_to_all({
                **dossier,
                "event": event,
                "source": "unified_client_file_autosync",
            })
        return dossier

    async def get_discord_user_funnel_state(self, discord_user_id: str, *, email_addr: str = "") -> dict[str, Any]:
        """Return form validation, funnel status, tier, roles, and verified Whop payment state."""
        uid = str(discord_user_id)
        clean_mail = (email_addr or "").strip().lower()

        def _check() -> dict[str, Any]:
            state: dict[str, Any] = {
                "form_validated": False,
                "paid": False,
                "whop_otp_verified": False,
                "email": clean_mail,
                "free_otp_url": "",
                "checkout_url": "",
                "viproom_checkout_url": "",
                "full_name": "",
                "phone": "",
                "status": "new",
                "tier": "",
                "paid_count": 0,
                "in_community": True,
                "roles": [],
            }
            for _, db in self._dual_dbs():
                try:
                    lead = db["trading_leads"].find_one({"discord_user_id": uid})
                    if not lead and clean_mail:
                        lead = db["trading_leads"].find_one({"email": clean_mail})
                    pend = db["pending_whop_checkouts"].find_one({"discord_user_id": uid})
                    pay = db["whop_payments"].find_one({"discord_user_id": uid})
                    email_val = ((lead or pend or {}).get("email", "") or clean_mail).lower()
                    if not pay and email_val:
                        pay = db["whop_payments"].find_one({"email": email_val})
                    if (lead and lead.get("form_validated")) or bool(pend):
                        src = lead or pend or {}
                        state["form_validated"] = True
                        state["email"] = email_val
                        state["full_name"] = src.get("full_name", "")
                        state["phone"] = src.get("phone", "")
                        state["free_otp_url"] = src.get("free_otp_url", "")
                        state["checkout_url"] = src.get("whop_checkout_url", "")
                        state["viproom_checkout_url"] = src.get("viproom_checkout_url", "")
                        state["status"] = src.get("status", "pending_whop_otp") or "pending_whop_otp"
                        state["tier"] = src.get("tier", "")
                        state["in_community"] = bool(src.get("in_community", True))
                        state["roles"] = list(src.get("roles") or [])
                        state["whop_otp_verified"] = bool(
                            src.get("whop_otp_verified")
                            or state["status"] in ("prospect", "prospect_paid_once", "vip_recurring", "viproom_workshop_vip")
                        )
                    for rec in (lead, pend, pay):
                        if not isinstance(rec, dict):
                            continue
                        st = str(rec.get("status") or "").lower()
                        if rec.get("whop_otp_verified") is True or st in ("prospect",):
                            state["whop_otp_verified"] = True
                            if state["status"] in ("new", "pending_whop_otp"):
                                state["status"] = "prospect"
                        if (
                            rec.get("paid") is True
                            or rec.get("is_paid") is True
                            or st in ("paid_whop", "vip_paid", "paid", "attended", "prospect_paid_once", "vip_recurring", "viproom_workshop_vip")
                            or bool((rec.get("payment") or {}).get("whopPaymentId") if isinstance(rec.get("payment"), dict) else False)
                        ):
                            state["paid"] = True
                            state["whop_otp_verified"] = True
                            state["form_validated"] = True
                            state["email"] = state["email"] or str(rec.get("email") or "").lower()
                            state["full_name"] = state["full_name"] or str(rec.get("full_name") or "")
                            state["status"] = str(rec.get("status") or "prospect_paid_once")
                            state["tier"] = str(rec.get("tier") or state["tier"] or "workshop_45")
                            state["paid_count"] = max(int(state["paid_count"]), int(rec.get("paid_count") or 1))
                            return state
                    if state["form_validated"]:
                        return state
                except Exception:
                    pass
            return state

        return await asyncio.to_thread(_check)

    async def fulfill_whop_payment_for_discord(
        self,
        *,
        email_addr: str = "",
        phone: str = "",
        discord_user_id: str = "",
        whop_payment_id: str = "",
        checkout_ref: str = "",
        tier: str = "",
        plan_id: str = "",
        amount_usd: float = 0.0,
        send_notifications: bool = True,
    ) -> dict[str, Any]:
        """Bind verified Whop payment, promote status & roles (out of Prospect into Workshop / V tradesejon), mint unique live link, and sync all systems."""
        clean_email = (email_addr or "").strip().lower()
        clean_uid = str(discord_user_id or "").strip()
        tier_payload: dict[str, Any] = {
            "tier": tier,
            "variant_id": plan_id,
            "metadata": {"tier": tier, "plan_id": plan_id},
        }
        if amount_usd > 0 or tier == "free_otp_test" or plan_id == "plan_TEMPLATE_01":
            tier_payload["amount"] = amount_usd
        tier_spec = resolve_tier_from_payment(tier_payload, self.cfg)

        def _fulfill() -> dict[str, Any]:
            now = datetime.now(timezone.utc)
            matched_uid = clean_uid
            matched_channel = ""
            matched_name = ""
            matched_email = clean_email
            matched_phone = phone
            prev_status = ""
            prev_paid_count = 0
            in_comm = True
            clauses = []
            if clean_uid:
                clauses.append({"discord_user_id": clean_uid})
            if clean_email:
                clauses.append({"email": clean_email})
            if checkout_ref:
                clauses.append({"checkout_ref": checkout_ref})
            if phone:
                clauses.append({"phone": phone})
            query = {"$or": clauses} if clauses else {}

            for _, db in self._dual_dbs():
                try:
                    if query:
                        for coll_name in ("pending_whop_checkouts", "trading_leads", "leads", "contacts"):
                            doc = db[coll_name].find_one(query)
                            if doc:
                                matched_uid = matched_uid or str(doc.get("discord_user_id") or "")
                                matched_channel = matched_channel or str(doc.get("discord_channel_id") or "")
                                matched_name = matched_name or str(doc.get("full_name") or doc.get("name") or "")
                                matched_email = matched_email or str(doc.get("email") or "").lower()
                                matched_phone = matched_phone or str(doc.get("phone") or doc.get("phoneE164") or "")
                                prev_status = prev_status or str(doc.get("status") or "")
                                prev_paid_count = max(prev_paid_count, int(doc.get("paid_count") or (1 if doc.get("paid") else 0)))
                                if "in_community" in doc:
                                    in_comm = bool(doc["in_community"])

                    pay_id = whop_payment_id or f"whop_{int(time.time())}"
                    already_logged = False
                    try:
                        already_logged = bool(db["whop_payments"].find_one({"whopPaymentId": pay_id}))
                    except Exception:
                        already_logged = False
                    is_free_otp = tier_spec["tier"] == "free_otp_test"
                    new_paid_count = prev_paid_count if is_free_otp else (prev_paid_count if already_logged and prev_paid_count >= 1 else prev_paid_count + 1)
                    next_status = compute_next_status(
                        event_type="payment.succeeded",
                        current_status=prev_status,
                        tier=tier_spec["tier"],
                        paid_count=new_paid_count,
                    )
                    upd = {
                        "paid": (not is_free_otp) or (prev_paid_count >= 1),
                        "is_paid": (not is_free_otp) or (prev_paid_count >= 1),
                        "whop_otp_verified": True,
                        "status": next_status,
                        "tier": tier_spec["tier"],
                        "plan_id": tier_spec["plan_id"],
                        "whop_plan_id": tier_spec["plan_id"],
                        "amount_usd": 0.0 if is_free_otp else (amount_usd or tier_spec["price_usd"]),
                        "paid_count": new_paid_count,
                        "roles": list(tier_spec["add_roles"]),
                        "in_community": in_comm,
                        "lead_score": (
                            "PROSPECT_OTP_VERIFIED"
                            if is_free_otp
                            else ("VIPROOM_WORKSHOP_VIP" if tier_spec["tier"] == "viproom_75" else "VIP_WORKSHOP_PAID")
                        ),
                        "whopPaymentId": pay_id,
                        "otp_verified_at": now,
                        "updated_at": now,
                    }
                    if not is_free_otp:
                        upd["paid_at"] = now
                    if query:
                        db["pending_whop_checkouts"].update_many(query, {"$set": upd})
                        db["trading_leads"].update_many(query, {"$set": upd})
                        db["leads"].update_many(query, {"$set": upd})
                        db["contacts"].update_many(query, {"$set": {"status": next_status, "tier": tier_spec["tier"], "paid": upd["paid"], "whop_otp_verified": True, "updated_at": now}})
                    db["whop_payments"].update_one(
                        {"whopPaymentId": pay_id} if pay_id else {"email": matched_email or matched_uid or "unknown"},
                        {
                            "$set": {
                                **upd,
                                "email": matched_email,
                                "discord_user_id": matched_uid,
                                "full_name": matched_name,
                                "phone": matched_phone,
                            }
                        },
                        upsert=True,
                    )
                    db["funnel_status_events"].insert_one({
                        "discord_user_id": matched_uid,
                        "email": matched_email,
                        "status": next_status,
                        "tier": tier_spec["tier"],
                        "plan_id": tier_spec["plan_id"],
                        "amount_usd": 0.0 if is_free_otp else (amount_usd or tier_spec["price_usd"]),
                        "whop_payment_id": pay_id,
                        "event": "whop.otp_verified" if is_free_otp else "payment.succeeded",
                        "at": now,
                    })
                    break_result = {
                        "ok": bool(matched_uid or matched_email),
                        "discord_user_id": matched_uid,
                        "discord_channel_id": matched_channel,
                        "full_name": matched_name or "Tradesejon VIP",
                        "email": matched_email,
                        "phone": matched_phone,
                        "tier": tier_spec["tier"],
                        "tier_title": tier_spec["title"],
                        "plan_id": tier_spec["plan_id"],
                        "amount_usd": 0.0 if is_free_otp else (amount_usd or tier_spec["price_usd"]),
                        "status": next_status,
                        "whop_otp_verified": True,
                        "is_free_otp": is_free_otp,
                        "paid": upd["paid"],
                        "paid_count": new_paid_count,
                        "roles_added": list(tier_spec["add_roles"]),
                        "roles_removed": list(tier_spec["remove_roles"]),
                        "mentor": str(tier_spec.get("mentor") or ""),
                        "workshop_mode": str(tier_spec.get("workshop_mode") or ""),
                    }
                except Exception:
                    break_result = {}
            is_free_fallback = tier_spec["tier"] == "free_otp_test"
            return break_result or {
                "ok": bool(matched_uid or matched_email),
                "discord_user_id": matched_uid,
                "discord_channel_id": matched_channel,
                "full_name": matched_name or "Tradesejon VIP",
                "email": matched_email,
                "phone": matched_phone,
                "tier": tier_spec["tier"],
                "tier_title": tier_spec["title"],
                "plan_id": tier_spec["plan_id"],
                "amount_usd": 0.0 if is_free_fallback else (amount_usd or tier_spec["price_usd"]),
                "status": tier_spec["default_status"],
                "whop_otp_verified": True,
                "is_free_otp": is_free_fallback,
                "paid": not is_free_fallback,
                "paid_count": 0 if is_free_fallback else 1,
                "roles_added": list(tier_spec["add_roles"]),
                "roles_removed": list(tier_spec["remove_roles"]),
                "mentor": str(tier_spec.get("mentor") or ""),
                "workshop_mode": str(tier_spec.get("workshop_mode") or ""),
            }

        res = await asyncio.to_thread(_fulfill)
        zoom_res: dict[str, Any] = {}
        is_free_otp_res = bool(res.get("is_free_otp") or res.get("tier") == "free_otp_test")
        if (not is_free_otp_res) and (res.get("discord_user_id") or res.get("email") or checkout_ref or whop_payment_id):
            zoom_res = await self.create_unique_zoom_link_for_payer(
                discord_user_id=res.get("discord_user_id", ""),
                email_addr=res.get("email", ""),
                full_name=res.get("full_name", "Tradesejon VIP"),
                checkout_ref=checkout_ref,
                whop_payment_id=whop_payment_id,
            )
        official_live_url = self.cfg.marhaban_invite_url or "https://discord.gg/YOUR_INVITE_CODE"
        live_url = "" if is_free_otp_res else str(zoom_res.get("discord_live_url") or zoom_res.get("unique_zoom_url") or official_live_url)
        if live_url and ("KbYSbUtj" in live_url or "9RMcZVdVE6" in live_url):
            live_url = official_live_url
        res["unique_zoom_url"] = live_url
        res["discord_live_url"] = live_url
        res["zoom_token"] = str(zoom_res.get("token") or "")
        res["zoom_source"] = str(zoom_res.get("zoom_source") or "")

        role_res = {"ok": False, "skipped": True}
        if res.get("discord_user_id"):
            role_res = await self.discord_roles.apply_funnel_roles(
                res["discord_user_id"],
                add_role_ids=res.get("roles_added") or [],
                remove_role_ids=res.get("roles_removed") or [],
                reason=f"Whop {'OTP' if is_free_otp_res else 'payment'} fulfilled: {res['tier']} ({res['status']})",
            )
        res["role_transition"] = role_res

        if send_notifications and str(res.get("discord_user_id") or "").isdigit():
            uid_str = str(res["discord_user_id"])
            mention = f"<@{uid_str}>"
            if is_free_otp_res:
                await self.discord_roles.send_persona_message(
                    "prospect",
                    recipient_user_id=uid_str,
                    content=(
                        f"🎉 أهلاً بك يا {mention} في **Prospects Bot** (`#prospects`)!\n"
                        f"✅ تم تأكيد كود **Whop OTP** عبر بريدك بنجاح وتم نقلك فوراً إلى **Prospects Bot** (`Prospect Role`).\n"
                        "💬 يمكنك الآن طرح أي سؤال هنا وسيرد عليك **الـ AI Agent وفريق الدعم البشري معاً**!\n"
                        "💳 **الباقات الرسمية المتاحة للترقية لـ `#workshop-channel`:**\n"
                        "• لايف الديسكورد اليومي + الـ Scanner: **`$45 / شهر`** (https://whop.com/Tradesejon/exp_TEMPLATE_VIP"
                        "• Offline Workshop (Upcoming Scheduled Workshop): **`4,000 EGP ($74.39)`** للأعضاء | **`6,000 EGP ($111.59)`** لغير الأعضاء\n"
                        "• Online Workshop الخاصة (4 ساعات مع المحاضر): **`$100`**"
                    ),
                )
            else:
                if res["tier"] == "viproom_75":
                    await self.discord_roles.send_persona_message(
                        "viproom",
                        recipient_user_id=uid_str,
                        content=(
                            f"🌟 أهلاً بك في **Online Workshop VIP ($100) + V tradesejon** يا {mention}!\n"
                            f"✅ تم تفعيل الدورين لك (**V tradesejon Role + Workshop Role**) وإخراجك من قناة Prospect.\n"
                            f"🎯 رابط البث المباشر للورشة: {live_url}\n"
                            f"📊 حالتك الآن: `{res['status']}`"
                        ),
                    )
                await self.discord_roles.send_persona_message(
                    "workshop",
                    recipient_user_id=uid_str,
                    content=(
                        f"🚀 مرحباً بك في **Workshop Channel** يا {mention}!\n"
                        f"✅ تم تأكيد اشتراكك (**${res['amount_usd']}**) وتحويل حالتك إلى `{res['status']}` وإزالتك من Prospect Bot.\n"
                        f"🔴 دخول البث المباشر: <#{self.cfg.discord_live_channel_id}> | {live_url}"
                    ),
                )

        await self.sync_unified_client_file(
            discord_user_id=res.get("discord_user_id", ""),
            email_addr=res.get("email", ""),
            event="whop.otp_verified" if is_free_otp_res else "payment.succeeded",
            extra={
                "full_name": res.get("full_name", ""),
                "email": res.get("email", ""),
                "phone_e164": res.get("phone", ""),
                "tier": res["tier"],
                "tier_title": res.get("tier_title", ""),
                "plan_id": res["plan_id"],
                "amount_usd": res["amount_usd"],
                "mentor": res.get("mentor", ""),
                "workshop_mode": res.get("workshop_mode", ""),
                "status": res["status"],
                "whop_otp_verified": True,
                "paid": res.get("paid", not is_free_otp_res),
                "paid_count": res.get("paid_count", 0 if is_free_otp_res else 1),
                "roles": res.get("roles_added") or [],
                "whop_payment_id": whop_payment_id,
                "unique_zoom_url": live_url,
            },
            sync_all=True,
        )
        return res

    async def update_live_session_zoom(
        self,
        *,
        zoom_url: str = "",
        zoom_id: str = "",
        zoom_pass: str = "",
        session_time: str = "",
    ) -> dict[str, Any]:
        """Update active Tradesejon Workshop Zoom settings (CEO Admin only)."""
        def _upd() -> dict[str, Any]:
            fields: dict[str, Any] = {"updated_at": datetime.now(timezone.utc)}
            if zoom_url:
                fields["zoom_url"] = zoom_url.strip()
            if zoom_id:
                fields["zoom_id"] = sanitize_untrusted_text(zoom_id, 64)
            if zoom_pass:
                fields["zoom_pass"] = sanitize_untrusted_text(zoom_pass, 64)
            if session_time:
                fields["session_time"] = sanitize_untrusted_text(session_time, 120)
            for _, db in self._dual_dbs():
                try:
                    if zoom_url or zoom_id or zoom_pass:
                        db["aios_config"].update_one({"key": "zoom_config"}, {"$set": fields}, upsert=True)
                    if session_time:
                        db["aios_config"].update_one({"key": "session_time"}, {"$set": {"value": session_time, **fields}}, upsert=True)
                    camp_set = {}
                    if zoom_url:
                        camp_set["zoomUrl"] = zoom_url.strip()
                    if zoom_id:
                        camp_set["zoomMeetingId"] = fields["zoom_id"]
                    if zoom_pass:
                        camp_set["zoomPasscode"] = fields["zoom_pass"]
                    if session_time:
                        camp_set["startAt"] = fields["session_time"]
                    if camp_set:
                        db["campaigns"].update_one({"_id": "workshop-poc"}, {"$set": camp_set}, upsert=True)
                    if zoom_url:
                        db["zoom_access_tokens"].update_many(
                            {"revoked": {"$ne": True}, "zoom_source": {"$ne": "zoom_registrant_oauth_plus_gateway"}},
                            {"$set": {"upstream_zoom_url": zoom_url.strip(), "updated_at": fields["updated_at"]}},
                        )
                except Exception:
                    pass
            return fields

        await asyncio.to_thread(_upd)
        return await self.get_live_session_info()

    @staticmethod
    def _extract_zoom_meeting_id(zoom_url: str, fallback_id: str = "") -> str:
        """Extract the numeric Zoom meeting ID from a Zoom URL or formatted ID string."""
        m = re.search(r"/j/(\d+)", zoom_url or "")
        if m:
            return m.group(1)
        digits = "".join(ch for ch in (fallback_id or "") if ch.isdigit())
        return digits

    def _resolve_zoom_gateway_base(self) -> str:
        """Return the active per-user Zoom gateway endpoint base URL (auto-healing trycloudflare rotations)."""
        raw = (self.cfg.zoom_gateway_url or "").strip().rstrip("/")
        if raw and "trycloudflare.com" in raw and "example.trycloudflare.com" not in raw:
            import os

            cf_file = r"./data/workspace"
            try:
                if os.path.exists(cf_file):
                    live_cf = open(cf_file, "r", encoding="utf-8").read().strip().rstrip("/")
                    if live_cf.startswith("https://") and "trycloudflare.com" in live_cf:
                        return f"{live_cf}/webhook/zoom-join"
            except Exception:
                pass
        if raw:
            if "zoom-join" in raw:
                return raw
            return f"{raw}/api/zoom-join"
        return f"{self.cfg.aios_base_url.rstrip('/')}/api/zoom-join"

    async def _register_zoom_native_registrant(
        self,
        *,
        zoom_url: str,
        zoom_id: str,
        email_addr: str,
        full_name: str,
    ) -> str:
        """Create a unique per-user Zoom meeting registrant join_url when Zoom OAuth creds are set."""
        if not (self.cfg.zoom_account_id and self.cfg.zoom_client_id and self.cfg.zoom_client_secret):
            return ""
        numeric_id = self._extract_zoom_meeting_id(zoom_url, zoom_id)
        if not numeric_id or not email_addr:
            return ""
        parts = sanitize_untrusted_text(full_name or "Tradesejon VIP", 80).split()
        first_name = parts[0] if parts else "Tradesejon"
        last_name = " ".join(parts[1:]) if len(parts) > 1 else "VIP"
        try:
            import base64

            sess = await self.get_http()
            creds_b64 = base64.b64encode(
                f"{self.cfg.zoom_client_id}:{self.cfg.zoom_client_secret}".encode("utf-8")
            ).decode("ascii")
            token_url = (
                f"https://zoom.us/oauth/token?grant_type=account_credentials"
                f"&account_id={self.cfg.zoom_account_id}"
            )
            async with sess.post(
                token_url,
                headers={"Authorization": f"Basic {creds_b64}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r_tok:
                if r_tok.status >= 400:
                    return ""
                tok_data = await r_tok.json(content_type=None)
                access_token = str(tok_data.get("access_token") or "")
            if not access_token:
                return ""
            reg_url = f"https://api.zoom.us/v2/meetings/{numeric_id}/registrants"
            headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
            payload = {
                "email": email_addr.strip().lower(),
                "first_name": first_name,
                "last_name": last_name,
                "auto_approve": True,
            }
            async with sess.post(reg_url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as r_reg:
                if r_reg.status >= 400:
                    return ""
                reg_data = await r_reg.json(content_type=None)
                return str(reg_data.get("join_url") or "")
        except Exception:
            return ""

    async def _create_unique_discord_live_invite(self, live_channel_id: str) -> tuple[str, str]:
        """Mint a unique 1-time-use Discord Live invite expiring in 15 minutes (max_age=900, max_uses=1)."""
        tok = (self.cfg.ceo_token or self.cfg.trader_token or "").strip()
        if is_protected_client_live_target(live_channel_id) or is_protected_client_discord_guild(self.cfg.guild_id):
            return "", ""
        if not live_channel_id or not live_channel_id.isdigit() or len(tok) < 25 or "." not in tok:
            return "", ""
        try:
            sess = await self.get_http()
            async with sess.post(
                f"https://discord.com/api/v10/channels/{live_channel_id}/invites",
                headers={
                    "Authorization": f"Bot {tok}",
                    "User-Agent": "DiscordBot (https://tradesejon.com, 1.0)",
                    "Content-Type": "application/json",
                },
                json={"max_age": 900, "max_uses": 1, "temporary": False, "unique": True},
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                if resp.status in (200, 201):
                    data = await resp.json(content_type=None)
                    code = str(data.get("code") or "").strip()
                    if code:
                        return f"https://discord.gg/{code}", code
        except Exception as exc:
            logger.warning("Discord invite creation skipped: %s", redact_secrets(str(exc), self.cfg.known_secrets))
        return "", ""

    async def _revoke_discord_invite(self, invite_code: str) -> bool:
        """Delete a previously issued Discord invite code immediately upon CEO revocation."""
        tok = (self.cfg.ceo_token or self.cfg.trader_token or "").strip()
        code = (invite_code or "").strip()
        if not code or len(tok) < 25 or "." not in tok:
            return False
        try:
            sess = await self.get_http()
            async with sess.delete(
                f"https://discord.com/api/v10/invites/{code}",
                headers={"Authorization": f"Bot {tok}", "User-Agent": "DiscordBot (https://tradesejon.com, 1.0)"},
                timeout=aiohttp.ClientTimeout(total=6),
            ) as resp:
                return resp.status in (200, 204, 404)
        except Exception:
            return False

    async def create_unique_zoom_link_for_payer(
        self,
        *,
        discord_user_id: str = "",
        email_addr: str = "",
        full_name: str = "",
        checkout_ref: str = "",
        whop_payment_id: str = "",
        force_new: bool = False,
    ) -> dict[str, Any]:
        """Issue or fetch a strictly per-user unique Discord Live (or Zoom fallback) direct access link."""
        try:
            from .security import sign_zoom_access_token
        except ImportError:
            from security import sign_zoom_access_token

        uid = str(discord_user_id or "").strip()
        clean_email = (email_addr or "").strip().lower()
        clean_ref = (checkout_ref or whop_payment_id or f"free_{int(time.time())}").strip()
        clean_name = sanitize_untrusted_text(full_name or "Tradesejon VIP", 80)
        if not uid and not clean_email and not checkout_ref and not whop_payment_id:
            return {"ok": False, "error": "missing_payer_identity", "unique_zoom_url": "", "discord_live_url": ""}

        live_sess = await self.get_live_session_info()
        upstream_url = str(live_sess.get("zoom_url") or self.cfg.default_zoom_url)
        prefer_discord_live = "discord.com/channels/" in upstream_url or "discord.gg/" in upstream_url

        def _find_existing() -> dict[str, Any] | None:
            if force_new:
                return None
            for _, db in self._dual_dbs():
                try:
                    existing = None
                    if uid:
                        cand = db["zoom_access_tokens"].find_one(
                            {"discord_user_id": uid, "paid": True, "revoked": {"$ne": True}}
                        )
                        if cand and (
                            not clean_email
                            or not cand.get("email")
                            or str(cand.get("email")).lower() == clean_email
                        ):
                            existing = cand
                    if existing is None and clean_email:
                        cand = db["zoom_access_tokens"].find_one(
                            {"email": clean_email, "paid": True, "revoked": {"$ne": True}}
                        )
                        if cand and (
                            not uid
                            or not cand.get("discord_user_id")
                            or str(cand.get("discord_user_id")) == uid
                        ):
                            existing = cand
                    if existing and existing.get("unique_zoom_url"):
                        now_utc = datetime.now(timezone.utc)
                        exp_at = existing.get("expires_at")
                        if isinstance(exp_at, str):
                            try:
                                exp_at = datetime.fromisoformat(exp_at.replace("Z", "+00:00"))
                            except Exception:
                                exp_at = None
                        if isinstance(exp_at, datetime) and exp_at.tzinfo is None:
                            exp_at = exp_at.replace(tzinfo=timezone.utc)
                        created_at = existing.get("created_at")
                        if isinstance(created_at, datetime) and created_at.tzinfo is None:
                            created_at = created_at.replace(tzinfo=timezone.utc)
                        is_expired = bool(
                            (isinstance(exp_at, datetime) and exp_at <= now_utc)
                            or (not isinstance(exp_at, datetime) and isinstance(created_at, datetime) and (created_at + timedelta(minutes=15)) <= now_utc)
                            or int(existing.get("use_count") or 0) >= 1
                        )
                        if is_expired:
                            db["zoom_access_tokens"].update_one(
                                {"token": existing.get("token")},
                                {"$set": {"revoked": True, "expired_rotated": True, "updated_at": now_utc}},
                            )
                            return None
                        cur_url = str(existing["unique_zoom_url"])
                        # Upgrade old dead 88392145021/tunnel tokens seamlessly when switched to Discord Live
                        if prefer_discord_live and not cur_url.startswith("https://discord.") and "discord.com" not in str(existing.get("upstream_zoom_url") or ""):
                            return None
                        return {
                            "ok": True,
                            "token": str(existing.get("token") or ""),
                            "sig": str(existing.get("sig") or ""),
                            "unique_zoom_url": cur_url,
                            "discord_live_url": cur_url,
                            "discord_invite_code": str(existing.get("discord_invite_code") or ""),
                            "zoom_source": str(existing.get("zoom_source") or "gateway_token"),
                            "reused": True,
                        }
                except Exception:
                    pass
            return None

        existing_res = await asyncio.to_thread(_find_existing)
        if existing_res:
            live_gw = self._resolve_zoom_gateway_base()
            if (
                ".trycloudflare.com" in existing_res["unique_zoom_url"]
                and existing_res.get("token")
                and existing_res.get("sig")
                and "trycloudflare.com" in live_gw
            ):
                sep = "&" if "?" in live_gw else "?"
                upgraded = f"{live_gw}{sep}t={existing_res['token']}&s={existing_res['sig']}"
                existing_res["unique_zoom_url"] = upgraded
                existing_res["discord_live_url"] = upgraded
            def _sync_existing():
                for _, db in self._dual_dbs():
                    try:
                        if uid:
                            db["zoom_access_tokens"].update_one(
                                {"token": existing_res["token"], "discord_user_id": ""},
                                {"$set": {"discord_user_id": uid, "updated_at": datetime.now(timezone.utc)}},
                            )
                        if not db["zoom_access_tokens"].find_one({"token": existing_res["token"]}):
                            for _, sdb in self._dual_dbs():
                                src = sdb["zoom_access_tokens"].find_one({"token": existing_res["token"]})
                                if src:
                                    src.pop("_id", None)
                                    db["zoom_access_tokens"].update_one({"token": existing_res["token"]}, {"$set": src}, upsert=True)
                                    break
                    except Exception:
                        pass
            await asyncio.to_thread(_sync_existing)
            return existing_res

        zoom_id = str(live_sess.get("zoom_id") or self.cfg.default_zoom_id)
        native_join_url = ""
        discord_invite_url = ""
        discord_invite_code = ""

        if prefer_discord_live:
            live_ch_id = self.cfg.discord_live_channel_id or self._extract_discord_channel_id(upstream_url)
            discord_invite_url, discord_invite_code = await self._create_unique_discord_live_invite(live_ch_id)
        else:
            native_join_url = await self._register_zoom_native_registrant(
                zoom_url=upstream_url,
                zoom_id=zoom_id,
                email_addr=clean_email,
                full_name=clean_name,
            )

        signing_secret = self.cfg.bridge_secret or self.cfg.whop_api_key or "tradesejon-zoom-signing"
        token, sig = sign_zoom_access_token(
            discord_user_id=uid,
            email_addr=clean_email,
            checkout_ref=clean_ref,
            secret=signing_secret,
        )
        gateway_base = self._resolve_zoom_gateway_base()
        sep = "&" if "?" in gateway_base else "?"
        gateway_url = f"{gateway_base}{sep}t={token}&s={sig}"

        if discord_invite_url:
            zoom_source = "discord_live_unique_invite"
            target_upstream = upstream_url
            final_direct_url = discord_invite_url
        elif native_join_url:
            zoom_source = "zoom_registrant_oauth_plus_gateway"
            target_upstream = native_join_url
            final_direct_url = gateway_url
        else:
            zoom_source = "discord_live_gateway" if prefer_discord_live else "gateway_token"
            target_upstream = upstream_url
            final_direct_url = gateway_url

        def _persist() -> None:
            now = datetime.now(timezone.utc)
            doc = {
                "token": token,
                "sig": sig,
                "discord_user_id": uid,
                "email": clean_email,
                "full_name": clean_name,
                "checkout_ref": clean_ref,
                "whop_payment_id": whop_payment_id,
                "paid": True,
                "free_access": True,
                "revoked": False,
                "unique_zoom_url": final_direct_url,
                "discord_live_url": final_direct_url,
                "discord_invite_code": discord_invite_code,
                "upstream_zoom_url": target_upstream,
                "zoom_source": zoom_source,
                "ip_hashes": [],
                "max_distinct_ips": 1,
                "max_uses": 1,
                "max_age_sec": 900,
                "one_time": True,
                "use_count": 0,
                "last_used_at": None,
                "expires_at": now + timedelta(minutes=15),
                "created_at": now,
                "updated_at": now,
            }
            for _, db in self._dual_dbs():
                try:
                    db["zoom_access_tokens"].update_one({"token": token}, {"$set": doc}, upsert=True)
                    clauses = []
                    if uid:
                        clauses.append({"discord_user_id": uid})
                    if clean_email:
                        clauses.append({"email": clean_email})
                    if clauses:
                        q = {"$or": clauses}
                        upd_link = {
                            "$set": {
                                "unique_zoom_url": final_direct_url,
                                "discord_live_url": final_direct_url,
                                "discord_invite_code": discord_invite_code,
                                "zoom_token": token,
                                "updated_at": now,
                            }
                        }
                        db["trading_leads"].update_many(q, upd_link)
                        db["leads"].update_many(q, upd_link)
                        db["pending_whop_checkouts"].update_many(q, upd_link)
                        db["whop_payments"].update_many(q, upd_link)
                except Exception:
                    pass

        await asyncio.to_thread(_persist)
        await self.log_security_audit(
            bot_role="trader",
            action="unique_discord_live_issued",
            verdict="ALLOW",
            actor_id=uid or clean_email or clean_ref,
            actor_name=clean_name,
            summary=f"source={zoom_source}",
        )
        return {
            "ok": True,
            "token": token,
            "sig": sig,
            "unique_zoom_url": final_direct_url,
            "discord_live_url": final_direct_url,
            "discord_invite_code": discord_invite_code,
            "zoom_source": zoom_source,
            "reused": False,
        }

    @staticmethod
    def _extract_discord_channel_id(url: str) -> str:
        m = re.search(r"discord\.com/channels/\d+/(\d+)", url or "")
        return m.group(1) if m else ""

    async def verify_whop_payment_live_for_email(self, email_addr: str, *, discord_user_id: str = "") -> dict[str, Any]:
        """Verify active paid OR $0.00 Free No-Card Whop membership/payment via Whop REST v1/v5/v2 API."""
        clean_email = (email_addr or "").strip().lower()
        clean_uid = str(discord_user_id or "").strip()
        if not self.cfg.whop_api_key or (not clean_email and not clean_uid):
            return {"paid": False, "reason": "no_whop_api_key_or_target"}
        sess = await self.get_http()
        headers = {"Authorization": f"Bearer {self.cfg.whop_api_key}", "Accept": "application/json"}
        endpoints = [
            "https://api.whop.com/api/v1/payments?account_id=biz_TEMPLATE_16&first=25",
            "https://api.whop.com/api/v1/memberships?company_id=biz_TEMPLATE_16&first=25",
            "https://api.whop.com/api/v5/company/memberships?valid=true&per=50",
            "https://api.whop.com/api/v2/memberships?valid=true&per=50",
        ]
        for url in endpoints:
            try:
                async with sess.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status >= 400:
                        continue
                    data = await resp.json(content_type=None)
                    items = data.get("data") if isinstance(data, dict) else []
                    if not isinstance(items, list):
                        continue
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        user_obj = item.get("user") if isinstance(item.get("user"), dict) else {}
                        member_obj = item.get("member") if isinstance(item.get("member"), dict) else {}
                        meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                        cand_email = str(
                            item.get("customer_email")
                            or item.get("email")
                            or user_obj.get("email")
                            or member_obj.get("email")
                            or meta.get("email")
                            or ""
                        ).strip().lower()
                        ext_uids = {
                            str(ea.get("external_id") or "").strip()
                            for ea in (item.get("external_accounts") or [])
                            if isinstance(ea, dict) and ea.get("platform") == "discord"
                        }
                        meta_uid = str(meta.get("discord_user_id") or "").strip()
                        email_hit = bool(clean_email and cand_email == clean_email)
                        uid_hit = bool(clean_uid and (clean_uid == meta_uid or clean_uid in ext_uids))
                        if not (email_hit or uid_hit):
                            continue
                        st = str(item.get("status") or "").lower()
                        sub = str(item.get("substatus") or "").lower()
                        # Strictly reject unverified / pre-OTP Whop checkouts or invalid memberships
                        if st in ("pending", "incomplete", "open", "draft", "requires_action", "unpaid", "canceled", "cancelled", "expired", "failed"):
                            continue
                        if item.get("valid") is False:
                            continue
                        is_valid = bool(item.get("valid") is True or st in ("paid", "active", "completed", "trialing") or sub == "succeeded")
                        if is_valid:
                            tier_spec = resolve_tier_from_payment(item, self.cfg)
                            is_free_otp = tier_spec["tier"] == "free_otp_test"
                            return {
                                "paid": not is_free_otp,
                                "otp_verified": True,
                                "is_free_otp": is_free_otp,
                                "whop_payment_id": str(item.get("id") or ""),
                                "email": cand_email or clean_email,
                                "discord_user_id": clean_uid or meta_uid or next(iter(ext_uids), ""),
                                "tier": tier_spec["tier"],
                                "plan_id": tier_spec["plan_id"],
                                "amount_usd": tier_spec["price_usd"],
                            }
            except Exception:
                continue
        return {"paid": False, "otp_verified": False, "is_free_otp": False, "reason": "not_found_in_whop"}

    async def reconcile_recent_whop_payments(self) -> dict[str, Any]:
        """Poll Whop v1/v5 payments + $0 OTP-confirmed memberships and auto-promote matching users immediately."""
        if not self.cfg.whop_api_key:
            return {"ok": False, "reason": "no_whop_api_key", "fulfilled": 0}
        sess = await self.get_http()
        headers = {"Authorization": f"Bearer {self.cfg.whop_api_key}", "Accept": "application/json"}
        urls = [
            "https://api.whop.com/api/v1/payments?account_id=biz_TEMPLATE_16&first=25",
            "https://api.whop.com/api/v1/memberships?company_id=biz_TEMPLATE_16&first=25",
            "https://api.whop.com/api/v5/company/memberships?valid=true&per=50",
        ]
        items: list[dict[str, Any]] = []
        for url in urls:
            try:
                async with sess.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=12)) as resp:
                    if resp.status < 400:
                        data = await resp.json(content_type=None)
                        if isinstance(data, dict) and isinstance(data.get("data"), list):
                            items.extend(x for x in data["data"] if isinstance(x, dict))
            except Exception:
                continue

        if not items:
            return {"ok": True, "fulfilled": 0}

        fulfilled_users: list[str] = []
        for item in items:
            st = str(item.get("status") or "").lower()
            sub = str(item.get("substatus") or "").lower()
            if st in ("pending", "incomplete", "open", "draft", "requires_action", "unpaid", "canceled", "cancelled", "expired", "failed"):
                continue
            if item.get("valid") is False:
                continue
            if not (item.get("valid") is True or st in ("paid", "active", "completed", "trialing") or sub == "succeeded"):
                continue
            pay_id = str(item.get("id") or "")
            meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            user_obj = item.get("user") if isinstance(item.get("user"), dict) else {}
            member_obj = item.get("member") if isinstance(item.get("member"), dict) else {}
            email_val = str(
                item.get("customer_email")
                or item.get("email")
                or user_obj.get("email")
                or member_obj.get("email")
                or meta.get("email")
                or ""
            ).strip().lower()
            ext_uids = [
                str(ea.get("external_id") or "").strip()
                for ea in (item.get("external_accounts") or [])
                if isinstance(ea, dict) and ea.get("platform") == "discord" and ea.get("external_id")
            ]
            uid_val = str(meta.get("discord_user_id") or (ext_uids[0] if ext_uids else "")).strip()
            if not pay_id or (not email_val and not uid_val):
                continue

            def _is_done() -> bool:
                for _, db in self._dual_dbs():
                    try:
                        if db["whop_payments"].find_one({"whopPaymentId": pay_id}):
                            return True
                    except Exception:
                        pass
                return False

            if await asyncio.to_thread(_is_done):
                continue

            tier_spec = resolve_tier_from_payment(item, self.cfg)
            res = await self.fulfill_whop_payment_for_discord(
                email_addr=email_val,
                discord_user_id=uid_val,
                whop_payment_id=pay_id,
                checkout_ref=str(meta.get("checkout_ref") or ""),
                tier=tier_spec["tier"],
                plan_id=tier_spec["plan_id"],
                amount_usd=float(tier_spec["price_usd"]),
            )
            if res.get("ok"):
                fulfilled_users.append(email_val or uid_val)
        return {"ok": True, "fulfilled": len(fulfilled_users), "users": fulfilled_users}

    async def revoke_zoom_access_for_user(
        self,
        *,
        discord_user_id: str = "",
        email_addr: str = "",
        reason: str = "ceo_revoked",
    ) -> dict[str, Any]:
        """Revoke a specific payer's unique Zoom access token immediately."""
        uid = str(discord_user_id or "").strip()
        clean_email = (email_addr or "").strip().lower()
        if not uid and not clean_email:
            return {"ok": False, "error": "missing_target", "modified": 0}

        def _revoke() -> tuple[int, list[str]]:
            now = datetime.now(timezone.utc)
            clauses = []
            if uid:
                clauses.append({"discord_user_id": uid})
            if clean_email:
                clauses.append({"email": clean_email})
            query = {"$or": clauses}
            total_mod = 0
            invite_codes: list[str] = []
            for _, db in self._dual_dbs():
                try:
                    for existing_doc in db["zoom_access_tokens"].find(query):
                        ic = str(existing_doc.get("discord_invite_code") or "").strip()
                        if ic and ic not in invite_codes:
                            invite_codes.append(ic)
                    res = db["zoom_access_tokens"].update_many(
                        query,
                        {"$set": {"revoked": True, "revoked_reason": reason, "revoked_at": now, "updated_at": now}},
                    )
                    total_mod += int(getattr(res, "modified_count", 0))
                except Exception:
                    pass
            return total_mod, invite_codes

        modified, invite_codes = await asyncio.to_thread(_revoke)
        for ic in invite_codes:
            await self._revoke_discord_invite(ic)
        await self.log_security_audit(
            bot_role="ceo",
            action="zoom_access_revoked",
            verdict="ALLOW",
            actor_id=uid or clean_email,
            actor_name=uid or clean_email,
            summary=f"modified={modified} invites_revoked={len(invite_codes)} reason={reason}",
        )
        return {"ok": True, "modified": modified, "invites_revoked": len(invite_codes)}

    async def find_telegram_chats_for_user(self, *, email_addr: str = "", phone_e164: str = "") -> list[str]:
        """Find Telegram chat IDs linked to a user by email or trailing phone digits across MongoDB."""
        clean_email = (email_addr or "").strip().lower()
        digits = "".join(ch for ch in (phone_e164 or "") if ch.isdigit())
        tail9 = digits[-9:] if len(digits) >= 9 else digits

        def _search() -> list[str]:
            clauses: list[dict[str, Any]] = []
            if clean_email:
                clauses.append({"email": clean_email})
            if tail9:
                rx = {"$regex": re.escape(tail9) + "$"}
                clauses.extend([
                    {"phone": rx},
                    {"phoneE164": rx},
                    {"phone_number": rx},
                    {"number": rx},
                ])
            if not clauses:
                return []
            q = {"$or": clauses}
            found: list[str] = []
            for _, db in self._dual_dbs():
                try:
                    for coll in ("telegram_users", "zoom_access_tokens", "trading_leads", "leads", "contacts", "conversations"):
                        for doc in db[coll].find(q).limit(10):
                            tg_obj = doc.get("telegram") if isinstance(doc.get("telegram"), dict) else {}
                            cand = (
                                doc.get("telegram_chat_id")
                                or doc.get("chat_id")
                                or tg_obj.get("chatId")
                                or doc.get("telegramChatId")
                                or doc.get("user_id")
                                or ""
                            )
                            s_cand = str(cand).strip()
                            if s_cand and s_cand.lstrip("-").isdigit() and s_cand not in found:
                                found.append(s_cand)
                except Exception:
                    pass
            return found

        return await asyncio.to_thread(_search)

    async def send_telegram_live_link(
        self,
        *,
        chat_id: str,
        token: str,
        full_name: str,
        live_url: str,
        whop_url: str,
        session_name: str,
        session_time: str,
        include_qr: bool = True,
    ) -> bool:
        """Send Discord Live direct link + optional QR code directly to a Telegram chat ID."""
        if not chat_id or not token or not live_url:
            return False
        caption_txt = (
            f"🔓 مرحباً يا {sanitize_untrusted_text(full_name, 50)} — تم تفعيل دخولك المجاني إلى Discord Live!\n\n"
            f"🎙️ {session_name}\n"
            f"⏰ {session_time}\n\n"
            f"🔗 رابط Discord Live المباشر الخاص بك:\n{live_url}\n\n"
            f"🎁 بطاقة Whop المجانية ($0 بدون بطاقة):\n{whop_url}"
        )
        try:
            sess = await self.get_http()
            if include_qr:
                qr_bytes = self.generate_qr_png(live_url)
                form = aiohttp.FormData()
                form.add_field("chat_id", str(chat_id))
                form.add_field("caption", caption_txt[:1020])
                form.add_field("photo", qr_bytes, filename="tradesejon_discord_live_qr.png", content_type="image/png")
                async with sess.post(
                    f"https://api.telegram.org/bot{token}/sendPhoto",
                    data=form,
                    timeout=aiohttp.ClientTimeout(total=12),
                ) as r:
                    if r.status == 200:
                        return True
            async with sess.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": str(chat_id), "text": caption_txt, "disable_web_page_preview": False},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                return r.status == 200
        except Exception as exc:
            logger.warning("Telegram delivery failed for %s: %s", chat_id, redact_secrets(str(exc), self.cfg.known_secrets))
            return False

    async def deliver_live_link_everywhere(
        self,
        *,
        email_addr: str,
        phone_e164: str = "",
        full_name: str = "Tradesejon VIP",
        discord_user_id: str = "",
        telegram_chat_id: str = "",
        checkout_ref: str = "",
        whop_checkout_url: str = "",
        tier: str = "workshop_45",
        paid: bool = True,
        send_ceo_alerts: bool = True,
    ) -> dict[str, Any]:
        """Mint unique Discord Live link for verified payer OR send Prospect checkout links across Email + Telegram."""
        sess_info = await self.get_live_session_info()
        whop_url = whop_checkout_url or self.cfg.whop_checkout_workshop_45 or sess_info["whop_url"]
        live_url = ""
        live_tok = ""
        if paid:
            live_res = await self.create_unique_zoom_link_for_payer(
                discord_user_id=discord_user_id,
                email_addr=email_addr,
                full_name=full_name,
                checkout_ref=checkout_ref or tier,
            )
            live_url = str(live_res.get("discord_live_url") or live_res.get("unique_zoom_url") or sess_info["zoom_url"])
            live_tok = str(live_res.get("token") or "")
        tg_deep_link = f"https://t.me/YOUR_TRADER_BOT?start=live_{live_tok}" if live_tok else "https://t.me/YOUR_TRADER_BOT"

        tiers = build_tier_defs(self.cfg)
        tier_spec = tiers.get(tier) or tiers["workshop_45"]
        email_subject = (
            f"Tradesejon VIP — رابط Workshop المباشر الخاص بك ({tier_spec['title']})"
            if paid
            else f"Tradesejon Marhaban — رابط التسجيل والدفع ({tier_spec['title']})"
        )
        email_body = (
            (
                f"أهلاً بك يا {full_name}! تم تأكيد اشتراكك بنجاح ({tier_spec['title']}).\n\n"
                f"رابط Discord Live للورشة الخاصة بك:\n{live_url}\n\n"
                f"لاستلام الرابط أيضاً على تيليجرام بضغطة واحدة:\n{tg_deep_link}"
            )
            if paid
            else (
                f"مرحباً بك يا {full_name} في Tradesejon!\n"
                f"تم تسجيل بريدك الإلكتروني ({email_addr}) وبانتظار تأكيد كود التحقق (Whop OTP) للدخول فوراً إلى Prospects Bot.\n\n"
                f"1️⃣ افتح رابط Whop ($0.00 مجاناً لتجربة الدفع عبر Email + OTP):\n{whop_url}\n"
                f"2️⃣ أدخل بريدك ثم أكد كود الـ OTP الذي سيصلك على الإيميل — وفوراً ستدخل قناة Prospects Bot على ديسكورد!\n\n"
                f"💳 الباقات الرسمية المتاحة:\n"
                f"• لايف الديسكورد اليومي + الـ Scanner: $45/شهر (https://whop.com/Tradesejon/exp_TEMPLATE_VIP"
                f"• Offline Workshop: 4,000 EGP ($74.39) للأعضاء | 6,000 EGP ($111.59) لغير الأعضاء\n"
                f"• Online Workshop (4 ساعات خاصة مع المحاضر): $100"
            )
        )

        # 1. Deliver via Email
        email_ok = False
        if email_addr and "@" in email_addr and not email_addr.endswith(".local") and self.cfg.gmail_app_password:
            em_res = await self.send_email_secure(
                to_email=email_addr,
                subject=email_subject,
                body_text=email_body,
                recipient_name=full_name,
                include_qr=paid,
                include_zoom=paid,
                unique_zoom_url=live_url,
            )
            email_ok = bool(em_res.get("ok"))
            ceo_mail = (self.cfg.gmail_user or "").strip().lower()
            if send_ceo_alerts and ceo_mail and ceo_mail != email_addr.strip().lower():
                await self.send_email_secure(
                    to_email=ceo_mail,
                    subject=f"[Tradesejon {'Paid VIP' if paid else 'New Prospect'}] {full_name} ({tier_spec['price_usd']}$)",
                    body_text=(
                        f"Name: {full_name}\nEmail: {email_addr}\nPhone: {phone_e164}\nTier: {tier_spec['title']}\nPaid: {paid}\n"
                        + (f"Discord Direct Link: {live_url}\n" if live_url else f"Checkout URL: {whop_url}\n")
                        + f"Google Sheet: {self.cfg.tradesajon_gsheet_url}"
                    ),
                    recipient_name="Tradesejon CEO",
                    include_qr=False,
                    include_zoom=False,
                )

        # 2. Deliver via Telegram
        matched_chats = await self.find_telegram_chats_for_user(email_addr=email_addr, phone_e164=phone_e164)
        if telegram_chat_id and str(telegram_chat_id) not in matched_chats:
            matched_chats.insert(0, str(telegram_chat_id))
        tg_user_sent = False
        tg_tok = self.cfg.telegram_trading_token or self.cfg.telegram_ceo_token
        target_link = live_url if paid else whop_url
        for cid in matched_chats:
            if await self.send_telegram_live_link(
                chat_id=cid,
                token=tg_tok,
                full_name=full_name,
                live_url=target_link,
                whop_url=whop_url,
                session_name=sess_info["session_name"],
                session_time=sess_info["session_time"],
                include_qr=paid,
            ):
                tg_user_sent = True

        tg_ceo_sent = False
        ceo_cid = (self.cfg.telegram_ceo_chat_id or "").strip()
        ceo_bot_tok = self.cfg.telegram_ceo_token or tg_tok
        if send_ceo_alerts and ceo_cid and ceo_bot_tok and ceo_cid not in matched_chats:
            tg_ceo_sent = await self.send_telegram_live_link(
                chat_id=ceo_cid,
                token=ceo_bot_tok,
                full_name=f"{full_name} ({tier_spec['tier']} | {email_addr} | {phone_e164})",
                live_url=target_link,
                whop_url=whop_url,
                session_name=sess_info["session_name"],
                session_time=sess_info["session_time"],
                include_qr=paid,
            )

        return {
            "ok": True,
            "paid": paid,
            "tier": tier_spec["tier"],
            "discord_live_url": live_url,
            "whop_checkout_url": whop_url,
            "telegram_deep_link": tg_deep_link,
            "zoom_token": live_tok,
            "email_sent": email_ok,
            "telegram_user_sent": tg_user_sent,
            "telegram_ceo_sent": tg_ceo_sent,
            "matched_telegram_chats": matched_chats,
            "session": sess_info,
        }

    async def get_recent_leads_and_paid(self, limit: int = 8) -> dict[str, list[dict[str, Any]]]:
        """Fetch latest form-validated leads and paid VIPs (strictly for CEO Admin view)."""
        def _fetch() -> dict[str, list[dict[str, Any]]]:
            for _, db in self._dual_dbs():
                try:
                    recent_leads = list(db["trading_leads"].find().sort("updated_at", -1).limit(limit))
                    paid_leads = list(
                        db["trading_leads"]
                        .find({"$or": [{"paid": True}, {"status": {"$in": ["paid_whop", "VIP_PAID", "paid"]}}]})
                        .sort("updated_at", -1)
                        .limit(limit)
                    )
                    return {
                        "recent": [
                            {
                                "name": str(x.get("full_name") or x.get("name") or "-"),
                                "email": str(x.get("email") or "-"),
                                "phone": str(x.get("phone") or "-"),
                                "status": str(x.get("status") or "-"),
                                "discord_user_id": str(x.get("discord_user_id") or ""),
                                "watched_videos": bool(x.get("watched_videos")),
                            }
                            for x in recent_leads
                        ],
                        "paid": [
                            {
                                "name": str(x.get("full_name") or x.get("name") or "-"),
                                "email": str(x.get("email") or "-"),
                                "discord_user_id": str(x.get("discord_user_id") or ""),
                            }
                            for x in paid_leads
                        ],
                    }
                except Exception:
                    pass
            return {"recent": [], "paid": []}

        return await asyncio.to_thread(_fetch)

    async def check_ecosystem_health(self) -> dict[str, str]:
        """Probe n8n (5678), Nod (4310), AIOS (8088), WA (8089), and Discord Bridge (8095)."""
        sess = await self.get_http()
        probes = {
            "AIOS 8088": f"{self.cfg.aios_base_url}/api/market-prices",
            "WA Bridge 8089": "http://127.0.0.1:8089/api/status",
            "Discord Bridge 8095": "http://127.0.0.1:8095/health",
            "n8n 5678": "http://127.0.0.1:5678/healthz",
            "n8n Nod 4310": "http://127.0.0.1:4310/",
        }
        out: dict[str, str] = {}
        for name, url in probes.items():
            try:
                async with sess.get(url, timeout=aiohttp.ClientTimeout(total=3)) as r:
                    out[name] = f"ONLINE ({r.status})"
            except Exception as exc:
                out[name] = f"OFFLINE ({type(exc).__name__})"
        return out

    async def get_crm_summary(self, search: str = "") -> dict[str, Any]:
        """Read-only CRM metrics for verified CEO/PR requests."""
        def _stats():
            for name, db in self._dual_dbs():
                try:
                    summary: dict[str, Any] = {
                        "source": name,
                        "contacts": db["contacts"].count_documents({}),
                        "trading_leads": db["trading_leads"].count_documents({}),
                        "leads": db["leads"].count_documents({}),
                        "conversations": db["conversations"].count_documents({}),
                        "outbound_emails": db["outbound_emails"].count_documents({}),
                        "unique_zoom_tokens": db["zoom_access_tokens"].count_documents({"revoked": {"$ne": True}}),
                        "security_events": db["security_audit_logs"].count_documents({}),
                        "prospects_pending": db["trading_leads"].count_documents({"status": "prospect"}),
                        "prospects_paid_once": db["trading_leads"].count_documents({"status": "prospect_paid_once"}),
                        "vip_recurring": db["trading_leads"].count_documents({"status": "vip_recurring"}),
                        "viproom_vip": db["trading_leads"].count_documents({"status": "viproom_workshop_vip"}),
                        "gsheet_url": self.cfg.tradesajon_gsheet_url,
                    }
                    if search:
                        rx = {"$regex": re.escape(sanitize_untrusted_text(search, 64)), "$options": "i"}
                        matches = list(
                            db["trading_leads"]
                            .find({"$or": [{"full_name": rx}, {"email": rx}, {"phone": rx}, {"discord_username": rx}]})
                            .limit(5)
                        )
                        summary["matches"] = [
                            {
                                "name": m.get("full_name") or m.get("name", ""),
                                "email": m.get("email", ""),
                                "phone": m.get("phone", ""),
                                "status": m.get("status", ""),
                            }
                            for m in matches
                        ]
                    return summary
                except Exception:
                    pass
            return {"source": "none"}

        return await asyncio.to_thread(_stats)

    async def list_discord_broadcast_targets(self) -> tuple[list[str], list[str]]:
        """Collect known Discord trader user IDs and channel IDs for controlled broadcasts."""
        def _collect():
            users, channels = set(), set()
            for _, db in self._dual_dbs():
                try:
                    for doc in db["conversations"].find({"discord_user_id": {"$exists": True}}):
                        if doc.get("discord_user_id"):
                            users.add(str(doc["discord_user_id"]))
                    for doc in db["contacts"].find({"discord_user_id": {"$exists": True}}):
                        if doc.get("discord_user_id"):
                            users.add(str(doc["discord_user_id"]))
                except Exception:
                    pass
            for ch in self.cfg.trader_channel_ids:
                channels.add(str(ch))
            if self.cfg.announce_channel_id:
                channels.add(str(self.cfg.announce_channel_id))
            return sorted(users), sorted(channels)

        return await asyncio.to_thread(_collect)

    async def export_all_crm_rows(self, limit: int = 200) -> list[dict[str, Any]]:
        """Gather deduplicated CRM rows across MongoDB for CSV, Google Sheets, and AIOS sync."""
        def _gather() -> list[dict[str, Any]]:
            by_key: dict[str, dict[str, Any]] = {}
            for _, db in self._dual_dbs():
                try:
                    for doc in db["trading_leads"].find().sort("updated_at", -1).limit(limit):
                        key = str(doc.get("email") or doc.get("discord_user_id") or "").strip().lower()
                        if key and key not in by_key:
                            clean = {k: v for k, v in doc.items() if k != "_id"}
                            if isinstance(clean.get("updated_at"), datetime):
                                clean["updated_at"] = clean["updated_at"].isoformat()
                            if isinstance(clean.get("created_at"), datetime):
                                clean["created_at"] = clean["created_at"].isoformat()
                            if isinstance(clean.get("paid_at"), datetime):
                                clean["paid_at"] = clean["paid_at"].isoformat()
                            by_key[key] = clean
                except Exception:
                    pass
            return list(by_key.values())

        return await asyncio.to_thread(_gather)

    async def sync_crm_snapshot_to_all(self, trigger_record: dict[str, Any] | None = None) -> dict[str, Any]:
        """Instant omnichannel sync across CSV master, n8n funnel webhook, Google Sheets, and AIOS."""
        rows = await self.export_all_crm_rows(limit=250)
        csv_res = await self.omnichannel.write_csv_snapshot(rows)
        n8n_res = await self.omnichannel.notify_n8n_funnel_sync(trigger_record or {"event": "snapshot_sync"}, all_rows=rows)
        aios_ok = False
        if trigger_record and self.cfg.aios_base_url:
            try:
                sess = await self.get_http()
                headers = {"Content-Type": "application/json"}
                if self.cfg.aios_internal_token:
                    headers["X-AIOS-Admin-Key"] = self.cfg.aios_internal_token
                async with sess.post(
                    f"{self.cfg.aios_base_url}/api/save-discord-form",
                    headers=headers,
                    json=trigger_record,
                    timeout=aiohttp.ClientTimeout(total=4),
                ) as r:
                    aios_ok = r.status == 200
            except Exception:
                aios_ok = False
        return {"ok": bool(csv_res.get("ok")), "csv": csv_res, "n8n": n8n_res, "aios_saved": aios_ok, "row_count": len(rows)}

    async def embed_text_mongodb_ai(self, text: str) -> list[float]:
        """Generate a 1024-dim vector embedding via MongoDB Atlas AI Model API (ai.mongodb.com / voyage-3.5)."""
        api_key = (self.cfg.mongodb_ai_api_key or "").strip()
        endpoint = (self.cfg.mongodb_ai_endpoint or "https://ai.mongodb.com/v1").rstrip("/")
        model = (self.cfg.mongodb_ai_embed_model or "voyage-3.5").strip()
        clean = (text or "").strip()
        if not api_key or not clean:
            return []
        try:
            sess = await self.get_http()
            async with sess.post(
                f"{endpoint}/embeddings",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"input": [clean[:4000]], "model": model},
                timeout=aiohttp.ClientTimeout(total=12),
            ) as resp:
                if resp.status != 200:
                    return []
                payload = await resp.json()
                data = payload.get("data") or []
                if data and isinstance(data[0].get("embedding"), list):
                    return [float(x) for x in data[0]["embedding"]]
        except Exception:
            return []
        return []
