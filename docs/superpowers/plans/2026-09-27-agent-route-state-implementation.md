# Agent Route Strategy and State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make commute, leisure recommendation, explicit strategy selection, profile learning, and travel-mode changes deterministic, reproducible, and immune to route-context loss.

**Architecture:** A pure Python policy module decides the route strategy before routing. The backend owns a validated `route_state` snapshot and exposes one deterministic replan endpoint for direct, via, tour, and multimodal routes. The browser keeps that snapshot as the single source of truth and commits a mode or strategy change only after a successful response.

**Tech Stack:** Python 3, Flask, Pydantic, NetworkX, pytest, browser JavaScript, Node built-in test runner, JSON telemetry.

## Global Constraints

- Ordinary commute and explicit shortest requests use the `shortest` strategy with semantic weights `1.00/0.00/0.00`.
- `shortest` bypasses `resolve_weights()` and runs one length-based Dijkstra after mode, closure, and hard-constraint filtering.
- Leisure recommendation uses a reliable profile when available; otherwise it uses `0.50/0.20/0.30`.
- The visible scenery strategy uses `0.50/0.10/0.40`; the visible flat strategy uses `0.50/0.40/0.10`.
- `recommended`, `scenery`, and `flat` use a default detour cap of `1.25`; an explicit willingness to detour may raise it to `1.50`.
- POI category is supporting evidence only. A scenic destination by itself never changes an ordinary “go to X” request into leisure.
- Explicit hard constraints stay separate from soft strategy weights and are never relaxed by a strategy button.
- `route_state` is the only source for deterministic replanning; mode and strategy changes preserve endpoints, via points, tour stops, legs, original query, and constraints.
- Replanning never invokes the LLM. A failed replan leaves the previously displayed route and state intact.
- Route exposure and a 20-second delay never update the user profile.
- Only a user-selected leisure strategy confirmed by navigation start or completion updates the leisure profile, at most once per route ID.
- Profile weights keep distance at least `0.40` and each of slope and scenery at most `0.50` after normalization.
- Formal paper experiments begin only after the research snapshot and readiness gate pass.

---

## File Map

**Create**

- `agents/routing_policy.py` — task classification, strategy precedence, fixed weights, profile bounding, and detour intent.
- `agents/route_state.py` — server-owned route-state schema, sanitization, version checking, and single-change application.
- `spatial/routing_index.py` — reusable static graph preparation and per-query routing context.
- `static/js/route-state.js` — browser state transaction helpers that are testable without the DOM.
- `tests/test_routing_policy.py` — policy matrix and profile-bound tests.
- `tests/test_route_state.py` — whitelist, version, and immutable-field tests.
- `tests/test_replan_api.py` — direct/via/tour/multimodal replan contract tests.
- `tests/js/route-state.test.js` — browser state transaction and rollback tests.
- `scripts/validate/check_agent_research_readiness.py` — one command for the structural research gate.
- `experiments/research_snapshot.json` — generated version manifest, committed only when the gate is green.

**Modify**

- `agents/parser.py` and `agents/preferences.py` — emit task evidence while leaving the final strategy to the policy module.
- `agents/planner.py` and `agents/tools.py` — pass policy decisions into all route kinds and return canonical route state.
- `agents/profile.py` — learn only from confirmed, explicit leisure choices and deduplicate by route ID.
- `agents/prompts/parse_system.txt` — remove obsolete numeric-policy ownership from the LLM prompt.
- `spatial/routing.py` — exact shortest branch, strict detour cap, prepared-context reuse, and timing counters.
- `spatial/network.py` — build and invalidate the static routing index with the graph cache.
- `api/routes.py` — central strategy resolution, route-state response, deterministic `/api/route/replan`, and telemetry signals.
- `static/index.html` — visible shortest/scenery/flat strategy selector and route-state script.
- `static/css/style.css` — accessible strategy-selector states.
- `static/js/app.js` — replace `lastRouteRequest`/split route intent with atomic `routeState` commits.
- `static/js/navigation.js` — send navigation confirmation events with route ID and strategy.
- `package.json` — add the Node state-test command without adding a dependency.
- `tests/test_routing.py`, `tests/test_multiturn.py`, `tests/test_profile.py`, `tests/test_preferences.py`, `tests/test_telemetry.py` — regression and contract coverage.

---

### Task 1: Centralize Task Classification and Strategy Precedence

**Files:**
- Create: `agents/routing_policy.py`
- Create: `tests/test_routing_policy.py`
- Modify: `agents/preferences.py`
- Modify: `agents/parser.py:77-87,700-933`
- Modify: `agents/prompts/parse_system.txt:64-86`

**Interfaces:**
- Consumes: `query: str`, normalized destination POI, optional explicit strategy, optional profile record.
- Produces: `select_route_strategy(...) -> StrategyDecision`; `StrategyDecision.as_dict() -> dict` with `name`, `source`, `task_class`, `weights`, and `detour_cap`.

- [ ] **Step 1: Write the policy matrix as failing tests**

```python
# tests/test_routing_policy.py
import pytest

from agents.routing_policy import select_route_strategy


@pytest.mark.parametrize("query", [
    "从梅园到教五", "去食堂", "赶课，快点到", "到樱顶",
])
def test_commute_is_exact_shortest_even_for_scenic_destination(query):
    decision = select_route_strategy(
        query=query,
        end_poi={"name": "樱顶", "type": "scenery"},
    )
    assert decision.as_dict() == {
        "name": "shortest",
        "source": "commute_default",
        "task_class": "commute",
        "weights": {"distance": 1.0, "slope": 0.0, "scenery": 0.0},
        "detour_cap": 1.0,
    }


def test_leisure_uses_balanced_default_without_profile():
    decision = select_route_strategy(query="带朋友逛武大，沿途看看风景")
    assert decision.name == "recommended"
    assert decision.source == "tour_default"
    assert decision.weights == {"distance": 0.5, "slope": 0.2, "scenery": 0.3}
    assert decision.detour_cap == 1.25


def test_button_beats_profile_and_query_inference():
    decision = select_route_strategy(
        query="从梅园到教五",
        explicit_strategy="scenery",
        strategy_source="button",
        profile={"weights": {"distance": 0.5, "slope": 0.4, "scenery": 0.1}, "accepted_count": 8},
    )
    assert decision.name == "scenery"
    assert decision.source == "button"
    assert decision.weights == {"distance": 0.5, "slope": 0.1, "scenery": 0.4}


def test_explicit_detour_language_raises_but_caps_ratio():
    decision = select_route_strategy(query="远一点没关系，尽量走风景好的路")
    assert decision.name == "scenery"
    assert decision.source == "explicit_nl"
    assert decision.detour_cap == 1.5


def test_profile_is_bounded_before_use():
    decision = select_route_strategy(
        query="随便逛逛，推荐一条路线",
        profile={"weights": {"distance": 0.1, "slope": 0.1, "scenery": 0.8}, "accepted_count": 9},
    )
    assert decision.source == "profile"
    assert decision.weights["distance"] >= 0.40
    assert decision.weights["scenery"] <= 0.50
    assert sum(decision.weights.values()) == pytest.approx(1.0)


def test_custom_numeric_weights_remain_custom_and_bounded():
    decision = select_route_strategy(
        query="距离坡度风景按 6:3:1",
        explicit_strategy="custom",
        custom_weights={"distance": 0.6, "slope": 0.3, "scenery": 0.1},
    )
    assert decision.name == "custom"
    assert decision.weights == {"distance": 0.6, "slope": 0.3, "scenery": 0.1}
```

