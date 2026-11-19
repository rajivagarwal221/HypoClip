CREATE TABLE IF NOT EXISTS live_auto_posts (
    clip_id VARCHAR(36) PRIMARY KEY,
    account_id VARCHAR(36) NOT NULL,
    claimed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    post_id VARCHAR(36)
);
