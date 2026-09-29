"""Pure, atomic task updates. Ownership and CAS must also be checked in storage."""

from copy import deepcopy
from datetime import datetime
from typing import Iterable

from pydantic import ValidationError

from agents.state_models import FieldProvenance, LocationRef, RouteSpec, TaskPatch, TaskState, utc_now


class PatchValidationError(ValueError):
    pass


class RevisionConflict(PatchValidationError):
    pass


class ScopeViolation(PatchValidationError):
    pass


# Model/system-owned fields are deliberately absent. Field operations cannot
# alter identifiers, versions, artifacts, completion state or provenance.
EDITABLE_PATHS = frozenset({
    "route_spec.start", "route_spec.end", "route_spec.stops",
    "route_spec.travel_mode", "route_spec.objective", "route_spec.route_kind",
    "route_spec.strategy", "route_spec.tour", "route_spec.legs",
    "route_spec.hard_constraints",
    "route_spec.hard_constraints.avoid_slope",
    "route_spec.hard_constraints.avoid_steps",
    "route_spec.hard_constraints.require_verified_accessibility",
    "route_spec.hard_constraints.max_distance_m",
    "route_spec.hard_constraints.max_duration_s",
    "route_spec.hard_constraints.excluded_edge_ids",
})
LIST_PATHS = frozenset({"route_spec.stops", "route_spec.legs", "route_spec.hard_constraints.excluded_edge_ids"})
RESET_PATHS = frozenset({
    "route_spec.start", "route_spec.end", "route_spec.stops", "route_spec.tour", "route_spec.legs",
    *[path for path in EDITABLE_PATHS if path.startswith("route_spec.hard_constraints")],
})


def _slot(document, path):
    parts = path.split(".")
    parent = document
    for component in parts[:-1]:
        parent = parent[component]
    return parent, parts[-1]


def _read(document, path):
    parent, key = _slot(document, path)
    return parent[key]


def _remove_list_item(items, value, path):
    # A POI display name may change without changing its identity.
    if path == "route_spec.stops":
        target = LocationRef.model_validate(value)
        matches = [i for i, item in enumerate(items) if (
            item.get("poi_id") == target.poi_id if target.poi_id else
            item.get("coordinates") == target.coordinates.model_dump()
        )]
        if len(matches) != 1:
            raise PatchValidationError("stop removal must identify exactly one stored location")
        del items[matches[0]]
    else:
        if items.count(value) != 1:
            raise PatchValidationError("list removal must identify exactly one stored value")
        items.remove(value)


def validate_and_apply_patch(
    state: TaskState | dict,
    patch: TaskPatch | dict,
    *,
    trusted_history: Iterable[TaskState] | None = None,
    now: datetime | None = None,
) -> TaskState:
    """Return a fresh revision or raise; inputs are never changed.

    ``trusted_history`` must come from the owner's repository, never the request.
    Undo restores only editable route fields from revision N-1; it cannot roll
    back server data/event versions, artifacts, task identity or completed runs.
    Profile/session updates require their own separately authorized stores.
    """
    state = TaskState.model_validate(state)
    patch = TaskPatch.model_validate(patch)
    if state.task_id != patch.task_id:
        raise PatchValidationError("patch task does not match current task")
    if state.revision != patch.base_revision:
        raise RevisionConflict("patch base revision is stale")
    if any(operation.scope != "task" for operation in patch.operations):
        raise ScopeViolation("task patch cannot write session or profile scope")
    stamp = now if now is not None else utc_now()
    provenance = FieldProvenance(source=patch.source, scope="task", source_message_id=patch.source_message_id, updated_at=stamp)
    if provenance.updated_at < state.updated_at:
        raise PatchValidationError("patch timestamp must not precede current state")
    document = state.model_dump(mode="python")
    touched = set()
    if patch.operations[0].op == "undo":
        matches = [entry for entry in trusted_history or () if (
            isinstance(entry, TaskState) and entry.task_id == state.task_id
            and entry.conversation_id == state.conversation_id
            and entry.revision == state.revision - 1
        )]
        if len(matches) != 1:
            raise PatchValidationError("undo requires one trusted previous revision in task history")
        previous = TaskState.model_validate(matches[0].model_dump()).model_dump()
        # Restore top-level editable fields once; avoid conflicting nested resets.
        for path in sorted(path for path in EDITABLE_PATHS if path.count(".") == 1):
            value = _read(previous, path)
            if _read(document, path) != value:
                parent, key = _slot(document, path)
                parent[key] = deepcopy(value)
                touched.add(path)
    else:
        defaults = {"route_spec": RouteSpec().model_dump()}
        for operation in patch.operations:
            path = operation.path
            if path not in EDITABLE_PATHS:
                raise PatchValidationError(f"field is not editable: {path}")
            parent, key = _slot(document, path)
            if operation.op == "set":
                parent[key] = deepcopy(operation.value)
            elif operation.op == "add":
                if path not in LIST_PATHS or not isinstance(parent[key], list):
                    raise PatchValidationError("add requires an editable list field")
                parent[key].append(deepcopy(operation.value))
            elif operation.op == "remove" and path in LIST_PATHS and "value" in operation.model_fields_set:
                _remove_list_item(parent[key], operation.value, path)
            elif operation.op in {"remove", "clear"}:
                if path not in RESET_PATHS:
                    raise PatchValidationError("field does not support removal or clearing")
                if operation.op == "remove" and "value" in operation.model_fields_set:
                    raise PatchValidationError("scalar removal must not contain a value")
                if operation.op == "remove" and path in LIST_PATHS:
                    raise PatchValidationError("remove requires a list item; use clear to empty the list")
                parent[key] = deepcopy(_read(defaults, path))
            else:
                raise PatchValidationError("unsupported patch operation")
            touched.add(path)
    document["revision"] = state.revision + 1
    document["updated_at"] = provenance.updated_at
    for path in touched:
        # Replacing a parent invalidates provenance for all descendant values.
        for existing in list(document["field_provenance"]):
            if existing.startswith(path + "."):
                del document["field_provenance"][existing]
        document["field_provenance"][path] = provenance.model_dump()
    try:
        return TaskState.model_validate(document)
    except ValidationError as exc:
        raise PatchValidationError(f"invalid task patch: {exc}") from exc