- [ ] **Step 2: Run the focused test and verify the module is missing**

Run: `python -m pytest tests/test_routing_policy.py -q`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'agents.routing_policy'`.

- [ ] **Step 3: Implement the pure policy module**

```python
# agents/routing_policy.py
from dataclasses import asdict, dataclass
import re
from typing import Mapping, Optional


STRATEGY_WEIGHTS = {
    "shortest": {"distance": 1.0, "slope": 0.0, "scenery": 0.0},
    "recommended": {"distance": 0.5, "slope": 0.2, "scenery": 0.3},
    "scenery": {"distance": 0.5, "slope": 0.1, "scenery": 0.4},
    "flat": {"distance": 0.5, "slope": 0.4, "scenery": 0.1},
}
_LEISURE = re.compile(r"游览|逛|散步|赏樱|赏花|看风景|赏景|拍照|打卡|带朋友|推荐.*路线")
_SCENERY = re.compile(r"风景|景观|临湖|林荫|赏樱|赏花|拍照|打卡")
_FLAT = re.compile(r"平坦|平路|少爬坡|不爬坡|避开陡坡|省力|无障碍")
_SHORTEST = re.compile(r"最短|赶时间|赶课|快点|尽快|不绕路|别绕路")
_ALLOW_DETOUR = re.compile(r"远一点.{0,6}(没关系|可以|无所谓)|可以.{0,6}绕|不在乎.{0,4}距离")


@dataclass(frozen=True)
class StrategyDecision:
    name: str
    source: str
    task_class: str
    weights: dict
    detour_cap: float

    def as_dict(self) -> dict:
        return asdict(self)


def _bounded_profile(profile: Optional[Mapping]) -> Optional[dict]:
    if not profile or int(profile.get("accepted_count", 0)) < 3:
        return None
    raw = profile.get("weights") or {}
    try:
        distance = max(0.40, float(raw["distance"]))
        slope = min(0.50, max(0.0, float(raw["slope"])))
        scenery = min(0.50, max(0.0, float(raw["scenery"])))
    except (KeyError, TypeError, ValueError):
        return None
    total = distance + slope + scenery
    distance, slope, scenery = distance / total, slope / total, scenery / total
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


def select_route_strategy(
    query: str = "",
    end_poi: Optional[Mapping] = None,
    explicit_strategy: Optional[str] = None,
    strategy_source: str = "explicit_nl",
    profile: Optional[Mapping] = None,
    custom_weights: Optional[Mapping] = None,
) -> StrategyDecision:
    text = query or ""
    if explicit_strategy == "custom" and custom_weights:
        values = {
            key: min(0.90, max(0.05, float(custom_weights.get(key, 0.0))))
            for key in ("distance", "slope", "scenery")
        }
        total = sum(values.values())
        values = {key: value / total for key, value in values.items()}
        return StrategyDecision("custom", strategy_source, "explicit_preference", values,
                                1.50 if _ALLOW_DETOUR.search(text) else 1.25)
    if explicit_strategy in STRATEGY_WEIGHTS:
        cap = 1.0 if explicit_strategy == "shortest" else (1.50 if _ALLOW_DETOUR.search(text) else 1.25)
        task_class = "commute" if explicit_strategy == "shortest" else "explicit_preference"
        return StrategyDecision(explicit_strategy, strategy_source, task_class,
                                dict(STRATEGY_WEIGHTS[explicit_strategy]), cap)
    if _SHORTEST.search(text):
        return StrategyDecision("shortest", "explicit_nl", "commute",
                                dict(STRATEGY_WEIGHTS["shortest"]), 1.0)
    if _FLAT.search(text):
        return StrategyDecision("flat", "explicit_nl", "explicit_preference",
                                dict(STRATEGY_WEIGHTS["flat"]),
                                1.50 if _ALLOW_DETOUR.search(text) else 1.25)
    if _SCENERY.search(text):
        return StrategyDecision("scenery", "explicit_nl", "explicit_preference",
                                dict(STRATEGY_WEIGHTS["scenery"]),
                                1.50 if _ALLOW_DETOUR.search(text) else 1.25)
    if _LEISURE.search(text):
        learned = _bounded_profile(profile)
        if learned:
            return StrategyDecision("recommended", "profile", "leisure", learned, 1.25)
        return StrategyDecision("recommended", "tour_default", "leisure",
                                dict(STRATEGY_WEIGHTS["recommended"]), 1.25)
    return StrategyDecision("shortest", "commute_default", "commute",
                            dict(STRATEGY_WEIGHTS["shortest"]), 1.0)
