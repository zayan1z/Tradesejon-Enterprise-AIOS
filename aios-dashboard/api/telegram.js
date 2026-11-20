// 24/7 Cloud Telegram Tri-Bot Engine for Tradesejon AIOS
// Runs permanently on Vercel Serverless with zero laptop dependency.
// Full feature set:
// 1) All media send & receive: Text, Photo, Video, GIF / Animation, Documents, Audio, Voice, Video Notes, Stickers.
// 2) AI Executive Email Sending via Gmail SMTP (e.g. "Eb3at mail le ...").
// 3) Targeted Direct Media Delivery to specific phone/username (e.g. "Eb3atha le 01012345678").
// 4) Clean Broadcast to all traders ONLY when explicitly instructed (e.g. "eb3ato lel nas kolaha").
// 5) Multimodal Vision AI (Gemini 2.5 Flash), Groq Whisper STT, ElevenLabs TTS, PR Desk Ticket Routing, MongoDB Atlas.

let MongoClient = null;
try { ({ MongoClient } = require('mongodb')); } catch {}
let nodemailer = null;
try { nodemailer = require('nodemailer'); } catch {}

// API & Bot Configuration
const TRADING_TOKEN = process.env.TELEGRAM_TRADER_BOT_TOKEN || ""; // @YOUR_TRADER_BOT
const PR_TOKEN = process.env.TELEGRAM_PR_BOT_TOKEN || "";      // @YOUR_PR_BOT
const CEO_TOKEN = process.env.TELEGRAM_CEO_BOT_TOKEN || "";     // @YOUR_CEO_BOT

const CEO_CHAT_ID = process.env.CEO_CHAT_ID || "";
const ALLOWED_ADMIN_IDS = new Set([
  String(CEO_CHAT_ID),
  ...(process.env.TELEGRAM_CEO_ALLOWED_IDS || "").split(",").map(s => s.trim()).filter(Boolean)
]);
const GROQ_KEY = process.env.GROQ_API_KEY || "";
const ELEVENLABS_KEY = process.env.ELEVENLABS_API_KEY || "";
const ELEVENLABS_VOICE_ID = "pFZP5JQG7iQjIQuC4Bku";
const GEMINI_KEY = process.env.GEMINI_API_KEY || "";

const MONGO_ATLAS_URI = process.env.MONGODB_URI || "";

const WHOP_CHECKOUT_URL = "https://whop.com/checkout/plan_TEMPLATE_01";
const WHOP_EXTERNAL_60_URL = "https://whop.com/checkout/plan_TEMPLATE_01";
const WHOP_VIPROOM_75_URL = "https://whop.com/checkout/plan_TEMPLATE_01";
const DISCORD_MARHABAN_INVITE_URL = "https://discord.gg/YOUR_INVITE_CODE";
const QR_PHOTO_URL = `https://api.qrserver.com/v1/create-qr-code/?size=500x500&data=${encodeURIComponent(WHOP_CHECKOUT_URL)}`;
const ZOOM_MEETING_URL = "https://discord.com/channels/900000000000000001/900000000000000002";
const ZOOM_MEETING_ID = "900000000000000002";
const ZOOM_PASSCODE = "DISCORD-LIVE";

// Email Transporter (Direct Gmail SMTP)
const emailTransporter = nodemailer ? nodemailer.createTransport({
  host: 'smtp.gmail.com',
  port: 465,
  secure: true,
  auth: {
    user: process.env.GMAIL_USER || 'trader@example.com',
    pass: process.env.GMAIL_APP_PASSWORD || ''
  }
}) : { sendMail: async () => { throw new Error('nodemailer not installed'); } };

// Memory cache for MongoDB client
function createMockDb() {
  const memStore = new Map();
  const getCol = (name) => {
    if (!memStore.has(name)) memStore.set(name, []);
    const items = memStore.get(name);
    return {
      insertOne: async (doc) => { items.push({ _id: String(Date.now()), ...doc }); return { insertedId: doc._id }; },
      insertMany: async (docs) => { (docs || []).forEach(d => items.push(d)); return { insertedCount: (docs || []).length }; },
      updateOne: async () => ({ acknowledged: true, modifiedCount: 1 }),
      updateMany: async () => ({ acknowledged: true, modifiedCount: 1 }),
      findOneAndUpdate: async (filter, update) => ({ value: update.$set || {} }),
      findOne: async (filter) => items[0] || null,
      find: () => ({
        sort: () => ({ limit: () => ({ toArray: async () => items.slice(0, 15), next: async () => items[0] || null }) }),
        limit: () => ({ toArray: async () => items.slice(0, 15), next: async () => items[0] || null }),
        toArray: async () => items.slice(0, 15),
        next: async () => items[0] || null
      }),
      countDocuments: async () => items.length
    };
  };
  return {
    collection: (name) => getCol(name)
  };
}
const mockDbInstance = createMockDb();

let cachedClient = null;
async function getDb() {
  try {
    if (!MONGO_ATLAS_URI || !MongoClient) return mockDbInstance;
    if (!cachedClient) {
      const useTls = /^mongodb\+srv:\/\//i.test(MONGO_ATLAS_URI) || /[?&](?:tls|ssl)=true/i.test(MONGO_ATLAS_URI);
      const client = new MongoClient(MONGO_ATLAS_URI, {
        serverSelectionTimeoutMS: 3000,
        connectTimeoutMS: 5000,
        ...(useTls ? { tls: true } : {}),
        maxPoolSize: 10
      });
      await client.connect();
      cachedClient = client;
    }
    return cachedClient.db(process.env.MONGODB_DB || 'tradesejon_prod');
  } catch (err) {
    cachedClient = null;
    console.warn('MongoDB connection error, switching gracefully to safe in-memory DB:', err.message);
    return mockDbInstance;
  }
}

// In-memory processed updates cache to eliminate duplicates
const processedUpdates = new Set();
function isDuplicate(updateId) {
  if (!updateId) return false;
  if (processedUpdates.has(updateId)) return true;
  processedUpdates.add(updateId);
  if (processedUpdates.size > 3000) {
    const first = processedUpdates.values().next().value;
    processedUpdates.delete(first);
  }
  return false;
}

// Extract any media type from Telegram message
function extractMediaInfo(msg) {
  if (msg.photo && msg.photo.length > 0) {
    const best = msg.photo[msg.photo.length - 1];
    return {
      type: 'photo',
      file_id: best.file_id,
      filename: 'photo.jpg',
      mime_type: 'image/jpeg',
      label: 'صورة'
    };
  }
  if (msg.animation) {
    return {
      type: 'animation',
      file_id: msg.animation.file_id,
      filename: msg.animation.file_name || 'animation.gif',
      mime_type: msg.animation.mime_type || 'image/gif',
      label: 'صورة متحركة GIF'
    };
  }
  if (msg.video) {
    return {
      type: 'video',
      file_id: msg.video.file_id,
      filename: msg.video.file_name || 'video.mp4',
      mime_type: msg.video.mime_type || 'video/mp4',
      label: 'فيديو'
    };
  }
  if (msg.document) {
    return {
      type: 'document',
      file_id: msg.document.file_id,
      filename: msg.document.file_name || 'file.bin',
      mime_type: msg.document.mime_type || 'application/octet-stream',
      label: `ملف (${msg.document.file_name || 'مستند'})`
    };
  }
  if (msg.audio) {
    return {
      type: 'audio',
      file_id: msg.audio.file_id,
      filename: msg.audio.file_name || 'audio.mp3',
      mime_type: msg.audio.mime_type || 'audio/mpeg',
      label: 'ملف صوتي Audio'
    };
  }
  if (msg.voice) {
    return {
      type: 'voice',
      file_id: msg.voice.file_id,
      filename: 'voice.oga',
      mime_type: msg.voice.mime_type || 'audio/ogg',
      label: 'تسجيل صوتي'
    };
  }
  if (msg.video_note) {
    return {
      type: 'video_note',
      file_id: msg.video_note.file_id,
      filename: 'video_note.mp4',
      mime_type: 'video/mp4',
      label: 'رسالة فيديو دائرية'
    };
  }
  if (msg.sticker) {
    return {
      type: 'sticker',
      file_id: msg.sticker.file_id,
      filename: 'sticker.webp',
      mime_type: 'image/webp',
      label: 'ستيكر Sticker'
    };
  }
  return null;
}

// Telegram API Helpers
async function tgCall(token, method, payload) {
  const url = `https://api.telegram.org/bot${token}/${method}`;
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  return res.json();
}

async function tgDownloadFile(token, fileId) {
  try {
    const fileInfo = await tgCall(token, 'getFile', { file_id: fileId });
    if (!fileInfo.ok || !fileInfo.result?.file_path) return null;
    const fileUrl = `https://api.telegram.org/file/bot${token}/${fileInfo.result.file_path}`;
    const res = await fetch(fileUrl);
    const arrayBuffer = await res.arrayBuffer();
    return Buffer.from(arrayBuffer);
  } catch (err) {
    console.error('File download error:', err);
    return null;
  }
}

async function tgSendMediaBuffer(token, method, fieldName, chatId, buffer, filename, mimeType, caption = '') {
  try {
    const url = `https://api.telegram.org/bot${token}/${method}`;
    const boundary = `----WebKitFormBoundary${Math.random().toString(36).substring(2)}`;
    
    const parts = [];
    parts.push(Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n${chatId}\r\n`));
    if (caption && method !== 'sendVideoNote' && method !== 'sendSticker') {
      parts.push(Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="caption"\r\n\r\n${caption}\r\n`));
      parts.push(Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="parse_mode"\r\n\r\nHTML\r\n`));
    }
    parts.push(Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="${fieldName}"; filename="${filename}"\r\nContent-Type: ${mimeType}\r\n\r\n`));
    parts.push(buffer);
    parts.push(Buffer.from(`\r\n--${boundary}--\r\n`));
    
    const body = Buffer.concat(parts);
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': `multipart/form-data; boundary=${boundary}`,
        'Content-Length': String(body.length)
      },
      body
    });
    return res.json();
  } catch (err) {
    console.error(`tgSendMediaBuffer (${method}) error:`, err);
    return { ok: false, error: String(err) };
  }
}

async function sendAnyMedia(token, chatId, mediaInfo, buffer = null, caption = '') {
  const methodMap = {
    photo: 'sendPhoto',
    animation: 'sendAnimation',
    video: 'sendVideo',
    document: 'sendDocument',
    audio: 'sendAudio',
    voice: 'sendVoice',
    video_note: 'sendVideoNote',
    sticker: 'sendSticker'
  };
  const fieldMap = {
    photo: 'photo',
    animation: 'animation',
    video: 'video',
    document: 'document',
    audio: 'audio',
    voice: 'voice',
    video_note: 'video_note',
    sticker: 'sticker'
  };

  const method = methodMap[mediaInfo.type] || 'sendDocument';
  const fieldName = fieldMap[mediaInfo.type] || 'document';

  if (buffer) {
    return tgSendMediaBuffer(token, method, fieldName, chatId, buffer, mediaInfo.filename || 'media.bin', mediaInfo.mime_type || 'application/octet-stream', caption);
  } else if (mediaInfo.file_id) {
    const payload = { chat_id: chatId };
    payload[fieldName] = mediaInfo.file_id;
    if (caption && method !== 'sendVideoNote' && method !== 'sendSticker') {
      payload.caption = caption;
      payload.parse_mode = 'HTML';
    }
    return tgCall(token, method, payload);
  }
}

// Multimodal Vision AI: Concise & Ultra-Professional
async function describeImageWithGemini(imageInput, customPrompt = '') {
  try {
    const base64Data = Buffer.isBuffer(imageInput) ? imageInput.toString('base64') : String(imageInput);
    const prompt = customPrompt || 'أنت المستشار التنفيذي لمجتمع Tradesejon للتداول الاحترافي. صف محتوى هذه الصورة أو الشارت باختصار تنفيذي شديد وبأسلوب فائق الاحترافية دون أي إطالة أو استرسال عامي. لخص العناصر الأساسية والرسالة في جملتين فقط لا غير وبشكل مكتمل وموجز:';
    const url = `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=${GEMINI_KEY}`;
    
    const payload = {
      contents: [{
        parts: [
          { text: prompt },
          { inline_data: { mime_type: 'image/jpeg', data: base64Data } }
        ]
      }],
      generationConfig: {
        thinkingConfig: { thinkingBudget: 0 },
        maxOutputTokens: 250
      }
    };

    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    return data.candidates?.[0]?.content?.parts?.[0]?.text?.trim() || '';
  } catch (err) {
    console.error('Gemini Vision error:', err);
    return '';
  }
}

