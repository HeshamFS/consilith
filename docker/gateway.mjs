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

// What the caller sent after /api, checked as written and after each round of percent-decoding
// (a proxy in front or the room itself may decode again). It must be one absolute path on the room:
// a single leading "/", no backslash, no control character, no "scheme:" first segment.
function plainPath(path) {
  if (path[0] !== '/' || path[1] === '/' || path[1] === '\\') return false;
  if (/[\\\u0000-\u001f\u007f]/.test(path)) return false;
  return !path.split('/')[1].includes(':');
}

/**
 * The room URL for a gateway request target such as "/api/rooms/x?after=3", or null when the
 * target is not a plain path on the room. This is the only place a request path becomes an
 * upstream address, so the origin can never come from the caller.
 */
export function upstreamUrl(target, room) {
  if (typeof target !== 'string' || !target.startsWith('/api/')) return null;
  const cut = target.indexOf('?');
  const path = target.slice(4, cut < 0 ? undefined : cut);
  const query = cut < 0 ? '' : target.slice(cut);
  let seen = path;
  for (let round = 0; ; round++) {
    if (!plainPath(seen)) return null;
    let next;
    // A later round that fails to decode holds a literal "%", not an escape: it is stable.
    try { next = decodeURIComponent(seen); } catch { if (round === 0) return null; break; }
    if (next === seen) break;
    if (round >= 4) return null;
    seen = next;
  }
  const base = new URL(room);
  let resolved;
  try { resolved = new URL(path + query, base); } catch { return null; }
  if (resolved.origin !== base.origin || resolved.username || resolved.password) return null;
  return resolved;
}

// Hosted mode (decision 22): CONSILITH_ENV=hosted or CONSILITH_HOSTED=1. Then the gateway never
// serves the development mailbox and never adds the development capability header.
export function isHosted(env = process.env) {
  return env.CONSILITH_ENV === 'hosted' || ['1', 'true', 'yes', 'on'].includes((env.CONSILITH_HOSTED || '').toLowerCase());
}

export function createGateway({ root = '/app/public', room = 'http://room:8080', capability = '/gateway-capability/dev.env', hosted = isHosted() } = {}) {
root = resolve(root);
return createServer((req, res) => {
  const url = new URL(req.url || '/', 'http://localhost');
  if (url.pathname === '/health') { res.writeHead(200); res.end('ok'); return; }
  if (url.pathname.startsWith('/api/')) {
    const target = upstreamUrl(req.url || '', room);
    if (!target) { res.writeHead(400); res.end('bad upstream path'); return; }
    const headers = { ...req.headers };
    // Never let a caller supply the gateway's capability; it is read from its own volume.
    delete headers['x-consilith-dev'];
    if (url.pathname.startsWith('/api/dev/')) {
      if (hosted) { res.writeHead(404); res.end(); return; }
      if (!local(`http://${req.headers.host}`) ||
          (req.headers.origin && !local(req.headers.origin)) || forwards.some(k => k in headers)) {
        res.writeHead(403); res.end('local development mailbox only'); return;
      }
      try {
        const content = readFileSync(capability, 'utf8');
        headers['x-consilith-dev'] = content.split('\n').find(l => l.startsWith('CONSILITH_DEV_CAPABILITY='))?.split('=')[1] || '';
      } catch { /* backend fails closed until its capability is available */ }
    }
    const upstream = request(target,
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
  createGateway({ room: process.env.CONSILITH_ROOM_URL || 'http://room:8080', hosted: isHosted() }).listen(8080, '0.0.0.0');
}
