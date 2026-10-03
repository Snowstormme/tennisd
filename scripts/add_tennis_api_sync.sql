CREATE TABLE IF NOT EXISTS ingestion_cursor (
  provider VARCHAR(32) NOT NULL,
  feed VARCHAR(64) NOT NULL,
  value VARCHAR(500) NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (provider, feed)
);

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rallylog_web') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON ingestion_cursor TO rallylog_web;
  END IF;
END $$;
