const http = require('http');
const fs = require('fs');
const path = require('path');

const PORT = Number(process.env.PORT || 3000);
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.zip': 'application/zip'
};

const CLEAN_HTML_ROUTES = {
  '/': 'index.html',
  '/index': 'index.html',
  '/form': 'form.html',
  '/db': 'db.html',
  '/aios': 'aios.html',
  '/knowledge': 'knowledge.html'
};

function wrapRes(res) {
  res.status = (code) => {
    res.statusCode = code;
    return res;
  };
  res.json = (obj) => {
    if (!res.headersSent) res.setHeader('Content-Type', 'application/json; charset=utf-8');
    res.end(JSON.stringify(obj));
  };
  res.send = (payload) => {
    if (Buffer.isBuffer(payload)) {
      res.end(payload);
    } else if (typeof payload === 'object' && payload !== null) {
      if (!res.headersSent) res.setHeader('Content-Type', 'application/json; charset=utf-8');
      res.end(JSON.stringify(payload));
    } else {
      res.end(String(payload ?? ''));
    }
  };
  return res;
}

const server = http.createServer(async (req, res) => {
  const reqPath = (req.url || '/').split('?')[0];
  if (reqPath.startsWith('/api/')) {
    const modName = reqPath.replace(/^\/api\//, '').split('/')[0].replace(/[^a-zA-Z0-9_-]/g, '');
    const apiFile = path.join(__dirname, 'api', (modName || 'index') + '.js');
    const handlerPath = fs.existsSync(apiFile) ? apiFile : path.join(__dirname, 'api', 'index.js');
    try {
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      const rawBuf = Buffer.concat(chunks);
      req.rawBody = rawBuf;
      if (modName === 'discord') {
        req.body = rawBuf;
      } else {
        const bodyStr = rawBuf.toString('utf8');
        try { req.body = bodyStr ? JSON.parse(bodyStr) : {}; } catch { req.body = {}; }
      }
      const urlObj = new URL(req.url, 'http://localhost');
      req.query = Object.fromEntries(urlObj.searchParams.entries());
      const fn = require(handlerPath);
      await fn(req, wrapRes(res));
    } catch (err) {
      wrapRes(res).status(500).json({ ok: false, error: err.message });
    }
    return;
  }

  const mapped = CLEAN_HTML_ROUTES[reqPath];
  const rel = mapped || reqPath.replace(/^\/+/, '');
  let fp = path.join(__dirname, rel);
  if (!fs.existsSync(fp) && !path.extname(fp) && fs.existsSync(fp + '.html')) {
    fp = fp + '.html';
  }
  if (!fs.existsSync(fp) && rel === 'tradesejon_n8n_workflows_package.zip') {
    const wfZip = path.join(__dirname, '..', 'n8n-workflows', 'tradesejon_n8n_workflows_package.zip');
    if (fs.existsSync(wfZip)) fp = wfZip;
  }
  if (fs.existsSync(fp) && fs.statSync(fp).isFile()) {
    const ext = path.extname(fp).toLowerCase();
    res.setHeader('Content-Type', MIME[ext] || 'application/octet-stream');
    fs.createReadStream(fp).pipe(res);
  } else {
    res.statusCode = 404;
    res.end('Not Found');
  }
});

if (require.main === module) {
  server.listen(PORT, '0.0.0.0', () => console.log(`Tradesejon AIOS Cloud Server listening on port ${PORT}`));
}

module.exports = { server, wrapRes };