```

- [ ] **Step 4: Make parser output evidence rather than inventing numeric policy**

Add `strategy_hint` to `TaskIntent`, map explicit natural-language preference to `shortest|scenery|flat|recommended`, and keep `weights` only for backward-compatible custom requests. Replace prompt examples that use `0.90/0.05/0.05`, `0.2/0.1/0.7`, and `0.2/0.6/0.2` with strategy names. The post-processor must call the same regex helpers used by `agents.preferences`, so a mode-only follow-up cannot create a new preference.

```python
# agents/parser.py — TaskIntent addition
strategy_hint: Optional[Literal["shortest", "recommended", "scenery", "flat", "custom"]] = None
```

```text
# agents/prompts/parse_system.txt — replacement policy sentence
你只负责识别任务和明确偏好。普通通勤输出 strategy_hint="shortest"；明确游览或推荐输出
strategy_hint="recommended"；明确风景、平坦、最短分别输出 scenery、flat、shortest。
不要为固定策略生成数值权重；只有用户同时指定可量化比例时才输出 custom weights。
```

- [ ] **Step 5: Run focused parser and policy tests**

Run: `python -m pytest tests/test_routing_policy.py tests/test_preferences.py tests/test_parser.py -q`

Expected: PASS, including “到樱顶” remaining `shortest` and “带朋友逛武大” becoming `recommended`.

- [ ] **Step 6: Commit the policy boundary**

```bash
git add agents/routing_policy.py agents/preferences.py agents/parser.py agents/prompts/parse_system.txt tests/test_routing_policy.py tests/test_preferences.py tests/test_parser.py
git commit -m "feat(agent): centralize route strategy policy"
```

---

### Task 2: Implement Exact Shortest and Enforced Detour Caps

**Files:**
- Modify: `spatial/routing.py:671-700,832-970,1017-1287`
- Modify: `tests/test_routing.py`

**Interfaces:**
- Consumes: `strategy_name: str`, `weights: dict`, `detour_cap: float`.
- Produces: `compute_route(..., strategy_name="shortest", detour_cap=1.0) -> dict` with `strategy`, `detour_cap`, `dijkstra_runs`, and a recommended route that never exceeds its cap.

- [ ] **Step 1: Add failing routing tests for one-search shortest and cap enforcement**

```python
# tests/test_routing.py
@pytest.fixture
def detour_graph():
    G = nx.MultiDiGraph()
    for node in (1, 2, 4):
        G.add_node(node, x=114.360, y=30.535)
    G.add_edge(1, 4, length=100.0, slope_level=5, scenery_level=1)
    G.add_edge(1, 2, length=80.0, slope_level=2, scenery_level=5)
    G.add_edge(2, 4, length=80.0, slope_level=2, scenery_level=5)
    return G


def test_shortest_strategy_runs_one_search_and_returns_identical_paths(mock_graph):
    result = compute_route(mock_graph, 0, 5, strategy_name="shortest")
    assert result["applied_weights"] == {"distance": 1.0, "slope": 0.0, "scenery": 0.0}
    assert result["recommended_edges"] == result["shortest_edges"]
    assert result["recommended_length_m"] == result["shortest_length_m"]
    assert result["dijkstra_runs"] == 1


def test_weighted_strategy_never_returns_route_beyond_cap(detour_graph):
    result = compute_route(
        detour_graph, 1, 4,
        strategy_name="scenery",
        weights={"distance": 0.5, "slope": 0.1, "scenery": 0.4},
        detour_cap=1.25,
    )
    assert result["recommended_length_m"] <= result["shortest_length_m"] * 1.25 + 0.1
    assert result["length_capped"] is True


def test_shortest_still_obeys_hard_closure(detour_graph):
    result = compute_route(
        detour_graph, 1, 4,
        strategy_name="shortest",
        constraints={"slope": "avoid"},
    )
    assert (1, 4, 0) not in result["recommended_edges"]
```

- [ ] **Step 2: Verify the new arguments fail**

Run: `python -m pytest tests/test_routing.py -k "shortest_strategy or beyond_cap or hard_closure" -q`

Expected: FAIL with `TypeError: compute_route() got an unexpected keyword argument 'strategy_name'`.

- [ ] **Step 3: Extract one baseline weight and one path-search helper**

```python
# spatial/routing.py
def _length_weight(u, v, data):
    if data and all(isinstance(value, dict) for value in data.values()):
        return min(float(attrs.get("length", 0) or 0) for attrs in data.values())
    return float((data or {}).get("length", 0) or 0)


def _run_path(G, start_node, end_node, weight):
    nodes = nx.dijkstra_path(G, start_node, end_node, weight=weight)
    edges = _select_path_edges(G, nodes, weight)
    return nodes, edges
```

Extend the signature:

```python
def compute_route(
    G, start_node, end_node, constraints=None, weights=None, mode="walk",
    road_conditions=None, weather_info=None, disable_hard_filter=False,
    strategy_name="recommended", detour_cap=1.25, prepared=None,
):
```

- [ ] **Step 4: Add the exact shortest branch before weighted search**

```python
if strategy_name == "shortest":
    resolved_weights = {"distance": 1.0, "slope": 0.0, "scenery": 0.0}
    shortest, shortest_edges = _run_path(G_filtered, start_node, end_node, _length_weight)
    recommended, recommended_edges = shortest, shortest_edges
    dijkstra_runs = 1
else:
    shortest, shortest_edges = _run_path(G_filtered, start_node, end_node, _length_weight)
    recommended, recommended_edges = _run_path(G_filtered, start_node, end_node, edge_weight)
    dijkstra_runs = 2
```

Keep closures, mode filtering, access restrictions, and hard constraints before this branch. Remove the current `resolve_weights()` call from the shortest path.

- [ ] **Step 5: Replace “mark only” capping with deterministic feasible fallback**

If the weighted route is over cap, interpolate the requested weights toward exact distance with fixed factors `0.75`, `0.50`, and `0.25`. Keep the first route within cap; if none qualifies, use the already-computed shortest route. Count every actual search in `dijkstra_runs`.

```python
def _interpolate_to_distance(weights, factor):
    return {
        "distance": factor * weights["distance"] + (1.0 - factor),
        "slope": factor * weights["slope"],
        "scenery": factor * weights["scenery"],
    }


limit = shortest_len * float(detour_cap)
if strategy_name != "shortest" and recommended_len > limit:
    length_capped = True
    for factor in (0.75, 0.50, 0.25):
        candidate_weights = _interpolate_to_distance(resolved_weights, factor)
        candidate_weight = _edge_cost_factory(
            G_filtered, norm_lengths, candidate_weights, penalty_map,
            annotation_degraded, outside_road_penalty=outside_penalty,
        )
        candidate, candidate_edges = _run_path(
            G_filtered, start_node, end_node, candidate_weight,
        )
        dijkstra_runs += 1
        candidate_len = _path_length(G, candidate, candidate_edges)
        if candidate_len <= limit:
            recommended, recommended_edges = candidate, candidate_edges
            recommended_len = candidate_len
            break
    else:
        recommended, recommended_edges = shortest, shortest_edges
        recommended_len = shortest_len
