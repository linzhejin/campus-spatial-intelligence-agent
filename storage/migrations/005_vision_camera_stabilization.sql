ALTER TABLE manager_vision_job
    ADD COLUMN IF NOT EXISTS camera_stabilized BOOLEAN NOT NULL DEFAULT false;
