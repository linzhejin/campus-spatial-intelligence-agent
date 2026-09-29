CREATE TABLE IF NOT EXISTS conversation (
    conversation_id UUID PRIMARY KEY,
    owner_id UUID NOT NULL UNIQUE,
    credential_hash BYTEA NOT NULL,
    revision BIGINT NOT NULL DEFAULT 0 CHECK (revision >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS task (
    task_id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES conversation(conversation_id) ON DELETE CASCADE,
    revision BIGINT NOT NULL DEFAULT 0 CHECK (revision >= 0),
    task_kind TEXT NOT NULL DEFAULT 'route',
    status TEXT NOT NULL DEFAULT 'draft',
    state JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS task_conversation_updated_idx
    ON task (conversation_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS conversation_message (
    conversation_id UUID NOT NULL REFERENCES conversation(conversation_id) ON DELETE CASCADE,
    seq BIGINT NOT NULL,
    message_id UUID NOT NULL UNIQUE,
    task_id UUID REFERENCES task(task_id) ON DELETE SET NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (conversation_id, seq)
);
CREATE INDEX IF NOT EXISTS conversation_message_task_idx ON conversation_message (task_id, seq);

CREATE TABLE IF NOT EXISTS task_history (
    task_id UUID NOT NULL REFERENCES task(task_id) ON DELETE CASCADE,
    revision BIGINT NOT NULL,
    state JSONB NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (task_id, revision)
);

CREATE TABLE IF NOT EXISTS profile (
    owner_id UUID PRIMARY KEY REFERENCES conversation(owner_id) ON DELETE CASCADE,
    state JSONB NOT NULL,
    revision BIGINT NOT NULL DEFAULT 0 CHECK (revision >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS run (
    run_id UUID PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES task(task_id) ON DELETE CASCADE,
    task_revision BIGINT NOT NULL,
    status TEXT NOT NULL,
    deadline_at TIMESTAMPTZ NOT NULL,
    lease_until TIMESTAMPTZ,
    attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    cancel_requested BOOLEAN NOT NULL DEFAULT false,
    idempotency_key TEXT NOT NULL UNIQUE,
    result JSONB,
    error JSONB,
    event_seq BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS run_claim_idx ON run (status, lease_until, created_at);

CREATE TABLE IF NOT EXISTS run_event (
    run_id UUID NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
    seq BIGINT NOT NULL,
    event_type TEXT NOT NULL,
    node_id TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, seq)
);

CREATE TABLE IF NOT EXISTS agent_schema_migration (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