```

- [ ] **Step 6: Return traceable strategy and search data**

Add to the result:

```python
"strategy": strategy_name,
"detour_cap": float(detour_cap),
"dijkstra_runs": dijkstra_runs,
```

- [ ] **Step 7: Run the routing regression slice**

Run: `python -m pytest tests/test_routing.py tests/test_road_conditions.py -q`

Expected: PASS; no returned weighted route exceeds the requested ratio and shortest reports exactly one search.

- [ ] **Step 8: Commit the routing semantics**

```bash
git add spatial/routing.py tests/test_routing.py tests/test_road_conditions.py
git commit -m "feat(routing): enforce exact shortest and detour caps"
```

---

### Task 3: Reuse Static Routing Preparation

**Files:**
- Create: `spatial/routing_index.py`
- Modify: `spatial/network.py:18-129,267-285`
- Modify: `spatial/routing.py:703-720,1017-1287,1406-1623`
- Modify: `agents/tools.py:612-942`
- Modify: `tests/test_network.py`
- Modify: `tests/test_routing.py`

**Interfaces:**
- Produces: `get_routing_index(G) -> RoutingIndex`, `invalidate_routing_index() -> None`, `RoutingIndex.for_mode(mode) -> ModeIndex`.
- `ModeIndex` contains `graph`, `mode_status`, `mode_penalty`, `max_len`, and keyed `norm_lengths`.
- `compute_route(..., prepared: ModeIndex | None)` reuses the same object across direct, via, tour, and multimodal legs.

- [ ] **Step 1: Write failing cache-reuse tests**

```python
# tests/test_network.py
from spatial.routing_index import get_routing_index, invalidate_routing_index


def test_routing_index_reuses_mode_graph_and_invalidates_on_reload():
    G = _make_graph(4)
    first = get_routing_index(G).for_mode("walk")
    second = get_routing_index(G).for_mode("walk")
    assert first is second
    invalidate_routing_index()
    third = get_routing_index(G).for_mode("walk")
    assert third is not first
```

```python
# tests/test_routing.py
def test_via_route_prepares_mode_graph_once(monkeypatch, mock_graph):
    calls = 0
    original = routing_index.build_mode_index
    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)
    monkeypatch.setattr(routing_index, "build_mode_index", counted)
    compute_via_route(mock_graph, 0, 2, 5, mode="walk")
    assert calls == 1
```

- [ ] **Step 2: Run and verify missing index API**

Run: `python -m pytest tests/test_network.py tests/test_routing.py -k "routing_index or prepares_mode_graph_once" -q`

Expected: FAIL importing `spatial.routing_index`.

- [ ] **Step 3: Implement the bounded in-process index**

```python
# spatial/routing_index.py
from dataclasses import dataclass
from threading import RLock
import weakref

@dataclass(frozen=True)
class ModeIndex:
    graph: object
    mode_status: str
    mode_penalty: dict
    max_len: float
    norm_lengths: dict


class RoutingIndex:
    def __init__(self, graph):
        self.graph = graph
        self._modes = {}

    def for_mode(self, mode):
        from spatial.routing import normalize_mode
        mode = normalize_mode(mode)
        if mode not in self._modes:
            self._modes[mode] = build_mode_index(self.graph, mode)
        return self._modes[mode]


def build_mode_index(graph, mode):
    # Local import avoids a module cycle when spatial.routing asks for the index.
    from spatial.routing import _normalize_lengths, filter_graph_for_mode, normalize_mode
    mode = normalize_mode(mode)
    mode_graph, status, penalty = filter_graph_for_mode(graph, mode)
    max_len, norm_lengths = _normalize_lengths(mode_graph)
    return ModeIndex(mode_graph, status, penalty, max_len, norm_lengths)


_lock = RLock()
_graph_ref = None
_index = None


def get_routing_index(graph):
    global _graph_ref, _index
    with _lock:
        if _graph_ref is None or _graph_ref() is not graph:
            _graph_ref = weakref.ref(graph)
            _index = RoutingIndex(graph)
        return _index


def invalidate_routing_index():
    global _graph_ref, _index
    with _lock:
        _graph_ref = None
        _index = None
```

- [ ] **Step 4: Use the index without allowing dynamic state to leak**

`compute_route()` must take the cached mode graph as a base and copy only when dynamic road closures or hard filters need mutation. Weather, road-condition penalties, and request-specific constraints remain local dictionaries. `reload_network()` and `clear_cache()` call `invalidate_routing_index()`.

```python
mode_index = prepared or get_routing_index(G).for_mode(mode)
G_mode = mode_index.graph
mode_status = mode_index.mode_status
mode_penalty = dict(mode_index.mode_penalty)
max_len = mode_index.max_len
norm_lengths = mode_index.norm_lengths
```

- [ ] **Step 5: Share one `ModeIndex` across compound route legs**

At the beginning of `compute_via_route()`, `compute_tour_route()`, and `_tool_plan_multimodal_route()`, resolve one index for each distinct travel mode and pass it into every `compute_route()` call. Never call `filter_graph_for_mode()` inside each leg after the index exists.

- [ ] **Step 6: Run focused performance-structure tests**

Run: `python -m pytest tests/test_network.py tests/test_routing.py tests/test_planner.py -q`

Expected: PASS; a same-mode via route builds the mode index once and exact shortest still reports one Dijkstra per leg.

- [ ] **Step 7: Commit reusable graph preparation**

```bash
git add spatial/routing_index.py spatial/network.py spatial/routing.py agents/tools.py tests/test_network.py tests/test_routing.py tests/test_planner.py
git commit -m "perf(routing): reuse prepared mode graphs"
```

---

### Task 4: Add the Canonical Server Route State and Deterministic Replan API

**Files:**
- Create: `agents/route_state.py`
- Create: `tests/test_route_state.py`
- Create: `tests/test_replan_api.py`
- Modify: `agents/tools.py:520-942`
- Modify: `agents/planner.py:188-339`
- Modify: `api/routes.py:453-520,631-1145`

**Interfaces:**
- Produces: `build_route_state(...) -> dict`, `validate_route_state(raw) -> dict`, `apply_change(state, change) -> dict`.
- Adds: `POST /api/route/replan` with `{route_state, change}` and the same route response shape as `/api/chat`, including a new `route_state`.
- Supported changes are exactly one of `travel_mode` or `strategy`.

- [ ] **Step 1: Write failing schema and mutation tests**

```python
# tests/test_route_state.py
import pytest
from agents.route_state import apply_change, validate_route_state


