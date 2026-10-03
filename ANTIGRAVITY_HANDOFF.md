# Tennisd — complete handoff for Antigravity AI

Updated: 2026-09-30  
Production: https://tennisd.vercel.app  
Repository: https://github.com/Snowstormme/tennisd  
Default branch: `main`  
Verified handoff commit: `4e5206d` (`Compact search results and showcase matches`)

## Copy this prompt into Antigravity AI

```text
You are continuing the existing Tennisd project. Work in the existing repository and preserve its architecture, provider provenance, security controls, visual language, and deployed behavior.

Before changing anything:
1. Read ANTIGRAVITY_HANDOFF.md, README.md, SECURITY.md, DESIGN.md and .env.example.
2. Inspect git status and recent commits. Do not overwrite uncommitted work.
3. Read tennisd/__init__.py, tennisd/models.py, tennisd/routes.py and the files directly related to the requested feature.
4. Run the existing test suite with `.venv/bin/python -m pytest -q` (or `python -m unittest discover -s tests -v` in a clean environment).
5. Never put secrets, database URLs, API keys, user data, dumps, or .env files in Git or chat output.

Implementation rules:
- Extend the current Flask/Jinja/SQLAlchemy application. Do not rebuild it in another framework unless the owner explicitly requests that.
- Keep SQLite usable for clean local development and PostgreSQL/Neon for production.
- Production schema changes must be explicit, rerunnable migrations/scripts. The Vercel web process must never create schema.
- Preserve CSRF, rate limits, password hashing, email verification, host validation, CSP/security headers, privacy boundaries, and the restricted `rallylog_web` database role.
- Preserve provider provenance for imported match data and keep API keys server-side.
- Live matches must transition upcoming → live → finished/cancelled without deleting their provider record.
- Rankings are latest positions present in the loaded catalog, not guaranteed official live rankings. Label them honestly.
- News may show publisher metadata, images where permitted, and extracted previews. Do not republish copyrighted full articles without a license.
- Reuse the current dark green, cream and lime visual system. Reuse existing match/player components and responsive breakpoints.
- Add or update meaningful tests for behavior changes. Run all tests and `git diff --check` before committing.
- After pushing, wait for Vercel, verify the affected production URLs plus `/healthz`, and report the commit hash.

Current task from the owner follows below. First inspect the relevant current implementation, then implement it completely.
```

## 1. Product summary

Tennisd is an English-language tennis match diary and community database inspired by Letterboxd/Serializd style products. Users discover professional tennis matches, record watched matches, rate them from 1–10, write reviews, comment, favorite matches, keep a watchlist, follow players and tournaments, add friends, and view personal statistics.

The intended long-term product is a global tennis database and community platform covering ATP, WTA, Grand Slams, Masters/1000, 500, 250, Challenger and ITF data. The current deployment is an early public version running on free/low-cost services.

## 2. Current stack and hosting

| Area | Current implementation |
|---|---|
| Web application | Python 3.13, Flask 3, Jinja templates |
| ORM/auth | Flask-SQLAlchemy, Flask-Login |
| Passwords | Argon2id; legacy Werkzeug hashes upgrade after login |
| Images | Pillow; profile images converted to WebP and stored in PostgreSQL |
| Local database | SQLite at `instance/tennisd.db` |
| Production database | Neon PostgreSQL over TLS |
| Production web host | Vercel at `https://tennisd.vercel.app` |
| Live feed | Live Tennis API, fetched only by GitHub Actions |
| Historical data | Tennis API on RapidAPI, fetched only by GitHub Actions |
| News discovery | Google News RSS pointing to ATP, WTA, ITF, Wimbledon, BBC, ESPN, Sky and Tennis365 |
| Transactional email | Resend HTTPS API |
| CI/operations | GitHub Actions |
| Tests | `unittest` test cases, runnable through pytest; 31 passing at handoff |

Render configuration is retained as an alternative, but production currently uses Vercel + Neon.

## 3. Repository layout

