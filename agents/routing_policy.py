"""Deterministic route-task classification and strategy selection.

The language model may extract evidence, but this module owns the final routing
semantics so commute, leisure, buttons and profile use stay reproducible.
"""

from dataclasses import asdict, dataclass
import re
from typing import Mapping, Optional


STRATEGY_WEIGHTS = {
    "shortest": {"distance": 1.0, "slope": 0.0, "scenery": 0.0},
    "recommended": {"distance": 0.5, "slope": 0.2, "scenery": 0.3},
    "scenery": {"distance": 0.5, "slope": 0.1, "scenery": 0.4},
    "flat": {"distance": 0.5, "slope": 0.4, "scenery": 0.1},
}

_LEISURE = re.compile(
    r"游览|逛|散步|赏樱|赏花|拍照|打卡|带朋友|推荐.{0,6}路线|游玩|参观"
)
_SCENERY = re.compile(r"风景|景观|临湖|林荫|赏樱|赏花|拍照|打卡")
_FLAT = re.compile(r"平坦|平路|少爬坡|不爬坡|避开陡坡|省力|无障碍")
_SHORTEST = re.compile(r"最短|赶时间|赶课|快点|尽快|不绕路|别绕路")
_ALLOW_DETOUR = re.compile(
    r"远一点.{0,6}(没关系|可以|无所谓)|可以.{0,6}绕|不在乎.{0,4}距离"
)
_POI_LEISURE_VERB = re.compile(r"看看|参观|游玩")
_SCENIC_POI_TYPES = {"scenery", "landmark", "heritage", "attraction"}
_WEIGHT_KEYS = ("distance", "slope", "scenery")


@dataclass(frozen=True)
class StrategyDecision:
    """A complete, serializable routing-policy decision."""

    name: str
    source: str
    task_class: str
    weights: dict
    detour_cap: float

    def as_dict(self) -> dict:
        return asdict(self)


def _is_scenic_poi(end_poi: Optional[Mapping]) -> bool:
    if not isinstance(end_poi, Mapping):
        return False
    values = {
        str(end_poi.get("type") or "").lower(),
        str(end_poi.get("category") or "").lower(),
        str(end_poi.get("subcategory") or "").lower(),
    }
    return bool(values & _SCENIC_POI_TYPES)


def _bounded_profile(profile: Optional[Mapping]) -> Optional[dict]:
    if not profile or int(profile.get("accepted_count", 0)) < 3:
        return None
    raw = profile.get("weights") or {}
    try:
        values = {key: max(0.0, float(raw[key])) for key in _WEIGHT_KEYS}
    except (KeyError, TypeError, ValueError):
        return None
    total = sum(values.values())
    if total <= 0:
        return None

    distance = values["distance"] / total
    slope = values["slope"] / total
    scenery = values["scenery"] / total

    distance = max(0.40, distance)
    remaining = 1.0 - distance
    non_distance = slope + scenery
    slope_share = 0.5 if non_distance == 0 else slope / non_distance
    slope = min(0.50, remaining * slope_share)
    scenery = remaining - slope
    if scenery > 0.50:
        scenery = 0.50
        slope = remaining - scenery
    return {"distance": distance, "slope": slope, "scenery": scenery}


def _custom_weights(raw: Optional[Mapping]) -> Optional[dict]:
    if not isinstance(raw, Mapping):
        return None
    try:
        values = {
            key: min(0.90, max(0.05, float(raw.get(key, 0.0))))
            for key in _WEIGHT_KEYS
        }
    except (TypeError, ValueError):
        return None
    total = sum(values.values())
    if total <= 0:
        return None
    return {key: value / total for key, value in values.items()}


def select_route_strategy(
    query: str = "",
    end_poi: Optional[Mapping] = None,
    explicit_strategy: Optional[str] = None,
    strategy_source: str = "explicit_nl",
    profile: Optional[Mapping] = None,
    custom_weights: Optional[Mapping] = None,
) -> StrategyDecision:
    """Return the final routing strategy using the documented precedence."""

    text = query or ""
    if explicit_strategy == "custom":
        values = _custom_weights(custom_weights)
        if values:
            return StrategyDecision(
                "custom",
                strategy_source,
                "explicit_preference",
                values,
                1.50 if _ALLOW_DETOUR.search(text) else 1.25,
            )

    if explicit_strategy in STRATEGY_WEIGHTS:
        cap = (
            1.0
            if explicit_strategy == "shortest"
            else (1.50 if _ALLOW_DETOUR.search(text) else 1.25)
        )
        task_class = (
            "commute" if explicit_strategy == "shortest" else "explicit_preference"
        )
        return StrategyDecision(
            explicit_strategy,
            strategy_source,
            task_class,
            dict(STRATEGY_WEIGHTS[explicit_strategy]),
            cap,
        )

    if _SHORTEST.search(text):
        return StrategyDecision(
            "shortest",
            "explicit_nl",
            "commute",
            dict(STRATEGY_WEIGHTS["shortest"]),
            1.0,
        )
    if _FLAT.search(text):
        return StrategyDecision(
            "flat",
            "explicit_nl",
            "explicit_preference",
            dict(STRATEGY_WEIGHTS["flat"]),
            1.50 if _ALLOW_DETOUR.search(text) else 1.25,
        )
    if _SCENERY.search(text):
        return StrategyDecision(
            "scenery",
            "explicit_nl",
            "explicit_preference",
            dict(STRATEGY_WEIGHTS["scenery"]),
            1.50 if _ALLOW_DETOUR.search(text) else 1.25,
        )

    is_leisure = bool(_LEISURE.search(text))
    if not is_leisure and _POI_LEISURE_VERB.search(text) and _is_scenic_poi(end_poi):
        is_leisure = True
    if is_leisure:
        learned = _bounded_profile(profile)
        if learned:
            return StrategyDecision(
                "recommended", "profile", "leisure", learned, 1.25
            )
        return StrategyDecision(
            "recommended",
            "tour_default",
            "leisure",
            dict(STRATEGY_WEIGHTS["recommended"]),
            1.25,
        )

    return StrategyDecision(
        "shortest",
        "commute_default",
        "commute",
        dict(STRATEGY_WEIGHTS["shortest"]),
        1.0,
    )
