const { MongoClient } = require('mongodb');
const nodemailer = require('nodemailer');

const GROQ_KEY = process.env.GROQ_API_KEY || "";
const MONGO_ATLAS_URI = process.env.MONGODB_URI || "";

const WHOP_CHECKOUT_URL = "https://whop.com/checkout/plan_TEMPLATE_01";
const QR_PHOTO_URL = `https://api.qrserver.com/v1/create-qr-code/?size=500x500&data=${encodeURIComponent(WHOP_CHECKOUT_URL)}`;
const ZOOM_MEETING_URL = "https://discord.com/channels/900000000000000001/900000000000000002";
const ZOOM_MEETING_ID = "900000000000000002";
const ZOOM_PASSCODE = "DISCORD-LIVE";

const DEFAULT_GMAIL_USER = process.env.SMTP_USER || '';
const DEFAULT_GMAIL_PASS = process.env.SMTP_PASSWORD || '';
const RESEND_OWNER_EMAIL = (process.env.RESEND_OWNER_EMAIL || 'trader@example.com').trim().toLowerCase();

const DISCORD_BOT_TOKEN = process.env.DISCORD_CEO_BOT_TOKEN || "";

const DISCORD_KNOWN_MAP = JSON.parse(process.env.DISCORD_KNOWN_MAP_JSON || '{}');

async function dispatchDiscordDmDirectly(discordUserId, messageText) {
  const uid = String(discordUserId || '').trim();
  if (!uid || !/^\d{16,21}$/.test(uid)) {
    return { ok: false, error: 'Invalid or missing Discord User ID' };
  }
  const cleanMsg = String(messageText || '').trim();
  if (!cleanMsg) {
    return { ok: false, error: 'Empty message text' };
  }

  // 1. Try loopback bridge first if reachable
  try {
    const brRes = await fetch('http://127.0.0.1:8095/api/discord/dm-user', {
      method: 'POST',
      headers: {
        'Authorization': 'Bearer ',
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ user_id: uid, message: cleanMsg }),
      signal: AbortSignal.timeout(3000)
    });
    if (brRes.ok) {
      const data = await brRes.json();
      if (data && data.delivered) {
        return { ok: true, delivered: true, via: 'loopback_bridge', userId: uid };
      }
    }
  } catch (_) {}

  // 2. Direct Discord REST API fallback (works from Vercel & cloud serverless)
  try {
    const chanRes = await fetch('https://discord.com/api/v10/users/@me/channels', {
      method: 'POST',
      headers: {
        'Authorization': 'Bot ' + DISCORD_BOT_TOKEN,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ recipient_id: uid }),
      signal: AbortSignal.timeout(6000)
    });
    const chanData = await chanRes.json();
    if (!chanData || !chanData.id) {
      return { ok: false, error: chanData?.message || 'Failed to open Discord DM channel' };
    }

    const msgRes = await fetch(`https://discord.com/api/v10/channels/${chanData.id}/messages`, {
      method: 'POST',
      headers: {
        'Authorization': 'Bot ' + DISCORD_BOT_TOKEN,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        content: cleanMsg,
        allowed_mentions: { parse: [] }
      }),
      signal: AbortSignal.timeout(6000)
    });
    const msgData = await msgRes.json();
    if (msgData && msgData.id) {
      return { ok: true, delivered: true, messageId: msgData.id, channelId: chanData.id, via: 'discord_rest_api', userId: uid };
    }
    return { ok: false, error: msgData?.message || 'Failed to deliver message to Discord DM' };
  } catch (err) {
    return { ok: false, error: err.message };
  }
}

function getSmtpCredentials() {
  const user = String(process.env.SMTP_USER || process.env.GMAIL_USER || DEFAULT_GMAIL_USER).trim();
  const pass = String(process.env.SMTP_PASSWORD || process.env.GMAIL_APP_PASSWORD || DEFAULT_GMAIL_PASS).trim();
  const host = String(process.env.SMTP_HOST || 'smtp.gmail.com').trim();
  return { user, pass, host };
}

function createSmtpTransporter(port = 465) {
  const { user, pass, host } = getSmtpCredentials();
  return nodemailer.createTransport({
    host,
    port,
    secure: port === 465,
    connectionTimeout: 8000,
    greetingTimeout: 8000,
    socketTimeout: 12000,
    auth: { user, pass }
  });
}

const emailTransporter = createSmtpTransporter(465);

let cachedDbClient = null;
async function getMailDb() {
  if (!cachedDbClient) {
    cachedDbClient = new MongoClient(MONGO_ATLAS_URI, { serverSelectionTimeoutMS: 5000, connectTimeoutMS: 10000, tls: true, maxPoolSize: 10 });
    await cachedDbClient.connect();
  }
  return cachedDbClient.db('automation_hub_dev');
}

const NAME_TRANSLITERATIONS = {
  mohamed: 'محمد|mohamed|muhammad|mohammed',
  muhammad: 'محمد|mohamed|muhammad|mohammed',
  mohammed: 'محمد|mohamed|muhammad|mohammed',
  'محمد': 'محمد|mohamed|muhammad|mohammed',
  Member: 'Member|احمد|Member|Member',
  Member: 'Member|احمد|Member|Member',
  'Member': 'Member|احمد|Member|Member',
  'احمد': 'Member|احمد|Member|Member',
  Epsilon: 'Epsilon|Epsilon|Epsilon',
  Epsilon: 'Epsilon|Epsilon|Epsilon',
  'Epsilon': 'Epsilon|Epsilon|Epsilon',
  tarek: 'طارق|tarek|tariq',
  tariq: 'طارق|tarek|tariq',
  'طارق': 'طارق|tarek|tariq',
  menshawy: 'منشاوي|menshawy|elmenshawy',
  'منشاوي': 'منشاوي|menshawy|elmenshawy',
  'المنشاوي': 'منشاوي|menshawy|elmenshawy',
  admin_user: 'admin_user|admin_user|admin_user',
  'admin_user': 'admin_user|admin_user|admin_user',
  ali: 'علي|على|ali|aly',
  aly: 'علي|على|ali|aly',
  'علي': 'علي|على|ali|aly',
  sameh: 'سامح|sameh',
  'سامح': 'سامح|sameh',
  fahad: 'فهد|fahad|fahd',
  fahd: 'فهد|fahad|fahd',
  'فهد': 'فهد|fahad|fahd',
  omar: 'عمر|omar',
  'عمر': 'عمر|omar',
  amr: 'عمرو|amr',
  'عمرو': 'عمرو|amr',
  karim: 'كريم|karim|kareem',
  kareem: 'كريم|karim|kareem',
  'كريم': 'كريم|karim|kareem',
  samer: 'سامر|samer',
  'سامر': 'سامر|samer',
  mahmoud: 'محمود|mahmoud',
  'محمود': 'محمود|mahmoud',
  MentorDelta: 'MentorDelta|MentorDelta|MentorDelta|mustafa',
  mustafa: 'MentorDelta|MentorDelta|MentorDelta|mustafa',
  'MentorDelta': 'MentorDelta|MentorDelta|MentorDelta|mustafa',
  hassan: 'حسن|hassan',
  'حسن': 'حسن|hassan',
  hussein: 'حسين|hussein',
  'حسين': 'حسين|hussein',
  ibrahim: 'إبراهيم|ابراهيم|ibrahim',
  'إبراهيم': 'إبراهيم|ابراهيم|ibrahim',
  'ابراهيم': 'إبراهيم|ابراهيم|ibrahim'
};

