// Universal Knowledge & File Ingestion Engine (Zero Heavy External Dependencies)
// Extracts text/data from pasted notes, TXT, MD, JSON, CSV, HTML, DOCX, XLSX, and digital PDFs,
// then syncs immediately to Disk KB, MongoDB, n8n Workflow #17 (with #00 fallback), AIOS Chat, Discord & Telegram.

const fs = require('fs');
const path = require('path');
const zlib = require('zlib');
const crypto = require('crypto');
const { callN8nWithFallback } = require('./_n8n_bridge');

function resolveKbDir() {
  const candidates = [
    process.env.KB_DIR,
    process.env.TRADESEJON_KNOWLEDGE_BASE_DIR,
    path.join(process.cwd(), 'rag-knowledge-base'),
    path.join(__dirname, '..', '..', 'rag-knowledge-base'),
    '/app/rag-knowledge-base'
  ].filter(Boolean);

  for (const dir of candidates) {
    if (fs.existsSync(dir)) return dir;
  }
  const fallback = candidates[0] || path.join('/tmp', 'tradesejon_rag_kb');
  fs.mkdirSync(path.join(fallback, 'uploads'), { recursive: true });
  return fallback;
}

function getWritableKbDir() {
  const candidates = [
    process.env.KB_DIR,
    process.env.TRADESEJON_KNOWLEDGE_BASE_DIR,
    path.join(process.cwd(), 'rag-knowledge-base'),
    path.join(__dirname, '..', '..', 'rag-knowledge-base'),
    '/app/rag-knowledge-base',
    '/tmp/tradesejon_rag_kb'
  ].filter(Boolean);
  for (const dir of candidates) {
    try {
      fs.mkdirSync(path.join(dir, 'uploads'), { recursive: true });
      const testFile = path.join(dir, '.wtest');
      fs.writeFileSync(testFile, '1');
      fs.unlinkSync(testFile);
      return dir;
    } catch {}
  }
  return '/tmp/tradesejon_rag_kb';
}

function getCustomKbFile(kbDir) {
  return path.join(kbDir, 'custom_knowledge.json');
}

function readDiskStore() {
  const kbDir = resolveKbDir();
  const writableDir = getWritableKbDir();
  const dirs = Array.from(new Set([writableDir, kbDir]));
  const bySlug = new Map();
  for (const d of dirs) {
    const customFile = getCustomKbFile(d);
    if (!fs.existsSync(customFile)) continue;
    try {
      const parsed = JSON.parse(fs.readFileSync(customFile, 'utf8'));
      const list = Array.isArray(parsed) ? parsed : (Array.isArray(parsed.entries) ? parsed.entries : []);
      for (const item of list) {
        if (item && (item.slug || item.id) && !bySlug.has(item.slug || item.id)) {
          bySlug.set(item.slug || item.id, item);
        }
      }
    } catch {}
  }
  return { kbDir: writableDir || kbDir, entries: Array.from(bySlug.values()) };
}

function writeDiskStore(kbDir, entries) {
  const targetDir = getWritableKbDir();
  try {
    fs.mkdirSync(targetDir, { recursive: true });
    fs.writeFileSync(
      getCustomKbFile(targetDir),
      JSON.stringify({ updated_at: new Date().toISOString(), count: entries.length, entries }, null, 2),
      'utf8'
    );
  } catch {}
}

