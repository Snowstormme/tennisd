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

Dynamic widths, heights, and rating tones are carried in `data-*` attributes in
`tennisd/templates/player.html`, `tennisd/templates/match.html`, and
`tennisd/templates/profile.html`. `public/static/dynamic-styles.js` applies
those values through CSSOM, so the production CSP omits `style-src
'unsafe-inline'`. If AdSense is enabled later, verify its widget in preview;
only re-add `unsafe-inline` to `style-src` if the widget demonstrably requires
it, and document that exception here. There are no inline script blocks, and
`script-src` does not allow `unsafe-inline`.

## AdSense and CSP

When advertising is connected, do not restore `unsafe-inline` to `style-src`
as a whole. Allow only the specific Google origins required by the widget in
`script-src`, `frame-src`, and `img-src`, then check production and preview
for `Refused` errors in the browser console. The current production CSP
already lists `pagead.googlesyndication.com`, `googleads.g.doubleclick.net`,
`tpc.googlesyndication.com`, and `fundingchoicesmessages.google.com` for the
relevant directives, but advertising is not currently connected, so no ad
slot is rendered.