function getSearchTerms(rawWord) {
  const clean = String(rawWord || '').toLowerCase().replace(/^ال/, '');
  const rawLow = String(rawWord || '').toLowerCase();
  const terms = new Set([rawLow, clean]);
  if (NAME_TRANSLITERATIONS[rawLow]) {
    NAME_TRANSLITERATIONS[rawLow].split('|').forEach(t => terms.add(t));
  }
  if (NAME_TRANSLITERATIONS[clean]) {
    NAME_TRANSLITERATIONS[clean].split('|').forEach(t => terms.add(t));
  }
  return Array.from(terms).filter(Boolean);
}

function cleanEmailAddr(e) {
  let s = String(e || '').trim().toLowerCase();
  if (s.endsWith('.comm')) s = s.slice(0, -1);
  if (s === 'trader@example.com') s = 'trader@example.com';
  return /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/.test(s) ? s : '';
}

const STOP_CONNECTORS_REGEX = /^(?:2ollo|2olo|2olha|2ol|qollo|qolo|kollo|kolo|قوله|قولها|قولوا|قول|بلغه|بلغها|بلغ|عرفه|عرفها|عرف|واكتبله|واكتبلها|اكتبله|اكتبلها|وابعتله|وابعتلها|en|inn|in|that|an|إنه|انه|إنها|انها|إن|ان|بأن|بان|بخصوص|عن|content|msg|message|saying|tell\s+him|tell\s+her|telling\s+him|telling\s+her|subject|body|text|:|-)+$/i;

function stripConnectorsFromTarget(rawTarget) {
  let t = String(rawTarget || '').trim();
  // Remove leading prepositions or mail words
  t = t.replace(/^(?:mail|email|e-mail|ايميل|إيميل|مايل|ميل|رسالة|رساله|بريد|دعوة|دعوه|شير|فورا|فوراً|دلوقتي|حالا|حالاً|le|li|to|for|لـ|لرقم|لعميل|لمتداول|ل)\s+/i, '').trim();
  t = t.replace(/^لـ\s*/i, '').trim();
  // Strip trailing connector words
  const tokens = t.split(/\s+/);
  while (tokens.length > 1 && STOP_CONNECTORS_REGEX.test(tokens[tokens.length - 1])) {
    tokens.pop();
  }
  while (tokens.length > 1 && /^(?:mail|email|ايميل|إيميل|مايل|ميل|رسالة|رساله|le|li|to|لـ|ل)$/i.test(tokens[0])) {
    tokens.shift();
  }
  return tokens.join(' ').replace(/^لـ\s*/, '').trim();
}

function parseTargetAndContent(text) {
  const clean = String(text || '').trim();
  if (!clean) return null;

  // Must start with or contain a send verb
  const hasSendVerb = /^(?:prompt\s*:|توجيه\s*:|أمر\s*:)?\s*(?:eb3at(?:ha|o|lo|laha)?|ib3at(?:ha|o|lo|laha)?|ab3at(?:ha|o|lo|laha)?|ابعت(?:ها|وا|و|له|لها)?|أبعت(?:ها|وا|و|له|لها)?|ارسل(?:ها|وا|و|له|لها)?|أرسل(?:ها|وا|و|له|لها)?|send|forward|dispatch|mail|email)/i.test(clean);
  if (!hasSendVerb) return null;

  // Avoid matching broadcast to all
  if (/(?:lel\s*nas|el\s*nas|لكل\s*الناس|للناس|الناس\s*كل(?:ها|ه|هم)|لكل\s*المتداولين|للمتداولين|للكل|لكله|للجميع|broadcast|\bto\s+all\b|\beveryone\b)/i.test(clean)) {
    return null;
  }

  // Case A: Direct email present anywhere in the command
  const emailMatch = clean.match(/([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})/);
  if (emailMatch) {
    const email = cleanEmailAddr(emailMatch[1]);
    const idx = clean.indexOf(emailMatch[1]);
    const beforeEmail = clean.slice(0, idx).trim();
    const afterEmail = clean.slice(idx + emailMatch[1].length).trim();

    let contentStr = '';
    if (afterEmail) {
      contentStr = afterEmail.replace(/^(?:2ollo|2olo|2olha|2ol|qollo|qolo|قوله|قولها|قول|بلغه|بلغها|عرفه|عرفها|واكتبله|اكتبله|en|inn|that|an|إنه|انه|إنها|انها|إن|ان|بأن|بان|بخصوص|عن|saying|tell\s+him|tell\s+her|:|-|\s)+/i, '').trim();
    }
    if (!contentStr && beforeEmail) {
      // e.g. "ابعت qr الدفع ل trader@example.com"
      const strippedBefore = beforeEmail
        .replace(/^(?:prompt\s*:|توجيه\s*:|أمر\s*:)?\s*(?:eb3at(?:ha|o|lo|laha)?|ib3at(?:ha|o|lo|laha)?|ab3at(?:ha|o|lo|laha)?|ابعت(?:ها|وا|و|له|لها)?|أبعت(?:ها|وا|و|له|لها)?|ارسل(?:ها|وا|و|له|لها)?|أرسل(?:ها|وا|و|له|لها)?|send|forward|dispatch)\s*/i, '')
        .replace(/(?:le|li|to|for|لـ|لرقم|لإيميل|لايميل|ل)\s*$/i, '')
        .replace(/^(?:mail|email|ايميل|إيميل|مايل|ميل|رسالة|رساله)\s*/i, '')
        .trim();
      if (strippedBefore) contentStr = strippedBefore;
    }
    return { targetStr: email, contentStr };
  }

  // Strip the leading send verb and optional "mail / email / ايميل"
  const bodyAfterVerb = clean
    .replace(/^(?:prompt\s*:|توجيه\s*:|أمر\s*:)?\s*(?:eb3at(?:ha|o|lo|laha)?|ib3at(?:ha|o|lo|laha)?|ab3at(?:ha|o|lo|laha)?|ابعت(?:ها|وا|و|له|لها)?|أبعت(?:ها|وا|و|له|لها)?|ارسل(?:ها|وا|و|له|لها)?|أرسل(?:ها|وا|و|له|لها)?|send|forward|dispatch)\s+/i, '')
    .replace(/^(?:mail|email|e-mail|ايميل|إيميل|مايل|ميل|رسالة|رساله|بريد)\s+/i, '')
    .trim();

  // Case B: "<WHAT> (le|to|لـ|ل) <TARGET>" where WHAT is qr/payment/code and TARGET is at the end
  // e.g. "ابعت كود الدفع ل محمد Epsilon" or "eb3at el qr code le 01012345678"
  const whatBeforeTarget = bodyAfterVerb.match(/^((?:el\s*)?(?:qr(?:\s*code)?|كود\s*الدفع|رمز\s*الـ?\s*qr|رابط\s*الدفع|الدفع|لينك\s*الدفع|payment\s*(?:qr|link|code)?))\s+(?:le|li|to|لـ|لرقم|ل)\s*(.+)$/i);
  if (whatBeforeTarget) {
    return {
      targetStr: stripConnectorsFromTarget(whatBeforeTarget[2]),
      contentStr: whatBeforeTarget[1].trim()
    };
  }

  // Case C: "(le|to|لـ|ل)? <TARGET> <CONNECTOR> <CONTENT>"
  // Connectors: 2ollo, 2olo, قوله, قولها, بلغه, عرفه, en, that, ان, إن, إنه, بأن, بخصوص, :
  const sepRegex = /^(?:le|li|to|for|لـ|لرقم|لعميل|لمتداول|ل\s+|ل)?\s*(.+?)\s+(?:(?:2ollo|2olo|2olha|2ol|qollo|qolo|kollo|kolo|قوله|قولها|قولوا|قول|بلغه|بلغها|بلغ|عرفه|عرفها|عرف|واكتبله|اكتبله|tell\s+him|tell\s+her|saying)\s*(?:en|inn|that|an|إنه|انه|إنها|انها|إن|ان|بأن|بان)?|(?:en|inn|that|an|إنه|انه|إنها|انها|إن|ان|بأن|بان|بخصوص|content|msg|message))\s+(.+)$/i;
  const matchSep = bodyAfterVerb.match(sepRegex);
  if (matchSep) {
    const tStr = stripConnectorsFromTarget(matchSep[1]);
    const cStr = matchSep[2].trim();
    if (tStr) {
      return { targetStr: tStr, contentStr: cStr };
    }
  }

  // Case D: "(le|to|لـ|ل) <TARGET> <CONTENT>" where TARGET is a phone number
  const phoneTargetMatch = bodyAfterVerb.match(/^(?:le|li|to|for|لـ|لرقم|ل)?\s*(\+?[0-9]{9,15})(?:\s+(.+))?$/i);
  if (phoneTargetMatch) {
    return {
      targetStr: phoneTargetMatch[1].trim(),
      contentStr: (phoneTargetMatch[2] || '').replace(/^(?:2ollo|2olo|قوله|قولها|بلغه|en|that|ان|إن|إنه)\s+/i, '').trim()
    };
  }

  // Case E: "(le|to|لـ|ل) <TARGET>" at the end (e.g. "eb3at le Mohamed Epsilon", "ابعت لـ محمد Epsilon كود الدفع")
  const targetOnlyMatch = bodyAfterVerb.match(/^(?:le|li|to|for|لـ|لرقم|ل\s+|ل)\s*(.+)$/i);
  if (targetOnlyMatch) {
    const rem = targetOnlyMatch[1].trim();
    if (/(?:الناس|ناس|المتداولين|الكل|كله|كلها|الجميع|all|everyone|kolaha)/i.test(rem)) {
      return null;
    }
    // Check if rem has trailing "كود الدفع" or "qr code" after a 1-2 word name
    const nameThenQr = rem.match(/^([^\s]+(?:\s+[^\s]+)?)\s+((?:كود\s*الدفع|رمز\s*qr|qr(?:\s*code)?|رابط\s*الدفع|لينك\s*الزوم).*)$/i);
    if (nameThenQr) {
      return {
        targetStr: stripConnectorsFromTarget(nameThenQr[1]),
        contentStr: nameThenQr[2].trim()
      };
    }
    return {
      targetStr: stripConnectorsFromTarget(rem),
      contentStr: ''
    };
  }

  return null;
}

