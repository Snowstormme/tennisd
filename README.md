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

Open <http://127.0.0.1:5000>. Test runs receive a small handcrafted demo catalog; production initialization creates an empty schema. Current fixtures and results enter through the scheduled [Live Tennis API](https://livetennisapi.com/) feed. Once a live singles match finishes, the same provider identity is copied into the permanent match catalog, so its score and winner remain available for the diary. Existing historical rows remain in the database, but Tennisd no longer imports new history from a second tennis provider.

To install the complete development and CI environment, use the shared dev requirements file:

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m unittest discover -s tests -v
```

The dev file includes the runtime dependencies and the pinned dependency auditor used by GitHub Actions, so local verification and CI use the same dependency set.

GitHub Actions also runs a PostgreSQL smoke check against a temporary PostgreSQL 16 service. It creates the application schema, verifies a database query, and checks the health endpoint before a change can merge.

Database schema changes use the owner-only migration runner documented in [docs/migrations.md](docs/migrations.md); the web role is never responsible for creating schema objects.

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

- [Live Tennis API](https://livetennisapi.com/) rows use the scheduled match start when available. Development fixtures still use the tournament week.
- Win rates, titles, head-to-head and rankings are calculated from **loaded matches only**. Rankings shown are from the player's latest imported match, not current rankings.
- Prize money appears only when [Wikidata](https://www.wikidata.org/) lists a USD amount for that player's linked entity. The source and date are visible. Missing data is shown as unavailable. A displayed amount may be out of date.
- Local development and automated tests use a compact handcrafted demo seed. Production match data should come from configured provider imports.

## Security model

- New passwords use Argon2id with OWASP's 19 MiB / 2 iteration baseline. Existing Werkzeug hashes are accepted and upgraded after a successful login.
- Every state-changing form requires a session CSRF token. Login, registration, password reset, account deletion and reports use database-backed limits that survive web restarts.
- Email verification uses single-use four-digit codes that expire after 10 minutes. Only keyed digests are stored, and verification attempts are rate-limited per IP and email. Password reset links expire after 30 minutes, and password changes invalidate older sessions.
- Production requires HTTPS cookies, a stable secret, an allowed host, PostgreSQL over TLS, configured email delivery and contact addresses before registration can open.
- Responses include HSTS in production, CSP, clickjacking, MIME-sniffing, referrer and browser-permission protections. Authenticated pages are not cacheable.
- User content is escaped by Jinja. Private diary entries and watchlists are never returned on another member's profile.
- Profile photos are validated, resized to at most 640 × 640, converted to WebP and stored in PostgreSQL rather than the ephemeral Render filesystem.
- Player portraits use a linked Wikidata image first, then an exact-name English Wikipedia tennis result. A designed portrait placeholder remains when neither project has a freely hosted image.
- The web database role receives data access but no schema creation rights. The owner connection is reserved for initialization and recovery.
- Users can export or delete their data. The privacy page explains stored fields and third-party processing.
- AdSense support is opt-in. Set `ADSENSE_CLIENT_ID` after account creation; keep `ADSENSE_ENABLED=false` until Google approves the site and a certified consent message is published. The app then serves the matching root `ads.txt` automatically.

## Public deployment

Tennisd runs as a Flask web service on Render with automatic HTTPS and registration initially disabled. The connected production branch deploys automatically on Render; the old Vercel hostname is retained only as a redirect to Render. It keeps the existing [Neon PostgreSQL](https://neon.com/) database; Render's filesystem is ephemeral, so production must not use SQLite.

1. Create a Neon project in a nearby region. Do not create the website role in the Neon console because console-created roles inherit `neon_superuser`.
2. Add the owner TLS connection string as the temporary GitHub secret `DATABASE_OWNER_URL` and a random 32-character-or-longer value as `DATABASE_APP_PASSWORD`, then run **Initialize production database**. The action applies the ordered schema migrations, creates `rallylog_web`, grants only runtime data access and verifies that the role has no elevated privileges. Rotate the owner password and remove both temporary GitHub secrets afterward.
3. Build a pooled TLS URL using `rallylog_web` and the generated application password, then set it as Render's `DATABASE_URL`.
4. Create a Brevo account and verify the sender email. Set `BREVO_API_KEY`, `MAIL_FROM`, `MAIL_FROM_EMAIL`, `ADMIN_EMAIL` and `CONTACT_EMAIL` in Render before opening registration.
5. Create the service from `render.yaml`, or configure it manually with build command `pip install -r requirements.txt` and start command `gunicorn --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120 --access-logfile /dev/null wsgi:app`. Python is pinned by `.python-version`. Set `APP_ENV=production`, `AUTO_CREATE_DB=false`, `REGISTRATION_ENABLED=false`, a stable random 64-character `SECRET_KEY`, and the pooled `DATABASE_URL`. Render supplies `PORT` and `RENDER_EXTERNAL_HOSTNAME`.
6. Verify `/healthz`, the catalog, account email, login, password reset, reports, export and account deletion. Then change `REGISTRATION_ENABLED` to `true` and redeploy.

### Render environment variables

Set these on the Render web service without committing their values:

- Required: `APP_ENV=production`, `AUTO_CREATE_DB=false`, `SECRET_KEY`, and the pooled TLS `DATABASE_URL`.
- Registration: `REGISTRATION_ENABLED`, `REQUIRE_EMAIL_VERIFICATION`, `BREVO_API_KEY`, `MAIL_FROM`, and `MAIL_FROM_EMAIL`. Keep registration disabled until Brevo is configured.
- Administration: `ADMIN_EMAIL` and `CONTACT_EMAIL`.
- Optional public integrations: `ADSENSE_CLIENT_ID`, `ADSENSE_ENABLED`, and `INDEXNOW_KEY`.
- Optional canonical hostname: `PUBLIC_HOST`. Omit it to use Render's automatic `RENDER_EXTERNAL_HOSTNAME`.

Render supplies `PORT` and `RENDER_EXTERNAL_HOSTNAME`; do not create them manually. Keep `LIVETENNISAPI_KEY` and `TENNISD_SYNC_DATABASE_URL` in GitHub Actions rather than the web service.

### Live top-tier feed

Tennisd mirrors ATP and WTA singles at Grand Slams, tour events, Challenger and ITF events. Create a free Live Tennis API key, add it to GitHub as `LIVETENNISAPI_KEY`, and add the limited `rallylog_web` pooled TLS connection as `TENNISD_SYNC_DATABASE_URL`. Run `scripts/add_live_matches.sql` once as the database owner, then enable the **Sync live top-tier matches** workflow. It runs once every 24 hours at 02:07 UTC and refreshes the live slate plus fixtures for the next seven days. Existing finished singles remain in Tennisd's permanent `match` table. This is a daily snapshot rather than real-time scoring, and GitHub scheduled workflows can start several minutes late.

The Live Tennis API refresh is a daily snapshot rather than point-by-point streaming. Every live card shows how long ago its stored score was updated. API keys are used only by GitHub Actions and must not be placed in Render or sent to the browser.

The checked-in [Render Blueprint](render.yaml) is the production web-service configuration. Neon free compute sleeps while idle, so the first request after inactivity can be slower. Free plans suit an early public preview and are not an uptime guarantee.

## Backups and recovery

The **Encrypted database backup** GitHub action creates a PostgreSQL custom-format dump, encrypts it with an age public key before upload and retains the artifact for 30 days. Configure:

- GitHub secret `BACKUP_DATABASE_URL`: a TLS database URL that can read all Tennisd tables.
- GitHub variable `BACKUP_RECIPIENT`: the public `age1...` key. Keep the private age key offline and outside GitHub.
- GitHub variable `BACKUPS_ENABLED=true` only after performing a restore drill.

To validate recovery, download an encrypted artifact, decrypt it locally with the offline age key, restore it into a fresh temporary PostgreSQL database, and compare table counts plus a test account export. A backup is only trusted after that restore succeeds. Neon Free includes a short instant-restore window, while the encrypted export protects against longer incidents.

## Secrets and release process

No account data, database files, API keys or connection URLs belong in Git. Copy `.env.example` to `.env` for local variables; `.env`, dumps and encrypted dumps are ignored. GitHub Actions runs the test suite and dependency vulnerability audit on every push and pull request. Dependabot proposes weekly Python and GitHub Actions updates. Report security issues privately as described in [SECURITY.md](SECURITY.md). Production variables stay in the hosting provider and a tested Git commit can be rolled back to an earlier deployment.

The current application version is stored in [`VERSION`](VERSION) and is shown in the site footer. Update that file when preparing a semantic-versioned release and record the change in [`CHANGELOG.md`](CHANGELOG.md).

## License

Tennisd's original source code is source-available under the [PolyForm Noncommercial License 1.0.0](LICENSE). You may study, test, modify and redistribute it only for purposes permitted by that license, while keeping the required license and copyright notices.

Commercial use requires a separate written license from the copyright holder. Without that permission, you may not sell Tennisd or copies of it, charge for access or hosting, monetize it with advertising or subscriptions, include it in a paid product or service, or use it for an anticipated commercial application. Commercial licensing requests can be sent to [notsnowstorm@proton.me](mailto:notsnowstorm@proton.me).

The source code license does not grant rights to the Tennisd name, logo, third-party photographs, tennis data, news content, or API responses. Those materials and services remain subject to their respective owners' licenses and terms. Contributions are accepted only when the contributor has the right to submit them under the project's current license.

## Structure

```text
tennisd/
  tennisd/__init__.py     application setup and import command
  tennisd/models.py       database tables
  tennisd/importer.py     local demo seed for tests and development
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
