CREATE TABLE IF NOT EXISTS manager_vision_worker (
    worker_id TEXT PRIMARY KEY,
    ready BOOLEAN NOT NULL DEFAULT false,
    status_detail TEXT NOT NULL DEFAULT '',
    heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS manager_vision_worker_freshness_idx
    ON manager_vision_worker (heartbeat_at DESC) WHERE ready = true;