const CEO_CHAT_ID_STR = '1000000001';
const CEO_PHONE_LAST10 = '1007624884';
const CEO_DISCORD_OWNER_IDS = new Set(['dc_1565098888648720445', 'dc_1483551309893206236']);
const CEO_PRIMARY_EMAILS = new Set(['trader@example.com', 'trader@example.com']);

function phoneLast10(raw) {
  const digits = String(raw || '').replace(/[^0-9]/g, '');
  if (!digits) return '';
  return digits.length >= 10 ? digits.slice(-10) : digits;
}

function isCeoOwnerPhone(raw) {
  const l10 = phoneLast10(raw);
  return Boolean(l10 && l10 === CEO_PHONE_LAST10);
}

function extractDocPhone(doc) {
  if (!doc || typeof doc !== 'object') return '';
  for (const val of [doc.phoneE164, doc.number, doc.phone_number, doc.phone]) {
    const s = String(val || '').trim();
    if (s) return s;
  }
  return '';
}

function cleanTgHandle(raw) {
  const s = String(raw || '').replace(/^@/, '').trim().toLowerCase();
  if (!s || s === 'none' || s === 'بدون معرف') return '';
  return s;
}

function getDocChatId(doc) {
  if (!doc || typeof doc !== 'object') return null;
  const raw =
    doc.telegram?.chatId ||
    doc.telegramChatId ||
    doc.chatId ||
    doc.user_id ||
    doc.telegram?.userId ||
    (doc.contact_identifier && String(doc.contact_identifier).startsWith('tg_')
      ? String(doc.contact_identifier).replace('tg_', '')
      : null);
  return raw ? String(raw) : null;
}

function isCeoContactDoc(doc) {
  if (!doc || typeof doc !== 'object') return false;
  const cid = getDocChatId(doc);
  if (cid === CEO_CHAT_ID_STR) return true;
  const ident = String(doc.contact_identifier || '').trim();
  if (ident === `tg_${CEO_CHAT_ID_STR}`) return true;
  const nameLower = String(doc.name || doc.full_name || doc.fullName || '').trim().toLowerCase();
  if (CEO_DISCORD_OWNER_IDS.has(ident) && /^(?:x(?:\s*\(ceo\))?|mohamed\s*admin_user|x\s*\(mohamed\s*admin_user\)|Tradesejon\s*solution)$/i.test(nameLower)) {
    return true;
  }
  const em = cleanEmailAddr(doc.email);
  if (em && CEO_PRIMARY_EMAILS.has(em)) return true;
  return false;
}

function extractSafePhoneForDoc(doc, { explicitUserId, explicitChatId, isCeoContext } = {}) {
  const raw = extractDocPhone(doc);
  if (!raw) return '';
  const isOwner = Boolean(
    isCeoContext ||
    (explicitUserId && String(explicitUserId) === CEO_CHAT_ID_STR) ||
    (explicitChatId && String(explicitChatId) === CEO_CHAT_ID_STR) ||
    isCeoContactDoc(doc)
  );
  if (!isOwner && isCeoOwnerPhone(raw)) {
    return '';
  }
  return raw;
}

function areSamePersonDocs(a, b) {
  if (!a || !b) return false;
  const cidA = getDocChatId(a);
  const cidB = getDocChatId(b);
  if (cidA && cidB && cidA !== cidB) return false;
  if (cidA && cidB && cidA === cidB) return true;

  const hA = cleanTgHandle(a.telegram_user || a.telegram?.username || a.telegramUsername || a.username);
  const hB = cleanTgHandle(b.telegram_user || b.telegram?.username || b.telegramUsername || b.username);
  if (hA && hB && hA !== hB) return false;
  if (hA && hB && hA === hB) return true;

  const eA = cleanEmailAddr(a.email);
  const eB = cleanEmailAddr(b.email);
  if (eA && eB && eA !== eB) return false;
  if (eA && eB && eA === eB) return true;

  if (a.contact_identifier && b.contact_identifier && a.contact_identifier === b.contact_identifier) return true;

  const nA = String(a.name || a.full_name || a.fullName || '').trim().toLowerCase();
  const nB = String(b.name || b.full_name || b.fullName || '').trim().toLowerCase();
  return Boolean(nA && nB && nA === nB);
}