```text
wsgi.py                         Flask entry point
requirements.txt                Python dependencies
render.yaml                     alternative Render deployment
.env.example                    environment variable names only
README.md                       main setup/deployment documentation
SECURITY.md                     vulnerability reporting and security notes
DESIGN.md                       visual/product direction

tennisd/__init__.py             app factory, config, security headers, CLI commands
tennisd/models.py               all SQLAlchemy models
tennisd/routes.py               pages, APIs and state-changing form handlers
tennisd/security.py             email, tokens, abuse limits and auth helpers
tennisd/importer.py             local demo seed for tests and development
tennisd/live_tennis.py          live/upcoming API normalization and persistence
tennisd/tennis_api.py           free Tennis API historical-result sync
tennisd/stats.py                player/profile/community statistics
tennisd/tournament_catalog.py   tournament profiles, tier mapping, slugs
tennisd/news_feed.py            RSS aggregation and article preview extraction
tennisd/prize_money.py          optional Wikidata prize-money lookup

tennisd/templates/              Jinja pages and macros
public/static/style.css         full responsive design system
public/static/app.js            browser behaviors/live refresh
public/static/images/           checked-in static fallback images

scripts/                        SQL migrations and operational scripts
tests/                          app, live-data and security tests
.github/workflows/              CI, imports, migrations, sync and backup jobs
```

## 4. Existing public pages and behavior

### Discovery and catalog

- `/` — featured carousel, photographic archive match cards, recent public community reviews.
- `/matches` — historical match filters plus live, upcoming and recently finished live-feed cards.
- `/matches/<match_id>` — score, metadata, serve statistics, community rating, reviews, comments, log/watchlist actions.
- `/live-matches/<provider_id>` — live/upcoming/finished provider match details.
- `/api/live-matches` — JSON for browser refresh.
- `/players` — one combined directory with internal `Players` and `Rankings` tabs. `?view=rankings` switches mode. Both modes have name search and All/ATP/WTA filters.
- `/rankings` — permanent redirect to `/players?view=rankings`.
- `/players/<player_id>` — player biography, catalog record, surfaces, seasons, titles, serve metrics and opponents.
- `/players/<player_id>/photo` — stored photo, Wikidata/Wikipedia fallback, then generated placeholder.
- `/tournaments` — tournament browser with importance/tier information and surface styling.
- `/tournaments/<tour>/<slug>` — tournament overview, editions, leaders and subscription action.
- `/tournaments/<tour>/<slug>/<season>` — edition/draw page.
- `/search` — compact unified results for players, matches and members. Match results reuse photographic home-page cards.
- `/news` — source filters and curated current feed.
- `/news/<source>/<story_id>` — on-site preview/reader with source link.

### Accounts and community

- Registration, login/logout, email verification code flow, resend verification, password reset.
- `/me`, `/u/<username>` — profile, compact Letterboxd-inspired navigation, top favorites, diary and rating distribution.
- `/settings` — display name, bio, password and profile photo.
- Diary entries: viewing date, rating 1–10, review body, favorite, spoiler, public/private.
- Watchlist, likes/favorites, player follows, tournament subscriptions.
- Friend requests through notifications; no private messaging.
- Comments/replies on reviews.
- User export and account deletion.
- Reporting and administrator moderation queue.

### Search engine files

- `/robots.txt`
- `/sitemap.xml` plus core, players, tournaments and paged match sitemaps.
- Two Google Search Console verification files in `public/`.

## 5. Database model map

The source of truth is `tennisd/models.py`.

### Identity/security

- `User`
- `ProfileImage`
- `AuthState`
- `AuthToken`
- `RateLimitEvent`

### Tennis catalog

- `Player`
- `PlayerExternalId`
- `PlayerPhoto`
- `Tournament`
- `TournamentEdition`
- `PrizeMoneyAward`
- `Match`
- `RankingSnapshot`
- `MatchStatistic`
- `MatchParticipant`
- `MatchSet`
- `IngestionRun`
- `LiveMatch`

### Community

