CREATE TABLE IF NOT EXISTS manager_vision_job (
    job_id UUID PRIMARY KEY,
    created_by TEXT NOT NULL,
    original_name TEXT NOT NULL,
    media_kind TEXT NOT NULL CHECK (media_kind IN ('image','video')),
    media_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    anchor_gcj JSONB NOT NULL,
    camera_stabilized BOOLEAN NOT NULL DEFAULT false,
    status TEXT NOT NULL CHECK (status IN ('queued','running','needs_review','completed','failed')),
    worker_id TEXT,
    lease_until TIMESTAMPTZ,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    result JSONB,
    error JSONB,
    review_status TEXT CHECK (review_status IS NULL OR review_status IN ('confirmed','dismissed')),
    review_note TEXT,
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS manager_vision_queue_idx
    ON manager_vision_job (status, lease_until, created_at);