function escapeRxLiteral(s) {
  return String(s || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

async function resolveTargetProfile(db, targetQuery) {
  if (!db) db = await getMailDb();
  const clean = stripConnectorsFromTarget(targetQuery);
  if (!clean) return null;

  const cleanKey = clean.toLowerCase();
  const knownDiscord = DISCORD_KNOWN_MAP[cleanKey] ||
                       DISCORD_KNOWN_MAP[cleanKey.replace(/^@/, '')] ||
                       null;

  // 0. Direct Discord ID
  if (/^\d{16,21}$/.test(clean)) {
    const contact = await db.collection('contacts').findOne({
      $or: [{ discord_user_id: clean }, { discordUserId: clean }, { 'discord.userId': clean }]
    }) || await db.collection('leads').findOne({
      $or: [{ discord_user_id: clean }, { discordUserId: clean }, { 'discord.userId': clean }]
    });
    return {
      type: 'discord',
      discord_user_id: clean,
      name: contact?.name || contact?.full_name || 'متداول ديسكورد',
      email: contact?.email || '',
      emails: contact?.email ? [contact.email] : [],
      phone: extractSafePhoneForDoc(contact),
      chatId: getDocChatId(contact)
    };
  }

  // 1. Direct Email
  const emailMatch = clean.match(/([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})/);
  if (emailMatch) {
    const email = cleanEmailAddr(emailMatch[1]);
    if (email) {
      const emailLower = email.toLowerCase();
      const contact = await db.collection('contacts').findOne({
        $or: [{ email }, { emails: email }, { secondary_emails: email }]
      }) || await db.collection('leads').findOne({ email }) || await db.collection('trading_leads').findOne({ email });
      const dId = contact?.discord_user_id || contact?.discordUserId || contact?.discord?.userId || DISCORD_KNOWN_MAP[emailLower] || knownDiscord;
      return {
        type: 'email',
        email,
        emails: [email],
        discord_user_id: dId || null,
        name: contact?.name || contact?.full_name || email.split('@')[0],
        phone: extractSafePhoneForDoc(contact, { isCeoContext: CEO_PRIMARY_EMAILS.has(email) }),
        chatId: getDocChatId(contact)
      };
    }
  }

  // 2. Direct Phone
  const digits = clean.replace(/[^0-9]/g, '');
  if (digits.length >= 8) {
    const lastDigits = digits.slice(-10);
    const [contacts, leadsV2, leads] = await Promise.all([
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

    let strictMatches = [...contacts, ...leadsV2, ...leads].filter(m => phoneLast10(extractDocPhone(m)) === lastDigits);
    if (lastDigits === CEO_PHONE_LAST10) {
      const ceoMatches = strictMatches.filter(isCeoContactDoc);
      if (ceoMatches.length > 0) {
        ceoMatches.sort((a, b) => (getDocChatId(b) === CEO_CHAT_ID_STR ? 1 : 0) - (getDocChatId(a) === CEO_CHAT_ID_STR ? 1 : 0));
        strictMatches = ceoMatches;
      }
    }

    if (strictMatches.length > 0) {
      const best = strictMatches.find(m => getDocChatId(m)) || strictMatches[0];
      const samePersonDocs = strictMatches.filter(m => areSamePersonDocs(best, m));
      const emailSet = new Set();
      for (const m of samePersonDocs) {
        if (m.email && cleanEmailAddr(m.email)) emailSet.add(cleanEmailAddr(m.email));
        if (Array.isArray(m.emails)) m.emails.forEach(e => { if (cleanEmailAddr(e)) emailSet.add(cleanEmailAddr(e)); });
      }
      const emails = Array.from(emailSet);
      return {
        type: 'phone',
        phone: extractSafePhoneForDoc(best, { isCeoContext: lastDigits === CEO_PHONE_LAST10 }) || digits,
        name: best?.name || best?.full_name || 'متداول',
        email: emails[0] || '',
        emails,
        chatId: getDocChatId(best)
      };
    }

    return {
      type: 'phone',
      phone: digits,
      name: 'متداول',
      email: '',
      emails: [],
      chatId: lastDigits === CEO_PHONE_LAST10 ? CEO_CHAT_ID_STR : null
    };
  }

  // 3. Name / Username Search across CRM Contacts & Leads
  const words = clean
    .replace(/^@/, '')
    .split(/\s+/)
    .filter(w => w.length >= 1 && !STOP_CONNECTORS_REGEX.test(w) && !/^(?:le|li|to|for|al|el|من|الى|إلى|يا|عميل|متداول|استاذ|أستاذ|كابتن|باشا)$/i.test(w));

  if (words.length > 0) {
    const allSearchTerms = words.map(w => getSearchTerms(w));
    const firstWordTerms = allSearchTerms[0].map(escapeRxLiteral).filter(Boolean);
    if (firstWordTerms.length === 0) return null;
    const regex1 = new RegExp(firstWordTerms.join('|'), 'i');

    const [contacts, leadsV2, leads] = await Promise.all([
      db.collection('contacts').find({
        $or: [
          { name: { $regex: regex1 } },
          { full_name: { $regex: regex1 } },
          { fullName: { $regex: regex1 } },
          { telegram_user: { $regex: regex1 } },
          { username: { $regex: regex1 } }
        ]
      }).toArray(),
      db.collection('leads').find({
        $or: [
          { name: { $regex: regex1 } },
          { full_name: { $regex: regex1 } },
          { 'telegram.username': { $regex: regex1 } },
          { telegramUsername: { $regex: regex1 } }
        ]
      }).toArray(),
      db.collection('trading_leads').find({
        $or: [
          { full_name: { $regex: regex1 } },
          { name: { $regex: regex1 } },
          { telegram_user: { $regex: regex1 } },
          { telegramUsername: { $regex: regex1 } }
        ]
      }).toArray()
    ]);

    let candidates = [...contacts, ...leadsV2, ...leads];
    if (candidates.length > 0) {
      if (allSearchTerms.length > 1) {
        const secondWordTerms = allSearchTerms[1].map(escapeRxLiteral).filter(Boolean);
        if (secondWordTerms.length > 0) {
          const regex2 = new RegExp(secondWordTerms.join('|'), 'i');
          const exactMatches = candidates.filter(c => regex2.test(`${c.name || ''} ${c.full_name || ''} ${c.fullName || ''} ${c.telegram_user || c.telegramUsername || ''}`));
          if (exactMatches.length > 0) {
            candidates = exactMatches;
          }
        }
      }

      const qHandle = cleanTgHandle(clean);
      const qLower = clean.replace(/^@/, '').trim().toLowerCase();
      const firstTermExactRx = new RegExp(`(?:^|[^\\p{L}0-9_])${firstWordTerms.join('|')}(?:$|[^\\p{L}0-9_])`, 'iu');

      const scored = candidates.map(c => {
        const uName = cleanTgHandle(c.telegram_user || c.telegram?.username || c.telegramUsername || c.username);
        const cName = String(c.name || c.full_name || c.fullName || '').trim();
        const cNameLower = cName.toLowerCase();
        let score = 0;

        if (qHandle && uName && uName === qHandle) score += 250;
        else if (qLower && cNameLower && cNameLower === qLower) score += 220;
        else if (qHandle && uName && uName.includes(qHandle) && qHandle.length >= 3) score += 120;
        else if (cName && firstTermExactRx.test(cName)) score += 90;
        else if (cNameLower && qLower && cNameLower.includes(qLower) && qLower.length >= 2) score += 50;
        else score += 10;

        if (getDocChatId(c)) score += 6;
        if (cleanEmailAddr(c.email)) score += 4;
        if (extractSafePhoneForDoc(c)) score += 2;
        return { doc: c, score };
      });

      scored.sort((a, b) => b.score - a.score);
      const primary = scored[0].doc;
      const samePersonDocs = scored
        .map(s => s.doc)
        .filter(c => c === primary || areSamePersonDocs(primary, c));

      const emailSet = new Set();
      let bestChatId = getDocChatId(primary);
      let bestPhone = extractSafePhoneForDoc(primary);
      let bestName = String(primary.name || primary.full_name || primary.fullName || '').trim();

      for (const c of samePersonDocs) {
        if (!bestName && (c.name || c.full_name || c.fullName)) bestName = c.name || c.full_name || c.fullName;
        const safeP = extractSafePhoneForDoc(c, { explicitChatId: bestChatId, isCeoContext: isCeoContactDoc(primary) });
        if (!bestPhone && safeP) bestPhone = safeP;
        const cid = getDocChatId(c);
        if (!bestChatId && cid) bestChatId = cid;

        if (c.email && cleanEmailAddr(c.email)) emailSet.add(cleanEmailAddr(c.email));
        if (Array.isArray(c.emails)) {
          for (const e of c.emails) {
            if (cleanEmailAddr(e)) emailSet.add(cleanEmailAddr(e));
          }
        }
      }

      // Final isolation guard: non-CEO profile must never emit CEO owner phone
      if (!isCeoContactDoc(primary) && bestChatId !== CEO_CHAT_ID_STR && isCeoOwnerPhone(bestPhone)) {
        bestPhone = '';
      }

      const emails = Array.from(emailSet);
      const nameLower = (bestName || clean).toLowerCase();
      const resolvedDId = primary?.discord_user_id || primary?.discordUserId || primary?.discord?.userId ||
                          DISCORD_KNOWN_MAP[nameLower] || DISCORD_KNOWN_MAP[cleanKey] ||
                          (emails[0] ? DISCORD_KNOWN_MAP[emails[0].toLowerCase()] : null) || null;
      return {
        type: 'crm_profile',
        name: bestName || clean,
        email: emails[0] || '',
        emails,
        discord_user_id: resolvedDId,
        phone: bestPhone,
        chatId: bestChatId
      };
    }
  }

  return null;
}

function escapeMailHtml(text) {
  return String(text ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function parseSessionTimeFromInstruction(cleanInst) {
  const s = String(cleanInst || '').trim();
  if (!s) return null;
  const suffix = (raw = '') => {
    const t = String(raw || '').toLowerCase();
    if (/masaa|masa2|pm|مساء|م\b|bellel|ليل/i.test(t)) return ' مساءً';
    if (/sobh|sob7|am|صباح|ص\b/i.test(t)) return ' صباحاً';
    return '';
  };

  // Specific spoken fractions (e.g., "5 w rob3", "1 w nos", "9 ela rob3")
  const fracMatch = s.match(/(?:^|\b)(?:الساعة|الساعه|ساعة|ساعه|e?l?\s*sa3a|saa?3a|esa3a|at)?\s*([0-9]{1,2})\s*(?:w\s*rob3|و\s*ربع|وربع|w\s*nos|و\s*نص|ونص|و\s*نصف|ela\s*rob3|إلا\s*ربع|الا\s*ربع)(?:\s*(masaa|masa2|pm|am|مساءً?|صباحاً?))?/i);
  if (fracMatch) {
    const hr = Number(fracMatch[1]);
    if (hr >= 1 && hr <= 24) {
      let mins = '00';
      let dispHr = hr;
      const whole = fracMatch[0].toLowerCase();
      if (/rob3|ربع/.test(whole) && /ela|إلا|الا/.test(whole)) {
        dispHr = hr === 1 ? 12 : hr - 1;
        mins = '45';
      } else if (/rob3|ربع/.test(whole)) {
        mins = '15';
      } else if (/nos|نص/.test(whole)) {
        mins = '30';
      }
      return `اليوم الساعة ${dispHr}:${mins}${suffix(fracMatch[2])} بتوقيت القاهرة`;
    }
  }

  // Explicit time keywords ("elgalsa esa3a 1", "esa3a 2", "الساعة 9:30", "at 8 pm")
  const timeMatch = s.match(/(?:الساعة|الساعه|ساعة|ساعه|e?l?\s*sa3a|saa?3a|esa3a|at)\s*([0-9]{1,2})(?::([0-9]{2}))?(?:\s*(masaa|masa2|pm|am|مساءً?|صباحاً?))?/i);
  if (timeMatch) {
    const hr = Number(timeMatch[1]);
    if (hr >= 1 && hr <= 24) {
      const mins = timeMatch[2] || '00';
      return `اليوم الساعة ${hr}:${mins}${suffix(timeMatch[3])} بتوقيت القاهرة`;
    }
  }

  return null;
}

function extractZoomIdFromUrl(url) {
  const m = String(url || '').match(/\/(?:j|wc\/join)\/([0-9]{9,12})/i);
  if (!m) return '';
  const digits = m[1];
  if (digits.length === 11) return `${digits.slice(0, 3)} ${digits.slice(3, 7)} ${digits.slice(7)}`;
  if (digits.length === 10) return `${digits.slice(0, 3)} ${digits.slice(3, 6)} ${digits.slice(6)}`;
  return digits;
}

function stripHtmlTags(html) {
  return String(html || '')
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/p>/gi, '\n\n')
    .replace(/<\/div>/gi, '\n')
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'")
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

function sanitizeSafeMailParagraph(html) {
  return String(html || '')
    .replace(/<script[\s\S]*?>[\s\S]*?<\/script>/gi, '')
    .replace(/<style[\s\S]*?>[\s\S]*?<\/style>/gi, '')
    .replace(/\son[a-z]+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)/gi, '')
    .trim();
}

function isEmailCopySafe(subject, bodyHtml, cleanInst, expectedHour = null) {
  const combined = `${String(subject || '')} ${String(bodyHtml || '')}`;
  const instAsksTrade = /(?:buy|sell|stock|share|shares|شراء|بيع|سهم|أسهم|اسهم|صفقة|صفقه|اوردر|order\b)/i.test(cleanInst);
  if (!instAsksTrade && /(?:أمر\s*شراء|شراء\s*[:\-]?\s*[0-9]*\s*سهم|سهم\s+من|أسهم\s+من|تنفيذ\s*أمر|صفقة\s*(?:شراء|بيع)|buy\s+\d*\s*share|stock\s+purchase)/i.test(combined)) {
    return false;
  }
  const instAsksPkg = /(?:باقة|باقه|الباقة|الباقه|package|tier|upgrade|ترقية)/i.test(cleanInst);
  if (!instAsksPkg && /(?:الباقة\s*الثانية|تفعيل\s*الباقة|باقة\s*ثانية|package\s*2|tier\s*2)/i.test(combined)) {
    return false;
  }
  if (expectedHour !== null) {
    // Reject LLM copy if it dropped or changed the explicitly requested session hour
    const hrRegex = new RegExp(`(?:الساعة|الساعه|at)\\s*${expectedHour}\\b|(?:\\b0?${expectedHour}:[0-5][0-9])`);
    if (!hrRegex.test(combined)) {
      return false;
    }
  }
  return Boolean(subject && bodyHtml);
}

async function generateDynamicEmailCopy(instructionText, recipientName, campaignOverrides = {}) {
  const cleanInst = String(instructionText || '').trim();
  const displayRecipient = recipientName && !recipientName.includes('@') ? recipientName : 'عميلنا العزيز';
  const liveZoomUrl = campaignOverrides.zoomMeetingUrl || ZOOM_MEETING_URL;
  const urlZoomId = extractZoomIdFromUrl(liveZoomUrl);
  const overrideIdDigits = String(campaignOverrides.zoomMeetingId || '').replace(/\D/g, '');
  const urlIdDigits = urlZoomId.replace(/\D/g, '');
  const liveZoomId = urlZoomId && overrideIdDigits !== urlIdDigits ? urlZoomId : (campaignOverrides.zoomMeetingId || urlZoomId || ZOOM_MEETING_ID);
  const liveZoomPass = campaignOverrides.zoomPasscode || ZOOM_PASSCODE;
  const liveWhopUrl = campaignOverrides.whopCheckoutUrl || WHOP_CHECKOUT_URL;
  const liveQrUrl = `https://api.qrserver.com/v1/create-qr-code/?size=400x400&data=${encodeURIComponent(liveWhopUrl)}`;

  const explicitSessionTime = parseSessionTimeFromInstruction(cleanInst);
  const expectedHourMatch = explicitSessionTime ? explicitSessionTime.match(/الساعة\s+([0-9]{1,2}):/) : null;
  const expectedHour = expectedHourMatch ? Number(expectedHourMatch[1]) : null;
  const sessionTimeDisplay = explicitSessionTime || campaignOverrides.sessionTimeDisplay || 'اليوم الساعة 5:00 مساءً بتوقيت القاهرة';

  const wantsPaymentOrQr = /(?:qr|كود\s*الدفع|رابط\s*الدفع|whop|دفع|سداد|اشتراك|باقة|باقه|عضوية|تسجيل|تفعيل\s*المقعد|checkout|payment)/i.test(cleanInst);
  const wantsZoomOrSession = Boolean(explicitSessionTime) || /(?:galsa|galset|session|جلسة|جلسه|zoom|زوم|ميعاد|موعد|رابط\s*الجلسة|meeting)/i.test(cleanInst);

  let defaultSubject = `رسالة من إدارة Tradesejon إلى ${displayRecipient}`;
  let defaultParagraph = `<p>تحية طيبة وبعد،</p><p>نود إبلاغكم بالتوجيه التالي من إدارة <b>Tradesejon</b>:</p><p><b>${escapeMailHtml(cleanInst || 'إشعار هام بخصوص عضويتك في غرفة تداول Tradesejon.')}</b></p><p>مع خالص التحية،<br/>فريق Tradesejon</p>`;

  if (explicitSessionTime) {
    defaultSubject = `موعد جلسة التداول المباشرة (${explicitSessionTime}) - Tradesejon`;
    defaultParagraph = `<p>تحية طيبة يا ${escapeMailHtml(displayRecipient)}،</p><p>نود إبلاغك بأن <b>موعد جلسة التداول المباشرة عبر Zoom هو ${escapeMailHtml(explicitSessionTime)}</b>. يمكنك الانضمام في الموعد المحدد باستخدام بيانات الدخول المرفقة أدناه.</p><p>نراك في الجلسة،<br/>فريق Tradesejon</p>`;
  } else if (wantsPaymentOrQr) {
    defaultSubject = `بيانات تفعيل عضوية Tradesejon VIP`;
    defaultParagraph = `<p>تحية طيبة يا ${escapeMailHtml(displayRecipient)}،</p><p>بناءً على طلبك، مرفق أدناه رابط التفعيل المباشر ورمز الـ QR الخاص بالاشتراك في غرفة تداول Tradesejon VIP (تجربة مجانية لأول 7 أيام ثم $29 شهرياً).</p>`;
  } else if (wantsZoomOrSession) {
    defaultSubject = `بيانات جلسة التداول المباشرة عبر Zoom - Tradesejon`;
    defaultParagraph = `<p>تحية طيبة يا ${escapeMailHtml(displayRecipient)}،</p><p>مرفق أدناه تفاصيل ورابط جلسة التداول المباشرة القادمة عبر Zoom (${escapeMailHtml(sessionTimeDisplay)}).</p>`;
  }

  // Craft clean executive Arabic copy with Franco-Arabic awareness + strict anti-hallucination validation
  if (cleanInst && cleanInst.length > 3) {
    const prompt = `أنت المساعد التنفيذي الرسمي لفريق Tradesejon (VIP Trading Room).
المطلوب: صياغة عنوان بريد إلكتروني مهني مختصر (Subject) وفقرة رسالة واضحة ومباشرة موجهة إلى "${displayRecipient}" بناءً على توجيه المدير التالي فقط:
"${cleanInst}"

قاموس الفرانكو أراب الخاص بفريقنا:
- "elgalsa" / "el galsa" / "galsa" / "galset" = "جلسة التداول المباشرة عبر Zoom" (وليست اسم شركة أو سهم!).
- "esa3a" / "el sa3a" / "sa3a" = "الساعة / التوقيت" (مثال: "elgalsa esa3a 1" تعني "جلسة التداول الساعة 1:00"؛ "elgalsa esa3a 2" تعني "جلسة التداول الساعة 2:00").
- "2ollo" / "qollo" / "kollo" = "أبلغه / قل له".

قواعد صارمة (يمنع مخالفتها):
1. التزم حرفياً بمضمون توجيه المدير دون زيادة أو مبالغة أو وعود مالية.
2. ممنوع تماماً تأليف أي أوامر شراء أو بيع أسهم أو صفقات أو باقات أو ترويج غير مذكور في التوجيه.
3. إذا ذُكرت ساعة محددة (مثل esa3a 1 أو esa3a 2) فالتزم بنفس الرقم تماماً بدون تغييره.
4. أخرج JSON صالحاً فقط بهذا الشكل:
{"subject": "عنوان مختصر وواضح", "bodyHtml": "فقرة عربية مهنية واضحة بصيغة HTML بسيطة <p>...</p> بدون Markdown"}`;

    let accepted = false;
    for (const model of ['openai/gpt-oss-120b', 'llama-3.3-70b-versatile']) {
      if (accepted) break;
      try {
        const res = await fetch('https://api.groq.com/openai/v1/chat/completions', {
          method: 'POST',
          headers: {
            'Authorization': `Bearer ${GROQ_KEY}`,
            'Content-Type': 'application/json'
          },
          body: JSON.stringify({
            model,
            messages: [{ role: 'user', content: prompt }],
            temperature: 0.1,
            max_tokens: 350,
            response_format: { type: 'json_object' }
          }),
          signal: AbortSignal.timeout(4500)
        });
        if (res.ok) {
          const data = await res.json();
          const content = data.choices?.[0]?.message?.content;
          if (content) {
            const parsed = JSON.parse(String(content).replace(/^```json|```$/gm, '').trim());
            if (isEmailCopySafe(parsed.subject, parsed.bodyHtml, cleanInst, expectedHour)) {
              defaultSubject = String(parsed.subject).trim();
              defaultParagraph = sanitizeSafeMailParagraph(parsed.bodyHtml);
              accepted = true;
            }
          }
        }
      } catch (_) {}
    }

    if (!accepted && process.env.GEMINI_API_KEY) {
      try {
        const gRes = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=${process.env.GEMINI_API_KEY}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            contents: [{ parts: [{ text: prompt }] }],
            generationConfig: { responseMimeType: 'application/json', temperature: 0.1, maxOutputTokens: 350 }
          }),
          signal: AbortSignal.timeout(4500)
        });
        if (gRes.ok) {
          const gData = await gRes.json();
          const txt = gData.candidates?.[0]?.content?.parts?.[0]?.text;
          if (txt) {
            const parsed = JSON.parse(String(txt).replace(/^```json|```$/gm, '').trim());
            if (isEmailCopySafe(parsed.subject, parsed.bodyHtml, cleanInst, expectedHour)) {
              defaultSubject = String(parsed.subject).trim();
              defaultParagraph = sanitizeSafeMailParagraph(parsed.bodyHtml);
            }
          }
        }
      } catch (_) {}
    }
  }

  const zoomHtmlSection = wantsZoomOrSession ? `
  <div style="background-color: #f8fafc; padding: 16px 20px; border-radius: 8px; border-right: 4px solid #0284c7; border: 1px solid #e2e8f0; margin: 18px 0;">
    <p style="margin: 0; font-size: 15px; color: #0f172a;"><b>موعد الجلسة:</b> ${escapeMailHtml(sessionTimeDisplay)}</p>
    <p style="margin: 8px 0 0 0; font-size: 15px; color: #0f172a;"><b>رابط Zoom:</b> <a href="${escapeMailHtml(liveZoomUrl)}" style="color: #0284c7; font-weight: bold;">${escapeMailHtml(liveZoomUrl)}</a></p>
    <p style="margin: 6px 0 0 0; font-size: 13px; color: #475569;">Meeting ID: <b>${escapeMailHtml(liveZoomId)}</b> | Passcode: <b>${escapeMailHtml(liveZoomPass)}</b></p>
  </div>` : '';

  const paymentHtmlSection = wantsPaymentOrQr ? `
  <div style="background-color: #f8fafc; padding: 18px 20px; border-radius: 8px; border: 1px solid #e2e8f0; margin: 18px 0; text-align: center;">
    <p style="margin: 0 0 12px 0; font-size: 14px; color: #334155;">تجربة مجانية لأول 7 أيام لتأكيد المقعد ثم $29 شهرياً عبر Whop:</p>
    <img src="${escapeMailHtml(liveQrUrl)}" alt="Tradesejon QR Code" style="width: 190px; height: 190px; border-radius: 8px; border: 1px solid #cbd5e1; background: #ffffff; padding: 8px;" />
    <div style="margin-top: 16px;">
      <a href="${escapeMailHtml(liveWhopUrl)}" style="background-color: #0284c7; color: #ffffff; padding: 12px 26px; text-decoration: none; border-radius: 6px; font-weight: bold; font-size: 15px; display: inline-block;">فتح رابط التفعيل عبر Whop</a>
    </div>
  </div>` : '';

  const htmlBody = `<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head><meta charset="utf-8" /></head>
<body style="margin: 0; padding: 20px; background-color: #f1f5f9; font-family: 'Segoe UI', Tahoma, Arial, sans-serif;">
  <div style="direction: rtl; text-align: right; background-color: #ffffff; color: #0f172a; padding: 28px; border-radius: 10px; max-width: 600px; margin: auto; border: 1px solid #e2e8f0;">
    <div style="border-bottom: 2px solid #0284c7; padding-bottom: 12px; margin-bottom: 18px;">
      <h2 style="color: #0f172a; margin: 0; font-size: 20px;">Tradesejon Executive Desk</h2>
      <p style="color: #64748b; margin: 4px 0 0 0; font-size: 12px;">مراسلة رسمية من غرفة عمليات Tradesejon</p>
    </div>
    <p style="font-size: 15px; line-height: 1.7; color: #0f172a; margin-top: 0;">مرحباً <b>${escapeMailHtml(displayRecipient)}</b>،</p>
    <div style="font-size: 15px; line-height: 1.8; color: #1e293b; margin: 14px 0;">
      ${defaultParagraph}
    </div>
    ${zoomHtmlSection}
    ${paymentHtmlSection}
    <hr style="border: 0; border-top: 1px solid #e2e8f0; margin: 22px 0 14px 0;" />
    <p style="font-size: 12px; color: #64748b; margin: 0;">تم الإرسال بواسطة فريق Tradesejon — للرد أو الاستفسار يمكنك الرد مباشرة على هذا البريد الإلكتروني.</p>
  </div>
</body>
</html>`;

  const plainParts = [
    `مرحباً ${displayRecipient}،`,
    '',
    stripHtmlTags(defaultParagraph)
  ];
  if (wantsZoomOrSession) {
    plainParts.push(
      '',
      `موعد الجلسة: ${sessionTimeDisplay}`,
      `رابط Zoom: ${liveZoomUrl}`,
      `Meeting ID: ${liveZoomId}`,
      `Passcode: ${liveZoomPass}`
    );
  }
  if (wantsPaymentOrQr) {
    plainParts.push('', `رابط التفعيل عبر Whop: ${liveWhopUrl}`);
  }
  plainParts.push('', 'تحيات فريق Tradesejon');

  return {
    subject: defaultSubject,
    htmlBody,
    textBody: plainParts.join('\n'),
    plainSummary: stripHtmlTags(defaultParagraph),
    sessionTimeDisplay
  };
}

async function trySendViaSmtp(port, mailPayload) {
  const transporter = createSmtpTransporter(port);
  const info = await new Promise((resolve, reject) => {
    transporter.sendMail(mailPayload, (err, sentInfo) => {
      if (err) reject(err);
      else resolve(sentInfo);
    });
  });
  const accepted = Array.isArray(info?.accepted) ? info.accepted : [];
  const rejected = Array.isArray(info?.rejected) ? info.rejected : [];
  if (!info?.messageId || (accepted.length === 0 && rejected.length > 0)) {
    throw new Error(`SMTP port ${port} did not accept recipients (rejected: ${rejected.join(', ') || 'none'})`);
  }
  return {
    provider: port === 465 ? 'gmail_smtp_ssl_465' : 'gmail_smtp_starttls_587',
    messageId: info.messageId,
    smtpResponse: info.response || '250 2.0.0 OK',
    accepted,
    rejected
  };
}

async function trySendViaResend(emailList, subject, htmlBody, textBody, replyTo) {
  const apiKey = String(process.env.RESEND_API_KEY || '').trim();
  if (!apiKey) return { ok: false, skipped: true, reason: 'RESEND_API_KEY not configured on server' };

  const customFrom = String(process.env.RESEND_FROM_EMAIL || '').trim();
  const hasVerifiedCustomDomain = Boolean(customFrom && !/@resend\.dev\b/i.test(customFrom));
  const allToOwner = emailList.every(e => e.toLowerCase() === RESEND_OWNER_EMAIL);
  if (!hasVerifiedCustomDomain && !allToOwner) {
    return {
      ok: false,
      skipped: true,
      reason: `Resend free tier only delivers onboarding@resend.dev to ${RESEND_OWNER_EMAIL}; verify a custom domain at resend.com/domains and set RESEND_FROM_EMAIL`
    };
  }

  const fromHeader = hasVerifiedCustomDomain
    ? (customFrom.includes('<') ? customFrom : `Tradesejon VIP <${customFrom}>`)
    : 'Tradesejon VIP <onboarding@resend.dev>';

  const resp = await fetch('https://api.resend.com/emails', {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${apiKey}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      from: fromHeader,
      to: emailList,
      reply_to: replyTo,
      subject,
      html: htmlBody,
      text: textBody
    }),
    signal: AbortSignal.timeout(8000)
  });
  const raw = await resp.text();
  if (!resp.ok) {
    throw new Error(`Resend HTTP ${resp.status}: ${raw.slice(0, 240)}`);
  }
  const parsed = JSON.parse(raw || '{}');
  return {
    ok: true,
    provider: hasVerifiedCustomDomain ? 'resend_verified_domain' : 'resend_sandbox_owner',
    messageId: parsed.id ? `<${parsed.id}@resend.dev>` : `resend-${Date.now()}`,
    smtpResponse: `200 Resend Accepted (${parsed.id || 'ok'})`,
    accepted: emailList,
    rejected: []
  };
}

