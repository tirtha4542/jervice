'use strict';

const fs = require('node:fs');
const fsp = require('node:fs/promises');
const http = require('node:http');
const https = require('node:https');
const path = require('node:path');
const { Transform, pipeline } = require('node:stream');

const ROOT = __dirname;
const PUBLIC_ROOT = path.resolve(ROOT, 'public');
const DEFAULT_BACKEND_URL = 'http://127.0.0.1:8000';

function integerFromEnv(name, fallback, { min, max }) {
  const raw = process.env[name];
  if (raw === undefined || raw === '') return fallback;
  const value = Number(raw);
  if (!Number.isInteger(value) || value < min || value > max) {
    throw new Error(`${name} must be an integer between ${min} and ${max}.`);
  }
  return value;
}

const HOST = process.env.HOST || '127.0.0.1';
const PORT = integerFromEnv('PORT', 3000, { min: 1, max: 65535 });
const MAX_BODY_BYTES = integerFromEnv('MAX_BODY_BYTES', 10 * 1024 * 1024, {
  min: 1024,
  max: 100 * 1024 * 1024,
});
const BACKEND_TIMEOUT_MS = integerFromEnv('BACKEND_TIMEOUT_MS', 120000, {
  min: 1000,
  max: 600000,
});

function validatedBackendUrl(value) {
  const url = new URL(value || DEFAULT_BACKEND_URL);
  if (!['http:', 'https:'].includes(url.protocol)) {
    throw new Error('BACKEND_URL must use http:// or https://.');
  }
  if (url.username || url.password || url.hash) {
    throw new Error('BACKEND_URL must not contain credentials or a fragment.');
  }
  return url;
}

const BACKEND_URL = validatedBackendUrl(process.env.BACKEND_URL);
const BACKEND_BASE_PATH = BACKEND_URL.pathname.replace(/\/$/, '');

const MIME_TYPES = Object.freeze({
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.ico': 'image/x-icon',
  '.jpeg': 'image/jpeg',
  '.jpg': 'image/jpeg',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml; charset=utf-8',
  '.txt': 'text/plain; charset=utf-8',
  '.webp': 'image/webp',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
});

const FORWARDED_HEADERS = new Set([
  'accept',
  'accept-language',
  'authorization',
  'cache-control',
  'content-length',
  'content-type',
  'pragma',
  'x-request-id',
  'x-dev-bootstrap',
]);

const HOP_BY_HOP_HEADERS = new Set([
  'connection',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
]);

function setSecurityHeaders(res) {
  res.setHeader('Content-Security-Policy', [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self' data:",
    "connect-src 'self'",
    "font-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "frame-ancestors 'none'",
    "form-action 'self'",
  ].join('; '));
  res.setHeader('Referrer-Policy', 'no-referrer');
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('X-Frame-Options', 'DENY');
  res.setHeader('Cross-Origin-Opener-Policy', 'same-origin');
  res.setHeader('Permissions-Policy', 'camera=(), microphone=(), geolocation=()');
}

function sendError(res, statusCode, message, requestId) {
  if (res.headersSent || res.destroyed) {
    if (!res.destroyed) res.destroy();
    return;
  }
  setSecurityHeaders(res);
  const payload = JSON.stringify({
    error: message,
    request_id: requestId,
  });
  res.writeHead(statusCode, {
    'Cache-Control': 'no-store',
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(payload),
  });
  res.end(payload);
}

function isWithin(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative === '' || (
    relative !== '..'
    && !relative.startsWith(`..${path.sep}`)
    && !path.isAbsolute(relative)
  );
}

function isProxyPath(pathname) {
  return pathname === '/health'
    || pathname === '/ready'
    || pathname === '/api'
    || pathname.startsWith('/api/')
    || pathname === '/v1'
    || pathname.startsWith('/v1/');
}