- `Review`
- `Comment`
- `Report`
- `FollowedPlayer`
- `TournamentSubscription`
- `WatchlistItem`
- `Friendship`

Important invariants:

- `Review` is unique per user/match and rating is null or integer 1–10.
- `RankingSnapshot` is unique per player/date/type.
- Tournaments are unique by tour + slug; editions by tournament + season.
- Live provider records use `LiveMatch.provider_id` as permanent identity.
- State-changing actions require a session CSRF token.
- Private reviews/watchlists must not leak to another user's profile.

## 6. Data flows

### Historical results

Historical ATP and WTA results are imported through Tennis API on RapidAPI.

- Workflow: `.github/workflows/sync-tennis-api-history.yml`.
- Script: `scripts/sync_tennis_api.py`.
- Importer: `tennisd/tennis_api.py`.
- Cursor table: `ingestion_cursor`.
- GitHub secret: `RAPIDAPI_TENNIS_KEY`.
- The free plan is budgeted at 48 requests per run.
- The importer refreshes recent finals, then backfills 7-day historical chunks down to 2010.
- It writes tournaments, editions, matches, participants and provider IDs as part of the import.
- Live-feed finished matches are deduplicated by provider ID and by same tour/date/tournament/player pair.

### Live/upcoming matches

- Provider: Live Tennis API (`https://api.livetennisapi.com/api/public/v1`).
- GitHub workflow: `sync-live-matches.yml` every 15 minutes.
- API key stays only in GitHub Actions.
- Live sync normally fetches one page to stay near the free 100 requests/day allowance.
- Upcoming fetch may read two pages and is limited to seven days.
- Supported tier tuple currently includes Grand Slams, ATP/WTA 1000/500/250, WTA 125, Challenger and ITF tiers.
- When a live record disappears from the provider response it becomes `finished`, keeps the same provider ID and stored score, and is not deleted.
- This is near-live polling, not point-by-point streaming.
- The free provider plan does not provide unrestricted historical completed-match ingestion.

### Rankings

Rankings shown today are the latest rank captured from loaded match data. They are not guaranteed to be current official ATP/WTA rankings. Any future official ranking source needs licensing/API review and must retain source/date fields.

### Player images

Resolution order in `routes.py`:

1. primary `PlayerPhoto` database record;
2. linked Wikidata P18 image;
3. exact English Wikipedia tennis-person result;
4. Wikidata name search constrained to tennis;
5. generated local placeholder.

Do not hotlink random copyrighted photos. Store source URL, license and attribution when adding photo records.

### News

- Headlines are discovered through Google News RSS and filtered by source.
- The app attempts to resolve the publisher URL, image and a readable preview.
- Full publisher articles cannot simply be copied into Tennisd. Keep a clear source link and respect publisher copyright/terms.
- Source definitions and allowed image domains live in `tennisd/news_feed.py`.

## 7. Environment variables and secrets

Never copy actual values into this document, Git, prompts, logs or screenshots.

### Vercel production variables

- `APP_ENV=production`
- `SECRET_KEY` — stable random value, at least 32 characters
- `DATABASE_URL` — pooled TLS Neon URL using the restricted `rallylog_web` role
- `PUBLIC_HOST` — optional explicit production hostname; Vercel variables can supply it
- `REGISTRATION_ENABLED`
- `REQUIRE_EMAIL_VERIFICATION`
- `RESEND_API_KEY`
- `MAIL_FROM`
- `ADMIN_EMAIL`
- `CONTACT_EMAIL`
- `AUTO_CREATE_DB=false`

### GitHub Actions secrets

- `TENNISD_SYNC_DATABASE_URL` — TLS database URL used by import/sync jobs
- `LIVETENNISAPI_KEY`
- `DATABASE_OWNER_URL` — owner connection used only for schema migrations; remove/rotate when not needed
- `DATABASE_APP_PASSWORD` — temporary role-initialization value; remove after initialization
- `BACKUP_DATABASE_URL` — read-capable URL for encrypted dumps

### GitHub variables

- `BACKUP_RECIPIENT` — public age encryption recipient
- `BACKUPS_ENABLED=true` only after a successful restore drill

