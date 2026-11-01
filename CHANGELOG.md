# Changelog

This file records notable user-facing and maintainer-facing changes.

## [Unreleased]

## [0.11.0] - 2026-11-01

- Add complete 30-item notification, pending-request and reply histories with
  stable pages, unread counts/filter and an owner-scoped mark-all-read action.
- Preserve the first read timestamp and validate stored destinations. Repair
  legacy review links in the view; disable deleted/private/unsafe destinations
  without rewriting stored notification URLs.
- Add comment permalinks that locate the exact 20-reply discussion page, with
  the same public/owner visibility checks. Keep all inboxes private and no-store.

## [0.10.0] - 2026-11-01

- Add full paginated search categories for players, matches, tournaments and
  members. Show complete counts and explicit preview limits, with links to every
  category, bounded page input and query/category-preserving navigation.
- Search literal text consistently across global search, match archives, player
  directories and tournament lists. Percent, underscore and the escape marker
  no longer act as unintended SQL wildcards.
- Eager-load match participants for search cards and wrap category tabs on small
  screens. Preserve public visibility, R128 exclusion and noindex metadata.

## [0.9.0] - 2026-11-01

- Page exact tournament archives and season results by 18 matches. Expose all
  loaded seasons, round filters, full selection counts and stable ordering.
- Calculate title leaders and season lists in SQL without hydrating every final
  or tournament date. Keep complete counts independent of the displayed page.
- Retain every visible round, including RR, later qualifying, unknown and blank
  codes. Distinguish multiple recorded finals from a uniquely recorded champion.
- Describe calendar-year result pages as collections in structured metadata;
  do not claim a complete draw or a single completed event.

## [0.8.0] - 2026-11-01

- Bound archive, player and recorded-ranking page requests before computing SQL
  offsets. Preserve filter parameters and add accessible page links above/below
  results with explicit page counts and section anchors.
- Select only the latest positive singles rank in directory cards and recorded
  rankings; doubles and invalid ranks cannot override a valid singles snapshot.
- Add unique ID tie breakers to archive/directory/search result orderings. Ignore
  malformed or oversized archive years instead of raising integer errors.

## [0.7.0] - 2026-11-01

- Page match discussions by 18 public reviews, with three latest reply previews
  per review selected by a SQL window. Aggregate ratings over the full public
  discussion without loading every review body.
- Add stable 20-reply discussion pages, owner-only access to private entries,
  spoiler protection and escaped content. Preserve comment deletion permissions.
- Direct new reply notifications and comment action redirects to the discussion
  so a reply remains reachable when its review leaves the first match page.

## [0.6.0] - 2026-11-01

- Apply a shared current-snapshot policy to match lists, player profiles, live
  polling and follower notifications. Exclude stale checks and out-of-window
  schedules without changing stored scores, statuses, winners or diary records.
- Preserve direct saved reports with explicit freshness explanations. Expire
  live cards during browser polling and avoid winner labels for ongoing matches.
- Report eligible/excluded observation counts separately from feed job health;
  select the latest live/upcoming run independently of historical import volume.

## [0.5.0] - 2026-11-01

- Aggregate player wins/losses, titles, surfaces, seasons, opponents and service
  metrics in SQL across the complete visible history. Load at most twelve title
  records; keep full title counts and distinct namesake opponent IDs.
- Add 18-match player archive pages with stable date/ID ordering, bounded page
  requests and direct player-ID navigation. Remove the ambiguous name-only
  "Find all" link. Summaries remain independent of the displayed page.
- Compute each service percentage only from paired, consistent point counts;
  show its contributing match count, retain known zeros and exclude invalid
  negative/impossible observations. Keep missing values distinct from zero.
- Ignore invalid rank zero, retain zero ranking points and clarify that best
  ranks cover loaded history. All queries preserve the R128 visibility policy.


## [0.4.0] - 2026-11-01

- Public discovery starts at R64 (1/32 final). Hide R128 in archive searches,
  home feeds, player statistics, tournament editions, live slates and sitemaps.
  Preserve database rows, direct result pages, account exports and diaries.
- Use fractional main-draw round labels consistently across match cards, detail
  pages and tournament headings. Identity audits still inspect hidden results.

## [0.3.4] - 2026-11-01

- Correct twenty individually reviewed Paris 2024 opening-round results from
  R128 to R64 using the official 64-position ATP main draw. Preserve every
  participant, score, date and diary reference. Record exact participant snapshots
  and source draw positions; retain atomic group apply/rollback.
- Test the 20-record group boundary, idempotency and late-failure rollback.

## [0.3.3] - 2026-11-01

### Verified rounds

- Add five source-reviewed round-only repairs: Chengdu R128 → R32, Beijing
  qualifying Q2 → Q1, Paris main draw R128 → R64, Paris qualifying Q2 → Q1 and
  qualifying final Q4 → Q2. Preserve participants, scores, dates and diary IDs.
- Guard exact result/round and related-row snapshots with the existing atomic
  group apply/rollback. Keep participant and round reviews independent; indicate
  when both are verified. Use readable round names on the match detail page.

### Catalog review

- Add database-enforced read-only cohort reports filtered by tour, calendar
  year, exact tournament name and provider. Include valid identities as well as
  conflicts, observed round distributions, bounded samples and ID cursors.
- Keep totals scoped to the complete cohort across sample pages. Export public
  match fields only and retain the original unfiltered quality report format.
- Expose the filters and downloadable JSON reports in the manual audit workflow.

## [0.3.2] - 2026-11-01

