# Changelog

This file records notable user-facing and maintainer-facing changes.

## [Unreleased]

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