async function sendAiExecutiveEmail(db, targetEmailOrList, instructionText, recipientName = '', options = {}) {
  try {
    if (!db) db = await getMailDb();

    // Normalize email list
    const rawList = Array.isArray(targetEmailOrList)
      ? targetEmailOrList
      : String(targetEmailOrList || '').split(/[,;\s]+/);
    const emailList = Array.from(new Set(rawList.map(cleanEmailAddr).filter(Boolean)));

    if (emailList.length === 0) {
      return { ok: false, error: 'لا يوجد بريد إلكتروني صالح للإرسال' };
    }

    const dedupeKey = options?.dedupeKey ? String(options.dedupeKey).trim() : '';
    if (dedupeKey) {
      const existing = await db.collection('outbound_emails').findOne({ dedupeKey, status: 'delivered' }).catch(() => null);
      if (existing) {
        return {
          ok: true,
          deduped: true,
          subject: existing.subject,
          summary: existing.summary,
          sessionTimeDisplay: existing.sessionTimeDisplay || '',
          to: existing.to,
          recipients: existing.recipients || emailList,
          messageId: existing.messageId,
          smtpResponse: existing.smtpResponse || '250 OK (deduped)',
          provider: existing.provider || 'deduped_cache'
        };
      }
    }

    const camp = await db.collection('campaigns').findOne({ _id: 'workshop-poc' }).catch(() => null);
    const { subject, htmlBody, textBody, plainSummary, sessionTimeDisplay } = await generateDynamicEmailCopy(instructionText, recipientName, camp || {});
    const toHeader = emailList.join(', ');
    const { user: smtpUser } = getSmtpCredentials();
    const replyTo = String(options?.replyTo || process.env.MAIL_REPLY_TO || smtpUser || DEFAULT_GMAIL_USER).trim();
    const fromHeader = `Tradesejon VIP <${smtpUser || DEFAULT_GMAIL_USER}>`;
    const refId = `trd-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

    const mailPayload = {
      from: fromHeader,
      to: toHeader,
      replyTo,
      subject,
      text: textBody,
      html: htmlBody,
      headers: {
        'X-Entity-Ref-ID': refId
      }
    };

    const attempts = [];
    let delivered = null;

    const customResendFrom = String(process.env.RESEND_FROM_EMAIL || '').trim();
    const preferVerifiedResend = Boolean(process.env.RESEND_API_KEY && customResendFrom && !/@resend\.dev\b/i.test(customResendFrom));

    if (preferVerifiedResend) {
      try {
        const r = await trySendViaResend(emailList, subject, htmlBody, textBody, replyTo);
        if (r && r.ok) delivered = r;
        else if (r && r.skipped) attempts.push({ provider: 'resend', skipped: true, reason: r.reason });
      } catch (e) {
        attempts.push({ provider: 'resend', error: e.message });
      }
    }

    // Self-healing SMTP chain: SSL 465 -> STARTTLS 587
    for (const port of [465, 587]) {
      if (delivered) break;
      try {
        delivered = await trySendViaSmtp(port, mailPayload);
      } catch (smtpErr) {
        attempts.push({ provider: `gmail_smtp_${port}`, error: smtpErr.message });
        console.warn(`SMTP port ${port} failed:`, smtpErr.message);
      }
    }

    // Secondary failover: Resend API (when not already tried)
    if (!delivered && !preferVerifiedResend) {
      try {
        const r = await trySendViaResend(emailList, subject, htmlBody, textBody, replyTo);
        if (r && r.ok) delivered = r;
        else if (r && r.skipped) attempts.push({ provider: 'resend', skipped: true, reason: r.reason });
      } catch (resendErr) {
        attempts.push({ provider: 'resend', error: resendErr.message });
      }
    }

    // Tertiary failover: Configured external n8n webhook (never dead quick tunnels)
    const configuredN8nBase = String(process.env.N8N_WEBHOOK_BASE_URL || '').trim().replace(/\/+$/, '');
    const n8nWebhookUrl = process.env.N8N_EMAIL_WEBHOOK_URL || (configuredN8nBase ? `${configuredN8nBase}/webhook/send-vip-email` : '');
    if (!delivered && n8nWebhookUrl) {
      try {
        const resp = await fetch(n8nWebhookUrl, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ to: toHeader, subject, text: textBody, html: htmlBody, instruction: instructionText }),
          signal: AbortSignal.timeout(6000)
        });
        if (resp.ok) {
          const json = await resp.json();
          if (json.ok && json.deliveredTo) {
            delivered = {
              provider: 'n8n_webhook_failover',
              messageId: json.messageId || `n8n-${Date.now()}`,
              smtpResponse: `Delivered via n8n to ${json.deliveredTo}`,
              accepted: emailList,
              rejected: []
            };
          } else {
            attempts.push({ provider: 'n8n_webhook', error: json.error || 'n8n returned ok=false' });
          }
        } else {
          attempts.push({ provider: 'n8n_webhook', error: `HTTP ${resp.status}` });
        }
      } catch (n8nErr) {
        attempts.push({ provider: 'n8n_webhook', error: n8nErr.message });
        console.warn('n8n fallback failed:', n8nErr.message);
      }
    }

    const now = new Date();
    if (!delivered) {
      const diagSummary = attempts.map(a => `${a.provider}: ${a.error || a.reason || 'failed'}`).join(' | ');
      await db.collection('aios_self_heal_logs').insertOne({
        component: 'mail_agent',
        to: toHeader,
        recipients: emailList,
        subject,
        attempts,
        error: diagSummary || 'All mail providers failed',
        at: now
      }).catch(() => {});
      return {
        ok: false,
        error: `تعذر تسليم البريد الإلكتروني (${diagSummary || 'SMTP/Resend unreachable'})`,
        attempts
      };
    }

    // Persist verified outbound email + self-heal trace in MongoDB
    try {
      await db.collection('outbound_emails').insertOne({
        to: toHeader,
        recipients: emailList,
        acceptedRecipients: delivered.accepted,
        rejectedRecipients: delivered.rejected,
        recipientName: recipientName || null,
        subject,
        summary: plainSummary,
        sessionTimeDisplay,
        messageId: delivered.messageId,
        smtpResponse: delivered.smtpResponse,
        provider: delivered.provider,
        attempts,
        ...(dedupeKey ? { dedupeKey } : {}),
        instruction: instructionText || null,
        status: 'delivered',
        sentAt: now
      });
      await db.collection('email_logs').insertOne({
        to: toHeader,
        recipients: emailList,
        subject,
        messageId: delivered.messageId,
        smtpResponse: delivered.smtpResponse,
        provider: delivered.provider,
        status: 'sent',
        at: now
      });
    } catch (dbErr) {
      console.error('Mongo email log error:', dbErr.message);
    }

    return {
      ok: true,
      subject,
      summary: plainSummary,
      sessionTimeDisplay,
      to: toHeader,
      recipients: emailList,
      accepted: delivered.accepted,
      rejected: delivered.rejected,
      messageId: delivered.messageId,
      smtpResponse: delivered.smtpResponse,
      provider: delivered.provider,
      attempts
    };
  } catch (err) {
    console.error('sendAiExecutiveEmail error:', err);
    return { ok: false, error: err.message || String(err) };
  }
}

module.exports = {
  WHOP_CHECKOUT_URL,
  QR_PHOTO_URL,
  ZOOM_MEETING_URL,
  ZOOM_MEETING_ID,
  ZOOM_PASSCODE,
  emailTransporter,
  getSmtpCredentials,
  getMailDb,
  NAME_TRANSLITERATIONS,
  getSearchTerms,
  cleanEmailAddr,
  stripConnectorsFromTarget,
  parseTargetAndContent,
  CEO_CHAT_ID_STR,
  CEO_PHONE_LAST10,
  phoneLast10,
  isCeoOwnerPhone,
  extractDocPhone,
  cleanTgHandle,
  getDocChatId,
  isCeoContactDoc,
  extractSafePhoneForDoc,
  areSamePersonDocs,
  resolveTargetProfile,
  parseSessionTimeFromInstruction,
  extractZoomIdFromUrl,
  isEmailCopySafe,
  generateDynamicEmailCopy,
  sendAiExecutiveEmail,
  DISCORD_KNOWN_MAP,
  dispatchDiscordDmDirectly
};
