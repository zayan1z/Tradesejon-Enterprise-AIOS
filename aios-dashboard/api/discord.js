// Secure Discord HTTP Interactions Endpoint for Vercel (Ed25519 Verified)
// Fails closed with 401 on missing/invalid X-Signature-Ed25519 or stale timestamp.

const crypto = require('crypto');

const SPKI_ED25519_PREFIX = Buffer.from('302a300506032b6570032100', 'hex');

function verifyDiscordSignature(publicKeyHex, signatureHex, timestamp, rawBodyBuffer) {
  if (!publicKeyHex || !signatureHex || !timestamp || !rawBodyBuffer) return false;
  const tsNum = Number(timestamp);
  if (!Number.isFinite(tsNum) || Math.abs(Math.floor(Date.now() / 1000) - tsNum) > 300) {
    return false;
  }
  try {
    const pubDer = Buffer.concat([SPKI_ED25519_PREFIX, Buffer.from(publicKeyHex, 'hex')]);
    const keyObj = crypto.createPublicKey({ key: pubDer, format: 'der', type: 'spki' });
    const msg = Buffer.concat([Buffer.from(String(timestamp), 'utf8'), rawBodyBuffer]);
    return crypto.verify(null, msg, keyObj, Buffer.from(signatureHex, 'hex'));
  } catch {
    return false;
  }
}

async function readRawBody(req) {
  if (Buffer.isBuffer(req.body)) return req.body;
  if (typeof req.body === 'string') return Buffer.from(req.body, 'utf8');
  const chunks = [];
  for await (const c of req) chunks.push(c);
  return Buffer.concat(chunks);
}

module.exports = async (req, res) => {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method Not Allowed' });
  }

  const publicKey = process.env.DISCORD_PUBLIC_KEY || '';
  if (!publicKey) {
    return res.status(503).json({ error: 'DISCORD_PUBLIC_KEY not configured' });
  }

  const sig = req.headers['x-signature-ed25519'];
  const ts = req.headers['x-signature-timestamp'];
  const rawBody = await readRawBody(req);

  if (!verifyDiscordSignature(publicKey, sig, ts, rawBody)) {
    return res.status(401).json({ error: 'Invalid Discord Ed25519 signature' });
  }

  let interaction = {};
  try {
    interaction = JSON.parse(rawBody.toString('utf8'));
  } catch {
    return res.status(400).json({ error: 'Malformed JSON' });
  }

  // Discord PING verification handshake (Type 1)
  if (interaction.type === 1) {
    return res.status(200).json({ type: 1 });
  }

  const cmdName = String(interaction.data?.name || '').toLowerCase();
  if (cmdName === 'session') {
    return res.status(200).json({
      type: 4,
      data: {
        flags: 64,
        content: '🔒 **أدخل بريدك الإلكتروني ورقم هاتفك عبر `/form` أو في الشات لاستلام رابط Discord Live المباشر مجاناً ($0) عبر Discord والإيميل والتيليجرام.**',
        allowed_mentions: { parse: [] },
      },
    });
  }

  if (cmdName === 'trial') {
    return res.status(200).json({
      type: 4,
      data: {
        flags: 64, // Ephemeral
        content: '🎁 **بطاقة Tradesejon VIP على Whop مجانية 100% ($0 بدون بطاقة):** اكتب بريدك ورقم هاتفك في `/form` أو زر `/form` عبر الموقع لاستلام رابط **Discord Live** المباشر فوراً.',
        allowed_mentions: { parse: [] },
      },
    });
  }

  return res.status(200).json({
    type: 4,
    data: {
      flags: 64,
      content: '🤖 تم استلام أمرك عبر Tradesejon AIOS Secure Gateway.',
      allowed_mentions: { parse: [] },
    },
  });
};

module.exports.verifyDiscordSignature = verifyDiscordSignature;
