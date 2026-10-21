// Same-origin development gateway. API streams are piped without buffering.
import { createReadStream, readFileSync, statSync } from 'node:fs';
import { createServer, request } from 'node:http';
import { extname, isAbsolute, relative, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css',
  '.svg': 'image/svg+xml', '.png': 'image/png', '.woff2': 'font/woff2', '.json': 'application/json' };
const forwards = ['forwarded', 'x-forwarded-for', 'x-forwarded-host', 'x-forwarded-proto', 'x-real-ip'];
const loopback = new Set(['127.0.0.1', 'localhost', '[::1]']);
function local(value) { try { return loopback.has(new URL(value).hostname); } catch { return false; } }

export function createGateway({ root = '/app/public', room = 'http://room:8080', capability = '/gateway-capability/dev.env' } = {}) {
root = resolve(root);
return createServer((req, res) => {
  const url = new URL(req.url || '/', 'http://localhost');
  if (url.pathname === '/health') { res.writeHead(200); res.end('ok'); return; }
  if (url.pathname.startsWith('/api/')) {
    const headers = { ...req.headers };
    // Never let a caller supply the gateway's capability; it is read from its own volume.
    delete headers['x-consilith-dev'];
    if (url.pathname.startsWith('/api/dev/')) {
      if (!local(`http://${req.headers.host}`) ||
          (req.headers.origin && !local(req.headers.origin)) || forwards.some(k => k in headers)) {
        res.writeHead(403); res.end('local development mailbox only'); return;
      }
      try {
        const content = readFileSync(capability, 'utf8');
        headers['x-consilith-dev'] = content.split('\n').find(l => l.startsWith('CONSILITH_DEV_CAPABILITY='))?.split('=')[1] || '';
      } catch { /* backend fails closed until its capability is available */ }
    }
    const upstream = request(new URL(url.pathname.slice(4) + url.search, room),
      { method: req.method, headers }, incoming => {
        res.writeHead(incoming.statusCode || 502, incoming.headers); incoming.pipe(res);
      });
    upstream.on('error', () => { if (!res.headersSent) res.writeHead(502); res.end('room unavailable'); });
    res.on('close', () => upstream.destroy());
    req.pipe(upstream); return;
  }
  if (!['GET', 'HEAD'].includes(req.method || '')) { res.writeHead(405); res.end(); return; }
  let path;
  try { path = resolve(root, `.${decodeURIComponent(url.pathname)}`); } catch { res.writeHead(400); res.end(); return; }
  const child = relative(root, path);
  if (child.startsWith('..') || isAbsolute(child)) { res.writeHead(403); res.end(); return; }
  try { if (!statSync(path).isFile()) path = `${root}/index.html`; } catch { path = `${root}/index.html`; }
  res.writeHead(200, { 'Content-Type': types[extname(path)] || 'application/octet-stream',
    'Cache-Control': path.includes('/assets/') ? 'public, max-age=31536000, immutable' : 'no-cache',
    'X-Content-Type-Options': 'nosniff' });
  if (req.method === 'HEAD') { res.end(); return; }
  createReadStream(path).on('error', () => res.destroy()).pipe(res);
});
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  createGateway({ room: process.env.CONSILITH_ROOM_URL || 'http://room:8080' }).listen(8080, '0.0.0.0');
}
