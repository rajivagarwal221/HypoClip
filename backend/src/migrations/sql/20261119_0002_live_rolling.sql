ALTER TABLE live_sessions ADD COLUMN IF NOT EXISTS task_ids JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE live_sessions ADD COLUMN IF NOT EXISTS youtube_account_id VARCHAR(36);
ALTER TABLE live_sessions ADD COLUMN IF NOT EXISTS processed_until_seconds INTEGER NOT NULL DEFAULT 0;