function upstreamTarget(requestUrl) {
  const incoming = new URL(requestUrl, 'http://console.invalid');
  const target = new URL(BACKEND_URL);
  target.pathname = `${BACKEND_BASE_PATH}${incoming.pathname}` || '/';
  target.search = incoming.search;
  return target;
}

function proxyHeaders(req) {
  const headers = {};
  for (const [name, value] of Object.entries(req.headers)) {
    if (value !== undefined && FORWARDED_HEADERS.has(name.toLowerCase())) {
      headers[name] = value;
    }
  }
  headers.host = BACKEND_URL.host;
  headers['x-forwarded-host'] = req.headers.host || '';
  headers['x-forwarded-proto'] = 'http';
  const remoteAddress = req.socket.remoteAddress || '';
  if (remoteAddress) headers['x-forwarded-for'] = remoteAddress;
  return headers;
}

function bodyLimitStream(requestId) {
  let bytes = 0;
  return new Transform({
    transform(chunk, encoding, callback) {
      bytes += chunk.length;
      if (bytes > MAX_BODY_BYTES) {
        const error = new Error(`Request body exceeds ${MAX_BODY_BYTES} bytes.`);
        error.code = 'BODY_TOO_LARGE';
        error.requestId = requestId;
        callback(error);
        return;
      }
      callback(null, chunk);
    },
  });
}

function proxyRequest(req, res, requestId) {
  const target = upstreamTarget(req.url || '/');
  const transport = target.protocol === 'https:' ? https : http;
  const declaredLength = Number(req.headers['content-length'] || 0);

  if (Number.isFinite(declaredLength) && declaredLength > MAX_BODY_BYTES) {
    sendError(res, 413, `Request body exceeds ${MAX_BODY_BYTES} bytes.`, requestId);
    return;
  }

  let proxyFinished = false;
  const upstream = transport.request(
    target,
    {
      method: req.method,
      headers: proxyHeaders(req),
    },
    (upstreamResponse) => {
      proxyFinished = true;
      const responseHeaders = {};
      for (const [name, value] of Object.entries(upstreamResponse.headers)) {
        if (value !== undefined && !HOP_BY_HOP_HEADERS.has(name.toLowerCase())) {
          responseHeaders[name] = value;
        }
      }
      responseHeaders['x-request-id'] = requestId;
      setSecurityHeaders(res);
      res.writeHead(upstreamResponse.statusCode || 502, responseHeaders);
      upstreamResponse.on('error', (error) => {
        if (!res.writableEnded) res.destroy(error);
      });
      upstreamResponse.pipe(res);
    },
  );

  upstream.setTimeout(BACKEND_TIMEOUT_MS, () => {
    upstream.destroy(new Error(`Backend timed out after ${BACKEND_TIMEOUT_MS} ms.`));
  });

  upstream.on('error', (error) => {
    if (proxyFinished || res.writableEnded) return;
    proxyFinished = true;
    if (res.headersSent || res.destroyed) {
      if (!res.destroyed) res.destroy(error);
      return;
    }
    const statusCode = error.code === 'BODY_TOO_LARGE' ? 413 : 502;
    const message = statusCode === 413
      ? error.message
      : `Backend request failed (${error.code || 'connection error'}).`;
    sendError(res, statusCode, message, requestId);
  });

  res.on('close', () => {
    if (!res.writableEnded) {
      proxyFinished = true;
      upstream.destroy();
    }
  });

  if (req.method === 'GET' || req.method === 'HEAD') {
    upstream.end();
    return;
  }

  const limiter = bodyLimitStream(requestId);
  limiter.once('error', (error) => {
    if (proxyFinished || res.writableEnded) return;
    proxyFinished = true;
    sendError(res, 413, error.message, requestId);
  });
  pipeline(req, limiter, upstream, () => {
    // Request and upstream errors are handled by their dedicated listeners.
  });
}