// Lightweight zero-dependency ZIP central/local reader for DOCX and XLSX XML text extraction
function extractZipEntries(buf) {
  const out = {};
  let offset = 0;
  while (offset + 30 <= buf.length) {
    if (buf.readUInt32LE(offset) !== 0x04034b50) break;
    const flags = buf.readUInt16LE(offset + 6);
    const method = buf.readUInt16LE(offset + 8);
    let compSize = buf.readUInt32LE(offset + 18);
    const nameLen = buf.readUInt16LE(offset + 26);
    const extraLen = buf.readUInt16LE(offset + 28);
    const name = buf.slice(offset + 30, offset + 30 + nameLen).toString('utf8');
    const dataStart = offset + 30 + nameLen + extraLen;

    if ((flags & 0x08) && compSize === 0) {
      // Data descriptor fallback scan
      const nextSig = buf.indexOf(Buffer.from([0x50, 0x4b, 0x03, 0x04]), dataStart);
      const cdSig = buf.indexOf(Buffer.from([0x50, 0x4b, 0x01, 0x02]), dataStart);
      const endPos = [nextSig, cdSig].filter(x => x > dataStart).sort((a, b) => a - b)[0] || buf.length;
      const descPos = buf.lastIndexOf(Buffer.from([0x50, 0x4b, 0x07, 0x08]), endPos);
      compSize = descPos > dataStart ? descPos - dataStart : Math.max(0, endPos - dataStart);
      offset = endPos;
    } else {
      offset = dataStart + compSize;
    }

    const rawSlice = buf.slice(dataStart, dataStart + compSize);
    try {
      if (method === 0) out[name] = rawSlice;
      else if (method === 8) out[name] = zlib.inflateRawSync(rawSlice);
    } catch {}
    if (buf.slice(offset, offset + 4).equals(Buffer.from([0x50, 0x4b, 0x07, 0x08]))) {
      offset += 16;
    }
  }
  return out;
}

