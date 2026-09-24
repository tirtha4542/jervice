# AI integration contract

## Local route-only flow

The AI layer does not query the application database for live context. It calls:

```text
GET /api/v1/operations/context?branch_id={branch}&table_session_id={id?}
Authorization: Bearer <user JWT>
```

The backend operations route is responsible for:

- authenticating the actor;
- enforcing branch and resource scope;
- applying permissions;
- shaping a minimal context payload;
- returning only data safe for the requested role.

The local Node console proxies requests to FastAPI. The local `/api/v1/auth/dev-token` route issues a short-lived JWT only when `DEV_AUTH_TOKEN` is configured and the app is not running in production.

## QR test mode

The local join form sends:

```json
{
  "otp": "123456",
  "test_qr_verified": true
}
```

The backend accepts the checkbox only when `ALLOW_TEST_QR=true`. A real deployment should disable the flag and set `ALLOW_REAL_QR=true` only after replacing `_verify_qr()` with a signed QR-token and OTP verifier.

The request also accepts `qr_token`, so the future real QR resolver can be added without changing the UI contract.

## Backend responsibilities

The backend developer should preserve these route guarantees:

1. Every operational route requires a valid bearer token.
2. The authenticated branch is authoritative; request branch values are not trusted.
3. Resource IDs are checked against the actor's organization/branch/table scope.
4. Context responses are bounded and free of unnecessary payment/PII fields.
5. AI is read-only unless a separately confirmed command tool is explicitly introduced.
6. Route errors use consistent `401`, `403`, `404`, `409`, and `429` semantics.

## Configuration

```dotenv
BACKEND_API_URL=http://127.0.0.1:8000
BACKEND_API_TOKEN=
BACKEND_TIMEOUT_SECONDS=8
```

When the backend moves to the cloud, change only `BACKEND_API_URL` and provide a valid user/service token through the deployment environment.
