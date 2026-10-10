CREATE TABLE IF NOT EXISTS manager_vision_scene (
    scene_id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    created_by TEXT NOT NULL,
    anchor_gcj JSONB NOT NULL,
    camera_stabilized BOOLEAN NOT NULL DEFAULT false,
    frame_signature CHAR(16) NOT NULL,
    observation_regions JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT manager_vision_scene_name_not_blank CHECK (length(btrim(name)) BETWEEN 1 AND 100),
    CONSTRAINT manager_vision_scene_signature_format CHECK (frame_signature ~ '^[0-9a-f]{16}$')
);

CREATE UNIQUE INDEX IF NOT EXISTS manager_vision_scene_name_lower_uq
    ON manager_vision_scene (lower(name));

ALTER TABLE manager_vision_job
    ADD COLUMN IF NOT EXISTS observation_scene_id UUID
    REFERENCES manager_vision_scene(scene_id) ON DELETE SET NULL;
