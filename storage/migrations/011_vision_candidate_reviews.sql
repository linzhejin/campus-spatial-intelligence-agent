-- Keep every visual candidate's human decision independently auditable.
ALTER TABLE manager_vision_job
    ADD COLUMN IF NOT EXISTS candidate_reviews JSONB NOT NULL DEFAULT '{}'::jsonb;
