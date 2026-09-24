# Tavonza JARVIS Web Console

A self-contained Node.js hospitality operations console. It uses only Node.js built-ins and static HTML/CSS/JavaScript—there are no npm dependencies to install.

## Run

From this `web/` directory, with the Tavonza API running on port 8000:

```powershell
node server.js
```

or:

```powershell
npm start
```

Open <http://127.0.0.1:3000>.

For automatic restarts while editing the console:

```powershell
npm run dev
```

No `npm install` is required.

## Configuration

All settings are optional:

| Variable | Default | Purpose |
| --- | --- | --- |
| `HOST` | `127.0.0.1` | Console bind address |
| `PORT` | `3000` | Console port |
| `BACKEND_URL` | `http://127.0.0.1:8000` | Fixed upstream API origin (and optional path prefix) |
| `BACKEND_TIMEOUT_MS` | `120000` | Upstream inactivity timeout |
| `MAX_BODY_BYTES` | `10485760` | Maximum proxied request body size |

PowerShell example:

```powershell
$env:HOST = "0.0.0.0"
$env:PORT = "3000"
$env:BACKEND_URL = "http://127.0.0.1:8000"
npm start
```

The same-origin proxy forwards only `/api`, `/v1`, `/health`, and `/ready`. It forwards `Authorization`, `Content-Type`, request bodies, and selected request metadata, then streams upstream responses without buffering.

## Access

Operational routes are authenticated. The console supports two local workflows:

- **Bearer token**: paste a JWT issued by the backend. The token is sent as `Authorization: Bearer <token>`.
- **Local bootstrap**: enter the exact `DEV_AUTH_TOKEN` configured in the FastAPI `.env`, choose a branch/role, and the backend will issue a short-lived local JWT through `/api/v1/auth/dev-token`.

The bootstrap endpoint is disabled in production and the console never treats an empty token as an authenticated session. Tokens are kept only in `sessionStorage`; they are never written into the URL or rendered as API content.

For the QR checkbox flow, set `ALLOW_TEST_QR=true` only in the local `.env`. The checkbox is rejected by the backend when the setting is disabled. The form also accepts an optional `qr_token`, but real QR verification remains disabled until `ALLOW_REAL_QR=true` is configured after the backend QR provider is connected.

## Production notes

The server defaults to loopback binding, validates `BACKEND_URL`, sends security headers, uses a fixed allowlist for proxy routes/headers, limits request body size, and resolves static files with traversal and symlink checks. For deployment, terminate TLS and any host-based access control at a trusted reverse proxy, provide configuration through the process environment, and use a real issued token. The app is a single-process development/static console; put it behind process supervision and load balancing if it becomes a high-availability service.