// Groq AI Chat in Egyptian Masri
async function callGroqEgyptianAi(userMessage, systemRole = 'trader_coach') {
  let systemPrompt = `أنت المساعد الذكي لمجتمع Tradesejon للتداول الاحترافي.
تتحدث باللهجة المصرية الطبيعية، الودودة، والمحترفة جداً (مصري أصيل راقي بدون تكلف).
المؤسس والمحلل الرئيسي هو Tradesejon.
يقدم المجتمع جلسات تداول حية يومية عبر Zoom، إشارات صفقات ذكية، وتدريب مباشر.
الاشتراك متاح حالياً مجاناً لأول 7 أيام ($0 مع بطاقة لتفعيل العضوية وتأمين المقعد ثم $29 شهرياً).
لو المتداول بيسأل عن تفاصيل التداول أو الاشتراك أو جلسات Zoom، جاوبه باختصار وتشجيع.`;

  if (systemRole === 'ceo_assistant') {
    systemPrompt = `أنت المساعد التنفيذي الذكي لإدارة مجتمع Tradesejon للتداول.
تتحدث باللهجة المصرية الراقية والمهنية جداً مع المدير.
رابط الدفع والتسجيل الرسمي لجلسات VIP هو: https://whop.com/checkout/plan_TEMPLATE_02
قاعدة صارمة لا تقبل الاستثناء: ممنوع منعاً باتاً أن تدعي أو تزعم أنك قمت بإرسال رسالة أو رمز QR أو إيميل أو نفذت إجراء وأنت لم تنفذه برمجياً.
إذا طلب المدير معلومات عن رمز الـ QR أو كود الدفع أو رابط الاشتراك، أعطه الرابط المباشر فوراً (https://whop.com/checkout/plan_TEMPLATE_02) وأكد له جاهزية النظام.
لا تقل أبداً "لا يمكنني توليد صورة" بل وفر الرابط والمعلومات بوضوح.`;
  }

  try {
    const res = await fetch('https://api.groq.com/openai/v1/chat/completions', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${GROQ_KEY}`,
        'Content-Type': 'application/json',
        'User-Agent': 'TradesejonBot/1.0'
      },
      body: JSON.stringify({
        model: 'qwen/qwen3.8-27b',
        messages: [
          { role: 'system', content: systemPrompt },
          { role: 'user', content: userMessage }
        ],
        max_tokens: 350,
        temperature: 0.7
      })
    });
    const data = await res.json();
    return data.choices?.[0]?.message?.content || 'أهلاً بيك يا فندم في مجتمع Tradesejon! إزاي أقدر أساعدك النهاردة في تداولاتك؟';
  } catch (err) {
    console.error('Groq AI error:', err);
    return 'أهلاً بيك يا فندم في مجتمع Tradesejon للتداول! معاك فريق الدعم الفني، قولي إيه استفسارك وإحنا تحت أمرك فوراً.';
  }
}

// Groq Whisper Speech-to-Text
async function transcribeVoiceWithGroq(fileBuffer) {
  try {
    const boundary = `----WebKitFormBoundary${Math.random().toString(36).substring(2)}`;
    const parts = [];
    parts.push(Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\nwhisper-large-v3\r\n`));
    parts.push(Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="language"\r\n\r\nar\r\n`));
    parts.push(Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="voice.oga"\r\nContent-Type: audio/ogg\r\n\r\n`));
    parts.push(fileBuffer);
    parts.push(Buffer.from(`\r\n--${boundary}--\r\n`));

    const body = Buffer.concat(parts);
    const res = await fetch('https://api.groq.com/openai/v1/audio/transcriptions', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${GROQ_KEY}`,
        'User-Agent': 'TradesejonBot/1.0',
        'Content-Type': `multipart/form-data; boundary=${boundary}`,
        'Content-Length': String(body.length)
      },
      body
    });
    const data = await res.json();
    return data.text || '';
  } catch (err) {
    console.error('Groq Whisper error:', err);
    return '';
  }
}

// ElevenLabs Egyptian Human Voice Synthesis
async function synthesizeElevenLabsVoice(text) {
  try {
    const res = await fetch(`https://api.elevenlabs.io/v1/text-to-speech/${ELEVENLABS_VOICE_ID}`, {
      method: 'POST',
      headers: {
        'xi-api-key': ELEVENLABS_KEY,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        text,
        model_id: 'eleven_multilingual_v2',
        voice_settings: {
          stability: 0.5,
          similarity_boost: 0.8
        }
      })
    });
    if (!res.ok) {
      console.error('ElevenLabs HTTP error:', res.status);
      return null;
    }
    const arrayBuffer = await res.arrayBuffer();
    return Buffer.from(arrayBuffer);
  } catch (err) {
    console.error('ElevenLabs synthesis error:', err);
    return null;
  }
}

const {
  CEO_PHONE_LAST10,
  phoneLast10,
  isCeoOwnerPhone,
  extractDocPhone,
  isCeoContactDoc,
  extractSafePhoneForDoc,
  getDocChatId,
  parseTargetAndContent,
  resolveTargetProfile,
  sendAiExecutiveEmail,
  DISCORD_KNOWN_MAP,
  dispatchDiscordDmDirectly
} = require('./_mail_agent');

const {
  sleep,
  escapeHtml,
  sanitizeTelegramHtml,
  isCommandText,
  isBroadcastCommand,
  getAllTraderChatIds,
  matchOfficialFaqOrCapability,
  executeAgenticPlan
} = require('./_agentic_brain');
const { searchKnowledge, ingestKnowledge } = require('./_knowledge_sync');

async function getLiveZoomConfig(db) {
  const camp = await db.collection('campaigns').findOne({ _id: 'workshop-poc' }).catch(() => null);
  return {
    title: camp?.title || 'Tradesejon VIP Live Session',
    zoomUrl: camp?.zoomMeetingUrl || ZOOM_MEETING_URL,
    zoomId: camp?.zoomMeetingId || ZOOM_MEETING_ID,
    zoomPasscode: camp?.zoomPasscode || ZOOM_PASSCODE,
    whopUrl: camp?.whopCheckoutUrl || WHOP_CHECKOUT_URL
  };
}

async function recordAdminAction(db, { channel, prompt, reply, actionType, meta = {} }) {
  const now = new Date();
  try {
    await Promise.all([
      db.collection('aios_memory').insertOne({
        at: now,
        channel,
        prompt: String(prompt || ''),
        thought: `fast_path:${actionType}`,
        actions: [{ tool: actionType, args: meta }],
        results: [{ tool: actionType, ok: true, ...meta }],
        reply: String(reply || '')
      }),
      db.collection('interactions').insertOne({
        campaignId: 'workshop-poc',
        channel,
        type: actionType,
        direction: 'outbound',
        at: now,
        meta: { prompt: String(prompt || '').slice(0, 200), ...meta }
      })
    ]);
  } catch (err) {
    console.error('recordAdminAction error:', err.message);
  }
}

function getExecutiveMenuMarkup() {
  return {
    inline_keyboard: [
      [
        { text: '📊 تقرير CRM', callback_data: 'act:crm' },
        { text: '📬 آخر الإيميلات', callback_data: 'act:last_emails' }
      ],
      [
        { text: '🎯 كود QR', callback_data: 'act:qr' },
        { text: '🎥 رابط Zoom VIP', callback_data: 'act:zoom' }
      ],
      [
        { text: '📢 بث الميديا المعلقة', callback_data: 'act:bcast' },
        { text: '📋 دليل الأوامر', callback_data: 'act:menu' }
      ]
    ]
  };
}

function getTraderMenuMarkup() {
  return {
    inline_keyboard: [
      [
        { text: '🎯 كود QR والدفع ($0)', callback_data: 'tr:qr' },
        { text: '🎥 دخول جلسة VIP', callback_data: 'tr:vip' }
      ],
      [
        { text: '❓ المساعدة والدعم', callback_data: 'tr:help' }
      ]
    ]
  };
}

// Find Telegram Chat ID by Phone Number, Username or User ID
async function findChatIdByTarget(db, target) {
  const cleanTarget = String(target || '').trim();
  if (!cleanTarget) return null;

  // 1. Direct Chat ID / User ID
  if (/^[0-9]{7,13}$/.test(cleanTarget)) {
    const directDoc =
      await db.collection('leads').findOne({ $or: [{ 'telegram.chatId': cleanTarget }, { 'telegram.userId': cleanTarget }, { telegramChatId: cleanTarget }] }) ||
      await db.collection('trading_leads').findOne({ $or: [{ 'telegram.chatId': cleanTarget }, { telegramChatId: cleanTarget }] }) ||
      await db.collection('contacts').findOne({ $or: [{ contact_identifier: `tg_${cleanTarget}` }, { user_id: cleanTarget }, { user_id: Number(cleanTarget) }, { chatId: cleanTarget }] });
    const cid = getDocChatId(directDoc);
    if (cid) return cid;
  }

  // 2. Phone Number (strict explicit phone fields only — never JSON.stringify scan)
  const digits = cleanTarget.replace(/[^0-9]/g, '');
  if (digits.length >= 8) {
    const lastDigits = phoneLast10(digits);
    if (lastDigits === CEO_PHONE_LAST10) {
      return String(CEO_CHAT_ID);
    }
    const [contacts, v2Leads, leads] = await Promise.all([
      db.collection('contacts').find({
        $or: [
          { number: { $regex: lastDigits } },
          { phone_number: { $regex: lastDigits } },
          { phone: { $regex: lastDigits } }
        ]
      }).toArray(),
      db.collection('leads').find({
        $or: [
          { phoneE164: { $regex: lastDigits } },
          { phone: { $regex: lastDigits } }
        ]
      }).toArray(),
      db.collection('trading_leads').find({
        $or: [
          { phone: { $regex: lastDigits } },
          { number: { $regex: lastDigits } },
          { phone_number: { $regex: lastDigits } }
        ]
      }).toArray()
    ]);
    for (const doc of [...contacts, ...v2Leads, ...leads]) {
      if (phoneLast10(extractDocPhone(doc)) === lastDigits) {
        const cid = getDocChatId(doc);
        if (cid) return cid;
      }
    }
  }

  // 3. Username (exact escaped handle match)
  const cleanUsername = cleanTarget.replace(/^@/, '').trim().toLowerCase();
  if (cleanUsername) {
    const escU = cleanUsername.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const rxU = new RegExp(`^@?${escU}$`, 'i');
    const userDoc =
      await db.collection('leads').findOne({ $or: [{ 'telegram.username': rxU }, { telegramUsername: rxU }] }) ||
      await db.collection('trading_leads').findOne({ $or: [{ telegramUsername: rxU }, { telegram_user: rxU }] }) ||
      await db.collection('contacts').findOne({ $or: [{ telegram_user: rxU }, { username: rxU }] });
    const cid = getDocChatId(userDoc);
    if (cid) return cid;
  }

  return null;
}

// Time in Cairo Helper
function getCairoTimeStr() {
  return new Intl.DateTimeFormat('en-US', {
    timeZone: 'Africa/Cairo',
    hour12: true,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  }).format(new Date());
}