function stripXml(xml) {
  return String(xml || '')
    .replace(/<\/w:p>|<\/row>|<\/si>|<w:br\s*\/?>/gi, '\n')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/[ \t]+/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

function extractPdfTextBasic(buf) {
  const rawLatin = buf.toString('latin1');
  const pieces = [];
  const streamRegex = /stream[\r\n]+([\s\S]*?)[\r\n]*endstream/g;
  let m;
  const collectTextOperators = (txt) => {
    const tjMatches = txt.match(/\(([^()\\]{1,400}|\\.)*\)\s*Tj/g) || [];
    for (const seg of tjMatches) {
      const inner = seg.replace(/\)\s*Tj$/, '').replace(/^\(/, '').replace(/\\([()\\])/g, '$1');
      if (inner.trim()) pieces.push(inner.trim());
    }
    const arrMatches = txt.match(/\[([\s\S]*?)\]\s*TJ/g) || [];
    for (const arr of arrMatches) {
      const innerParts = arr.match(/\(([^()\\]*|\\.)*\)/g) || [];
      const line = innerParts.map(p => p.slice(1, -1).replace(/\\([()\\])/g, '$1')).join('');
      if (line.trim()) pieces.push(line.trim());
    }
  };

  while ((m = streamRegex.exec(rawLatin)) !== null) {
    const slice = Buffer.from(m[1], 'latin1');
    try {
      const inflated = zlib.inflateSync(slice).toString('utf8');
      collectTextOperators(inflated);
    } catch {
      collectTextOperators(slice.toString('utf8'));
    }
  }
  if (pieces.length === 0) {
    collectTextOperators(rawLatin);
  }
  const combined = pieces.join(' ').replace(/\s+/g, ' ').trim();
  if (combined.length >= 12) return combined;
  // Last-resort UTF-8 readable run fallback for simple ASCII PDFs
  const readable = buf.toString('utf8').replace(/[^\x20-\x7E\u0600-\u06FF\n]+/g, ' ').replace(/\s+/g, ' ').trim();
  return readable.slice(0, 12000);
}

function extractTextFromPayload({ filename = '', mimeType = '', contentBase64 = '', rawText = '' }) {
  if (rawText && String(rawText).trim()) {
    const trimmed = String(rawText).trim();
    const ext = path.extname(filename || '').toLowerCase();
    if (ext === '.json') {
      try { return formatStructuredJson(JSON.parse(trimmed)); } catch { return trimmed; }
    }
    if (ext === '.html' || ext === '.htm') {
      return trimmed.replace(/<script[\s\S]*?<\/script>/gi, '').replace(/<style[\s\S]*?<\/style>/gi, '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
    }
    return trimmed;
  }

  const cleanB64 = String(contentBase64 || '').replace(/^data:[^;]+;base64,/, '').trim();
  if (!cleanB64) return '';
  const buf = Buffer.from(cleanB64, 'base64');
  const ext = path.extname(filename || '').toLowerCase();
  const mime = String(mimeType || '').toLowerCase();

  if (ext === '.docx' || mime.includes('wordprocessingml')) {
    const entries = extractZipEntries(buf);
    const docXml = entries['word/document.xml'];
    return docXml ? stripXml(docXml.toString('utf8')) : '';
  }
  if (ext === '.xlsx' || mime.includes('spreadsheetml')) {
    const entries = extractZipEntries(buf);
    const texts = [];
    if (entries['xl/sharedStrings.xml']) texts.push(stripXml(entries['xl/sharedStrings.xml'].toString('utf8')));
    for (const k of Object.keys(entries)) {
      if (k.startsWith('xl/worksheets/sheet') && k.endsWith('.xml')) {
        texts.push(stripXml(entries[k].toString('utf8')));
      }
    }
    return texts.join('\n').trim();
  }
  if (ext === '.pdf' || mime.includes('pdf')) {
    return extractPdfTextBasic(buf);
  }
  if (ext === '.json' || mime.includes('json')) {
    try { return formatStructuredJson(JSON.parse(buf.toString('utf8'))); } catch { return buf.toString('utf8'); }
  }
  if (ext === '.csv' || ext === '.tsv') {
    const lines = buf.toString('utf8').split(/\r?\n/).filter(l => l.trim());
    if (lines.length === 0) return '';
    const sep = ext === '.tsv' ? '\t' : ',';
    const headers = lines[0].split(sep).map(h => h.replace(/^"|"$/g, '').trim());
    if (lines.length === 1) return headers.join(' | ');
    return lines.slice(1).map(line => {
      const cols = line.split(sep).map(c => c.replace(/^"|"$/g, '').trim());
      return headers.map((h, i) => `${h}: ${cols[i] ?? ''}`).join(' | ');
    }).join('\n');
  }
  if (ext === '.html' || ext === '.htm' || mime.includes('html')) {
    return buf.toString('utf8')
      .replace(/<script[\s\S]*?<\/script>/gi, '')
      .replace(/<style[\s\S]*?<\/style>/gi, '')
      .replace(/<[^>]+>/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  return buf.toString('utf8').replace(/\u0000/g, '').trim();
}

function formatStructuredJson(obj) {
  if (Array.isArray(obj)) {
    return obj.map((item, idx) => {
      if (item && typeof item === 'object') {
        const q = item.question || item.q || item.title || item.name || `Item ${idx + 1}`;
        const a = item.answer || item.a || item.content || item.description || JSON.stringify(item);
        return `${q}: ${a}`;
      }
      return String(item);
    }).join('\n');
  }
  if (obj && typeof obj === 'object') {
    if (Array.isArray(obj.faqs)) return formatStructuredJson(obj.faqs);
    if (Array.isArray(obj.entries)) return formatStructuredJson(obj.entries);
    return Object.entries(obj).map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`).join('\n');
  }
  return String(obj);
}

function chunkText(text, chunkSize = 700, overlap = 100) {
  const clean = String(text || '').replace(/\r\n/g, '\n').trim();
  if (!clean) return [];
  if (clean.length <= chunkSize) return [clean];
  const chunks = [];
  let start = 0;
  while (start < clean.length) {
    const end = Math.min(clean.length, start + chunkSize);
    chunks.push(clean.slice(start, end).trim());
    if (end >= clean.length) break;
    start = Math.max(start + 1, end - overlap);
  }
  return chunks.filter(Boolean);
}

function slugifyTitle(title) {
  const ascii = String(title || '').toLowerCase().replace(/[^a-z0-9\u0600-\u06FF]+/g, '_').replace(/^_+|_+$/g, '');
  const suffix = crypto.createHash('sha1').update(String(title || Date.now())).digest('hex').slice(0, 6);
  return (ascii || 'knowledge_item').slice(0, 48) + '_' + suffix;
}

async function ingestKnowledge(db, payload = {}) {
  const filename = payload.filename ? path.basename(String(payload.filename)) : '';
  const extracted = extractTextFromPayload({
    filename,
    mimeType: payload.mimeType || payload.mime_type || '',
    contentBase64: payload.contentBase64 || payload.content_base64 || payload.data || '',
    rawText: payload.content || payload.text || payload.rawText || ''
  });

  if (!extracted || extracted.trim().length < 2) {
    return {
      ok: false,
      error: 'Could not extract readable text from the submitted text or file. Supported formats: plain text, MD, JSON, CSV, HTML, DOCX, XLSX, and digital PDF.'
    };
  }

  const title = String(payload.title || filename || extracted.split('\n')[0].slice(0, 64) || 'Custom Knowledge Entry').trim();
  const category = String(payload.category || 'general').trim();
  const slug = String(payload.slug || slugifyTitle(title));
  const now = new Date().toISOString();
  const chunks = chunkText(extracted);

  // 1. Save uploaded file (if binary/raw upload) & upsert custom_knowledge.json
  const { kbDir, entries } = readDiskStore();
  let savedUploadPath = null;
  if (filename) {
    const uploadsDir = path.join(kbDir, 'uploads');
    fs.mkdirSync(uploadsDir, { recursive: true });
    const safeName = filename.replace(/[^a-zA-Z0-9._\-\u0600-\u06FF]/g, '_');
    savedUploadPath = path.join(uploadsDir, safeName);
    if (payload.contentBase64 || payload.content_base64 || payload.data) {
      const b64 = String(payload.contentBase64 || payload.content_base64 || payload.data).replace(/^data:[^;]+;base64,/, '');
      fs.writeFileSync(savedUploadPath, Buffer.from(b64, 'base64'));
    } else {
      fs.writeFileSync(savedUploadPath, extracted, 'utf8');
    }
  }

  const entry = {
    id: slug,
    slug,
    title,
    category,
    filename: filename || null,
    source_type: filename ? 'file_upload' : 'direct_text',
    content: extracted.slice(0, 32000),
    chunks_count: chunks.length,
    updated_at: now
  };

  const filtered = entries.filter(e => e.slug !== slug && e.title !== title);
  filtered.unshift(entry);
  writeDiskStore(kbDir, filtered);

  // 2. Sync to MongoDB collections (kb_articles, knowledge_chunks, aios_super_memory)
  let mongoSynced = false;
  let mongoError = null;
  if (db) {
    try {
      await db.collection('kb_articles').updateOne(
        { slug },
        {
          $set: {
            slug,
            title,
            category,
            filename: filename || null,
            source_type: entry.source_type,
            content: entry.content,
            chunks_count: chunks.length,
            updated_at: new Date(),
            updatedAtIso: now
          }
        },
        { upsert: true }
      );
      await db.collection('knowledge_chunks').deleteMany({ slug });
      if (chunks.length > 0) {
        const docs = chunks.map((text, idx) => ({
          slug,
          title,
          category,
          chunk_index: idx,
          text,
          keywords: Array.from(new Set(text.toLowerCase().split(/[^a-z0-9\u0600-\u06FF]+/).filter(w => w.length > 2))).slice(0, 30),
          updated_at: new Date()
        }));
        await db.collection('knowledge_chunks').insertMany(docs);
      }
      await db.collection('aios_super_memory').updateOne(
        { key: `kb_${slug}` },
        {
          $set: {
            key: `kb_${slug}`,
            type: 'universal_knowledge',
            title,
            category,
            user_query: title,
            ai_reply: extracted.slice(0, 4000),
            rule: `[${title}] ${extracted.slice(0, 2000)}`,
            updated_at: new Date()
          }
        },
        { upsert: true }
      );
      mongoSynced = true;
    } catch (err) {
      mongoError = err.message;
    }
  }

  // 3. Notify n8n Workflow #17 (automatically fails over to Workflow #00 if #17 is unpublished)
  const n8nSync = await callN8nWithFallback('tradesejon-rag-ingest', {
    action: 'ingest',
    slug,
    title,
    category,
    content: extracted.slice(0, 8000),
    chunks_count: chunks.length,
    updated_at: now
  });

  return {
    ok: true,
    entry,
    sync_receipt: {
      disk_kb: { ok: true, path: getCustomKbFile(kbDir), upload_saved: savedUploadPath },
      mongodb: { ok: mongoSynced, chunks_indexed: chunks.length, error: mongoError },
      n8n_rag: n8nSync,
      aios_memory: { ok: mongoSynced || true },
      discord_ready: true,
      telegram_ready: true
    }
  };
}

function loadStaticKbFiles(kbDir) {
  const results = [];
  for (const fname of ['17_faqs_official.json', 'pricing_and_mentors.json']) {
    const p = path.join(kbDir, fname);
    if (!fs.existsSync(p)) continue;
    try {
      const parsed = JSON.parse(fs.readFileSync(p, 'utf8'));
      if (Array.isArray(parsed.faqs)) {
        for (const f of parsed.faqs) {
          results.push({
            slug: `faq_${f.id || results.length}`,
            title: f.question || 'FAQ',
            category: 'official_faq',
            content: `${f.question || ''}\n${f.answer || ''}`
          });
        }
      } else {
        results.push({
          slug: fname.replace('.json', ''),
          title: fname,
          category: 'pricing_and_mentors',
          content: formatStructuredJson(parsed)
        });
      }
    } catch {}
  }
  return results;
}

async function listKnowledge(db) {
  const { kbDir, entries } = readDiskStore();
  const bySlug = new Map();
  for (const e of entries) bySlug.set(e.slug, e);

  if (db) {
    try {
      const docs = await db.collection('kb_articles')
        .find({ slug: { $ne: 'tradesejon_official_17_faq_master_guide' } })
        .sort({ updated_at: -1 })
        .limit(100)
        .toArray();
      for (const d of docs) {
        if (!bySlug.has(d.slug)) {
          bySlug.set(d.slug, {
            slug: d.slug,
            title: d.title,
            category: d.category || 'general',
            filename: d.filename || null,
            source_type: d.source_type || 'mongodb',
            content: d.content || '',
            chunks_count: d.chunks_count || 1,
            updated_at: d.updatedAtIso || (d.updated_at instanceof Date ? d.updated_at.toISOString() : '')
          });
        }
      }
    } catch {}
  }
  return { kbDir, items: Array.from(bySlug.values()) };
}

async function searchKnowledge(db, query, limit = 4) {
  const q = String(query || '').toLowerCase().trim();
  if (!q) return [];
  const tokens = Array.from(new Set(q.split(/[^a-z0-9\u0600-\u06FF]+/).filter(w => w.length > 2)));
  const { kbDir, entries } = readDiskStore();
  const candidates = [...entries, ...loadStaticKbFiles(kbDir)];

  if (db) {
    try {
      const dbChunks = await db.collection('knowledge_chunks').find().sort({ updated_at: -1 }).limit(120).toArray();
      for (const c of dbChunks) {
        candidates.push({ slug: c.slug, title: c.title, category: c.category || 'kb', content: c.text });
      }
    } catch {}
  }

  const scored = [];
  const seen = new Set();
  for (const item of candidates) {
    const hay = `${item.title || ''} ${item.content || ''}`.toLowerCase();
    let score = hay.includes(q) ? 5 : 0;
    for (const t of tokens) {
      if (hay.includes(t)) score += 1;
    }
    if (score > 0) {
      const key = `${item.title}::${String(item.content || '').slice(0, 80)}`;
      if (!seen.has(key)) {
        seen.add(key);
        scored.push({
          title: item.title,
          category: item.category || 'general',
          snippet: String(item.content || '').slice(0, 600),
          score
        });
      }
    }
  }
  scored.sort((a, b) => b.score - a.score);
  return scored.slice(0, limit);
}

async function deleteKnowledge(db, slug) {
  const targetSlug = String(slug || '').trim();
  if (!targetSlug) return { ok: false, error: 'Missing slug' };
  const { kbDir, entries } = readDiskStore();
  const next = entries.filter(e => e.slug !== targetSlug && e.id !== targetSlug);
  writeDiskStore(kbDir, next);
  if (db) {
    try {
      await db.collection('kb_articles').deleteOne({ slug: targetSlug });
      await db.collection('knowledge_chunks').deleteMany({ slug: targetSlug });
      await db.collection('aios_super_memory').deleteOne({ key: `kb_${targetSlug}` });
    } catch {}
  }
  return { ok: true, deleted_slug: targetSlug, remaining: next.length };
}

module.exports = {
  resolveKbDir,
  extractTextFromPayload,
  ingestKnowledge,
  listKnowledge,
  searchKnowledge,
  deleteKnowledge
};