BASE = {
    "schema_version": 1,
    "route_id": "route-test",
    "route_kind": "via",
    "original_query": "从牌坊经樱顶到图书馆",
    "start": {"name": "牌坊", "type": "poi"},
    "end": {"name": "图书馆", "type": "poi"},
    "via": {"name": "樱顶", "type": "poi"},
    "tour": None,
    "legs": [],
    "travel_mode": "walk",
    "hard_constraints": {"slope": "normal"},
    "strategy": {"name": "shortest", "source": "commute_default", "weights": {"distance": 1.0, "slope": 0.0, "scenery": 0.0}, "detour_cap": 1.0},
    "data_version": "data-v1",
    "road_condition_version": "roads-v1",
}


def test_mode_change_preserves_every_route_semantic_field():
    changed = apply_change(validate_route_state(BASE), {"travel_mode": "bike"})
    assert changed["travel_mode"] == "bike"
    for key in ("route_kind", "original_query", "start", "end", "via", "tour", "legs", "hard_constraints", "strategy"):
        assert changed[key] == BASE[key]


def test_rejects_client_computed_fields_and_multiple_changes():
    poisoned = dict(BASE, recommended_edge_ids=[[1, 2, 0]], distance_m=1)
    clean = validate_route_state(poisoned)
    assert "recommended_edge_ids" not in clean
    assert "distance_m" not in clean
    with pytest.raises(ValueError, match="exactly one"):
        apply_change(clean, {"travel_mode": "bike", "strategy": "flat"})
```

- [ ] **Step 2: Run and verify the route-state module is missing**

Run: `python -m pytest tests/test_route_state.py -q`

Expected: FAIL importing `agents.route_state`.

- [ ] **Step 3: Implement strict state sanitization**

```python
# agents/route_state.py
from copy import deepcopy
from uuid import uuid4

SCHEMA_VERSION = 1
ROUTE_KINDS = {"direct", "via", "tour", "multimodal"}
TRAVEL_MODES = {"walk", "bike", "drive"}
STRATEGIES = {"shortest", "recommended", "scenery", "flat", "custom"}
STATE_FIELDS = {
    "schema_version", "route_id", "route_kind", "original_query", "start", "end",
    "via", "tour", "legs", "travel_mode", "hard_constraints", "strategy",
    "data_version", "road_condition_version",
}
class RouteStateVersionConflict(ValueError):
    pass


def build_route_state(**values):
    state = {
        "schema_version": SCHEMA_VERSION,
        "route_id": values.pop("route_id", f"route-{uuid4().hex}"),
        "via": None, "tour": None, "legs": [],
    }
    state.update(values)
    return validate_route_state(state)


def validate_route_state(raw):
    if not isinstance(raw, dict):
        raise ValueError("route_state must be an object")
    state = {key: deepcopy(raw.get(key)) for key in STATE_FIELDS}
    if state["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported route_state schema_version")
    if state["route_kind"] not in ROUTE_KINDS:
        raise ValueError("invalid route_kind")
    if state["travel_mode"] not in TRAVEL_MODES:
        raise ValueError("invalid travel_mode")
    if not isinstance(state["start"], dict) or not isinstance(state["end"], dict):
        raise ValueError("route endpoints are required")
    strategy = state.get("strategy") or {}
    if strategy.get("name") not in STRATEGIES:
        raise ValueError("invalid strategy")
    return state


def apply_change(state, change):
    keys = [key for key in ("travel_mode", "strategy") if key in (change or {})]
    if len(keys) != 1:
        raise ValueError("change must contain exactly one supported field")
    out = deepcopy(validate_route_state(state))
    if keys[0] == "travel_mode":
        if change["travel_mode"] not in TRAVEL_MODES:
            raise ValueError("invalid travel_mode")
        out["travel_mode"] = change["travel_mode"]
    else:
        if change["strategy"] not in STRATEGIES:
            raise ValueError("invalid strategy")
        out["strategy"] = {"name": change["strategy"], "source": "button"}
    out["route_id"] = f"route-{uuid4().hex}"
    return out
```

- [ ] **Step 4: Build route state in every planner tool response**

Add one helper in `agents/tools.py` that receives the original normalized tool arguments and the resolved strategy; do not reconstruct via/tour/legs from returned polylines.

Both `/api/route` and the Agent tool path must resolve one `StrategyDecision` before calling `compute_route()`:

```python
decision = select_route_strategy(
    query=original_query,
    end_poi=end_poi,
    explicit_strategy=intent_data.get("strategy_hint"),
    strategy_source=intent_data.get("strategy_source", "explicit_nl"),
    profile=profile_record,
    custom_weights=intent_data.get("weights"),
)
route_result = compute_route(
    G, start_node, end_node,
    constraints=constraints,
    weights=decision.weights,
    strategy_name=decision.name,
    detour_cap=decision.detour_cap,
    mode=final_mode,
)
resolved_weights = route_result["applied_weights"]
```

The API must stop calling `resolve_weights()` to compute a separate response value for a shortest route; it returns the strategy decision and the router's `applied_weights` together.

```python
state = build_route_state(
    route_kind="via",
    original_query=ctx.get("query", ""),
    start=args["start"], end=args["end"], via=args["via"],
    travel_mode=mode,
    hard_constraints=args.get("constraints") or {},
    strategy=strategy.as_dict(),
    data_version=ctx["data_version"],
    road_condition_version=ctx["road_condition_version"],
)
payload["route_state"] = state
```

- [ ] **Step 5: Implement `/api/route/replan` as a non-LLM dispatcher**

```python
@api_bp.route("/route/replan", methods=["POST"])
def replan_route():
    body = request.get_json(silent=True) or {}
    try:
        prior = validate_route_state(body.get("route_state"))
        requested = apply_change(prior, body.get("change"))
        response = replan_from_state(requested)
    except ValueError as exc:
        return _err("invalid_route_state", str(exc), 400)
    except RouteStateVersionConflict as exc:
        return _err("route_state_version_conflict", str(exc), 409)
    return _ok(response)
```

`replan_from_state()` dispatches by `route_kind` to direct, via, tour, or multimodal deterministic functions in `agents.tools`. It re-resolves POIs and recomputes nodes, edges, costs, and coordinates. It never accepts those computed fields from the client and never calls `run_agent()` or an LLM client.

Before dispatch, compare `data_version` with the current server data version. A mismatch raises `RouteStateVersionConflict`; a changed `road_condition_version` is accepted and replaced because a replan must apply current restrictions.

- [ ] **Step 6: Add endpoint contract tests for all four route kinds**

```python
# tests/test_replan_api.py
def _state_for(route_kind):
    state = {
        "schema_version": 1, "route_id": "route-test", "route_kind": route_kind,
        "original_query": "测试路线", "start": {"name": "珞珈门", "type": "poi"},
        "end": {"name": "樱顶", "type": "poi"}, "via": None, "tour": None,
        "legs": [], "travel_mode": "walk", "hard_constraints": {},
        "strategy": {"name": "shortest", "source": "commute_default",
                     "weights": {"distance": 1.0, "slope": 0.0, "scenery": 0.0},
                     "detour_cap": 1.0},
        "data_version": "test-data", "road_condition_version": "test-roads",
    }
    if route_kind == "via":
        state["via"] = {"name": "老图书馆", "type": "poi"}
    elif route_kind == "tour":
        state["tour"] = {"pois": [{"name": "老图书馆", "type": "poi"}], "loop": False}
    elif route_kind == "multimodal":
        state["legs"] = [{"start": state["start"], "end": state["end"], "travel_mode": "walk"}]
    return state


