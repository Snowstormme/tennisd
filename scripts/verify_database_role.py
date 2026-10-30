"""Verify that the runtime PostgreSQL role has only application privileges."""

import os
import sys
from urllib.parse import parse_qs, urlparse

import psycopg


def check(label, condition):
    print(f"PASS {label}" if condition else f"FAIL {label}")
    return bool(condition)


def main():
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        print("FAIL DATABASE_URL is configured")
        return 1

    parsed = urlparse(database_url)
    sslmode = parse_qs(parsed.query).get("sslmode", [""])[0].lower()
    results = [check("DATABASE_URL uses TLS", sslmode in {"require", "verify-ca", "verify-full"})]
    if not results[-1]:
        return 1

    try:
        with psycopg.connect(database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT rolsuper, rolcreatedb, rolcreaterole
                    FROM pg_roles
                    WHERE rolname = current_user
                    """
                )
                role_flags = cursor.fetchone()
                results.append(check("current role exists", role_flags is not None))
                if role_flags is None:
                    return 1
                results.extend([
                    check("current role is not superuser", not role_flags[0]),
                    check("current role lacks CREATEDB", not role_flags[1]),
                    check("current role lacks CREATEROLE", not role_flags[2]),
                ])

                cursor.execute(
                    "SELECT has_schema_privilege(current_user, 'public', 'CREATE')"
                )
                results.append(check("current role lacks CREATE on public schema", not cursor.fetchone()[0]))

                cursor.execute(
                    """
                    SELECT count(*)
                    FROM pg_class AS c
                    JOIN pg_namespace AS n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public'
                      AND c.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')
                      AND c.relowner = current_user::regrole
                    """
                )
                results.append(check("current role owns no public tables or sequences", cursor.fetchone()[0] == 0))
    except Exception as exc:
        print(f"FAIL database connection ({type(exc).__name__})")
        return 1

    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
