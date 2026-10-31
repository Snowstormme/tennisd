# Tasks

## Active

## Waiting On

## Someday

- [ ] **Synchronize repository state and verify main** - Confirm that the working copy contains the expected merge commit `a23baca`; review the discrepancy with local `2127e6b` and update all GitHub Actions references consistently.
- [ ] **Introduce versioned database migrations** - Replace ad hoc `db.create_all()` and one-off SQL workflows with ordered, reviewable migrations; preserve the restricted runtime role model.
- [ ] **Add PostgreSQL integration coverage in CI** - Run schema/bootstrap, migration, role, and representative request tests against PostgreSQL in GitHub Actions.
- [ ] **Make local and CI environments reproducible** - Add a development dependency/bootstrap path and a lock or constraints file; ensure a clean install includes `defusedxml` and the complete test stack.
- [ ] **Add live-sync observability** - Record last successful sync, data age, imported counts, provider errors, and expose a safe operational status view; monitor `/readyz` in addition to `/healthz`.
- [ ] **Make rate limiting atomic under concurrency** - Replace count-then-insert behavior with an atomic PostgreSQL-safe limiter or a dedicated edge/shared limiter.
- [ ] **Split the Flask route module** - Separate catalog, live matches, authentication, profiles, moderation, feedback, and sitemap concerns into smaller blueprints/services.
- [ ] **Remove request-time schema probing** - Replace repeated `inspect(db.engine).has_table(...)` checks with migration-backed capability/configuration checks.
- [ ] **Add ranking history and player-form views** - Show historical ranking movement and transparent recent-form statistics based on loaded data.
- [ ] **Improve live match freshness** - Move beyond the daily snapshot when provider capacity and operating cost justify it; show provider freshness and outage state clearly.

## Done

- [x] ~~Version data exports~~ (2026-10-31) - JSON exports now include `schema_version: 1`.
- [x] ~~Expand moderation auditability~~ (2026-10-31) - Moderation actions now record actor, action, target, details and timestamp.
- [x] ~~Add followed-player and tournament notifications~~ (2026-10-31) - In-app alerts are created by live-sync with daily deduplication.