@pytest.mark.parametrize("route_kind", ["direct", "via", "tour", "multimodal"])
def test_replan_preserves_route_kind_and_skips_llm(client, monkeypatch, route_kind):
    state = _state_for(route_kind)
    monkeypatch.setattr("agents.planner.run_agent", lambda *a, **k: pytest.fail("LLM called"))
    response = client.post("/api/route/replan", json={
        "route_state": state,
        "change": {"travel_mode": "bike"},
    })
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["route_state"]["route_kind"] == route_kind
    assert data["route_state"]["travel_mode"] == "bike"
```

- [ ] **Step 7: Run server-state and API tests**

Run: `python -m pytest tests/test_route_state.py tests/test_replan_api.py tests/test_planner.py tests/test_multiturn.py -q`

Expected: PASS; no replan test calls the LLM and every compound route keeps its structure.

- [ ] **Step 8: Commit the canonical server state**

```bash
git add agents/route_state.py agents/tools.py agents/planner.py api/routes.py tests/test_route_state.py tests/test_replan_api.py tests/test_planner.py tests/test_multiturn.py
git commit -m "feat(agent): add canonical route state and deterministic replan"
```

---

### Task 5: Make Browser Replanning Atomic and Add Visible Strategy Buttons

**Files:**
- Create: `static/js/route-state.js`
- Create: `tests/js/route-state.test.js`
- Modify: `static/index.html:146-153,506-511`
- Modify: `static/css/style.css:374-411`
- Modify: `static/js/app.js:27-51,86-107,143-280,352-420,2938-3163`
- Modify: `package.json`

**Interfaces:**
- Browser global: `window.WHURouteState.createStore(initial)` with `current()`, `begin(change)`, `commit(token, responseState)`, and `rollback(token)`.
- Replan request: `{route_state: store.current(), change: {travel_mode|strategy}}`.

- [ ] **Step 1: Write failing dependency-free browser-state tests**

```javascript
// tests/js/route-state.test.js
const test = require('node:test');
const assert = require('node:assert/strict');
const { createStore } = require('../../static/js/route-state.js');

const base = {
  schema_version: 1, route_id: 'r1', route_kind: 'via',
  start: { name: '牌坊' }, via: { name: '樱顶' }, end: { name: '图书馆' },
  travel_mode: 'walk', hard_constraints: { slope: 'normal' },
  strategy: { name: 'shortest' },
};

test('begin does not mutate committed state and rollback preserves it', () => {
  const store = createStore(base);
  const tx = store.begin({ travel_mode: 'bike' });
  assert.equal(store.current().travel_mode, 'walk');
  store.rollback(tx);
  assert.deepEqual(store.current(), base);
});

test('only the newest transaction can commit', () => {
  const store = createStore(base);
  const oldTx = store.begin({ strategy: 'scenery' });
  const newTx = store.begin({ strategy: 'flat' });
  assert.equal(store.commit(oldTx, { ...base, strategy: { name: 'scenery' } }), false);
  assert.equal(store.commit(newTx, { ...base, strategy: { name: 'flat' } }), true);
  assert.equal(store.current().strategy.name, 'flat');
});
```

- [ ] **Step 2: Add the Node test command and verify failure**

```json
"scripts": {
  "start": "gunicorn app:app --workers 1 --timeout 60 --bind 0.0.0.0:$PORT",
  "test:js": "node --test tests/js/*.test.js"
}
```

Run: `npm run test:js`

Expected: FAIL because `static/js/route-state.js` does not exist.

- [ ] **Step 3: Implement the small transaction store**

```javascript
// static/js/route-state.js
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.WHURouteState = api;
})(typeof window !== 'undefined' ? window : globalThis, function () {
  function clone(value) { return value == null ? value : JSON.parse(JSON.stringify(value)); }
  function createStore(initial) {
    var committed = clone(initial || null);
    var seq = 0;
    var active = 0;
    return {
      current: function () { return clone(committed); },
      replace: function (value) { committed = clone(value); },
      begin: function (change) { active = ++seq; return { id: active, change: clone(change) }; },
      commit: function (token, value) {
        if (!token || token.id !== active) return false;
        committed = clone(value); return true;
      },
      rollback: function (token) { return !!token && token.id === active; },
    };
  }
  return { createStore: createStore };
});
```

- [ ] **Step 4: Add visible, accessible strategy controls**

Place this beside the travel-mode selector, always visible when a route exists:

```html
<div class="route-strategy-switch" id="route-strategy-switch" role="group" aria-label="路线偏好">
  <button type="button" class="route-strategy-btn" data-strategy="shortest" aria-pressed="true">最短路径</button>
  <button type="button" class="route-strategy-btn" data-strategy="scenery" aria-pressed="false">风景优先</button>
  <button type="button" class="route-strategy-btn" data-strategy="flat" aria-pressed="false">平坦优先</button>
</div>
```

Load `/js/route-state.js` before `/js/app.js`. Reuse the current button radius, focus ring, and active palette in `style.css`; do not encode state by color alone, and hide `flat` for drive without clearing the committed route strategy until a successful replacement is available.

- [ ] **Step 5: Replace split route state with one store**

In `app.js`, remove `lastRouteRequest`, `routeAcceptTimer`, `ROUTE_ACCEPT_DELAY_MS`, `scheduleRouteAccept()`, and `cancelRouteAccept()`. Add:

```javascript
var routeStore = window.WHURouteState.createStore(null);
state.routeState = null;

