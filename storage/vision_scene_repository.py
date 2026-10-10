"""Durable manager-owned templates for repeated aerial observation positions."""
from __future__ import annotations

import uuid
from typing import Any

from psycopg.types.json import Jsonb

from storage import database


def _scene_record(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {**row, "scene_id": str(row["scene_id"])}


def create_scene(url: str | None, *, name: str, created_by: str,
                 anchor_gcj: dict, camera_stabilized: bool, frame_signature: str,
                 observation_regions: list[dict]) -> dict:
    scene_id = str(uuid.uuid4())
    with database.connect(url) as conn:
        row = conn.execute(
            "INSERT INTO manager_vision_scene(scene_id,name,created_by,anchor_gcj,"
            "camera_stabilized,frame_signature,observation_regions)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s)"
            " RETURNING scene_id,name,created_by,anchor_gcj,camera_stabilized,"
            "frame_signature,observation_regions,created_at,updated_at",
            (scene_id, name, created_by, Jsonb(anchor_gcj), bool(camera_stabilized),
             frame_signature, Jsonb(observation_regions)),
        ).fetchone()
    return _scene_record(row)


def list_scenes(url: str | None) -> list[dict[str, Any]]:
    with database.connect(url) as conn:
        rows = conn.execute(
            "SELECT scene_id,name,created_by,anchor_gcj,camera_stabilized,frame_signature,"
            "observation_regions,created_at,updated_at FROM manager_vision_scene "
            "ORDER BY lower(name),scene_id"
        ).fetchall()
    return [_scene_record(row) for row in rows]


def get_scene(url: str | None, scene_id: str) -> dict[str, Any] | None:
    with database.connect(url) as conn:
        row = conn.execute(
            "SELECT scene_id,name,created_by,anchor_gcj,camera_stabilized,frame_signature,"
            "observation_regions,created_at,updated_at FROM manager_vision_scene WHERE scene_id=%s",
            (scene_id,),
        ).fetchone()
    return _scene_record(row)


def update_scene(url: str | None, scene_id: str, *, name: str, anchor_gcj: dict,
                 camera_stabilized: bool, frame_signature: str,
                 observation_regions: list[dict]) -> dict | None:
    with database.connect(url) as conn:
        row = conn.execute(
            "UPDATE manager_vision_scene SET name=%s,anchor_gcj=%s,camera_stabilized=%s,"
            "frame_signature=%s,observation_regions=%s,updated_at=now() WHERE scene_id=%s "
            "RETURNING scene_id,name,created_by,anchor_gcj,camera_stabilized,frame_signature,"
            "observation_regions,created_at,updated_at",
            (name, Jsonb(anchor_gcj), bool(camera_stabilized), frame_signature,
             Jsonb(observation_regions), scene_id),
        ).fetchone()
    return _scene_record(row)


def delete_scene(url: str | None, scene_id: str) -> bool:
    with database.connect(url) as conn:
        row = conn.execute(
            "DELETE FROM manager_vision_scene WHERE scene_id=%s RETURNING scene_id",
            (scene_id,),
        ).fetchone()
    return row is not None