### Data recovery

- Restore Fabio Fognini's identity in seven individually verified 2024 results:
  Chengdu, Beijing qualifying, two Shanghai matches, Paris main draw and two
  Paris qualifying matches. Store official ATP draw URLs, positions and PDF
  SHA-256 digests alongside exact original match/participant snapshots.
- Support correcting the reviewed winner or loser while preserving score,
  opponent, participant sides/winner flags, permanent IDs and diary entries.
- Add one fixed seven-result recovery group with a read-only preview and atomic
  apply/rollback. A database savepoint undoes the complete group on any error,
  even if the caller catches it. Arbitrary match lists are not accepted.
- Explicitly begin SQLite's outer transaction before creating the savepoint,
  so releasing a successful group still allows the caller to roll it back.
- Validate mixed winner/loser recovery, late failure, idempotency, private diary
  preservation and rollback in SQLite; cover the same group and savepoint
  failure behavior in PostgreSQL CI.

## [0.3.1] - 2026-11-01

### Data corrections

- Add individually reviewed US Open 2024 corrections: Tomas Machac defeated
  Fabio Fognini 7-5 6-1 6-3; Ugo Humbert defeated Thiago Monteiro 6-3 6-4 6-4.
  The legacy importer incorrectly assigned WTA opponents to both results.
- Reuse exact existing ATP profiles with name/tour/country guards. Preview now
  validates the target identity too and exposes public related rows when review
  is incomplete. Guard the exact participant/statistic/set snapshots; update the
  two reviewed loser participant references in the same transaction as their
  matches. Keep permanent match and diary IDs and preserve external mappings.
- Support idempotent apply/rollback for one selected result, refusing added,
  changed or partially repaired related records.
- Link each corrected result to the appropriate official draw. The manual
  workflow accepts only the three version-controlled reviewed corrections.

## [0.3.0] - 2026-11-01

### Added

- Paginate diary entries, likes, watchlist and friends in groups of 18, with
  stable date/ID ordering and bounded invalid/oversized page requests.
- Calculate profile totals and rating/surface/player summaries in SQL across the
  entire visible diary. Keep namesakes separate using player IDs.

### Performance and privacy

- Load only the active profile section; the overview fetches at most five recent
  and four favorite entries. Private rows, page counts and summary aggregates
  share the same visibility scope. Account exports remain complete.

### Operations

- Test encryption/decryption as well as restoring the recovered dump in CI.
- Allow opt-in backups to use the existing limited sync connection when a
  dedicated backup URL is absent, enforce a read-only dump transaction and
  serialize backup jobs. Backup activation remains disabled by default.

## [0.2.2] - 2026-11-01

### Fixed

- Check daily ingestion by UTC calendar date so an earlier scheduler run does
  not skip the next day's refresh behind a strict 24-hour interval.

### Data corrections

- Explain the paused diary form on results under review while preserving the
  owner's ability to delete an existing entry.
- Add the source-reviewed correction of lt-211673: Yi Yuan (HKG) is Koray
  Kirci's opponent in the official ITF M15 Sharm ElSheikh main draw, with a
  6–1 6–2 result. The active provider misidentifies the opponent as Yue Yuan.
- Provide a default read-only preview, exact original-field guards, a row lock,
  idempotent apply/rollback and a public result journal. Preserve match/diary IDs
  and all existing player/provider mappings; expose the official evidence link
  when the reviewed correction is present.

## [0.2.1] - 2026-11-01

### Fixed

- Keep namesakes and distinct source player IDs separate during import, profile
  linking and follower notifications; refuse ambiguous archive redirects.
- Reject a provider result that assigns the same source ID to both opponents.

### Operations

- Classify historical identity conflicts by provider and tour, include bounded
  public result samples and report the latest live/upcoming ingestion runs.
- Add a source-backed catalog recovery preview that cannot apply changes and
  enforces read-only SQL in PostgreSQL and SQLite. Stop provider calls after the
  first upstream failure, and never query live IDs for a retired source.
- Test database-level write refusal in the PostgreSQL smoke job.

## [0.2.0] - 2026-11-01

### Fixed

- Require explicit provider outcomes before archiving daily feed results; missing
  events are no longer treated as cancelled or settled from a partial listing.
- Validate player names and tours when resolving provider identities, contain
  inconsistent catalog rows, and preserve diary IDs when correcting results.
- Render en-dash scores as sets, suppress legacy null point annotations, and
  show the age and limitations of daily snapshots.
- Use content hashes for static asset URLs so returning visitors receive updated
  CSS and JavaScript.
- Improve mobile match readability, feedback/poll controls and profile checkboxes.
- Keep successful news snapshots during source failures, refresh outside web
  requests, and use direct BBC/ESPN publisher RSS feeds.

### Operations

- Add a read-only catalog audit and replace the destructive archive rebuild with
  bounded provider rechecks.
- Restore each configured backup into an isolated CI database before encryption,
  and test the restore guard in CI.
- Test Python 3.12 and 3.13, constrain runtime dependency versions, and configure
  the Render Blueprint to deploy after CI passes.

### Added

- Community contribution guidance, Code of Conduct, accessibility guidance, and issue and pull request templates.
- Private security reporting guidance and a standards-compliant `security.txt` endpoint.

### Security

- Continued security hardening of authentication, sessions, CSP, rate limiting, and database access.

## How to update this file

Add entries under `Unreleased` as changes land. Before a release, move those entries into a dated semantic-version section, for example `## [1.0.0] - 2027-01-15`.

