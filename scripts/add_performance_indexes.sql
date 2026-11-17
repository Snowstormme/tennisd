CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_match_archive_browse
    ON "match" (week_start DESC, tournament, id);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_match_featured_finals
    ON "match" (week_start DESC, tour)
    WHERE round = 'F' AND level IN ('G', 'M', 'PM', 'P', 'A', 'I');

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_match_home_featured_recent
    ON "match" ((COALESCE(completed_at, scheduled_at)) DESC, week_start DESC, id DESC)
    WHERE status = 'finished' AND round <> 'R128'
      AND level IN ('G', 'M', 'PM', 'P', 'A', 'I', 'F');

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_match_tournament_catalog
    ON "match" (tournament, tour, surface, level, week_start);

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_match_tournament_search_trgm
    ON "match" USING gin (tournament gin_trgm_ops);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_player_name_search_trgm
    ON player USING gin (name gin_trgm_ops);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_live_match_status_schedule
    ON live_match (status, starts_at, tournament);
