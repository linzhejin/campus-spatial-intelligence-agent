"""Route strategies emitted by routing policy must survive durable task storage."""

from agents.routing_policy import select_route_strategy
from agents.state_models import TaskState
from storage.task_repository import _route_spec_from_result


def test_explicit_flat_and_scenery_strategies_validate_before_run_completion():
    current = TaskState(task_id="task-strategy", conversation_id="conversation-strategy")
    for hint in ("flat", "scenery"):
        decision = select_route_strategy(
            query="刚才的路线改为平坦优先" if hint == "flat" else "刚才的路线改为风景优先",
            explicit_strategy=hint,
        )
        route_state = {
            "route_kind": "direct", "travel_mode": "walk",
            "start": {"name": "玉兰2门", "type": "poi"},
            "end": {"name": "樱顶", "type": "poi"},
            "strategy": decision.as_dict(),
            "hard_constraints": {},
        }
        spec, _document = _route_spec_from_result(current, route_state)
        assert spec.strategy.name == hint
        assert spec.strategy.task_class == "explicit_preference"