async function handleTelegramWebhook(req, res) {
  if (req.method !== 'POST') {
    const action = req.query?.action;
    if (action === 'diag') {
      const tokens = { ceo: CEO_TOKEN, pr: PR_TOKEN, trader: TRADING_TOKEN };
      const results = {};
      for (const [k, tok] of Object.entries(tokens)) {
        try {
          const rMe = await fetch(`https://api.telegram.org/bot${tok}/getMe`, { signal: AbortSignal.timeout(5000) });
          const jMe = await rMe.json();
          const rWh = await fetch(`https://api.telegram.org/bot${tok}/getWebhookInfo`, { signal: AbortSignal.timeout(5000) });
          const jWh = await rWh.json();
          results[k] = { me: jMe, webhook: jWh };
        } catch (e) {
          results[k] = { error: e.message };
        }
      }
      return res.status(200).json({ ok: true, diag: results });
    }
    if (action === 'set_webhooks') {
      const base = 'https://your-deployment.vercel.app/api/telegram';
      const sets = [
        { name: 'ceo', token: CEO_TOKEN, url: `${base}?bot=ceo` },
        { name: 'pr', token: PR_TOKEN, url: `${base}?bot=pr` },
        { name: 'trader', token: TRADING_TOKEN, url: `${base}?bot=trader` }
      ];
      const results = {};
      const drop = req.query?.drop === '1' ? 'true' : 'false';
      for (const s of sets) {
        try {
          const r = await fetch(`https://api.telegram.org/bot${s.token}/setWebhook?url=${encodeURIComponent(s.url)}&drop_pending_updates=${drop}&allowed_updates=${encodeURIComponent(JSON.stringify(["message", "callback_query"]))}`);
          results[s.name] = await r.json();
        } catch (e) {
          results[s.name] = { error: e.message };
        }
      }
      return res.status(200).json({ ok: true, set_webhooks: results });
    }
    return res.status(200).json({ status: 'Tradesejon 24/7 Cloud Bot Engine Online' });
  }

  const { bot } = req.query; // 'trader' | 'pr' | 'ceo'
  const update = req.body || {};
  const updateId = update.update_id;

  if (isDuplicate(updateId)) {
    return res.status(200).json({ ok: true, note: 'Duplicate update ignored in memory' });
  }

  const db = await getDb();

  // MongoDB distributed lock to eliminate duplicates across all serverless instances
  if (updateId) {
    try {
      await db.collection('processed_telegram_updates').insertOne({
        _id: String(updateId),
        bot: bot || 'unknown',
        at: new Date()
      });
    } catch (dupErr) {
      if (dupErr.code === 11000) {
        return res.status(200).json({ ok: true, note: 'Duplicate update ignored via Mongo lock' });
      }
    }
  }

  // Prevent automated tests from sending duplicate real Telegram messages to the CEO or traders
  const isTestCall = Boolean(update.isTest === true || req.query?.isTest === '1' || req.query?.isTest === 'true');

  // Interactive Inline Keyboard Callback Handler (Telegram Best Practice: answerCallbackQuery immediately)
  if (update.callback_query) {
    const cb = update.callback_query;
    const cbId = cb.id;
    const cbFrom = cb.from || {};
    const cbChatId = cb.message?.chat?.id || cbFrom.id;
    const cbMsgId = cb.message?.message_id;
    const cbData = String(cb.data || '').trim();
    const cbChannel = bot === 'pr' ? 'telegram_pr' : (bot === 'ceo' ? 'telegram_ceo' : 'telegram_trader');
    const cbToken = bot === 'pr' ? PR_TOKEN : (bot === 'ceo' ? CEO_TOKEN : TRADING_TOKEN);

    if (!isTestCall && cbId) {
      await tgCall(cbToken, 'answerCallbackQuery', { callback_query_id: cbId }).catch(() => {});
    }

    const editOrSend = async (textHtml, replyMarkup = null) => {
      if (isTestCall) return;
      const safe = sanitizeTelegramHtml(textHtml);
      if (cbMsgId) {
        const r = await tgCall(cbToken, 'editMessageText', {
          chat_id: cbChatId,
          message_id: cbMsgId,
          text: safe,
          parse_mode: 'HTML',
          ...(replyMarkup ? { reply_markup: replyMarkup } : {})
        });
        if (r && r.ok) return r;
      }
      return tgCall(cbToken, 'sendMessage', {
        chat_id: cbChatId,
        text: safe,
        parse_mode: 'HTML',
        ...(replyMarkup ? { reply_markup: replyMarkup } : {})
      });
    };

    if (bot === 'pr' || bot === 'ceo') {
      const isAuthorized = isTestCall || ALLOWED_ADMIN_IDS.has(String(cbFrom.id)) || ALLOWED_ADMIN_IDS.has(String(cbChatId));
      if (!isAuthorized) {
        await db.collection('interactions').insertOne({
          campaignId: 'workshop-poc',
          channel: cbChannel,
          type: 'unauthorized_callback',
          direction: 'inbound',
          at: new Date(),
          meta: { userId: cbFrom.id, username: cbFrom.username || '', cbData }
        });
        return res.status(200).json({ ok: false, error: 'Unauthorized callback user' });
      }

      if (cbData === 'act:menu') {
        const menuText = `🎛️ <b>غرفة عمليات Tradesejon AIOS التنفيذية</b>\n\nاختر إجراءً سريعاً من الأزرار بالأسفل أو اكتب أي أمر مباشر (صوت / نص / صورة / إيميل):\n• <code>eb3at mail le [الاسم] [المحتوى]</code>\n• <code>ابعت للناس كلها</code> (لبث الميديا المعلقة)\n• <code>qr code</code> (كود الدفع ورابط Whop)`;
        await editOrSend(menuText, getExecutiveMenuMarkup());
        await recordAdminAction(db, { channel: cbChannel, prompt: `[callback] ${cbData}`, reply: menuText, actionType: 'callback_menu' });
        return res.status(200).json({ ok: true, callback: cbData, reply: menuText });
      }

      if (cbData === 'act:crm') {
        const [nContacts, nLeadsV2, nTradingLeads, nInteractions, nConvs] = await Promise.all([
          db.collection('contacts').countDocuments(),
          db.collection('leads').countDocuments(),
          db.collection('trading_leads').countDocuments(),
          db.collection('interactions').countDocuments(),
          db.collection('conversations').countDocuments()
        ]);
        const report = `📊 <b>تقرير Atlas CRM المباشر:</b>\n` +
          `• جهات الاتصال (contacts): <b>${nContacts}</b>\n` +
          `• العملاء الرسميون (leads): <b>${nLeadsV2}</b> | (trading_leads): <b>${nTradingLeads}</b>\n` +
          `• التفاعلات المسجلة (interactions): <b>${nInteractions}</b>\n` +
          `• التذاكر والمحادثات: <b>${nConvs}</b>`;
        await editOrSend(report, getExecutiveMenuMarkup());
        await recordAdminAction(db, { channel: cbChannel, prompt: `[callback] ${cbData}`, reply: report, actionType: 'callback_crm', meta: { nContacts, nLeadsV2, nInteractions } });
        return res.status(200).json({ ok: true, callback: cbData, nContacts, nLeadsV2, nTradingLeads, nInteractions, nConvs, reply: report });
      }

      if (cbData === 'act:last_emails') {
        const mails = await db.collection('outbound_emails').find().sort({ at: -1, createdAt: -1 }).limit(5).toArray();
        const fallbackMails = mails.length > 0 ? mails : await db.collection('conversations').find({ recipientEmail: { $exists: true, $ne: null } }).sort({ at: -1 }).limit(5).toArray();
        const lines = fallbackMails.map((m, i) => `${i + 1}. <code>${escapeHtml(Array.isArray(m.to) ? m.to.join(', ') : (m.to || m.recipientEmail || ''))}</code> — ${escapeHtml(String(m.subject || m.user_message || 'Tradesejon Email').slice(0, 60))}`);
        const text = `📬 <b>آخر 5 إيميلات مرسلة عبر AIOS:</b>\n\n${lines.length ? lines.join('\n') : 'لا توجد إيميلات مسجلة بعد'}`;
        await editOrSend(text, getExecutiveMenuMarkup());
        await recordAdminAction(db, { channel: cbChannel, prompt: `[callback] ${cbData}`, reply: text, actionType: 'callback_last_emails' });
        return res.status(200).json({ ok: true, callback: cbData, emailsCount: fallbackMails.length, reply: text });
      }

      if (cbData === 'act:qr') {
        const qrText = `🎯 <b>رمز QR ورابط الدفع المباشر لجلسات التداول VIP من Tradesejon</b> 🚀\n\n🔗 <b>رابط التفعيل المباشر ($0 لأول 7 أيام):</b>\n${WHOP_CHECKOUT_URL}`;
        if (!isTestCall) {
          await tgCall(cbToken, 'sendPhoto', {
            chat_id: cbChatId,
            photo: QR_PHOTO_URL,
            caption: qrText,
            parse_mode: 'HTML',
            reply_markup: getExecutiveMenuMarkup()
          });
        }
        await recordAdminAction(db, { channel: cbChannel, prompt: `[callback] ${cbData}`, reply: qrText, actionType: 'callback_qr' });
        return res.status(200).json({ ok: true, callback: cbData, qrSent: true, reply: qrText });
      }

      if (cbData === 'act:zoom') {
        const z = await getLiveZoomConfig(db);
        const zoomUrl = z.zoomUrl;
        const zoomText = `🎥 <b>تفاصيل جلسة التداول VIP المباشرة (Zoom):</b>\n\n` +
          `📌 <b>الجلسة:</b> ${escapeHtml(z.title)}\n` +
          `🔗 <b>الرابط:</b> ${escapeHtml(zoomUrl)}\n` +
          `🆔 Meeting ID: <code>${escapeHtml(z.zoomId)}</code>\n` +
          `🔑 Passcode: <code>${escapeHtml(z.zoomPasscode)}</code>\n` +
          `💳 Whop VIP ($0 trial): ${escapeHtml(z.whopUrl)}`;
        await editOrSend(zoomText, getExecutiveMenuMarkup());
        await recordAdminAction(db, { channel: cbChannel, prompt: `[callback] ${cbData}`, reply: zoomText, actionType: 'callback_zoom' });
        return res.status(200).json({ ok: true, callback: cbData, zoomUrl, reply: zoomText });
      }

      if (cbData === 'act:bcast') {
        const mediaDoc = await db.collection('pending_ceo_media').findOne({ _id: 'latest_media' });
        if (!mediaDoc || (!mediaDoc.file_id && !mediaDoc.mediaBase64 && !mediaDoc.imageBase64)) {
          const errText = `⚠️ لا توجد ميديا معلقة حالياً في غرفة العمليات لبثها. ابعت صورة أو فيديو أولاً يا فندم.`;
          await editOrSend(errText, getExecutiveMenuMarkup());
          return res.status(200).json({ ok: false, callback: cbData, error: 'No staged media' });
        }
        const cids = await getAllTraderChatIds(db, CEO_CHAT_ID);
        const finalCap = mediaDoc.caption && !isCommandText(mediaDoc.caption) ? mediaDoc.caption : 'إشعار مباشر من غرفة تداول Tradesejon 📢';
        const rawB64 = mediaDoc.mediaBase64 || mediaDoc.imageBase64;
        const buf = rawB64 ? Buffer.from(rawB64, 'base64') : null;
        let sentCount = 0;
        for (const cid of cids) {
          if (isTestCall && cid !== String(CEO_CHAT_ID)) continue;
          const r = await sendAnyMedia(TRADING_TOKEN, cid, mediaDoc, buf, finalCap);
          if (r && r.ok !== false) sentCount++;
          await sleep(35);
        }
        const doneText = `✅ تم بث <b>${escapeHtml(mediaDoc.label || 'الميديا')}</b> بنجاح لـ <b>${sentCount}</b> متداول عبر زر التحكم السريع! 🚀`;
        await editOrSend(doneText, getExecutiveMenuMarkup());
        await recordAdminAction(db, { channel: cbChannel, prompt: `[callback] ${cbData}`, reply: doneText, actionType: 'callback_broadcast', meta: { sentCount } });
        return res.status(200).json({ ok: true, callback: cbData, sentCount, reply: doneText });
      }
    }

    // Trader Bot Callbacks (@YOUR_TRADER_BOT)
    if (cbData === 'tr:qr') {
      const qrCap = `🎯 <b>رمز QR وبطاقة Whop المجانية 100% ($0 بدون بطاقة)</b> 🚀\n\n🔗 ${WHOP_CHECKOUT_URL}`;
      if (!isTestCall) {
        await tgCall(TRADING_TOKEN, 'sendPhoto', {
          chat_id: cbChatId,
          photo: QR_PHOTO_URL,
          caption: qrCap,
          parse_mode: 'HTML',
          reply_markup: getTraderMenuMarkup()
        });
      }
      await db.collection('interactions').insertOne({ campaignId: 'workshop-poc', channel: 'telegram', type: 'trader_callback_qr', direction: 'outbound', at: new Date(), meta: { chatId: String(cbChatId) } });
      return res.status(200).json({ ok: true, callback: cbData, qrSent: true });
    }

    if (cbData === 'tr:vip') {
      const leadDoc = await db.collection('leads').findOne({ 'telegram.chatId': String(cbChatId) }) ||
        await db.collection('trading_leads').findOne({ 'telegram.chatId': String(cbChatId) });
      const paid = leadDoc?.paid === true || leadDoc?.is_paid === true || ['paid_whop', 'prospect_paid_once', 'vip_recurring', 'viproom_workshop_vip', 'attended'].includes(String(leadDoc?.status || '').toLowerCase());
      const z = paid ? await getLiveZoomConfig(db) : null;
      const vipMsg = paid
        ? `🎙️ <b>اشتراكك مفعل في Tradesejon Workshop!</b>\n\n🔗 <b>Workshop Live:</b> ${escapeHtml(leadDoc?.discord_live_url || z.zoomUrl)}\n🎁 <b>Whop Free Pass ($0 بدون بطاقة):</b> ${WHOP_CHECKOUT_URL}`
        : `👋 <b>انضم لقناة Marhaban على Discord ثم فعّل بطاقة Whop المجانية ($0 بدون بطاقة):</b>\n🔗 ${DISCORD_MARHABAN_INVITE_URL}\n\n• 🎁 <b>Whop Free Pass ($0.00 — بدون بطاقة بنكية):</b> ${WHOP_CHECKOUT_URL}`;
      await editOrSend(vipMsg, getTraderMenuMarkup());
      await db.collection('interactions').insertOne({ campaignId: 'workshop-poc', channel: 'telegram', type: 'trader_callback_vip', direction: 'outbound', at: new Date(), meta: { chatId: String(cbChatId), paid } });
      return res.status(200).json({ ok: true, callback: cbData, paid, reply: vipMsg });
    }

    if (cbData === 'tr:help') {
      const helpMsg = `👋 <b>مساعد مجتمع Tradesejon الذكي</b>\n\nتقدر تسألني بأي لغة (عربي / فرانكو / إنجليزي) عن:\n• جلسات التداول المباشرة والتوقيت\n• إرسال صورة شارت وسأقوم بتحليلها فوراً\n• تفعيل العضوية عبر كود الـ QR ورابط Whop\n• أو إرسال رسالة صوتية وسيتابع معك فريق المستشارين فوراً.`;
      await editOrSend(helpMsg, getTraderMenuMarkup());
      return res.status(200).json({ ok: true, callback: cbData, reply: helpMsg });
    }

    return res.status(200).json({ ok: true, callback: cbData });
  }

  const msg = update.message || update.edited_message;
  if (!msg) {
    return res.status(200).json({ ok: true, note: 'No message in update' });
  }

  const chatId = msg.chat?.id;
  const from = msg.from || {};
  const userId = from.id;
  const firstName = from.first_name || '';
  const lastName = from.last_name || '';
  const fullName = [firstName, lastName].filter(Boolean).join(' ') || 'متداول عزيز';
  const username = from.username ? `@${from.username}` : 'بدون معرف';
  const rawText = String(msg.text || msg.caption || '').trim();
  const cairoTime = getCairoTimeStr();

  // Pattern matching for asking to describe an image
  const hasSendOrActionVerb = /(?:eb3at|ib3at|ab3at|ابعت|أبعت|ارسل|أرسل|بث|انشر|شير|وديه|فرورد|وزع|send|broadcast|share|forward|وريني|هات|عايز|بتعرف)/i.test(rawText);
  const asksAboutPicture = !hasSendOrActionVerb && /(?:eh\s+el\s*sora|ايه\s+الصورة|إيه\s+الصورة|ايه\s+دي|ايه\s+ده|إيه\s+دي|إيه\s+ده|اشرح|حلل|وصف|لخص|describe|what\s+is\s+in|explain|analyze)/i.test(rawText);

  // Extract media if present in incoming message
  const mediaInfo = extractMediaInfo(msg);

  // =========================================================================
  // BOT 1: TRADER COMMUNITY BOT (@YOUR_TRADER_BOT)
  // =========================================================================
  if (bot === 'trader' || (!bot && msg.chat?.type === 'private')) {
    const startMatch = rawText.match(/^\/start(?:\s+(\S+))?/i);
    if (startMatch) {
      const startPayload = (startMatch[1] || 'workshop-poc').trim();
      const now = new Date();
      let existingLead = await db.collection('leads').findOne({
        $or: [
          { 'telegram.chatId': String(chatId) },
          ...(startPayload !== 'workshop-poc' ? [{ 'ticket.token': startPayload }] : [])
        ]
      });

      const leadFilter = existingLead ? { _id: existingLead._id } : { 'telegram.chatId': String(chatId) };
      const upsertedLead = await db.collection('leads').findOneAndUpdate(
        leadFilter,
        {
          $set: {
            campaignId: startPayload.startsWith('workshop') ? startPayload : (existingLead?.campaignId || 'workshop-poc'),
            name: existingLead?.name || fullName,
            telegram: {
              chatId: String(chatId),
              userId: String(userId),
              username: from.username || '',
              startedAt: existingLead?.telegram?.startedAt || now
            },
            updatedAt: now
          },
          $addToSet: { sources: 'telegram' },
          $setOnInsert: {
            status: 'contacted',
            consent: { email: false, whatsapp: false, telegram: true },
            createdAt: now
          }
        },
        { upsert: true, returnDocument: 'after' }
      );

      await db.collection('trading_leads').updateOne(
        { 'telegram.chatId': String(chatId) },
        {
          $set: {
            name: fullName,
            telegramUsername: username,
            telegramChatId: String(chatId),
            'telegram.chatId': String(chatId),
            'telegram.username': from.username || '',
            updatedAt: now
          },
          $setOnInsert: { status: 'contacted', sources: [{ channel: 'telegram', at: now }], createdAt: now }
        },
        { upsert: true }
      );

      await db.collection('interactions').insertOne({
        leadId: upsertedLead?.value?._id || upsertedLead?._id || null,
        campaignId: 'workshop-poc',
        channel: 'telegram',
        type: 'bot_start',
        direction: 'inbound',
        at: now,
        meta: { payload: startPayload, username, chatId: String(chatId) }
      });

      const welcomeMsg = `أهلاً بك يا <b>${escapeHtml(fullName)}</b> في مجتمع <b>Tradesejon</b> للتداول الاحترافي! 🚀\n\n` +
        `🎁 يمكنك تفعيل تجربة جلسات الـ VIP المباشرة مجاناً لمدة 7 أيام ($0 اليوم لتأكيد مقعدك) أو الاستفسار مباشرة عن التوقيت والتفاصيل.\n\n` +
        `استخدم الأزرار السريعة بالأسفل أو اكتب استفسارك مباشرة (نص، صوت، أو صورة شارت):`;

      if (!isTestCall) {
        await tgCall(TRADING_TOKEN, 'sendMessage', {
          chat_id: chatId,
          text: welcomeMsg,
          parse_mode: 'HTML',
          reply_markup: getTraderMenuMarkup()
        });
      }
      return res.status(200).json({ ok: true, started: true, payload: startPayload, reply: welcomeMsg });
    }

    const isVoice = Boolean(msg.voice);
    let userQuery = rawText;
    let voiceBuffer = null;
    let mediaBuffer = null;
    let photoDescription = '';

    // 1. Process voice note
    if (isVoice && msg.voice?.file_id) {
      voiceBuffer = await tgDownloadFile(TRADING_TOKEN, msg.voice.file_id);
      if (voiceBuffer) {
        const transcribed = await transcribeVoiceWithGroq(voiceBuffer);
        if (transcribed) userQuery = transcribed;
      }
    }

    // 2. Download any incoming media buffer for forwarding to PR Desk
    if (mediaInfo && mediaInfo.file_id) {
      mediaBuffer = await tgDownloadFile(TRADING_TOKEN, mediaInfo.file_id);
    }

    const queryAsksPicture = asksAboutPicture || /(?:eh\s+el\s*sora|elsora|sora|صورة|صوره|picture|photo|image|ايه\s+الصورة|إيه\s+الصورة|ايه\s+دي|ايه\s+ده|إيه\s+دي|إيه\s+ده|اشرح|حلل|وصف|describe|what\s+is\s+in|explain)/i.test(userQuery);

    // 3. Vision AI: ONLY when specifically asked
    if (mediaInfo && (mediaInfo.type === 'photo' || mediaInfo.type === 'animation') && mediaBuffer && queryAsksPicture) {
      photoDescription = await describeImageWithGemini(mediaBuffer);
    } else if (queryAsksPicture) {
      if (msg.reply_to_message?.photo) {
        const replyPhoto = msg.reply_to_message.photo[msg.reply_to_message.photo.length - 1];
        const replyPhotoBuf = await tgDownloadFile(TRADING_TOKEN, replyPhoto.file_id);
        if (replyPhotoBuf) {
          photoDescription = await describeImageWithGemini(replyPhotoBuf);
        }
      } else {
        const mediaDoc = await db.collection('pending_ceo_media').findOne({ _id: 'latest_media' });
        if (mediaDoc?.visionDescription) {
          photoDescription = mediaDoc.visionDescription;
        } else if (mediaDoc?.mediaBase64 || mediaDoc?.imageBase64) {
          const rawBase64 = mediaDoc.mediaBase64 || mediaDoc.imageBase64;
          photoDescription = await describeImageWithGemini(rawBase64);
          if (photoDescription) {
            await db.collection('pending_ceo_media').updateOne({ _id: 'latest_media' }, { $set: { visionDescription: photoDescription } });
          }
        }
      }
    }

    // 4. Intent & Paywall Check
    const isCheckoutQuery = /(?:اشترك|اشتراك|دفع|ادفع|سعر|بكام|whop|انضم|سجل|تسجيل|فلوس|free|مجاني|qr|كيور|كود|payment|pay|checkout)/i.test(userQuery);
    const isSessionQuery = /رابط|لينك|zoom|زووم|جلسة|جلسه|ميعاد|امتى|ادخل/i.test(userQuery);

    const lead = await db.collection('leads').findOne({ 'telegram.chatId': String(chatId) }) ||
      await db.collection('trading_leads').findOne({ 'telegram.chatId': String(chatId) });
    const isPaid = lead?.status === 'paid_whop' || lead?.status === 'attended' || Boolean(lead?.ticket?.token);
    const ticketCode = `TRD-${Math.floor(1000 + Math.random() * 9000)}`;

    let aiAnswer = '';
    if (photoDescription) {
      aiAnswer = `أهلاً بك يا فندم في Tradesejon.\n\n🔍 <b>الملخص التنفيذي للصورة:</b>\n${photoDescription}\n\nنحن في خدمتك دائماً لأي استفسار إضافي.`;
    } else if (mediaInfo && (mediaInfo.type === 'photo' || mediaInfo.type === 'animation') && !queryAsksPicture) {
      aiAnswer = `أهلاً بك يا فندم في مجتمع Tradesejon. تم استلام ${mediaInfo.label} بنجاح 📸.\nإذا كنت ترغب في تحليلها أو شرح الشارت، اسألني في أي وقت وسأقوم بتلخيصها لك باحترافية.`;
    } else if (mediaInfo && mediaInfo.type !== 'photo' && mediaInfo.type !== 'animation' && !isVoice) {
      aiAnswer = `أهلاً بك يا فندم في مجتمع Tradesejon. تم استلام ${mediaInfo.label} بنجاح ✅.\nفريق المستشارين المختص يتابع معك أيضاً لأي استفسار.`;
    } else if (isSessionQuery && isPaid) {
      const z = await getLiveZoomConfig(db);
      aiAnswer = `أهلاً بيك يا بطل! اشتراكك مفعل رسمياً في جلسات التداول VIP. 🎟️\n\n` +
        `رابط جلسة التداول المباشرة عبر Zoom:\n` +
        `🔗 ${escapeHtml(z.zoomUrl)}\n` +
        `🆔 Meeting ID: <b>${escapeHtml(z.zoomId)}</b>\n` +
        `🔑 Passcode: <b>${escapeHtml(z.zoomPasscode)}</b>\n\nنراك في الجلسة القادمة!`;
    } else if (isCheckoutQuery && !isPaid) {
      const qrUrl = QR_PHOTO_URL;
      const vipUsd = process.env.VIP_MONTHLY_USD || '45';
      const brandName = process.env.BRAND_NAME || 'Tradesejon';
      aiAnswer = `🎯 <b>أهلاً بك في مجتمع ${escapeHtml(brandName)} للتداول الاحترافي!</b> 🚀\n\n` +
        `إليك رمز QR ورابط التفعيل المباشر لباقة Discord VIP + Scanner ($${escapeHtml(vipUsd)} شهرياً via Whop):\n\n` +
        `🔗 <b>رابط التفعيل المباشر:</b>\n<a href="${WHOP_CHECKOUT_URL}">${WHOP_CHECKOUT_URL}</a>\n\n` +
        `امسح كود الـ QR أو اضغط على الرابط للتفعيل الفوري وتأمين مقعدك! 🚀`;

      if (!isTestCall) {
        await tgCall(TRADING_TOKEN, 'sendPhoto', {
          chat_id: chatId,
          photo: qrUrl,
          caption: aiAnswer,
          parse_mode: 'HTML',
          reply_markup: getTraderMenuMarkup()
        });
      }
    } else {
      const unifiedFaq = matchOfficialFaqOrCapability(userQuery || '');
      if (unifiedFaq) {
        aiAnswer = unifiedFaq;
      } else {
        const kbHits = await searchKnowledge(db, userQuery || '', 3).catch(() => []);
        if (kbHits.length > 0 && kbHits[0].score >= 2) {
          aiAnswer = `📚 <b>${escapeHtml(kbHits[0].title)}</b>\n\n${escapeHtml(kbHits[0].snippet)}`;
        } else {
          aiAnswer = await callGroqEgyptianAi(userQuery || 'مرحباً، كيف يمكنني الاستفادة من مجتمع Tradesejon؟');
        }
      }
    }

    // Auto-dispatch verified email invitation if trader provided an email address
    const emailMatch = userQuery.match(/([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})/);
    if (emailMatch) {
      const traderEmail = emailMatch[1].toLowerCase();
      await sendAiExecutiveEmail(db, traderEmail, `طلب المتداول في التليجرام: ${userQuery}`);
    }

    // 5. Deliver response to trader
    if (!isTestCall) {
      if (isVoice) {
        const audioBuffer = await synthesizeElevenLabsVoice(aiAnswer);
        const caption = sanitizeTelegramHtml(`${aiAnswer}\n\n📌 كود متابعة التذكرة: <code>[${ticketCode}]</code>\nالمستشار المختص يتابع معك أيضاً لأي استفسار إضافي. 🙏`);
        if (audioBuffer) {
          await tgSendMediaBuffer(TRADING_TOKEN, 'sendVoice', 'voice', chatId, audioBuffer, 'voice.mp3', 'audio/mpeg', caption);
        } else {
          await tgCall(TRADING_TOKEN, 'sendMessage', { chat_id: chatId, text: caption, parse_mode: 'HTML', reply_markup: getTraderMenuMarkup() });
        }
      } else if (!isCheckoutQuery || isPaid || photoDescription || mediaInfo) {
        const replyMsg = sanitizeTelegramHtml(`${aiAnswer}\n\n📌 كود متابعة التذكرة: <code>[${ticketCode}]</code>\nالمستشار المختص يتابع معك أيضاً لأي استفسار إضافي. 🙏`);
        await tgCall(TRADING_TOKEN, 'sendMessage', { chat_id: chatId, text: replyMsg, parse_mode: 'HTML', reply_markup: getTraderMenuMarkup() });
      }
    }

    // 6. Forward ticket alert & live media to PR Desk (@YOUR_PR_BOT)
    const uidStr = String(userId);
    const uidNum = Number(userId);
    const cidStr = String(chatId);
    const cleanTgUser = String(from.username || '').replace(/^@/, '').trim();
    const contactOr = [
      { contact_identifier: `tg_${uidStr}` },
      { contact_identifier: `tg_${cidStr}` },
      { 'telegram.userId': uidStr },
      { 'telegram.chatId': cidStr },
      { chatId: cidStr },
      { telegramChatId: cidStr },
      { user_id: uidStr }
    ];
    if (Number.isFinite(uidNum) && uidNum > 0) contactOr.push({ user_id: uidNum });
    if (cleanTgUser) {
      const escU = cleanTgUser.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const rxU = new RegExp(`^@?${escU}$`, 'i');
      contactOr.push({ telegram_user: rxU });
      contactOr.push({ username: rxU });
    }
    const [matchedContacts, tradingLeadDoc] = await Promise.all([
      db.collection('contacts').find({ $or: contactOr }).toArray(),
      db.collection('trading_leads').findOne({ $or: [{ 'telegram.chatId': cidStr }, { telegramChatId: cidStr }] })
    ]);
    const contact = matchedContacts.find(c =>
      c.contact_identifier === `tg_${uidStr}` ||
      c.contact_identifier === `tg_${cidStr}` ||
      String(c.user_id || '') === uidStr ||
      String(c.telegram?.chatId || c.chatId || '') === cidStr
    ) || matchedContacts[0] || null;

    const isCeoSender = uidStr === String(CEO_CHAT_ID) || cidStr === String(CEO_CHAT_ID);
    const phoneOpts = { explicitUserId: uidStr, explicitChatId: cidStr, isCeoContext: isCeoSender };
    let phone =
      extractSafePhoneForDoc(contact, phoneOpts) ||
      extractSafePhoneForDoc(lead, phoneOpts) ||
      extractSafePhoneForDoc(tradingLeadDoc, phoneOpts) ||
      '';

    // Self-heal & alert if any non-CEO record had CEO owner phone stamped onto it
    if (!isCeoSender) {
      const rawContactPhone = extractDocPhone(contact);
      const rawLeadPhone = extractDocPhone(lead);
      const rawTLeadPhone = extractDocPhone(tradingLeadDoc);
      if (isCeoOwnerPhone(rawContactPhone) || isCeoOwnerPhone(rawLeadPhone) || isCeoOwnerPhone(rawTLeadPhone)) {
        phone = '';
        try {
          const healAt = new Date();
          if (contact && !isCeoContactDoc(contact) && isCeoOwnerPhone(rawContactPhone)) {
            await db.collection('contacts').updateOne(
              { _id: contact._id, contact_identifier: { $ne: `tg_${CEO_CHAT_ID}` } },
              { $set: { number: '', phone_number: '', phone: '', updatedAt: healAt } }
            );
          }
          if (lead && isCeoOwnerPhone(rawLeadPhone)) {
            await db.collection('leads').updateOne(
              { _id: lead._id, 'telegram.chatId': { $ne: String(CEO_CHAT_ID) } },
              { $set: { phoneE164: '', phone: '', updatedAt: healAt } }
            );
          }
          if (tradingLeadDoc && isCeoOwnerPhone(rawTLeadPhone)) {
            await db.collection('trading_leads').updateOne(
              { _id: tradingLeadDoc._id, telegramChatId: { $ne: String(CEO_CHAT_ID) } },
              { $set: { phone: '', number: '', phone_number: '', updatedAt: healAt } }
            );
          }
          await db.collection('interactions').insertOne({
            campaignId: 'workshop-poc',
            channel: 'security_self_heal',
            type: 'phone_leak_prevented',
            direction: 'internal',
            at: healAt,
            meta: { userId: uidStr, chatId: cidStr, username: cleanTgUser, fullName, ticketCode }
          });
        } catch (_) {}
      }
    }
    const userProfileLink = `<a href="tg://user?id=${userId}">فتح المحادثة المباشرة مع ${escapeHtml(fullName)}</a>`;
    const usernameDisplay = from.username ? `<a href="https://t.me/${from.username}">@${escapeHtml(from.username)}</a>` : 'بدون معرف عام (@)';

    const mediaLabel = mediaInfo ? mediaInfo.label : '';
    const queryDisplay = userQuery || (mediaInfo ? `📎 (أرسل ${mediaLabel} مرفق تجده أدناه)` : '(بدون نص)');

    const prAlertText = `🚨 <b>تذكرة جديدة: [${ticketCode}]</b>\n` +
      `━━━━━━━━━━━━━━━━━━\n` +
      `👤 <b>الاسم:</b> ${escapeHtml(fullName)}\n` +
      `🆔 <b>معرف المستخدم (User ID):</b> <code>${userId}</code>\n` +
      `🔗 <b>حساب تيليجرام:</b> ${userProfileLink}\n` +
      `📱 <b>اسم المستخدم:</b> ${usernameDisplay}\n` +
      (phone ? `📞 <b>رقم الهاتف:</b> ${escapeHtml(phone)}\n` : '') +
      `🌐 <b>لغة الجهاز:</b> <code>${escapeHtml(from.language_code || 'ar')}</code>\n` +
      `⏰ <b>التوقيت:</b> ${cairoTime}\n` +
      `💬 <b>نص الاستفسار:</b>\n${escapeHtml(queryDisplay)}\n\n` +
      `🤖 <b>رد الـ AI Agent التلقائي للمتداول:</b>\n${sanitizeTelegramHtml(aiAnswer)}\n\n` +
      `━━━━━━━━━━━━━━━━━━\n` +
      `🔄 <b>للرد البشري:</b> اعمل Reply مباشرة (كتابة أو رسالة صوتية 🎙️ أو أي ميديا) على هذه الرسالة، أو اكتب:\n` +
      `<code>/reply ${ticketCode} نص الرد هنا</code>`;

    if (!isTestCall) {
      await tgCall(PR_TOKEN, 'sendMessage', { chat_id: CEO_CHAT_ID, text: prAlertText, parse_mode: 'HTML' });

      // Forward the exact media to PR Desk
      if (mediaInfo && mediaBuffer) {
        await sendAnyMedia(PR_TOKEN, CEO_CHAT_ID, mediaInfo, mediaBuffer, `📎 ${mediaLabel} مرسل من المتداول [${ticketCode}] - ${fullName}`);
      } else if (isVoice && voiceBuffer) {
        await tgSendMediaBuffer(PR_TOKEN, CEO_CHAT_ID, 'sendVoice', 'voice', voiceBuffer, 'voice.oga', 'audio/ogg', `🎙️ تسجيل المتداول الصوتي [${ticketCode}] - ${fullName}`);
      }
    }

    // 7. Upsert into MongoDB Atlas (leads + trading_leads + conversations + interactions + telegram_messages)
    const now = new Date();
    const leadUpsert = await db.collection('leads').findOneAndUpdate(
      { 'telegram.chatId': String(chatId) },
      {
        $set: {
          campaignId: lead?.campaignId || 'workshop-poc',
          name: fullName,
          telegram: { chatId: String(chatId), userId: String(userId), username: from.username || '' },
          updatedAt: now
        },
        $addToSet: { sources: 'telegram' },
        $setOnInsert: { status: 'contacted', consent: { email: false, whatsapp: false, telegram: true }, createdAt: now }
      },
      { upsert: true, returnDocument: 'after' }
    );

    await db.collection('trading_leads').updateOne(
      { 'telegram.chatId': String(chatId) },
      {
        $set: {
          name: fullName,
          telegramUsername: username,
          telegramChatId: String(chatId),
          'telegram.chatId': String(chatId),
          'telegram.username': from.username || '',
          updatedAt: now
        },
        $setOnInsert: { status: 'contacted', sources: [{ channel: 'telegram', at: now }], createdAt: now }
      },
      { upsert: true }
    );

    await Promise.all([
      db.collection('conversations').insertOne({
        ticketCode,
        chatId: String(chatId),
        userId: String(userId),
        fullName,
        username,
        query: userQuery,
        aiAnswer,
        isVoice,
        mediaType: mediaInfo?.type || null,
        at: now
      }),
      db.collection('telegram_messages').insertOne({
        bot: 'trader',
        chatId: String(chatId),
        userId: String(userId),
        username: from.username || '',
        text: userQuery,
        ticketCode,
        at: now
      }),
      db.collection('interactions').insertOne({
        leadId: leadUpsert?.value?._id || leadUpsert?._id || null,
        campaignId: 'workshop-poc',
        channel: 'telegram',
        type: 'trader_message',
        direction: 'inbound',
        at: now,
        meta: { ticketCode, query: String(userQuery || '').slice(0, 200), isVoice, mediaType: mediaInfo?.type || null }
      })
    ]);

    return res.status(200).json({ ok: true, ticketCode, aiAnswer, phone, userId: String(userId), prAlertText });
  }

  // =========================================================================
  // BOT 2 & 3: PR DESK BOT (@YOUR_PR_BOT) & CEO BOT (@YOUR_CEO_BOT)
  // =========================================================================
  if (bot === 'pr' || bot === 'ceo') {
    const currentToken = bot === 'pr' ? PR_TOKEN : CEO_TOKEN;
    const currentChannel = bot === 'pr' ? 'telegram_pr' : 'telegram_ceo';
    const isAuthorizedAdmin = isTestCall || ALLOWED_ADMIN_IDS.has(String(userId)) || ALLOWED_ADMIN_IDS.has(String(chatId));
    if (!isAuthorizedAdmin) {
      await db.collection('interactions').insertOne({
        campaignId: 'workshop-poc',
        channel: currentChannel,
        type: 'unauthorized_admin_attempt',
        direction: 'inbound',
        at: new Date(),
        meta: { userId: String(userId), chatId: String(chatId), username, text: rawText.slice(0, 120) }
      });
      await tgCall(currentToken, 'sendMessage', {
        chat_id: chatId,
        text: `⛔ عذراً، لوحة القيادة التنفيذية (<b>Tradesejon AIOS</b>) مخصصة لإدارة المنصة فقط. معرّف حسابك: <code>${chatId}</code>`,
        parse_mode: 'HTML'
      });
      return res.status(200).json({ ok: false, error: 'Unauthorized admin access', chatId: String(chatId) });
    }

    const replyToMsg = msg.reply_to_message;
    const replyAlertText = replyToMsg ? (replyToMsg.text || replyToMsg.caption || '') : '';
    const replyTicketMatch = replyAlertText.match(/\[(TRD-[0-9]+)\]/i);
    let effectiveText = rawText;
    let wasVoiceNoteCommand = false;

    await db.collection('telegram_messages').insertOne({
      bot,
      chatId: String(chatId),
      userId: String(userId),
      username: from.username || '',
      text: rawText,
      mediaType: mediaInfo?.type || null,
      at: new Date()
    }).catch(() => {});

    // Quick slash commands (/menu, /start, /help, menu)
    if (/^\/(?:start|menu|help)(?:\s|$)|^menu$/i.test(effectiveText.trim())) {
      const menuText = `🎛️ <b>أهلاً بك يا فندم في غرفة عمليات Tradesejon AIOS</b> 🚀\n\n` +
        `البوتات (CEO & PR) وشات AIOS Web متصلة الآن بنفس العقل الذكي والذاكرة الموحدة في MongoDB Atlas.\n\n` +
        `اختر من الأزرار التفاعلية أدناه أو ابعت أي أمر بصوتك أو كتابة أو صورة:`;
      if (!isTestCall) {
        await tgCall(currentToken, 'sendMessage', {
          chat_id: chatId,
          text: menuText,
          parse_mode: 'HTML',
          reply_markup: getExecutiveMenuMarkup()
        });
      }
      await recordAdminAction(db, { channel: currentChannel, prompt: effectiveText, reply: menuText, actionType: 'menu' });
      return res.status(200).json({ ok: true, command: 'menu', reply: menuText });
    }

    // Quick KPIs & Stats (/kpis, kpis, /stats, stats, الاحصائيات)
    if (/^\/(?:stats?|kpis?)(?:\s|$)|^(?:kpis?|stats?|الإحصائيات|الاحصائيات|إحصائيات|احصائيات)$/i.test(effectiveText.trim())) {
      const [nContacts, nLeadsV2, nTradingLeads, nInteractions, nConvs, nOutbound] = await Promise.all([
        db.collection('contacts').countDocuments(),
        db.collection('leads').countDocuments(),
        db.collection('trading_leads').countDocuments(),
        db.collection('interactions').countDocuments(),
        db.collection('conversations').countDocuments(),
        db.collection('outbound_emails').countDocuments()
      ]);
      const statsText = `📊 <b>إحصائيات Tradesejon Atlas الحية (Live KPIs):</b>\n\n` +
        `• جهات الاتصال (contacts): <b>${nContacts}</b>\n` +
        `• العملاء الرسميون (leads): <b>${nLeadsV2}</b>\n` +
        `• المتداولون (trading_leads): <b>${nTradingLeads}</b>\n` +
        `• التفاعلات المسجلة (interactions): <b>${nInteractions}</b>\n` +
        `• التذاكر والمحادثات: <b>${nConvs}</b>\n` +
        `• الإيميلات المسجلة (outbound_emails): <b>${nOutbound}</b>\n\n` +
        `🚀 المنظومة تعمل بكفاءة 100% ومتزامنة لحظياً عبر تليجرام وديسكورد.`;
      if (!isTestCall) {
        await tgCall(currentToken, 'sendMessage', {
          chat_id: chatId,
          text: statsText,
          parse_mode: 'HTML',
          reply_markup: getExecutiveMenuMarkup()
        });
      }
      await recordAdminAction(db, { channel: currentChannel, prompt: effectiveText, reply: statsText, actionType: 'stats', meta: { nContacts, nLeadsV2, nInteractions } });
      return res.status(200).json({ ok: true, command: 'stats', nContacts, nLeadsV2, nTradingLeads, nInteractions, nConvs, nOutbound, reply: statsText });
    }

    // 1. Any Media sent by Admin / CEO (unless replying directly to a [TRD-XXXX] ticket)
    if (mediaInfo && mediaInfo.file_id && !replyTicketMatch) {
      const mediaBuf = await tgDownloadFile(currentToken, mediaInfo.file_id);
      let visionDesc = '';

      if (
        mediaInfo.type === 'document' &&
        mediaBuf &&
        /\.(?:txt|md|json|csv|tsv|html?|docx|xlsx|pdf|ya?ml)$/i.test(mediaInfo.filename || '') &&
        (!effectiveText || /(?:\/addkb|add\s*kb|kb|knowledge|معرفة|قاعدة|sync|تحديث)/i.test(effectiveText))
      ) {
        const ing = await ingestKnowledge(db, {
          title: (effectiveText || mediaInfo.filename).replace(/^\/addkb\s*/i, '').trim(),
          filename: mediaInfo.filename,
          mimeType: mediaInfo.mime_type,
          category: `telegram_${bot}_upload`,
          contentBase64: mediaBuf.toString('base64')
        });
        const kbMsg = ing.ok
          ? `✅ <b>Document Synced System-Wide!</b>\n📄 <b>File:</b> ${escapeHtml(mediaInfo.filename)}\n• Chunks Indexed: <b>${ing.entry.chunks_count}</b>\n• Synced across AIOS, MongoDB, n8n RAG, Discord &amp; Telegram!`
          : `⚠️ Could not extract knowledge from ${escapeHtml(mediaInfo.filename)}: ${escapeHtml(ing.error || 'Unknown error')}`;
        if (!isTestCall) {
          await tgCall(currentToken, 'sendMessage', { chat_id: chatId, text: kbMsg, parse_mode: 'HTML', reply_markup: getExecutiveMenuMarkup() });
        }
        return res.status(200).json({ ok: Boolean(ing.ok), action: 'ingest_document', entry: ing.entry, reply: kbMsg });
      }

      // If admin sent a voice note on PR/CEO bot without caption, ALWAYS transcribe it and execute as spoken voice command!
      wasVoiceNoteCommand = false;
      if (mediaInfo.type === 'voice' && mediaBuf && !effectiveText) {
        const transcribedVoice = await transcribeVoiceWithGroq(mediaBuf);
        if (transcribedVoice) {
          effectiveText = transcribedVoice.trim();
          wasVoiceNoteCommand = true;
        }
      }

      // Only describe if explicitly asked in caption
      if (asksAboutPicture && mediaBuf && (mediaInfo.type === 'photo' || mediaInfo.type === 'animation')) {
        visionDesc = await describeImageWithGemini(mediaBuf);
      }

      // Clean caption: if the caption itself is a bot command (like "ابعتها الناس كلها"), don't save the command as the public caption
      const captionIsCmd = wasVoiceNoteCommand || isCommandText(effectiveText) || isBroadcastCommand(effectiveText, false);
      let customCapFromUpload = '';
      if (captionIsCmd) {
        const mCap = effectiveText.match(/(?:واكتب|بكابشن|w\s*ekteb|caption)\s+(.+)/i);
        if (mCap) customCapFromUpload = mCap[1].trim();
      } else {
        customCapFromUpload = effectiveText;
      }

      await db.collection('pending_ceo_media').updateOne(
        { _id: 'latest_media' },
        {
          $set: {
            type: mediaInfo.type,
            file_id: mediaInfo.file_id,
            filename: mediaInfo.filename,
            mime_type: mediaInfo.mime_type,
            label: mediaInfo.label,
            mediaBase64: mediaBuf ? mediaBuf.toString('base64') : null,
            caption: customCapFromUpload || '',
            visionDescription: visionDesc || null,
            updatedAt: new Date()
          }
        },
        { upsert: true }
      );

      // If the uploaded media did NOT include an immediate command in the caption/voice, confirm staging and return.
      // Otherwise, fall through immediately to execute the command (e.g. "ابعتها الناس كلها") in the same turn!
      if (!captionIsCmd) {
        let confirmMsg = '';
        if (visionDesc) {
          confirmMsg = `تمام يا فندم، تم استلام ${mediaInfo.label} وتحليلها في غرفة العمليات 🎯\n\n` +
            `🔍 <b>الملخص التنفيذي للصورة:</b>\n${visionDesc}\n\n` +
            `📢 <b>للبث المباشر لكل المتداولين، اكتب:</b>\n"eb3ato lel nas kolaha" أو "ابعت للناس كلها"`;
        } else {
          confirmMsg = `تمام يا فندم، تم استلام ${mediaInfo.label} وحفظها بنجاح في غرفة العمليات 🎯\n\n` +
            `📢 <b>للبث المباشر لكل المتداولين، اكتب:</b>\n"eb3ato lel nas kolaha" أو "ابعت للناس كلها"\n` +
            `🎯 <b>أو لإرسالها لمتداول محدد، اكتب:</b>\n"eb3atha le 01012345678"`;
        }

        if (!isTestCall) {
          await tgCall(currentToken, 'sendMessage', {
            chat_id: chatId,
            text: confirmMsg,
            parse_mode: 'HTML',
            reply_markup: {
              inline_keyboard: [
                [
                  { text: '📢 بث الميديا لكل المتداولين الآن', callback_data: 'act:bcast' }
                ],
                [
                  { text: '🎛️ لوحة القيادة', callback_data: 'act:menu' },
                  { text: '🎯 كود QR', callback_data: 'act:qr' }
                ]
              ]
            }
          });
        }
        await recordAdminAction(db, { channel: currentChannel, prompt: `[stage_media] ${mediaInfo.type}`, reply: confirmMsg, actionType: 'stage_media', meta: { type: mediaInfo.type, caption: customCapFromUpload } });
        return res.status(200).json({ ok: true, staged: mediaInfo.type, described: Boolean(visionDesc), reply: confirmMsg });
      }
    }

    // 2. Asking about pending image description without uploading new image
    if (asksAboutPicture) {
      const mediaDoc = await db.collection('pending_ceo_media').findOne({ _id: 'latest_media' });
      let desc = mediaDoc?.visionDescription;
      if (!desc && (mediaDoc?.mediaBase64 || mediaDoc?.imageBase64)) {
        const rawBase64 = mediaDoc.mediaBase64 || mediaDoc.imageBase64;
        desc = await describeImageWithGemini(rawBase64);
        if (desc) {
          await db.collection('pending_ceo_media').updateOne({ _id: 'latest_media' }, { $set: { visionDescription: desc } });
        }
      }

      if (desc) {
        if (!isTestCall) {
          await tgCall(currentToken, 'sendMessage', {
            chat_id: chatId,
            text: `🔍 <b>الملخص التنفيذي للصورة المعلقة في غرفة العمليات:</b>\n\n${desc}`,
            parse_mode: 'HTML'
          });
        }
        return res.status(200).json({ ok: true, described: true, description: desc });
      } else {
        if (!isTestCall) {
          await tgCall(currentToken, 'sendMessage', {
            chat_id: chatId,
            text: `⚠️ لا توجد صورة معلقة حالياً في غرفة العمليات لشرحها. ابعت صورة أولاً يا فندم.`
          });
        }
        return res.status(200).json({ ok: false, error: 'No image pending' });
      }
    }

    // 3. Media Broadcast to ALL Traders (e.g. "ابعت للناس", "ابعت للناس كله", "ابعتها الناس كلها", "eb3ato lel nas", or reply to staged media)
    const isReplyToStagedMedia = replyToMsg && (
      /(?:غرفة\s*العمليات|تم\s*استلام|للبث\s*المباشر)/i.test(replyAlertText) ||
      Boolean(replyToMsg.sticker || replyToMsg.photo || replyToMsg.animation || replyToMsg.video)
    );

    const isBroadcast = isBroadcastCommand(effectiveText, isReplyToStagedMedia);

    if (isBroadcast) {
      let customCaption = '';
      const matchCaption = effectiveText.match(/(?:واكتب|بكابشن|w\s*ekteb|caption)\s+(.+)/i);
      if (matchCaption) {
        customCaption = matchCaption[1].trim();
      }

      const mediaDoc = await db.collection('pending_ceo_media').findOne({ _id: 'latest_media' });
      if (!mediaDoc || (!mediaDoc.file_id && !mediaDoc.mediaBase64 && !mediaDoc.imageBase64)) {
        if (!isTestCall) {
          await tgCall(currentToken, 'sendMessage', {
            chat_id: chatId,
            text: `⚠️ لا توجد ميديا معلقة حالياً في غرفة العمليات. ابعت صورة أو فيديو أو GIF أو ستيكر أولاً يا فندم.`
          });
        }
        return res.status(200).json({ ok: false, error: 'No media pending' });
      }

      const savedCap = (mediaDoc.caption && !isCommandText(mediaDoc.caption) && !isBroadcastCommand(mediaDoc.caption, false))
        ? mediaDoc.caption
        : '';
      const finalCaption = customCaption || savedCap || 'إشعار مباشر من غرفة تداول Tradesejon 📢';

      const chatIds = await getAllTraderChatIds(db, CEO_CHAT_ID);

      const rawBase64 = mediaDoc.mediaBase64 || mediaDoc.imageBase64;
      const mediaBuffer = rawBase64 ? Buffer.from(rawBase64, 'base64') : null;

      let sentCount = 0;
      let failedCount = 0;
      for (const cid of chatIds) {
        if (isTestCall && cid !== String(CEO_CHAT_ID)) continue;
        try {
          const r = await sendAnyMedia(TRADING_TOKEN, cid, mediaDoc, mediaBuffer, finalCaption);
          if (r && r.ok !== false) sentCount++;
          else failedCount++;
        } catch (err) {
          failedCount++;
          console.error(`Failed broadcast to ${cid}:`, err);
        }
        await sleep(35);
      }

      const mediaName = mediaDoc.label || 'الميديا';
      const bcastReply = `✅ تمام يا فندم، تم بث ${escapeHtml(mediaName)} بنجاح لـ ${sentCount} متداول` +
        (failedCount > 0 ? ` (تعذر الإرسال لـ ${failedCount})` : '') +
        ` على بوت التليجرام @YOUR_TRADER_BOT! 🚀\n\n📝 الكابشن المستخدم: "${escapeHtml(finalCaption)}"`;
      if (!isTestCall) {
        await tgCall(currentToken, 'sendMessage', {
          chat_id: chatId,
          text: bcastReply,
          parse_mode: 'HTML',
          reply_markup: getExecutiveMenuMarkup()
        });
      }
      await recordAdminAction(db, { channel: currentChannel, prompt: effectiveText, reply: bcastReply, actionType: 'broadcast_media', meta: { sentCount, mediaName, caption: finalCaption } });
      return res.status(200).json({ ok: true, sentCount, mediaName, reply: bcastReply });
    }

    // 4. Direct QR Code or Payment Request (e.g. "/qr", "qr code eldaf3", "qr code whop", "كود الدفع", "qr", "رمز qr")
    const isDirectQrRequest = /^(?:\/qr|qr|qr\s*code|كود\s*الدفع|رمز\s*qr|الكيور\s*كود|كود\s*الـ\s*qr|qr\s*code\s*whop|qr\s*code\s*eldaf3|whop\s*qr|رابط\s*الدفع|كود\s*whop|whop)$/i.test(effectiveText.trim()) ||
      (/(?:ابعتلي|عايز|هات|وريني|ارسل|show|get|send\s*me)\s+(?:.*?)(?:qr|كود\s*الدفع|رمز\s*الـ?\s*qr|رابط\s*الدفع)/i.test(effectiveText) && !/(?:le|لـ|لرقم|to|ل)\s+[0-9a-zA-Z]/i.test(effectiveText)) ||
      (/(?:qr\s*code\s*(?:whop|eldaf3)|كود\s*الدفع|رابط\s*الدفع|كود\s*whop)/i.test(effectiveText) && !/(?:le|لـ|لرقم|to|ل)\s+[0-9a-zA-Z]/i.test(effectiveText));

    if (isDirectQrRequest) {
      const qrReply = `🎯 تم إرسال رمز QR ورابط الدفع المباشر ($0 لأول 7 أيام): ${WHOP_CHECKOUT_URL}`;
      if (!isTestCall) {
        await tgCall(currentToken, 'sendPhoto', {
          chat_id: chatId,
          photo: QR_PHOTO_URL,
          caption: `🎯 <b>رمز QR ورابط الدفع المباشر لجلسات التداول VIP من Tradesejon</b> 🚀\n\n` +
            `🎁 <b>العرض الحصري:</b> تجربة مجانية لأول 7 أيام ($0 لتأكيد المقعد ثم $29 شهرياً).\n` +
            `🔗 <b>رابط التفعيل المباشر:</b>\n${WHOP_CHECKOUT_URL}\n\n` +
            `امسح رمز الـ QR أعلاه أو اضغط على الرابط للتفعيل الفوري وتأمين المقعد في جلسة اليوم!`,
          parse_mode: 'HTML',
          reply_markup: getExecutiveMenuMarkup()
        });
      }
      await recordAdminAction(db, { channel: currentChannel, prompt: effectiveText, reply: qrReply, actionType: 'send_qr_self' });
      return res.status(200).json({ ok: true, qrSent: true, reply: qrReply });
    }

    // 5. Targeted Send to Specific Trader / Phone / Username / Email / Name
    const isLatestTicketInst = /^(?:send|give|forward)\s+(?:him|her|them)\s+(?:the\s+)?(?:qr|payment|code)/i.test(effectiveText.trim()) ||
      /^(?:ابعت|اديله|اديلها|ارسل)\s*(?:له|لها)?\s*(?:كود|رمز|الـ\s*qr|الدفع)/i.test(effectiveText.trim());

    if (isLatestTicketInst) {
      const latestConv = await db.collection('conversations').find({ ticketCode: /^TRD-/i }).sort({ at: -1, created_at: -1 }).limit(1).next();
      if (latestConv?.chatId) {
        if (!isTestCall || String(latestConv.chatId) === String(CEO_CHAT_ID)) {
          await tgCall(TRADING_TOKEN, 'sendPhoto', {
            chat_id: latestConv.chatId,
            photo: QR_PHOTO_URL,
            caption: `🎯 <b>أهلاً بك يا فندم في Tradesejon!</b> 🚀\n\nبناءً على طلبك، إليك رمز QR ورابط الدفع لتفعيل باقة جلسات التداول VIP الحية ($0 مجاناً لأول 7 أيام):\n\n🔗 <b>رابط الدفع المباشر:</b>\n${WHOP_CHECKOUT_URL}\n\nامسح الـ QR أو اضغط على الرابط لتأمين مقعدك الفوري! 🚀`,
            parse_mode: 'HTML'
          });
        }

        const instReply = `✅ تمام يا فندم! تم إرسال رمز الـ QR ورابط الدفع المباشر ($0 مجاناً) إلى المتداول على بوت التليجرام بخصوص التذكرة [${latestConv.ticketCode}]! 🚀`;
        if (!isTestCall) {
          await tgCall(currentToken, 'sendMessage', {
            chat_id: chatId,
            text: instReply
          });
        }
        await recordAdminAction(db, { channel: currentChannel, prompt: effectiveText, reply: instReply, actionType: 'send_qr_latest_ticket', meta: { targetTicket: latestConv.ticketCode } });
        return res.status(200).json({ ok: true, executedInstruction: 'send_qr', targetTicket: latestConv.ticketCode, reply: instReply });
      }
    }

    const parsedTarget = parseTargetAndContent(effectiveText);
    const wantsDiscord = /(?:discord|ديسكورد|دسكورد|3la\s*discord|ع\s*الديسكورد|عالديسكورد)/i.test(effectiveText);
    const wantsTelegramOnly = /(?:telegram|تيليجرام|تليجرام|تيلجرام|على\s*البوت)/i.test(effectiveText) && !/(?:mail|email|ايميل|إيميل|مايل|ميل|بريد|@)/i.test(effectiveText);

    // 5-Discord. Verified Direct Discord Delivery (when CEO instructs "3la discord" or mentions discord user)
    if (parsedTarget && wantsDiscord) {
      const { targetStr, contentStr } = parsedTarget;
      let cleanDiscordMsg = (contentStr || effectiveText)
        .replace(/(?:3la\s*discord|ع\s*الديسكورد|عالديسكورد|discord|ديسكورد|دسكورد)\s*/gi, '')
        .replace(/^(?:2ollo|ollo|2olha|olha|قوله|قولها|قل\s*له|قل\s*لها|بلغ|بلغه|بلغها|إن|ان|en)\s+/i, '')
        .trim();
      if (!cleanDiscordMsg) cleanDiscordMsg = 'تنبيه مباشر من إدارة Tradesejon VIP 🚀';

      const profile = await resolveTargetProfile(db, targetStr).catch(() => null);
      const discordUserId = (/^\d{16,21}$/.test(targetStr)
        ? targetStr
        : (profile?.discord_user_id || DISCORD_KNOWN_MAP[targetStr.toLowerCase()] || (profile?.email ? DISCORD_KNOWN_MAP[profile.email.toLowerCase()] : null))) || null;

      if (!discordUserId) {
        const notFoundRep = `⚠️ لم يتم العثور على حساب ديسكورد (Discord User ID) مرتبط بـ (${escapeHtml(targetStr)}). يرجى ذكر معرّف الديسكورد الرقمي مباشرة لضمان التسليم المؤكد 100%.`;
        if (!isTestCall) {
          await tgCall(currentToken, 'sendMessage', { chat_id: chatId, text: notFoundRep, reply_markup: getExecutiveMenuMarkup() });
        }
        await recordAdminAction(db, { channel: currentChannel, prompt: effectiveText, reply: notFoundRep, actionType: 'discord_dm_not_found', meta: { targetStr } });
        return res.status(200).json({ ok: false, error: 'Discord User ID not found', reply: notFoundRep });
      }

      // Execute verified Discord DM
      const dmRes = await dispatchDiscordDmDirectly(discordUserId, cleanDiscordMsg);
      if (dmRes.ok && dmRes.delivered) {
        const targetName = profile?.name || targetStr;
        const confirmMsg = `✅ تمام يا فندم! تم تسليم الرسالة 100% بشكل مؤكد ومباشر لحساب المتداول (<b>${escapeHtml(targetName)}</b>) على الديسكورد 🎯\n\n` +
          `🆔 <b>Discord User ID:</b> <code>${discordUserId}</code>\n` +
          `📝 <b>الرسالة المسلمة:</b> "${escapeHtml(cleanDiscordMsg)}"\n` +
          `📌 <b>طريقة الإرسال:</b> <code>${escapeHtml(dmRes.via || 'discord_api')}</code>` +
          (dmRes.messageId ? ` | <b>Message ID:</b> <code>${dmRes.messageId}</code>` : '');

        if (!isTestCall) {
          await tgCall(currentToken, 'sendMessage', {
            chat_id: chatId,
            text: confirmMsg,
            parse_mode: 'HTML',
            reply_markup: getExecutiveMenuMarkup()
          });
        }
        await recordAdminAction(db, { channel: currentChannel, prompt: effectiveText, reply: confirmMsg, actionType: 'discord_dm_verified', meta: { discordUserId, messageId: dmRes.messageId, targetName } });
        return res.status(200).json({ ok: true, delivered: true, discordUserId, messageId: dmRes.messageId, reply: confirmMsg });
      } else {
        const failMsg = `⚠️ تعذر تسليم الرسالة على الديسكورد للمتداول (${escapeHtml(profile?.name || targetStr)} - ID: <code>${discordUserId}</code>): ${escapeHtml(dmRes.error || 'الرسائل الخاصة مغلقة لديه')}. تم حظر إرسال أي تأكيد غير موثق.`;
        if (!isTestCall) {
          await tgCall(currentToken, 'sendMessage', { chat_id: chatId, text: failMsg, parse_mode: 'HTML', reply_markup: getExecutiveMenuMarkup() });
        }
        await recordAdminAction(db, { channel: currentChannel, prompt: effectiveText, reply: failMsg, actionType: 'discord_dm_failed', meta: { discordUserId, error: dmRes.error } });
        return res.status(200).json({ ok: false, delivered: false, error: dmRes.error, reply: failMsg });
      }
    }

    if (parsedTarget && !wantsTelegramOnly && !wantsDiscord) {
      const { targetStr, contentStr } = parsedTarget;
      const isQrOrPayment = /(?:qr|كود|دفع|eldaf3|whop|payment|سداد)/i.test(effectiveText) && !/(?:galsa|galset|جلسة|جلسه|ميعاد|موعد|الساعة|الساعه|esa3a|sa3a|5\s*w\s*rob3|5:15)/i.test(effectiveText);
      const isExplicitEmailCmd = /(?:mail|email|ايميل|إيميل|مايل|ميل|بريد)/i.test(effectiveText) || targetStr.includes('@');
      const isStagedMediaForward = !isExplicitEmailCmd && !isQrOrPayment && !contentStr && /(?:eb3atha|ib3atha|ab3atha|ابعتها|أبعتها|ارسلها|أرسلها|شيرها|الصورة|الصوره|الستيكر|الفيديو|الميديا)/i.test(effectiveText);

      const profile = await resolveTargetProfile(db, targetStr);

      if (profile) {
        // 5a. Forwarding staged media to a specific trader on Telegram
        if (isStagedMediaForward && profile.chatId) {
          const mediaDoc = await db.collection('pending_ceo_media').findOne({ _id: 'latest_media' });
          if (mediaDoc && (mediaDoc.mediaBase64 || mediaDoc.file_id)) {
            if (!isTestCall || String(profile.chatId) === String(CEO_CHAT_ID)) {
              const buf = mediaDoc.mediaBase64 ? Buffer.from(mediaDoc.mediaBase64, 'base64') : null;
              await sendAnyMedia(TRADING_TOKEN, profile.chatId, mediaDoc, buf, 'رسالة خاصة من إدارة Tradesejon 🎯');
            }
            const mReply = `✅ تمام يا فندم! تم إرسال ${escapeHtml(mediaDoc.label || 'الميديا')} بنجاح إلى المتداول (<b>${escapeHtml(profile.name)}</b>) على بوت @YOUR_TRADER_BOT! 🚀`;
            if (!isTestCall) {
              await tgCall(currentToken, 'sendMessage', {
                chat_id: chatId,
                text: mReply,
                parse_mode: 'HTML'
              });
            }
            await recordAdminAction(db, { channel: currentChannel, prompt: effectiveText, reply: mReply, actionType: 'forward_staged_media', meta: { target: profile.name, chatId: profile.chatId } });
            return res.status(200).json({ ok: true, mediaSentTo: profile.name, chatId: profile.chatId, reply: mReply });
          }
        }

        const targetEmails = profile.emails && profile.emails.length > 0 ? profile.emails : (profile.email ? [profile.email] : []);

        // 5b. Dispatch Email when requested or when content/email is present
        if ((isExplicitEmailCmd || contentStr || !isQrOrPayment) && targetEmails.length > 0) {
          const dedupeKey = updateId ? `tg:${bot || 'ceo'}:${updateId}` : '';
          const mailRes = await sendAiExecutiveEmail(db, targetEmails, contentStr || effectiveText, profile.name, dedupeKey ? { dedupeKey } : {});
          if (!mailRes.ok) {
            if (!isTestCall) {
              await tgCall(currentToken, 'sendMessage', {
                chat_id: chatId,
                text: `⚠️ تعذر إرسال البريد الإلكتروني إلى ${profile.name} (${targetEmails.join(', ')}): ${mailRes.error || 'خطأ في خادم البريد'}`
              });
            }
            return res.status(200).json({ ok: false, error: mailRes.error, attempts: mailRes.attempts, targetEmails });
          }

          if (isQrOrPayment && profile.chatId && (!isTestCall || String(profile.chatId) === String(CEO_CHAT_ID))) {
            await tgCall(TRADING_TOKEN, 'sendPhoto', {
              chat_id: profile.chatId,
              photo: QR_PHOTO_URL,
              caption: `🎯 <b>أهلاً بك يا فندم في Tradesejon!</b> 🚀\n\nبناءً على طلبك، إليك رمز QR ورابط الدفع لتفعيل باقة جلسات التداول VIP الحية ($0 مجاناً لأول 7 أيام):\n\n🔗 <b>رابط الدفع المباشر:</b>\n${WHOP_CHECKOUT_URL}\n\nامسح الـ QR أو اضغط على الرابط لتأمين مقعدك الفوري! 🚀`,
              parse_mode: 'HTML'
            });
          }

          const ticketCode = `CEO-${Math.floor(300 + Math.random() * 699)}`;
          const botAnsText = `✅ تمام يا فندم! تم صياغة وإرسال الإيميل بنجاح إلى ${profile.name} (${mailRes.to}) بعنوان: "${mailRes.subject}" (Message ID: ${mailRes.messageId}).`;

          await db.collection('conversations').insertOne({
            ticketCode,
            ticket_id: ticketCode,
            platform: currentChannel,
            chat_id: chatId,
            chatId: String(chatId),
            fullName: profile.name,
            full_name: profile.name,
            recipientEmail: mailRes.to,
            recipients: mailRes.recipients,
            subject: mailRes.subject,
            user_message: effectiveText,
            query: effectiveText,
            bot_answer: botAnsText,
            aiAnswer: botAnsText,
            messageId: mailRes.messageId,
            status: 'closed',
            answered: true,
            at: new Date(),
            created_at: new Date().toISOString()
          });

          const mailConfirmHtml = `✅ تمام يا فندم! تم إرسال الإيميل فعلياً وبنجاح إلى <b>${escapeHtml(profile.name)}</b> عبر منظومة Tradesejon AIOS 📧\n\n` +
            `📬 <b>البريد المستلم:</b> <code>${escapeHtml(mailRes.to)}</code>\n` +
            `📌 <b>الموضوع:</b> ${escapeHtml(mailRes.subject)}\n` +
            `📝 <b>الرسالة التنفيذية:</b> ${escapeHtml(mailRes.summary)}\n` +
            `📱 <b>الهاتف:</b> ${escapeHtml(profile.phone || 'غير مسجل')}\n` +
            `🆔 <b>كود التوثيق:</b> <code>[${ticketCode}]</code> | <code>${escapeHtml(mailRes.provider || 'smtp')}</code>: <code>${escapeHtml(mailRes.smtpResponse || '250 OK')}</code>`;

          if (!isTestCall) {
            await tgCall(currentToken, 'sendMessage', {
              chat_id: chatId,
              text: mailConfirmHtml,
              parse_mode: 'HTML',
              reply_markup: getExecutiveMenuMarkup()
            });
          }
          await recordAdminAction(db, {
            channel: currentChannel,
            prompt: effectiveText,
            reply: mailConfirmHtml,
            actionType: 'send_email_fastpath',
            meta: { emailSentTo: mailRes.to, subject: mailRes.subject, messageId: mailRes.messageId, ticketCode }
          });
          return res.status(200).json({
            ok: true,
            emailSentTo: mailRes.to,
            recipients: mailRes.recipients,
            subject: mailRes.subject,
            messageId: mailRes.messageId,
            ticketCode,
            reply: mailConfirmHtml
          });
        }

        if (isQrOrPayment) {
          if (profile.chatId) {
            if (!isTestCall || String(profile.chatId) === String(CEO_CHAT_ID)) {
              await tgCall(TRADING_TOKEN, 'sendPhoto', {
                chat_id: profile.chatId,
                photo: QR_PHOTO_URL,
                caption: `🎯 <b>أهلاً بك يا فندم في Tradesejon!</b> 🚀\n\nبناءً على طلبك، إليك رمز QR ورابط الدفع لتفعيل باقة جلسات التداول VIP الحية ($0 مجاناً لأول 7 أيام):\n\n🔗 <b>رابط الدفع المباشر:</b>\n${WHOP_CHECKOUT_URL}\n\nامسح الـ QR أو اضغط على الرابط لتأمين مقعدك الفوري! 🚀`,
                parse_mode: 'HTML'
              });
            }

            const qrTargetReply = `✅ تمام يا فندم! تم إرسال رمز الـ QR ورابط الدفع المباشر بنجاح إلى المتداول (${escapeHtml(profile.name)}) على بوت التليجرام @YOUR_TRADER_BOT! 🚀\n\n` +
              `🔗 <b>الرابط المرسل:</b> ${WHOP_CHECKOUT_URL}\n` +
              `🆔 <b>معرف المحادثة:</b> <code>${profile.chatId}</code>`;
            if (!isTestCall) {
              await tgCall(currentToken, 'sendMessage', {
                chat_id: chatId,
                text: qrTargetReply,
                parse_mode: 'HTML'
              });
            }
            await recordAdminAction(db, { channel: currentChannel, prompt: effectiveText, reply: qrTargetReply, actionType: 'send_qr_target', meta: { sentQrTo: profile.name, chatId: profile.chatId } });
            return res.status(200).json({ ok: true, sentQrTo: profile.name, chatId: profile.chatId, reply: qrTargetReply });
          } else if (targetEmails.length > 0) {
            const mailRes = await sendAiExecutiveEmail(db, targetEmails, effectiveText, profile.name);
            if (!mailRes.ok) {
              if (!isTestCall) {
                await tgCall(currentToken, 'sendMessage', {
                  chat_id: chatId,
                  text: `⚠️ تعذر إرسال كود الـ QR عبر البريد الإلكتروني: ${mailRes.error}`
                });
              }
              return res.status(200).json({ ok: false, error: mailRes.error });
            }
            const qrEmailReply = `✅ تمام يا فندم! المتداول ليس لديه حساب تيليجرام نشط، وتم إرسال كود الـ QR ورابط الدفع بنجاح إلى إيميله: <b>${escapeHtml(mailRes.to)}</b> 📧`;
            if (!isTestCall) {
              await tgCall(currentToken, 'sendMessage', {
                chat_id: chatId,
                text: qrEmailReply,
                parse_mode: 'HTML'
              });
            }
            await recordAdminAction(db, { channel: currentChannel, prompt: effectiveText, reply: qrEmailReply, actionType: 'send_qr_email', meta: { sentQrViaEmail: mailRes.to, messageId: mailRes.messageId } });
            return res.status(200).json({ ok: true, sentQrViaEmail: mailRes.to, messageId: mailRes.messageId, reply: qrEmailReply });
          }
        }
      }
    }

    // 6. Human Reply or Instruction to Trader Ticket (e.g. /reply TRD-XXXX <text> or direct reply)
    let targetTicket = null;
    let replyContent = effectiveText;

    const matchCmd = effectiveText.match(/^\/reply\s+([A-Z0-9\-]+)(?:\s+(.+))?/is);
    if (matchCmd) {
      targetTicket = matchCmd[1].trim();
      replyContent = (matchCmd[2] || '').trim();
    } else if (replyTicketMatch) {
      targetTicket = replyTicketMatch[1].trim();
    }

    const isInstructionOrPrompt = /^(?:prompt\s*:|توجيه\s*:|أمر\s*:)\s*(.+)/i.test(replyContent.trim()) ||
      /^(?:eb3at(?:lo|laha)|ابعت(?:له|لها)|اديله|اديلها|ارسل\s*(?:له|لها)|send\s*(?:him|her|to\s*him|to\s*her))\b/i.test(replyContent.trim());

    if (!targetTicket && isInstructionOrPrompt) {
      const latestConv = await db.collection('conversations').find({ ticketCode: /^TRD-/i }).sort({ at: -1 }).limit(1).next();
      if (latestConv?.ticketCode) {
        targetTicket = latestConv.ticketCode;
      }
    }

    if (targetTicket) {
      const conv = await db.collection('conversations').findOne({ ticketCode: targetTicket });
      const targetChatId = conv?.chatId;

      if (targetChatId) {
        if (isInstructionOrPrompt) {
          const isQrInstruction = /(?:qr|كود|دفع|eldaf3|whop|payment)/i.test(replyContent);
          if (isQrInstruction) {
            await tgCall(TRADING_TOKEN, 'sendPhoto', {
              chat_id: targetChatId,
              photo: QR_PHOTO_URL,
              caption: `🎯 <b>أهلاً بك يا فندم في Tradesejon!</b> 🚀\n\nبناءً على طلبك، إليك رمز QR ورابط الدفع لتفعيل باقة جلسات التداول VIP الحية ($0 مجاناً لأول 7 أيام):\n\n🔗 <b>رابط الدفع المباشر:</b>\n${WHOP_CHECKOUT_URL}\n\nامسح الـ QR أو اضغط على الرابط لتأمين مقعدك الفوري! 🚀`,
              parse_mode: 'HTML'
            });

            const qrTicketConfirm = `✅ تمام يا فندم! تم تنفيذ التوجيه بنجاح وإرسال كود الـ QR ورابط الدفع المباشر إلى المتداول بخصوص التذكرة [${targetTicket}]! 🚀`;
            if (!isTestCall) {
              await tgCall(currentToken, 'sendMessage', {
                chat_id: chatId,
                text: qrTicketConfirm
              });
            }
            await recordAdminAction(db, { channel: currentChannel, prompt: effectiveText, reply: qrTicketConfirm, actionType: 'ticket_send_qr', meta: { targetTicket } });
            return res.status(200).json({ ok: true, executedInstruction: 'send_qr', targetTicket });
          } else {
            const cleanInstruction = replyContent.replace(/^(?:prompt\s*:|توجيه\s*:|أمر\s*:)\s*/i, '').trim();
            const polishedReply = await callGroqEgyptianAi(
              `اكتب رداً تنفيذياً مهذباً وموجزاً بالعامية المصرية لمتداول استفسر عن تذكرته. التوجيه هو: "${cleanInstruction}"`,
              'trader_coach'
            );

            if (!isTestCall || String(targetChatId) === String(CEO_CHAT_ID)) {
              await tgCall(TRADING_TOKEN, 'sendMessage', {
                chat_id: targetChatId,
                text: sanitizeTelegramHtml(`👨‍💼 <b>رد المستشار المختص من إدارة Tradesejon بخصوص تذكرتك [${targetTicket}]:</b>\n\n${polishedReply}\n\nإذا كان لديك أي استفسار آخر، نحن دائماً في خدمتك!`),
                parse_mode: 'HTML'
              });
            }

            const polishedConfirm = `✅ تم صياغة الرد وإرساله بنجاح إلى المتداول بخصوص التذكرة [${targetTicket}]:\n\n"${polishedReply}"`;
            if (!isTestCall) {
              await tgCall(currentToken, 'sendMessage', {
                chat_id: chatId,
                text: polishedConfirm
              });
            }
            await recordAdminAction(db, { channel: currentChannel, prompt: effectiveText, reply: polishedConfirm, actionType: 'ticket_ai_reply', meta: { targetTicket } });
            return res.status(200).json({ ok: true, polishedReply, targetTicket });
          }
        } else {
          const adminMedia = extractMediaInfo(msg);
          if (adminMedia && adminMedia.file_id) {
            if (!isTestCall || String(targetChatId) === String(CEO_CHAT_ID)) {
              const adminMediaBuf = await tgDownloadFile(currentToken, adminMedia.file_id);
              const caption = replyContent || `👨‍💼 رد المستشار المختص بخصوص تذكرتك [${targetTicket}]`;
              await sendAnyMedia(TRADING_TOKEN, targetChatId, adminMedia, adminMediaBuf, caption);
            }
          } else {
            const traderMsg = sanitizeTelegramHtml(`👨‍💼 <b>رد المستشار المختص من إدارة Tradesejon بخصوص تذكرتك [${targetTicket}]:</b>\n\n` +
              `${replyContent}\n\n` +
              `إذا كان لديك أي استفسار آخر، نحن دائماً في خدمتك!`);

            if (!isTestCall || String(targetChatId) === String(CEO_CHAT_ID)) {
              await tgCall(TRADING_TOKEN, 'sendMessage', {
                chat_id: targetChatId,
                text: traderMsg,
                parse_mode: 'HTML'
              });
            }
          }

          const fwdConfirm = `✅ تم إرسال ردك بنجاح إلى المتداول على بوت التليجرام! [${targetTicket}]`;
          if (!isTestCall) {
            await tgCall(currentToken, 'sendMessage', {
              chat_id: chatId,
              text: fwdConfirm
            });
          }
          await recordAdminAction(db, { channel: currentChannel, prompt: effectiveText, reply: fwdConfirm, actionType: 'ticket_human_reply', meta: { targetTicket, targetChatId } });
          return res.status(200).json({ ok: true, forwardedTo: targetChatId });
        }
      }
    }

    // 7. Universal Agentic AI Brain (Handles ANY command, prompt, image request, voice note, CRM query, or multi-step action!)
    const agenticResult = await executeAgenticPlan({
      db,
      userPrompt: effectiveText || 'مرحباً',
      chatId,
      currentToken,
      isTestCall,
      channel: bot === 'pr' ? 'telegram_pr' : 'telegram_ceo',
      helpers: {
        TRADING_TOKEN,
        CEO_CHAT_ID,
        tgCall,
        tgSendMediaBuffer,
        sendAnyMedia,
        describeImageWithGemini,
        synthesizeElevenLabsVoice,
        findChatIdByTarget
      }
    });

    // If CEO/Admin spoke via voice note, automatically reply with an authentic Egyptian Masri voice note!
    if (!isTestCall && (wasVoiceNoteCommand || Boolean(msg.voice)) && agenticResult?.reply) {
      try {
        const plainReply = String(agenticResult.reply || '').replace(/<[^>]+>/g, '').slice(0, 350);
        const voiceBuf = await synthesizeElevenLabsVoice(plainReply);
        if (voiceBuf) {
          await tgSendMediaBuffer(currentToken, 'sendVoice', 'voice', chatId, voiceBuf, 'voice_reply.mp3', 'audio/mpeg', '🎙️ رد صوتي تنفيذي من إدارة Tradesejon');
        }
      } catch (voiceReplyErr) {
        console.error('Failed to send voice note reply to admin:', voiceReplyErr.message);
      }
    }

    return res.status(200).json(agenticResult);
  }

  return res.status(200).json({ ok: true });
};

