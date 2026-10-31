# Tennisd project memory

## Current state

Snowstormme/tennisd is a Flask/PostgreSQL tennis diary application deployed to Render with Neon PostgreSQL. Production changes require explicit user request. New repository changes should normally use a feature branch and pull request into `main`.

Security hardening, documentation, versioning, Dependabot configuration, database-role checks, backups, and the main user flows are already implemented.

## Constraints

- Never expose secrets, database URLs, passwords, or tokens.
- Do not modify, delete, or rewrite `feature/ml-model`.
- Do not touch `/Users/me/.gemini/antigravity/scratch/tennisd` or `rescue/05a213d`.
- Do not change Render, production, or Neon without an explicit request.
- Do not force-push, reset hard, rebase published branches, or delete files without explicit agreement.
- Review Dependabot changes manually; require a green test check and compatibility review before merging.

## Backlog priorities

1. Verify repository synchronization and reconcile the expected `a23baca` state with the local `main` at `2127e6b`; verify GitHub Actions versions.
2. Introduce versioned database migrations.
3. Add PostgreSQL integration tests and migration/role checks in CI.
4. Make local and CI dependency installation reproducible.
5. Keep the repository synchronized with the expected upstream `main` state when network access is available.

Completed platform work includes live-sync observability, PostgreSQL CI coverage, reproducible development requirements, atomic PostgreSQL rate limiting, focused route modules, removal of request-time schema introspection, and spreadsheet-friendly CSV exports. The live page now polls scores every three minutes and displays provider freshness/outage state. Player ranking history is implemented from loaded snapshots; JSON export versioning is implemented with `schema_version: 1`; moderation audit logging is implemented in migration `0002`; followed-player/tournament in-app notifications are implemented with daily deduplication.

## Audit note

The local test command could not complete because the current Python environment lacks `defusedxml`, even though it is declared in `requirements.txt`. This is an environment reproducibility signal to address; it is not evidence of a production regression.
