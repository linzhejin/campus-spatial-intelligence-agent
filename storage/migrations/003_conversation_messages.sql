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
