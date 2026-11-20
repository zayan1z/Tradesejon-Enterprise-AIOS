const crypto = require("crypto");
let MongoClient = null;
try { ({ MongoClient } = require("mongodb")); } catch {}

const FALLBACK_ATLAS_URI = process.env.MONGODB_URI || process.env.MONGODB_ATLAS_URI || "";
let cachedClient = null;

async function getDb() {
  if (!FALLBACK_ATLAS_URI || !MongoClient) return null;
  if (!cachedClient) {
    cachedClient = new MongoClient(FALLBACK_ATLAS_URI, { serverSelectionTimeoutMS: 3000 });
    await cachedClient.connect();
  }
  return cachedClient.db(process.env.MONGODB_DB || "tradesejon_prod");
}

function timingSafeEq(a, b) {
  const ba = Buffer.from(String(a || ""), "utf8");
  const bb = Buffer.from(String(b || ""), "utf8");
  if (ba.length === 0 || ba.length !== bb.length) return false;
  return crypto.timingSafeEqual(ba, bb);
}

module.exports = async function handler(req, res) {
  res.setHeader("Cache-Control", "no-store, private, max-age=0");
  const token = String((req.query && req.query.t) || "").trim();
  const sig = String((req.query && req.query.s) || "").trim();
  if (!token || !sig) {
    return res.status(403).send("<h2>403 - Missing personal Zoom access token.</h2>");
  }

  const db = await getDb();
  if (!db) {
    return res.status(503).send("<h2>503 - Zoom access verification store unavailable.</h2>");
  }

  const doc = await db.collection("zoom_access_tokens").findOne({ token });
  if (!doc || doc.paid !== true || doc.revoked === true) {
    return res.status(403).send("<h2>403 - Access Denied: Unpaid, invalid, or revoked personal link.</h2>");
  }

  const secret = (process.env.DISCORD_BRIDGE_SECRET || process.env.WHOP_API_KEY || "tradesejon-zoom-signing").trim();
  const payload = `${token}:${String(doc.discord_user_id || "")}:${String(doc.email || "").trim().toLowerCase()}:${String(doc.checkout_ref || "").trim()}`;
  const expected = crypto.createHmac("sha256", secret).update(payload).digest("hex").slice(0, 32);

  if (!timingSafeEq(expected, sig) && !(doc.sig && timingSafeEq(doc.sig, sig))) {
    return res.status(403).send("<h2>403 - Access Denied: Signature mismatch.</h2>");
  }

  const rawIp = String(req.headers["x-forwarded-for"] || (req.socket && req.socket.remoteAddress) || "").split(",")[0].trim();
  const ipHash = crypto.createHash("sha256").update(`${secret}:${rawIp}`).digest("hex").slice(0, 16);
  const ipHashes = Array.isArray(doc.ip_hashes) ? [...doc.ip_hashes] : [];
  const maxIps = Number(doc.max_distinct_ips || 2);

  if (!ipHashes.includes(ipHash)) {
    if (ipHashes.length >= maxIps) {
      await db.collection("zoom_join_audit").insertOne({
        token,
        discord_user_id: doc.discord_user_id || "",
        email: doc.email || "",
        ip_hash: ipHash,
        allowed: false,
        reason: "ip_limit_exceeded",
        created_at: new Date(),
      });
      return res.status(403).send("<h2>403 - Access Blocked: Personal Zoom link device limit reached.</h2>");
    }
    ipHashes.push(ipHash);
  }

  await db.collection("zoom_access_tokens").updateOne(
    { token },
    {
      $set: {
        ip_hashes: ipHashes,
        use_count: Number(doc.use_count || 0) + 1,
        last_used_at: new Date(),
        updated_at: new Date(),
      },
    }
  );
  await db.collection("zoom_join_audit").insertOne({
    token,
    discord_user_id: doc.discord_user_id || "",
    email: doc.email || "",
    ip_hash: ipHash,
    allowed: true,
    created_at: new Date(),
  });

  const rawTarget = String(doc.upstream_zoom_url || process.env.ZOOM_MEETING_URL || "").trim();
  const targetLive =
    !rawTarget || rawTarget.includes("88392145021") || rawTarget.includes("88392142026")
      ? "https://discord.com/channels/900000000000000001/900000000000000002"
      : rawTarget;
  res.writeHead(302, { Location: targetLive, "Cache-Control": "no-store, private, max-age=0" });
  return res.end();
};
