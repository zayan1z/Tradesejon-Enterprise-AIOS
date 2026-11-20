// Resilient n8n Bridge & Auto-Fallback Dispatcher
// Guarantees AIOS, Knowledge Sync, and Bots continue operating cleanly even if individual n8n workflows are unpublished.

const fs = require('fs');
const path = require('path');

function getN8nBaseUrl() {
  return String(
    process.env.N8N_BASE_URL ||
    process.env.N8N_WEBHOOK_BASE_URL ||
    'http://127.0.0.1:5678'
  ).trim().replace(/\/+$/, '');
}

async function postJson(url, payload, headers = {}, timeoutMs = 4500) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...headers },
      body: JSON.stringify(payload || {}),
      signal: controller.signal
    });
    const text = await resp.text();
    let parsed = null;
    try { parsed = text ? JSON.parse(text) : {}; } catch { parsed = { raw: text }; }
    return { ok: resp.ok, status: resp.status, data: parsed };
  } catch (err) {
    return { ok: false, status: 0, error: err.message };
  } finally {
    clearTimeout(timer);
  }
}

async function callN8nWithFallback(webhookPath, payload = {}, options = {}) {
  const baseUrl = (options.baseUrl || getN8nBaseUrl()).replace(/\/+$/, '');
  const cleanPath = String(webhookPath || '').replace(/^\/+/, '');
  const secret = process.env.TRADESAJON_BRIDGE_SECRET || process.env.DISCORD_BRIDGE_SECRET || '';
  const headers = {};
  if (secret) headers['x-tradesejon-secret'] = secret;

  if (!baseUrl) {
    return { ok: false, via: 'none', error: 'N8N_BASE_URL not configured' };
  }

  // 1. Primary attempt on dedicated workflow webhook (e.g. /webhook/tradesejon-rag-ingest)
  const primary = await postJson(`${baseUrl}/webhook/${cleanPath}`, payload, headers, options.timeoutMs || 4500);
  if (primary.ok) {
    return { ok: true, via: cleanPath, status: primary.status, data: primary.data };
  }

  // 2. If dedicated workflow is unpublished (404/405/502/503), route cleanly through #00 Unified Gateway
  if (cleanPath !== 'tradesejon-unified') {
    const unified = await postJson(
      `${baseUrl}/webhook/tradesejon-unified`,
      { route: cleanPath, ...payload },
      headers,
      options.timeoutMs || 4500
    );
    if (unified.ok) {
      return {
        ok: true,
        via: 'tradesejon-unified_fallback',
        fallback_from: cleanPath,
        primary_status: primary.status,
        status: unified.status,
        data: unified.data
      };
    }
  }

  return {
    ok: false,
    via: 'local_resilient_fallback',
    primary_status: primary.status,
    error: primary.error || `n8n HTTP ${primary.status} (workflow unpublished or n8n offline)`
  };
}

async function getN8nHealthAndWorkflows() {
  const baseUrl = getN8nBaseUrl();
  const status = {
    n8n_base_url: baseUrl,
    reachable: false,
    health_status: 0,
    workflows_total: 0,
    workflows_published: null,
    workflows_unpublished: null,
    bundled_workflows_count: 0,
    api_checked: false
  };

  try {
    const wfDirs = [
      path.join(process.cwd(), 'n8n-workflows'),
      path.join(__dirname, '..', '..', 'n8n-workflows'),
      '/app/n8n-workflows'
    ];
    for (const d of wfDirs) {
      if (fs.existsSync(d)) {
        status.bundled_workflows_count = fs.readdirSync(d).filter(f => f.endsWith('.json') && !f.includes('Bundle')).length;
        break;
      }
    }
  } catch {}

  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 3000);
    const hr = await fetch(`${baseUrl}/healthz`, { signal: controller.signal });
    clearTimeout(timer);
    status.health_status = hr.status;
    status.reachable = hr.ok;
  } catch {}

  const apiKey = String(process.env.N8N_API_KEY || '').trim();
  if (status.reachable && apiKey) {
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 3500);
      const wr = await fetch(`${baseUrl}/api/v1/workflows?limit=200`, {
        headers: { 'X-N8N-API-KEY': apiKey },
        signal: controller.signal
      });
      clearTimeout(timer);
      if (wr.ok) {
        const j = await wr.json();
        const list = Array.isArray(j.data) ? j.data : [];
        status.api_checked = true;
        status.workflows_total = list.length;
        status.workflows_published = list.filter(w => w.active).length;
        status.workflows_unpublished = list.filter(w => !w.active).length;
      }
    } catch {}
  }

  return status;
}

module.exports = {
  getN8nBaseUrl,
  callN8nWithFallback,
  getN8nHealthAndWorkflows
};
