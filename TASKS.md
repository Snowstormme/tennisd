# Tasks

## Active

- [ ] **Remove request-time schema probing** - Replace the remaining `has_table` checks with migration-backed capability/configuration checks.

## Waiting On

## Someday

- [ ] **Synchronize repository state and verify main** - Confirm that the working copy contains the expected merge commit `a23baca`; review the discrepancy with local `2127e6b` and update all GitHub Actions references consistently.
- [x] ~~Introduce versioned database migrations~~ (2026-10-31) - Ordered migrations and a migration ledger now cover the baseline, moderation audit, and notifications tables.
- [x] ~~Add PostgreSQL integration coverage in CI~~ (2026-10-31) - CI provisions PostgreSQL and runs the smoke/bootstrap checks.
- [x] ~~Make local and CI environments reproducible~~ (2026-10-31) - Development requirements and CI installation now include the complete declared test/tooling stack.
- [x] ~~Add live-sync observability~~ (2026-10-31) - Ingestion runs, safe `/api/sync-status`, and a live-page freshness indicator expose provider state without secrets.
- [x] ~~Make rate limiting atomic under concurrency~~ (2026-10-31) - PostgreSQL advisory locking protects the count-and-insert window.
- [x] ~~Split the Flask route module~~ (2026-10-31) - Live, auth, news, profile, moderation, social, and community routes are now focused modules.
- [x] ~~Improve live match freshness~~ (2026-10-31) - Live scores poll every three minutes and the page reports feed freshness/outage state.

## Done

- [x] ~~Version data exports~~ (2026-10-31) - JSON exports now include `schema_version: 1`.
- [x] ~~Expand moderation auditability~~ (2026-10-31) - Moderation actions now record actor, action, target, details and timestamp.
- [x] ~~Add followed-player and tournament notifications~~ (2026-10-31) - In-app alerts are created by live-sync with daily deduplication.
- [x] ~~Add ranking history and player-form views~~ (2026-10-31) - Player pages now show loaded singles ranking snapshots and best rank.
