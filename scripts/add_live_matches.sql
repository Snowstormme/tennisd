-- Run once as the database owner before enabling the live-score workflow.
CREATE TABLE IF NOT EXISTS live_match (
  provider_id VARCHAR(64) PRIMARY KEY,
  status VARCHAR(12) NOT NULL,
  tour VARCHAR(3) NOT NULL,
  tournament VARCHAR(160) NOT NULL,
  tournament_id VARCHAR(80),
  surface VARCHAR(12) NOT NULL,
  round VARCHAR(32),
  draw VARCHAR(16) NOT NULL DEFAULT 'singles',
  is_doubles BOOLEAN NOT NULL DEFAULT FALSE,
  tier VARCHAR(32),
  starts_at TIMESTAMPTZ,
  player1_name VARCHAR(120) NOT NULL,
  player2_name VARCHAR(120) NOT NULL,
  player1_provider_id VARCHAR(32),
  player2_provider_id VARCHAR(32),
  score VARCHAR(160) NOT NULL DEFAULT '',
  server INTEGER,
  winner_side INTEGER,
  outcome VARCHAR(16),
  finished_at TIMESTAMPTZ,
  provider_updated_at TIMESTAMPTZ,
  synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_live_match_status ON live_match (status);
CREATE INDEX IF NOT EXISTS ix_live_match_tour ON live_match (tour);
CREATE INDEX IF NOT EXISTS ix_live_match_starts_at ON live_match (starts_at);
REVOKE ALL ON TABLE live_match FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE live_match TO rallylog_web;
