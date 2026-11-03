# First-request latency

The repository's Render blueprint specifies `plan: free`. Render documents a
15-minute idle spin-down and roughly one minute to spin back up:
https://render.com/docs/free#spinning-down-on-idle

This is consistent with previously observed first-request timeouts followed by
fast replies, but is not proof of their cause. The active dashboard plan and
historical hosting logs must be checked to confirm it. Code cannot remove a
platform spin-up delay. An always-on compute plan is a separate billing decision.

## Distinguish hosting/network delay from application work

Inspect `Server-Timing: app;dur=…` in a fresh response. The number is application
processing milliseconds, including database acquisition and view rendering. It
excludes hosting startup, proxy queues, transport, and streaming the response
body. Cached responses can carry historical timing and request IDs; compare
fresh no-store `/healthz` (no database) and `/readyz` (SELECT 1) first, then HTML.
Do not treat client wall time minus app duration as proof of a particular host
or network failure.

Requests taking at least two seconds inside the app produce a warning with only
endpoint name, status, duration and request ID. No URL, query, form, header or SQL
content is logged by this instrumentation. Normal access logging stays disabled.

## Pool exhaustion

Render retains its existing maximum of three database connections per worker.
Pool acquisition now waits at most five seconds (previously the SQLAlchemy
default thirty). A real exhausted pool returns safe HTTP 503, Retry-After: 10,
no-store, and rolls back the request session. This permits retries without
automatically replaying writes. Connection establishment and SQL execution have
separate limits; this is not a five-second deadline for the entire request.

The single Gunicorn worker still has four threads. Under slow database load one
thread can wait for the three others; the shorter bound intentionally trades a
long wait for an earlier retryable response. Do not increase database connection
limits or buy hosting capacity without checking load and account constraints.

Rollback: revert the release and redeploy; no database migration is involved.
Investigate new 503 spikes or broken existing request/error headers before
adjusting thresholds. Tests exercise a genuinely exhausted one-connection pool
and confirm recovery after its connection is released.