Existing secrets must be read from the relevant provider by the owner. An AI agent should ask for a variable to be configured when missing, never request that its value be pasted into source code.

## 8. Security requirements that must remain

- Argon2id parameters: 19 MiB, 2 iterations, parallelism 1.
- Stable production secret; secure, HTTP-only, SameSite=Lax session cookie.
- PostgreSQL TLS required in production.
- Trusted host enforcement.
- CSRF on every state-changing form.
- Persistent database-backed limits for registration/login/reset/report actions.
- Single-use email verification and password-reset tokens with expiry.
- Session-version invalidation after password changes.
- HSTS, CSP, frame denial, MIME sniffing protection, no-referrer and permissions policy.
- Authenticated responses marked private/no-store.
- Profile uploads capped at 2 MiB, decoded/validated with Pillow, resized and converted to WebP.
- Restricted web database role `rallylog_web` with CRUD access but no schema CREATE, superuser, role creation or database creation.
- Owner database connection is for migrations/recovery only.
- No secrets or production data in tests.

Read `SECURITY.md`, `tennisd/security.py`, `tests/test_security.py`, `scripts/secure_database.sql` and `scripts/apply_database_security.py` before auth/database changes.

## 9. Local setup

```bash
git clone https://github.com/Snowstormme/tennisd.git
cd tennisd
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
flask --app wsgi run --debug
```

A clean local run creates SQLite tables and sample records. Do not assume an old `instance/tennisd.db` matches current models. If it reports a missing column such as `match.scheduled_at`, use a fresh disposable local database or deliberately migrate the old file. Never delete a user's local database without explicit permission.

Useful clean preview without touching the normal local DB:

```bash
DATABASE_URL=sqlite:////tmp/tennisd-preview.db flask --app wsgi run --port 5056
```

## 10. Test and release procedure

Before edits:

```bash
git status --short
git log -5 --oneline
.venv/bin/python -m pytest -q
```

Before commit:

```bash
.venv/bin/python -m pytest -q
git diff --check
git diff --stat
git status --short
```

The verified handoff state has **31 passing tests**.

Release normally means:

```bash
git add <specific files>
git commit -m "Clear description"
git push origin main
```

Then wait for Vercel and verify:

```bash
curl -fsSL https://tennisd.vercel.app/healthz
curl -I https://tennisd.vercel.app/<affected-page>
```

Also inspect the affected page visually at desktop and mobile widths. Do not declare completion just because a push succeeded.

Rollback: use Vercel's prior successful deployment or revert the faulty Git commit. Avoid rewriting shared history unless there is a concrete reason and use `--force-with-lease` only when unavoidable.

## 11. GitHub workflows

- `tests.yml` — tests and dependency audit on push/PR.
- `init-database.yml` — creates initial schema and restricted role.
- `add-tennis-api-sync.yml` — adds the durable historical import cursor table.
- `sync-tennis-api-history.yml` — daily historical-result sync through Tennis API.
- `add-live-matches-table.yml` — live-match migration.
- `add-tournament-subscriptions.yml` — subscription migration.
- `sync-live-matches.yml` — live/upcoming sync every 15 minutes.
- `backup.yml` — encrypted daily PostgreSQL dump when enabled.

Database changes are currently managed by explicit SQL/Python scripts rather than Alembic. New migrations must be idempotent and checked into `scripts/` with a manually dispatchable workflow when production execution is needed.

## 12. Design system and responsive behavior

Read `DESIGN.md` and reuse `public/static/style.css`.

- Palette: dark green base, cream text, lime highlight, surface-specific court colors.
- Display font: Oswald; body font: DM Sans.
- Avoid generic AI gradients, excessive glass effects, floating pills and empty oversized sections.
- Desktop header contains Matches, Players, Tournaments, Notifications, News and Search.
- Mobile header uses two rows; notifications stay beside profile/login.
- Players includes Rankings internally; do not restore a separate top-level Rankings item unless requested.
- Match cards use real player portraits, court-colored backgrounds and central match information.
- Search is intentionally compact. Player/member tiles are small; match results reuse the home showcase design.
- Validate at least desktop, tablet and phone breakpoints after CSS changes.

