ALTER TABLE manager_vision_job
    DROP CONSTRAINT IF EXISTS manager_vision_job_status_check;

ALTER TABLE manager_vision_job
    ADD CONSTRAINT manager_vision_job_status_check
    CHECK (status IN ('queued','running','needs_review','completed','failed','cancelled'));

ALTER TABLE manager_vision_job
    ADD COLUMN IF NOT EXISTS progress JSONB NOT NULL
    DEFAULT '{"phase":"queued","percent":0}'::jsonb;

ALTER TABLE manager_vision_job
    ADD COLUMN IF NOT EXISTS checkpoint JSONB;

ALTER TABLE manager_vision_job
    ADD COLUMN IF NOT EXISTS cancel_requested BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE manager_vision_job
    ADD COLUMN IF NOT EXISTS captured_at TIMESTAMPTZ;

ALTER TABLE manager_vision_job
    ADD COLUMN IF NOT EXISTS observation_regions JSONB NOT NULL DEFAULT '[]'::jsonb;
