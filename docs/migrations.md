# Database migrations

Schema changes are applied with the owner connection by `scripts/migrate.py`:

```bash
DATABASE_URL='[REDACTED]' python scripts/migrate.py
```

The runner stores applied versions in `tennisd_schema_migration` and takes a
PostgreSQL advisory lock so two owner jobs cannot apply the same version at the
same time. The web role must not run migrations and does not receive schema
creation privileges.

The current `0001_baseline` migration creates the schema represented by
`tennisd.models`. `0002_moderation_audit` adds the administrator action ledger.
`0003_notifications` adds durable in-app notifications.
Future schema changes should add the next ordered entry to `tennisd/migrations.py`,
implement it against the owner connection, and update the PostgreSQL smoke check
when the change affects startup or health behavior.

Existing databases can be adopted safely: the baseline uses idempotent
SQLAlchemy metadata creation, then records the version once the schema exists.