function commitRouteResponse(result, tx) {
  if (!result || !result.route_state) throw new Error('路线状态缺失，请重新规划');
  if (tx && !routeStore.commit(tx, result.route_state)) return false;
  if (!tx) routeStore.replace(result.route_state);
  state.routeState = routeStore.current();
  state.latestRoute = result;
  saveContext();
  renderRoute(result);
  showResults(result);
  syncStrategyUI(state.routeState.strategy.name);
  syncModeFromServer(result);
  return true;
}
```

Remove `lastRouteRequest` and `lastIntent` from `state`. `saveContext()` stores `{history, routeState}`. The next natural-language request sends `context.previous_route_state = state.routeState`; the backend converts `hard_constraints` and nested `strategy` into parser context only when the new utterance is a valid continuation. No browser code reconstructs separate `constraints` or `weights` fields.

- [ ] **Step 6: Use one atomic replan function for mode and strategy**

```javascript
async function replanCurrentRoute(change) {
  var prior = routeStore.current();
  if (!prior || state.loading) return;
  var tx = routeStore.begin(change);
  var previousRoute = state.latestRoute;
  state.requestSeq += 1;
  var mySeq = state.requestSeq;
  showLoading('正在重新规划…', '保留当前起终点和需求');
  try {
    var result = await apiRequest('/api/route/replan', { route_state: prior, change: change });
    if (mySeq !== state.requestSeq) return;
    commitRouteResponse(result, tx);
  } catch (err) {
    if (mySeq !== state.requestSeq) return;
    routeStore.rollback(tx);
    state.latestRoute = previousRoute;
    showError('没有切换路线', (err && err.message) || '当前路线仍然保留');
  } finally {
    if (mySeq === state.requestSeq) hideLoading();
  }
}
```

`setTravelMode(mode)` calls `replanCurrentRoute({travel_mode: mode})` when a route exists and updates the selector only after success. Strategy buttons call `replanCurrentRoute({strategy: name})`. They do not call `/api/parse`, do not create chat bubbles, and do not append conversation history.

- [ ] **Step 7: Run JavaScript and endpoint tests**

Run: `npm run test:js`

Expected: PASS with two state transaction tests.

Run: `python -m pytest tests/test_replan_api.py tests/test_multiturn.py -q`

Expected: PASS; switching mode or strategy preserves the same endpoint/via/tour/leg fields.

- [ ] **Step 8: Commit atomic browser state and buttons**

```bash
git add static/js/route-state.js tests/js/route-state.test.js static/index.html static/css/style.css static/js/app.js package.json tests/test_replan_api.py tests/test_multiturn.py
git commit -m "feat(ui): add atomic route strategy switching"
```

---

### Task 6: Replace Timed Auto-Learning with Confirmed Leisure Feedback

**Files:**
- Modify: `agents/profile.py`
- Modify: `api/routes.py:1551-1596`
- Modify: `static/js/app.js:53-107,1969-2005`
- Modify: `static/js/navigation.js:542-590`
- Modify: `tests/test_profile.py`
- Modify: `tests/test_telemetry.py`

**Interfaces:**
- Produces: `record_strategy_signal(uid, route_id, strategy, strategy_source, weights, signal) -> dict | None`.
- Signals: `strategy_selected`, `strategy_abandoned`, `navigation_started`, `navigation_completed`.
- Only the first start/completion confirmation for a non-shortest selected leisure route changes EMA weights.

- [ ] **Step 1: Write failing profile-signal tests**

```python
# tests/test_profile.py
def test_exposure_and_selection_do_not_learn(monkeypatch, isolated_profiles):
    for signal in ("route_shown", "strategy_selected"):
        profile.record_strategy_signal(
            "u1", "r1", "scenery", "button",
            {"distance": 0.5, "slope": 0.1, "scenery": 0.4}, signal,
        )
    assert profile.get_profile("u1").get("accepted_count", 0) == 0


def test_navigation_start_learns_once_and_shortest_never_learns(isolated_profiles):
    weights = {"distance": 0.5, "slope": 0.1, "scenery": 0.4}
    profile.record_strategy_signal("u1", "r1", "scenery", "button", weights, "navigation_started")
    profile.record_strategy_signal("u1", "r1", "scenery", "button", weights, "navigation_completed")
    assert profile.get_profile("u1")["accepted_count"] == 1
    profile.record_strategy_signal(
        "u1", "r2", "shortest", "commute_default",
        {"distance": 1.0, "slope": 0.0, "scenery": 0.0}, "navigation_started",
    )
    assert profile.get_profile("u1")["accepted_count"] == 1
```

- [ ] **Step 2: Run and verify the new signal API is missing**

Run: `python -m pytest tests/test_profile.py -q`

Expected: FAIL with `AttributeError: module 'agents.profile' has no attribute 'record_strategy_signal'`.

- [ ] **Step 3: Implement deduplicated confirmed learning**

```python
CONFIRM_SIGNALS = {"navigation_started", "navigation_completed"}
LEARNABLE_STRATEGIES = {"recommended", "scenery", "flat", "custom"}
LEARNABLE_SOURCES = {"button", "explicit_nl"}
from agents.routing_policy import STRATEGY_WEIGHTS
RECOMMENDED_WEIGHTS = STRATEGY_WEIGHTS["recommended"]


def record_strategy_signal(uid, route_id, strategy, strategy_source, applied_weights, signal):
    if not uid or not route_id or signal not in {
        "strategy_selected", "strategy_abandoned", *CONFIRM_SIGNALS,
    }:
        return None
    with _lock:
        profiles = _load()
        p = profiles.setdefault(uid, {
            "weights": dict(RECOMMENDED_WEIGHTS),
            "accepted_count": 0,
            "exposure_count": 0,
            "confirmed_route_ids": [],
            "last_updated_at": None,
            "last_signal_source": None,
        })
        if (signal in CONFIRM_SIGNALS and strategy in LEARNABLE_STRATEGIES
                and strategy_source in LEARNABLE_SOURCES):
            confirmed = p.setdefault("confirmed_route_ids", [])
            if route_id not in confirmed and _valid_weights(applied_weights):
                _apply_ema(p, applied_weights)
                confirmed.append(route_id)
                del confirmed[:-100]
                p["last_updated_at"] = datetime.now(timezone.utc).isoformat()
                p["last_signal_source"] = signal
        _save()
        return dict(p)
