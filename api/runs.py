"""Owner-scoped progress, result and cancellation endpoints."""
from flask import Blueprint, current_app, jsonify, request

from api.conversations import _failure, _owner
from storage.task_repository import cancel_run, get_run

runs_bp = Blueprint("runs", __name__, url_prefix="/api/runs")


@runs_bp.get("/<run_id>")
def run_status(run_id: str):
    conversation_id = request.args.get("conversation_id", "")
    owner_id = _owner(conversation_id) if conversation_id else None
    if owner_id is None:
        return _failure("run_not_found", "任务不存在。", 404)
    try:
        after_seq = max(0, int(request.args.get("after_seq", "0")))
    except ValueError:
        return _failure("invalid_cursor", "after_seq 必须是非负整数。", 400)
    run = get_run(current_app.config.get("DATABASE_URL"), owner_id, run_id, after_seq)
    if run is None:
        return _failure("run_not_found", "任务不存在。", 404)
    return jsonify({"data": run})


@runs_bp.post("/<run_id>/cancel")
def cancel(run_id: str):
    body = request.get_json(silent=True) or {}
    conversation_id = body.get("conversation_id")
    owner_id = _owner(conversation_id) if isinstance(conversation_id, str) else None
    if owner_id is None:
        return _failure("run_not_found", "任务不存在。", 404)
    result = cancel_run(current_app.config.get("DATABASE_URL"), owner_id, run_id)
    if result is None:
        return _failure("run_not_found", "任务不存在。", 404)
    return jsonify({"data": result})
