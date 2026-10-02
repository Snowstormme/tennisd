CREATE TABLE IF NOT EXISTS live_odds (
    id SERIAL PRIMARY KEY,
    live_match_id VARCHAR(64) NOT NULL REFERENCES live_match(provider_id) ON DELETE CASCADE,
    provider_event_id VARCHAR(100) NOT NULL,
    provider VARCHAR(32) NOT NULL DEFAULT 'the_odds_api',
    bookmaker VARCHAR(64) NOT NULL,
    market VARCHAR(16) NOT NULL DEFAULT 'h2h',
    player1_price DOUBLE PRECISION NOT NULL,
    player2_price DOUBLE PRECISION NOT NULL,
    player1_probability DOUBLE PRECISION NOT NULL,
    player2_probability DOUBLE PRECISION NOT NULL,
    source_updated_at TIMESTAMPTZ,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_live_match_bookmaker_market UNIQUE (live_match_id, bookmaker, market)
);
CREATE INDEX IF NOT EXISTS ix_live_odds_live_match_id ON live_odds (live_match_id);
