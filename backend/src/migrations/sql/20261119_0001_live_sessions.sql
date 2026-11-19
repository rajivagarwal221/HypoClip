CREATE TABLE IF NOT EXISTS live_sessions (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    platform VARCHAR(20) NOT NULL,
    url VARCHAR(1000) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'queued',
    stop_requested BOOLEAN NOT NULL DEFAULT false,
    chat_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
    task_id VARCHAR(36) REFERENCES tasks(id) ON DELETE SET NULL,
    error_message VARCHAR(500),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    CONSTRAINT live_sessions_platform_check CHECK (platform IN ('twitch', 'youtube', 'kick')),
    CONSTRAINT live_sessions_status_check CHECK (status IN ('queued', 'recording', 'processing', 'completed', 'stopped', 'error'))
);
CREATE INDEX IF NOT EXISTS idx_live_sessions_user_created ON live_sessions (user_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_live_sessions_one_active_source
    ON live_sessions (user_id, url) WHERE status IN ('queued', 'recording', 'processing');
