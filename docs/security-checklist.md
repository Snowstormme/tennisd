# Tennisd security checklist

Run these checks against the deployed Render service after each security-sensitive deployment:

```bash
curl -I https://tennisd.onrender.com
```

Expect these headers: `Strict-Transport-Security`, `Content-Security-Policy`,
`X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, and
`Permissions-Policy`.

```bash
curl -i https://tennisd.onrender.com/.env
curl -i https://tennisd.onrender.com/.git/config
```

Both responses must be `404` and must not contain secrets.

```bash
curl -i -H "Origin: https://evil.example" \
  https://tennisd.onrender.com/api/live-matches
```

The response must not contain `Access-Control-Allow-Origin`.

```bash
curl -sS https://tennisd.onrender.com/api/live-matches | grep -q 'password_hash' \
  && echo 'FAIL: password_hash exposed' \
  || echo 'PASS: password_hash absent'
```

## Database role verification

Run locally with the runtime database URL in the environment. The verifier never
prints the URL or its password:

```bash
DATABASE_URL='[REDACTED]' python scripts/verify_database_role.py
```

The connection must use `sslmode=require`, `verify-ca`, or `verify-full`. The
runtime role must not be a superuser, have `CREATEDB`/`CREATEROLE`, create schema
objects, or own application tables.

## Rotating `SECRET_KEY` on Render

1. Generate a new random value of at least 64 characters locally.
2. Replace the `SECRET_KEY` environment variable on the Render web service.
3. Redeploy the service.
4. Expect all existing sessions to be invalidated; users must log in again.

Rotation is required if the historical hard-coded fallback was ever active in a
production deployment.

## CSP style note

The templates currently contain five dynamic `style=` attributes in
`templates/player.html`, `templates/match.html`, and `templates/profile.html`.
They carry calculated widths, heights, and a per-rating CSS variable. Replacing
them with fixed classes would either lose those values or require a larger
layout rewrite, so `style-src 'unsafe-inline'` remains intentionally scoped to
styles. There are no inline script blocks, and `script-src` does not allow
`unsafe-inline`.