```

- [ ] **Step 4: Update telemetry and navigation events**

The telemetry endpoint validates that the route ID, strategy name, and weights arrive together. `startNavigation()` sends `navigation_started`; the arrival callback sends `navigation_completed`. Selecting a different strategy sends `strategy_abandoned` for the old pending strategy and `strategy_selected` for the new one. Remove all timer-driven `route_accept` calls.

```javascript
trackEvent('navigation_started', {
  route_id: state.routeState.route_id,
  strategy: state.routeState.strategy.name,
  strategy_source: state.routeState.strategy.source,
  applied_weights: state.routeState.strategy.weights,
});
```

- [ ] **Step 5: Run profile and telemetry tests**

Run: `python -m pytest tests/test_profile.py tests/test_telemetry.py -q`

Expected: PASS; selection/exposure do not change weights, start+complete for one route produces one sample, and shortest produces none.

- [ ] **Step 6: Commit reliable profile learning**

```bash
git add agents/profile.py api/routes.py static/js/app.js static/js/navigation.js tests/test_profile.py tests/test_telemetry.py
git commit -m "fix(profile): learn only from confirmed leisure routes"
```

---

### Task 7: Add Timing Separation and the Research Readiness Gate

**Files:**
- Create: `scripts/validate/check_agent_research_readiness.py`
- Create: `experiments/research_snapshot.json` only through the script's `--write-snapshot` option
- Modify: `agents/planner.py`
- Modify: `api/routes.py`
- Modify: `tests/test_end_to_end.py`
- Modify: `tests/test_deployment_readiness.py`

**Interfaces:**
- Every planning response adds `timings_ms` with `agent`, `poi_resolution`, `graph_prepare`, `path_search`, and `response_build` keys; non-applicable stages are `0.0`.
- Gate command exits zero only when strategy, state, profile, data-readiness, and version checks all pass.

- [ ] **Step 1: Write a failing timing-contract test**

```python
# tests/test_end_to_end.py
def test_chat_separates_agent_and_routing_timings(client):
    response = client.post("/api/chat", json={"query": "从珞珈门到教五"})
    assert response.status_code == 200
    timings = response.get_json()["data"]["timings_ms"]
    assert set(timings) == {
        "agent", "poi_resolution", "graph_prepare", "path_search", "response_build",
    }
    assert all(value >= 0 for value in timings.values())


def test_replan_agent_time_is_zero(client):
    valid_route_state = {
        "schema_version": 1, "route_id": "timing-route", "route_kind": "direct",
        "original_query": "从珞珈门到樱顶", "start": {"name": "珞珈门", "type": "poi"},
        "end": {"name": "樱顶", "type": "poi"}, "via": None, "tour": None,
        "legs": [], "travel_mode": "walk", "hard_constraints": {},
        "strategy": {"name": "shortest", "source": "commute_default",
                     "weights": {"distance": 1.0, "slope": 0.0, "scenery": 0.0},
                     "detour_cap": 1.0},
        "data_version": "test-data", "road_condition_version": "test-roads",
    }
    response = client.post("/api/route/replan", json={
        "route_state": valid_route_state,
        "change": {"strategy": "scenery"},
    })
    assert response.get_json()["data"]["timings_ms"]["agent"] == 0.0
```

- [ ] **Step 2: Run and verify timing fields are absent**

Run: `python -m pytest tests/test_end_to_end.py -k "timings or replan_agent_time" -q`

Expected: FAIL with missing `timings_ms`.

- [ ] **Step 3: Instrument stages with `time.perf_counter()`**

Create a request-local dictionary at the API boundary. Measure agent execution in `agents.planner`, POI normalization and snapping in `api.routes`, graph index acquisition separately from Dijkstra calls, and response geometry separately from path search. Round only when serializing.

```python
def _elapsed_ms(start):
    return round((time.perf_counter() - start) * 1000.0, 3)

timings = {
    "agent": 0.0,
    "poi_resolution": 0.0,
    "graph_prepare": 0.0,
    "path_search": 0.0,
    "response_build": 0.0,
}
```

- [ ] **Step 4: Implement the deterministic readiness checker**

```python
# scripts/validate/check_agent_research_readiness.py
CHECKS = [
    ("strategy_policy", [sys.executable, "-m", "pytest", "tests/test_routing_policy.py", "-q"]),
    ("route_state", [sys.executable, "-m", "pytest", "tests/test_route_state.py", "tests/test_replan_api.py", "-q"]),
    ("profile_signals", [sys.executable, "-m", "pytest", "tests/test_profile.py", "tests/test_telemetry.py", "-q"]),
    ("routing_contract", [sys.executable, "-m", "pytest", "tests/test_routing.py", "-q"]),
]


def build_snapshot():
    return {
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "model": config.LLM_MODEL,
        "prompt_sha256": sha256(Path("agents/prompts/parse_system.txt").read_bytes()).hexdigest(),
        "task_cards_sha256": sha256(Path("data/task_cards.json").read_bytes()).hexdigest(),
        "poi_sha256": sha256(Path("data/pois.json").read_bytes()).hexdigest(),
        "network_sha256": sha256(Path("data/whu_road_network.graphml").read_bytes()).hexdigest(),
        "attributes_sha256": sha256(Path("data/road_annotations.json").read_bytes()).hexdigest(),
        "profile_mode": "empty_or_fixture",
    }
```

The script must refuse `--write-snapshot` when Git has tracked modifications, when any check fails, or when the slope/scenery data report says a requested formal strategy is degraded.

- [ ] **Step 5: Run the full development verification once**

Run: `python -m pytest -q`

Expected: PASS with no failures.

Run: `npm run test:js`

Expected: PASS.

Run: `python scripts/validate/check_agent_research_readiness.py`

Expected: exits `0` and prints a green result for every structural check without writing a research snapshot.

- [ ] **Step 6: Commit observability and the readiness gate**

```bash
git add agents/planner.py api/routes.py scripts/validate/check_agent_research_readiness.py tests/test_end_to_end.py tests/test_deployment_readiness.py
git commit -m "feat(research): add agent readiness gate and timings"
```

---

## Completion Gate

Do not create `whu-agent-evaluation` or run formal paper experiments until all of the following are true:

1. `shortest` returns identical recommended and shortest edge IDs with `dijkstra_runs == 1`.
2. Every non-shortest route is at or below its declared detour cap.
3. Direct, via, tour, and multimodal replans preserve their complete task structures.
4. A failed replan leaves the map, result cards, route state, and context unchanged.
5. Button replans never call `/api/parse`, `/api/chat`, or an LLM.
6. Route exposure and elapsed time never update profile weights.
7. One confirmed leisure route creates at most one profile sample; commute shortest creates none.
8. Response timing separates agent and routing work, and replan agent time is zero.
9. The slope/scenery master-data quality gate passes before `flat`, `scenery`, or profile recommendation is included in formal evaluation.
10. The full Python and browser test suites pass, then a clean, versioned research snapshot can be generated.
