CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_match_archive_browse
    ON "match" (week_start DESC, tournament, id);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_match_featured_finals
    ON "match" (week_start DESC, tour)
    WHERE round = 'F' AND level IN ('G', 'M', 'PM', 'P', 'A', 'I');

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_match_tournament_catalog
    ON "match" (tournament, tour, surface, level, week_start);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_live_match_status_schedule
    ON live_match (status, starts_at, tournament);
