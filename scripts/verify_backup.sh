#!/usr/bin/env bash
# Restore only into the dedicated, empty GitHub Actions service database.
set -euo pipefail
archive=${1:?Usage: verify_backup.sh archive.dump}
target='postgresql://postgres:postgres@postgres:5432/tennisd_restore?sslmode=disable'
test -s "$archive"
existing=$(psql "$target" -X -A -t -v ON_ERROR_STOP=1 -c \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'")
if [ "$existing" != '0' ]; then
  echo 'Restore verification requires an empty CI database.' >&2
  exit 1
fi
pg_restore --exit-on-error --no-owner --no-acl --dbname="$target" "$archive"
required=$(psql "$target" -X -A -t -v ON_ERROR_STOP=1 -c \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('user','match','review')")
test "$required" = '3'
psql "$target" -X -t -v ON_ERROR_STOP=1 -c \
  'SELECT 1 FROM public."user" LIMIT 1; SELECT 1 FROM public.match LIMIT 1; SELECT 1 FROM public.review LIMIT 1;' >/dev/null
echo 'Backup restored successfully; core tables are readable.'