## 13. Recent decisions and latest work

Recent commits, newest first:

- `4e5206d` — compact Search, smaller people tiles, home-style match results.
- `b3943e0` — merged Rankings into Players and compacted player cards.
- `225b299` — ranking data and popularity ordering.
- `170a4d5` / `6654c22` / `b54dc7d` — normalized database backfill and permanent live-result structure.
- `8118762` — normalized tennis database migration.
- `20b1a8c` — SEO/sitemaps.

Current UI expectations:

- Players default ordering is community follows, then match appearances, then photo/name availability.
- `/players?view=rankings` shows ranked player cards with search.
- Search header and gaps are compact; results show up to 15 players, 9 matches and 15 members.
- Search match cards use photos for both players and court colors.

## 14. Known limitations and technical debt

1. There is no single free official ATP/WTA/ITF API covering every desired field. Current provider limits and provenance must remain visible.
2. The live free API request budget prevents high-frequency point-by-point coverage.
3. Live finished rows are retained and copied into `Match`, but player identity matching still needs continued hardening as the archive grows.
4. Ranking snapshots are imported only when a source provides them or when tests seed them; there is no complete official weekly ranking archive.
5. Prize money is sparse and may be outdated because it depends on available Wikidata values.
6. Player photo coverage depends on freely hosted/licensed sources; placeholders are correct when none exists.
7. News extraction is fragile because publisher pages and Google News resolution can change. Full copyrighted republication is not permitted by default.
8. The project has explicit SQL migrations but no versioned migration framework. Consider Alembic only through a planned, tested transition.
9. CSS is a large accumulated stylesheet. Refactor only in small verified stages because many later rules intentionally override earlier responsive rules.
10. Free Vercel/Neon/GitHub/API plans are suitable for early traffic, not guaranteed global scale or uptime.
11. Search currently has fixed result limits, not paginated result groups.
12. Accessibility has not yet had a full WCAG 2.1 AA audit.

## 15. Safe next priorities

Recommended order:

1. Restore-test and enable encrypted backups if this has not been completed.
2. Add structured migrations/version tracking before frequent schema evolution.
3. Add provider identity reconciliation so live players/tournaments reliably map to permanent entities.
4. Add a licensed rankings source or scheduled ranking import with provenance.
5. Add pagination/autocomplete to global search when the catalog grows.
6. Add observability for failed syncs, email failures and 5xx rates.
7. Run an accessibility audit and fix keyboard/focus/contrast issues.
8. Add legal pages/consent details appropriate to launch countries before larger global promotion.
9. Introduce staging or preview database separation before risky releases.
10. Load-test high-traffic catalog endpoints and add indexes/caching based on measurements.

## 16. Rules for database/API replacement decisions

The owner prefers free services and may replace Neon or providers only when the alternative is both meaningfully better and sustainable.

Before replacing anything, compare:

- free tier limits and likelihood of sudden billing;
- PostgreSQL compatibility and export portability;
- region, latency and connection pooling;
- backups/point-in-time recovery;
- role/privilege controls;
- vendor lock-in;
- live-data licensing, redistribution and commercial-use rights;
- rate limits and completeness;
- stable identifiers and historical retention;
- migration/rollback plan.

Never replace the current database or API merely because another service looks easier. Prepare a written comparison and migration plan first.

## 17. Definition of done for future tasks

A task is complete only when:

- the requested behavior is implemented in the existing architecture;
- repeated data/markup has been consolidated where practical;
- security/privacy/license constraints still hold;
- relevant tests cover the change and the full suite passes;
- `git diff --check` is clean;
- desktop/mobile behavior is visually inspected;
- the change is committed and pushed when deployment was requested or is part of the established workflow;
- Vercel has deployed successfully;
- `/healthz` and affected production pages are verified;
- the owner receives a concise report with the commit hash and any honest limitation.
