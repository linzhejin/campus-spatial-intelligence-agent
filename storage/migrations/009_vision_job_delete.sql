ALTER TABLE manager_vision_job
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS manager_vision_visible_jobs_idx
    ON manager_vision_job (created_at DESC)
    WHERE deleted_at IS NULL;
