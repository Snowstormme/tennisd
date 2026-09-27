-- Additive Tennisd catalog expansion. Safe to run repeatedly as the database owner.
CREATE TABLE IF NOT EXISTS tournament (
  id BIGSERIAL PRIMARY KEY, tour VARCHAR(3) NOT NULL, slug VARCHAR(160) NOT NULL,
  name VARCHAR(160) NOT NULL, location VARCHAR(160), country VARCHAR(3),
  CONSTRAINT uq_tournament_tour_slug UNIQUE (tour, slug)
);
CREATE TABLE IF NOT EXISTS tournament_edition (
  id BIGSERIAL PRIMARY KEY, tournament_id BIGINT NOT NULL REFERENCES tournament(id) ON DELETE CASCADE,
  season INTEGER NOT NULL, level VARCHAR(24), surface VARCHAR(12), starts_on DATE, ends_on DATE,
  status VARCHAR(16) NOT NULL DEFAULT 'finished', prize_money_usd BIGINT, currency VARCHAR(3) DEFAULT 'USD',
  CONSTRAINT uq_tournament_season UNIQUE (tournament_id, season)
);
ALTER TABLE tournament_edition ADD COLUMN IF NOT EXISTS level VARCHAR(24);
ALTER TABLE match ADD COLUMN IF NOT EXISTS scheduled_at TIMESTAMPTZ;
ALTER TABLE match ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;
ALTER TABLE match ADD COLUMN IF NOT EXISTS status VARCHAR(16) NOT NULL DEFAULT 'finished';
ALTER TABLE match ADD COLUMN IF NOT EXISTS provider VARCHAR(32);
ALTER TABLE match ADD COLUMN IF NOT EXISTS provider_id VARCHAR(80);
ALTER TABLE match ADD COLUMN IF NOT EXISTS edition_id BIGINT REFERENCES tournament_edition(id);
CREATE UNIQUE INDEX IF NOT EXISTS ix_match_provider_id ON match(provider_id) WHERE provider_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_match_status ON match(status);
CREATE INDEX IF NOT EXISTS ix_match_edition_id ON match(edition_id);
ALTER TABLE live_match ADD COLUMN IF NOT EXISTS draw VARCHAR(16) NOT NULL DEFAULT 'singles';
ALTER TABLE live_match ADD COLUMN IF NOT EXISTS is_doubles BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE live_match ADD COLUMN IF NOT EXISTS tier VARCHAR(32);
ALTER TABLE live_match ADD COLUMN IF NOT EXISTS winner_side INTEGER;
ALTER TABLE live_match ADD COLUMN IF NOT EXISTS outcome VARCHAR(16);
ALTER TABLE live_match ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS player_external_id (
 id BIGSERIAL PRIMARY KEY, player_id VARCHAR(24) NOT NULL REFERENCES player(id) ON DELETE CASCADE,
 provider VARCHAR(32) NOT NULL, external_id VARCHAR(120) NOT NULL,
 CONSTRAINT uq_player_provider_id UNIQUE(provider, external_id)
);
CREATE TABLE IF NOT EXISTS player_photo (
 id BIGSERIAL PRIMARY KEY, player_id VARCHAR(24) NOT NULL REFERENCES player(id) ON DELETE CASCADE,
 url VARCHAR(1000) NOT NULL, source_url VARCHAR(1000), license_name VARCHAR(80),
 attribution VARCHAR(300), is_primary BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE TABLE IF NOT EXISTS ranking_snapshot (
 id BIGSERIAL PRIMARY KEY, player_id VARCHAR(24) NOT NULL REFERENCES player(id) ON DELETE CASCADE,
 ranked_on DATE NOT NULL, ranking_type VARCHAR(16) NOT NULL DEFAULT 'singles', rank INTEGER NOT NULL,
 points INTEGER, source VARCHAR(32),
 CONSTRAINT uq_player_ranking_date_type UNIQUE(player_id, ranked_on, ranking_type)
);
CREATE TABLE IF NOT EXISTS match_statistic (
 id BIGSERIAL PRIMARY KEY, match_id VARCHAR(100) NOT NULL REFERENCES match(id) ON DELETE CASCADE,
 player_id VARCHAR(24) NOT NULL REFERENCES player(id), aces INTEGER, double_faults INTEGER,
 serve_points INTEGER, first_serves_in INTEGER, first_serve_points_won INTEGER,
 break_points_saved INTEGER, break_points_faced INTEGER, source VARCHAR(32),
 CONSTRAINT uq_match_player_stat UNIQUE(match_id, player_id)
);
CREATE TABLE IF NOT EXISTS match_participant (
 id BIGSERIAL PRIMARY KEY, match_id VARCHAR(100) NOT NULL REFERENCES match(id) ON DELETE CASCADE,
 player_id VARCHAR(24) REFERENCES player(id), side INTEGER NOT NULL, seed VARCHAR(12), entry VARCHAR(12),
 is_winner BOOLEAN, CONSTRAINT uq_match_side UNIQUE(match_id, side)
);
CREATE TABLE IF NOT EXISTS match_set (
 id BIGSERIAL PRIMARY KEY, match_id VARCHAR(100) NOT NULL REFERENCES match(id) ON DELETE CASCADE,
 set_number INTEGER NOT NULL, side1_games INTEGER, side2_games INTEGER,
 side1_tiebreak INTEGER, side2_tiebreak INTEGER,
 CONSTRAINT uq_match_set_number UNIQUE(match_id, set_number)
);
CREATE TABLE IF NOT EXISTS prize_money_award (
 id BIGSERIAL PRIMARY KEY, edition_id BIGINT NOT NULL REFERENCES tournament_edition(id) ON DELETE CASCADE,
 round VARCHAR(32) NOT NULL, amount BIGINT NOT NULL, currency VARCHAR(3) NOT NULL,
 source_url VARCHAR(1000), CONSTRAINT uq_edition_round_prize UNIQUE(edition_id, round, currency)
);
CREATE TABLE IF NOT EXISTS ingestion_run (
 id BIGSERIAL PRIMARY KEY, provider VARCHAR(32) NOT NULL, started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 finished_at TIMESTAMPTZ, status VARCHAR(16) NOT NULL DEFAULT 'running', records_seen INTEGER NOT NULL DEFAULT 0,
 records_written INTEGER NOT NULL DEFAULT 0, error VARCHAR(1000)
);

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rallylog_web') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO rallylog_web;
    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO rallylog_web;
  END IF;
END $$;
