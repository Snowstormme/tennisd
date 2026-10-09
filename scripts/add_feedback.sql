-- Run once as the Neon database owner for an existing Tennisd deployment.
CREATE TABLE IF NOT EXISTS feedback_submission (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES "user"(id) ON DELETE SET NULL,
    category VARCHAR(16) NOT NULL CHECK (category IN ('idea', 'bug', 'content', 'other')),
    rating INTEGER CHECK (rating IS NULL OR (rating >= 1 AND rating <= 5)),
    message VARCHAR(2000) NOT NULL,
    page_path VARCHAR(300),
    status VARCHAR(12) NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'reviewed', 'planned', 'closed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_feedback_submission_user_id ON feedback_submission (user_id);
CREATE INDEX IF NOT EXISTS ix_feedback_submission_category ON feedback_submission (category);
CREATE INDEX IF NOT EXISTS ix_feedback_submission_status ON feedback_submission (status);

CREATE TABLE IF NOT EXISTS poll (
    id SERIAL PRIMARY KEY,
    question VARCHAR(240) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    starts_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ends_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_poll_is_active ON poll (is_active);

CREATE TABLE IF NOT EXISTS poll_option (
    id SERIAL PRIMARY KEY,
    poll_id INTEGER NOT NULL REFERENCES poll(id) ON DELETE CASCADE,
    label VARCHAR(120) NOT NULL,
    position INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_poll_option_poll_id ON poll_option (poll_id);

CREATE TABLE IF NOT EXISTS poll_vote (
    id SERIAL PRIMARY KEY,
    poll_id INTEGER NOT NULL REFERENCES poll(id) ON DELETE CASCADE,
    option_id INTEGER NOT NULL REFERENCES poll_option(id) ON DELETE CASCADE,
    user_id INTEGER REFERENCES "user"(id) ON DELETE SET NULL,
    visitor_key VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_poll_visitor_vote UNIQUE (poll_id, visitor_key)
);
CREATE INDEX IF NOT EXISTS ix_poll_vote_poll_id ON poll_vote (poll_id);
CREATE INDEX IF NOT EXISTS ix_poll_vote_option_id ON poll_vote (option_id);
CREATE INDEX IF NOT EXISTS ix_poll_vote_user_id ON poll_vote (user_id);

INSERT INTO poll (question)
SELECT 'What should Tennisd improve next?'
WHERE NOT EXISTS (SELECT 1 FROM poll);
INSERT INTO poll_option (poll_id, label, position)
SELECT poll.id, option.label, option.position
FROM poll
CROSS JOIN (VALUES
    ('Live scores and match data', 1),
    ('Player and tournament pages', 2),
    ('Diary and community features', 3),
    ('Speed and mobile experience', 4)
) AS option(label, position)
WHERE poll.question = 'What should Tennisd improve next?'
  AND NOT EXISTS (SELECT 1 FROM poll_option WHERE poll_option.poll_id = poll.id);

REVOKE ALL PRIVILEGES ON TABLE feedback_submission, poll, poll_option, poll_vote FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE feedback_submission, poll, poll_option, poll_vote TO rallylog_web;
GRANT USAGE, SELECT ON SEQUENCE feedback_submission_id_seq, poll_id_seq, poll_option_id_seq, poll_vote_id_seq TO rallylog_web;
