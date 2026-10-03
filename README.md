# Tennisd

**A tennis match diary.** Browse real ATP and WTA matches, log what you watched, rate matches, write reviews, comment, follow players, and see your tennis taste in a personal profile.

Tennisd is a separate project from Movie Finder. The interface is in English. It uses Flask, Flask-SQLAlchemy and Flask-Login. SQLite works locally; set `DATABASE_URL` to use PostgreSQL when hosting.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
flask --app wsgi run --debug
```

Open <http://127.0.0.1:5000>. Test runs receive a small handcrafted demo catalog; production initialization creates an empty schema. Current fixtures and results enter through the scheduled Live Tennis API feed. Once a live singles match finishes, the same provider identity is copied into the permanent match catalog, so its score and winner remain available for the diary. Historical ATP and WTA results are backfilled gradually through Tennis API on RapidAPI.

## What is here

- **Discover:** featured match, archive highlights and recent public reviews.
- **Matches:** Grand Slam, ATP/WTA 1000 and ATP/WTA 500 live scores and the next seven days of singles, plus historical search by player, tournament, tour, surface, level and year.
- **Match page:** score, recorded serve statistics, community rating, reviews and comments.
- **Diary:** one editable entry per user and match with viewing date, optional half-star rating, review, favorite, spoiler flag and public/private choice.
- **Watchlist:** save matches to watch later; logging a match removes it from the watchlist.
- **Players:** biography, imported wins and losses, surface and season splits, titles, serve metrics, frequent opponents and followed players.
- **Profiles:** personal diary, watched surfaces, tour split, ratings and favorites.
- **Settings:** profile photo, display name, bio, password change, diary export and account deletion.
- **Account safety:** email verification, expiring single-use password reset links and session invalidation after password changes.
- **Community safety:** report controls and a private moderation queue for the verified administrator.

## Data honesty

- Live Tennis API rows use the scheduled match start when available. Tennis API historical rows use the provider's recorded result date; for old lower-level rows, the provider can use the tournament start date when no exact match day is available. Development fixtures still use the tournament week.
- Win rates, titles, head-to-head and rankings are calculated from **loaded matches only**. Rankings shown are from the player's latest imported match, not current rankings.
- Prize money appears only when [Wikidata](https://www.wikidata.org/) lists a USD amount for that player's linked entity. The source and date are visible. Missing data is shown as unavailable. A displayed amount may be out of date.
- Local development and automated tests use a compact handcrafted demo seed. Production match data should come from configured provider imports.

## Security model

- New passwords use Argon2id with OWASP's 19 MiB / 2 iteration baseline. Existing Werkzeug hashes are accepted and upgraded after a successful login.
- Every state-changing form requires a session CSRF token. Login, registration, password reset, account deletion and reports use database-backed limits that survive web restarts.
- Email verification uses single-use six-digit codes that expire after 10 minutes. Only keyed digests are stored. Password reset links expire after 30 minutes, and password changes invalidate older sessions.
- Production requires HTTPS cookies, a stable secret, an allowed host, PostgreSQL over TLS, configured email delivery and contact addresses before registration can open.
- Responses include HSTS in production, CSP, clickjacking, MIME-sniffing, referrer and browser-permission protections. Authenticated pages are not cacheable.
- User content is escaped by Jinja. Private diary entries and watchlists are never returned on another member's profile.
- Profile photos are validated, resized to at most 640 × 640, converted to WebP and stored in PostgreSQL rather than the ephemeral Vercel filesystem.
- Player portraits use a linked Wikidata image first, then an exact-name English Wikipedia tennis result. A designed portrait placeholder remains when neither project has a freely hosted image.
- The web database role receives data access but no schema creation rights. The owner connection is reserved for initialization and recovery.
- Users can export or delete their data. The privacy page explains stored fields and third-party processing.

## Public deployment

Tennisd can run as one Flask function on Vercel Hobby with automatic HTTPS and registration initially disabled. Use a separate [Neon PostgreSQL](https://neon.com/) database; serverless filesystems are ephemeral, so production must not use SQLite.

1. Create a Neon project in a nearby region. Do not create the website role in the Neon console because console-created roles inherit `neon_superuser`.
2. Add the owner TLS connection string as the temporary GitHub secret `DATABASE_OWNER_URL` and a random 32-character-or-longer value as `DATABASE_APP_PASSWORD`, then run **Initialize production database**. The action creates the schema, creates `rallylog_web`, grants only runtime data access and verifies that the role has no elevated privileges. Rotate the owner password and remove both temporary GitHub secrets afterward.
3. Build a pooled TLS URL using `rallylog_web` and the generated application password, then set it as Vercel's `DATABASE_URL`.
4. Create a Resend account and verify a sending domain. Tennisd uses its HTTPS API. Set `RESEND_API_KEY`, `MAIL_FROM`, `ADMIN_EMAIL` and `CONTACT_EMAIL` in Vercel.
5. Import the Git repository into a Vercel Hobby project. Set `APP_ENV=production`, `REGISTRATION_ENABLED=false`, a random 64-character `SECRET_KEY`, and `DATABASE_URL`. Vercel detects `wsgi.py` as the Flask entry point and supplies its hostname to the app.
6. Verify `/healthz`, the catalog, account email, login, password reset, reports, export and account deletion. Then change `REGISTRATION_ENABLED` to `true` and redeploy.

### Live top-tier feed

Tennisd mirrors ATP and WTA singles at Grand Slams, tour events, Challenger and ITF events. Create a free Live Tennis API key, add it to GitHub as `LIVETENNISAPI_KEY`, and add the limited `rallylog_web` pooled TLS connection as `TENNISD_SYNC_DATABASE_URL`. Run `scripts/add_live_matches.sql` once as the database owner, then enable the **Sync live top-tier matches** workflow. It refreshes the stored live slate every 15 minutes and replaces those cards' score text in open browsers once a minute. One midnight UTC hour refreshes fixtures for the next seven days instead, keeping the scheduled use inside the free plan's 100 request daily allowance. Finished singles matches are promoted into Tennisd's permanent `match` table and remain available after they leave the live feed.

### Current odds

Tennisd can show decimal full-match prices and margin-normalized chances from Pinnacle via [The Odds API](https://the-odds-api.com/). Add `THE_ODDS_API_KEY` to GitHub, run **Add live odds table** once, then enable **Sync live tennis odds**. The workflow runs every 90 minutes and rotates through one active tennis competition per run. That is 16 charged requests per day and at most 496 in a 31-day month, inside the current 500-credit free tier. With several simultaneous competitions, each individual competition updates less often than 90 minutes. The interface identifies the bookmaker, update time and that the values are informational market estimates.

### Free historical results

Tennisd can extend the permanent match database through [Tennis API on RapidAPI](https://docs.tennis-api.com/getting-started). The free plan currently allows 50 requests per day; the workflow uses 48 by default so the account has a small safety buffer. It imports ATP and WTA singles results from newest to oldest, keeps a durable cursor in `ingestion_cursor`, and deduplicates against provider IDs plus already preserved live results.

1. Subscribe to the free Tennis API plan in RapidAPI and add the key to GitHub Actions as `RAPIDAPI_TENNIS_KEY`.
2. Keep `TENNISD_SYNC_DATABASE_URL` pointed at the limited `rallylog_web` pooled TLS database URL.
3. Temporarily add the database owner URL as `DATABASE_OWNER_URL`, run **Add Tennis API sync state**, then remove the owner secret.
4. Enable **Sync Tennis API history**. It refreshes the previous two days of finals and spends the remaining requests on 7-day historical chunks down to 2010.

Confirm RapidAPI/provider storage and commercial terms before using the imported data in a monetized public release.

This is near-live on the free plan rather than point-by-point streaming. The API key is used only by GitHub Actions and must not be placed in Vercel or sent to the browser.

The checked-in [Render Blueprint](render.yaml) remains an alternative host configuration. Neon free compute sleeps while idle, so the first request after inactivity can be slower. Free plans suit an early public preview and are not an uptime guarantee.

## Backups and recovery

The **Encrypted database backup** GitHub action creates a PostgreSQL custom-format dump, encrypts it with an age public key before upload and retains the artifact for 30 days. Configure:

- GitHub secret `BACKUP_DATABASE_URL`: a TLS database URL that can read all Tennisd tables.
- GitHub variable `BACKUP_RECIPIENT`: the public `age1...` key. Keep the private age key offline and outside GitHub.
- GitHub variable `BACKUPS_ENABLED=true` only after performing a restore drill.

To validate recovery, download an encrypted artifact, decrypt it locally with the offline age key, restore it into a fresh temporary PostgreSQL database, and compare table counts plus a test account export. A backup is only trusted after that restore succeeds. Neon Free includes a short instant-restore window, while the encrypted export protects against longer incidents.

## Secrets and release process

No account data, database files, API keys or connection URLs belong in Git. Copy `.env.example` to `.env` for local variables; `.env`, dumps and encrypted dumps are ignored. GitHub Actions runs the test suite and dependency vulnerability audit on every push and pull request. Dependabot proposes weekly Python and GitHub Actions updates. Report security issues privately as described in [SECURITY.md](SECURITY.md). Production variables stay in the hosting provider and a tested Git commit can be rolled back to an earlier deployment.

## Structure

```text
tennisd/
  tennisd/__init__.py     application setup and import command
  tennisd/models.py       database tables
  tennisd/importer.py     local demo seed for tests and development
  tennisd/tennis_api.py   free Tennis API historical-result sync
  tennisd/stats.py        transparent statistics
  tennisd/prize_money.py  optional Wikidata figure
  tennisd/routes.py       pages and forms
  tennisd/security.py     tokens, email and persistent abuse limits
  tennisd/templates/     HTML pages
  public/static/          CSS and favicon served by the hosting CDN
  scripts/                database, sync and operations helpers
  tests/                  critical user-flow tests
  .github/workflows/      tests, initialization and encrypted backups
  render.yaml             public web service configuration
  wsgi.py                 web server entry point
```
