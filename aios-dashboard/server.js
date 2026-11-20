const http = require('http');
const fs = require('fs');
const path = require('path');

const PORT = Number(process.env.PORT || 3000);
const MIME = { '.html': 'text/html; charset=utf-8', '.js': 'application/javascript', '.json': 'application/json', '.css': 'text/css', '.svg': 'image/svg+xml' };

function wrapRes(res) {
  res.status = (code) => { res.statusCode = code; return res; };
  res.json = (obj) => {
    if (!res.headersSent) res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify(obj));
  };
  return res;
}

const server = http.createServer(async (req, res) => {
  const reqPath = (req.url || '/').split('?')[0];
  if (reqPath.startsWith('/api/')) {
    const modName = reqPath.replace(/^\/api\//, '').replace(/[^a-zA-Z0-9_-]/g, '');
    const apiFile = path.join(__dirname, 'api', (modName || 'index') + '.js');
    const handlerPath = fs.existsSync(apiFile) ? apiFile : path.join(__dirname, 'api', 'index.js');
    try {
      let bodyStr = '';
      for await (const chunk of req) bodyStr += chunk;
      try { req.body = bodyStr ? JSON.parse(bodyStr) : {}; } catch { req.body = {}; }
      const urlObj = new URL(req.url, 'http://localhost');
      req.query = Object.fromEntries(urlObj.searchParams.entries());
      const fn = require(handlerPath);
      await fn(req, wrapRes(res));
    } catch (err) {
      wrapRes(res).status(500).json({ ok: false, error: err.message });
    }
    return;
  }
  const rel = reqPath === '/' ? 'index.html' : reqPath.replace(/^\/+/, '');
  const fp = path.join(__dirname, rel);
  if (fs.existsSync(fp) && fs.statSync(fp).isFile()) {
    const ext = path.extname(fp);
    res.setHeader('Content-Type', MIME[ext] || 'application/octet-stream');
    fs.createReadStream(fp).pipe(res);
  } else {
    res.statusCode = 404;
    res.end('Not Found');
  }
});

server.listen(PORT, '0.0.0.0', () => console.log(`Tradesejon AIOS Cloud Server listening on port ${PORT}`));