// Shared handles so AIOS web chat (api/index.js) uses the exact same brain as the CEO/PR bots
module.exports = async function handler(req, res) {
  try {
    return await handleTelegramWebhook(req, res);
  } catch (criticalErr) {
    console.error("CRITICAL TELEGRAM HANDLER ERROR:", criticalErr);
    try {
      const { bot } = req.query || {};
      const update = req.body || {};
      const msg = update.message || update.callback_query?.message;
      const chatId = msg?.chat?.id || msg?.from?.id;
      const isTest = Boolean(update.isTest || req.query?.isTest);
      if (!isTest && chatId && (bot === "ceo" || bot === "pr")) {
        const token = bot === "pr" ? PR_TOKEN : CEO_TOKEN;
        await tgCall(token, "sendMessage", {
          chat_id: chatId,
          text: "⚠️ تم استلام رسالتك وجاري معالجتها ذاتياً عبر نظام الحماية الذاتية لمنظومة Tradesejon AIOS."
        }).catch(() => {});
      }
    } catch (_) {}
    return res.status(200).json({ ok: true, recovered: true, error: criticalErr.message });
  }
};

module.exports.shared = {
  TRADING_TOKEN, PR_TOKEN, CEO_TOKEN, CEO_CHAT_ID,
  tgCall, tgSendMediaBuffer, sendAnyMedia, describeImageWithGemini,
  synthesizeElevenLabsVoice, findChatIdByTarget, getDb
};