async function resolveStaticFile(pathname) {
  let decoded;
  try {
    decoded = decodeURIComponent(pathname);
  } catch {
    return { error: 400, message: 'Malformed URL path.' };
  }

  if (decoded.includes('\0')) {
    return { error: 400, message: 'Malformed URL path.' };
  }

  const relativePath = decoded.replace(/^[/\\]+/, '') || 'index.html';
  const candidate = path.resolve(PUBLIC_ROOT, relativePath);
  if (!isWithin(PUBLIC_ROOT, candidate)) {
    return { error: 403, message: 'Forbidden.' };
  }

  try {
    const [realRoot, realCandidate] = await Promise.all([
      fsp.realpath(PUBLIC_ROOT),
      fsp.realpath(candidate),
    ]);
    if (!isWithin(realRoot, realCandidate)) {
      return { error: 403, message: 'Forbidden.' };
    }
    const stat = await fsp.stat(realCandidate);
    if (!stat.isFile()) {
      return { error: 404, message: 'Not found.' };
    }
    return { file: realCandidate, stat };
  } catch (error) {
    if (error && (error.code === 'ENOENT' || error.code === 'ENOTDIR')) {
      return { error: 404, message: 'Not found.' };
    }
    throw error;
  }
}

async function serveStatic(req, res, pathname, requestId) {
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    res.setHeader('Allow', 'GET, HEAD');
    sendError(res, 405, 'Method not allowed.', requestId);
    return;
  }

  const result = await resolveStaticFile(pathname);
  if (result.error) {
    sendError(res, result.error, result.message, requestId);
    return;
  }

  const { file, stat } = result;
  const etag = `W/\"${stat.size.toString(16)}-${stat.mtimeMs.toString(16)}\"`;
  if (req.headers['if-none-match'] === etag) {
    setSecurityHeaders(res);
    res.writeHead(304, { ETag: etag, 'Cache-Control': 'no-cache' });
    res.end();
    return;
  }

  const extension = path.extname(file).toLowerCase();
  setSecurityHeaders(res);
  res.writeHead(200, {
    'Cache-Control': 'no-cache',
    'Content-Length': stat.size,
    'Content-Type': MIME_TYPES[extension] || 'application/octet-stream',
    'Last-Modified': stat.mtime.toUTCString(),
    ETag: etag,
  });

  if (req.method === 'HEAD') {
    res.end();
    return;
  }

  const stream = fs.createReadStream(file);
  stream.on('error', (error) => {
    if (!res.headersSent) sendError(res, 500, 'Unable to read static file.', requestId);
    else res.destroy(error);
  });
  stream.pipe(res);
}

const server = http.createServer(async (req, res) => {
  const requestId = req.headers['x-request-id'] || `web-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
  res.setHeader('X-Request-Id', requestId);

  try {
    const parsed = new URL(req.url || '/', 'http://console.invalid');
    if (isProxyPath(parsed.pathname)) {
      proxyRequest(req, res, requestId);
      return;
    }
    await serveStatic(req, res, parsed.pathname, requestId);
  } catch (error) {
    console.error(`[${requestId}] request failed:`, error);
    sendError(res, 500, 'Internal console server error.', requestId);
  }
});

server.on('clientError', (error, socket) => {
  if (socket.writable) {
    socket.end('HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n');
  }
});

server.requestTimeout = 130000;
server.headersTimeout = 65000;
server.keepAliveTimeout = 5000;

server.listen(PORT, HOST, () => {
  console.log(`Tavonza JARVIS Console: http://${HOST}:${PORT}`);
  console.log(`Proxying /api, /v1, /health, and /ready to ${BACKEND_URL.origin}${BACKEND_BASE_PATH}`);
});

let shuttingDown = false;
function shutdown(signal) {
  if (shuttingDown) return;
  shuttingDown = true;
  console.log(`\n${signal} received; closing console server.`);
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(1), 10000).unref();
}

process.on('SIGINT', () => shutdown('SIGINT'));
process.on('SIGTERM', () => shutdown('SIGTERM'));
