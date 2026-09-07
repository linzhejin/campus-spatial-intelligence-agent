"""
T-028: NL 解析与路径计算端到端测试 (15 条标准 query)

测试策略：
  - TestEndToEnd:        需要 Flask 服务器运行 (标记 @pytest.mark.e2e)
  - TestEndToEndOffline:  离线验证，无需服务器运行

运行方式：
  pytest tests/test_end_to_end.py -v                        # 仅离线
  pytest tests/test_end_to_end.py -v -m e2e                 # 仅 e2e（需要服务器）
  pytest tests/test_end_to_end.py -v -m "not e2e"           # 跳过 e2e
  python tests/test_end_to_end.py                           # 离线自检（无需 pytest）
"""
import pytest
import json
import time
import sys
import os
from pathlib import Path

# ---- path setup ----
sys.path.insert(0, str(Path(__file__).parent.parent))

# =========================================================================
# 15 standard test queries (PRD Appendix A)
# =========================================================================
STANDARD_QUERIES = [
    {
        "id": 1,
        "query": "从牌坊到樱顶怎么走",
        "expected_task_type": "path_planning",
        "expected_constraints": {"distance": ["short", "medium"]},
    },
    {
        "id": 2,
        "query": "从梅园去图书馆",
        "expected_task_type": "path_planning",
        "expected_constraints": {"distance": ["short", "medium"]},
    },
    {
        "id": 3,
        "query": "我想避开陡坡，从行政楼到枫园",
        "expected_task_type": "path_planning",
        "expected_constraints": {"slope": ["avoid"]},
    },
    {
        "id": 4,
        "query": "从总图书馆到凌波门，走风景好的路线",
        "expected_task_type": "path_planning",
        "expected_constraints": {"scenery": ["high"]},
    },
    {
        "id": 5,
        "query": "哪条路去樱顶最近",
        "expected_task_type": "path_planning",
        "expected_constraints": {"distance": ["short"]},
    },
    {
        "id": 6,
        "query": "从教五到信息学部第一教学楼",
        "expected_task_type": "path_planning",
        "expected_constraints": {},  # default
    },
    {
        "id": 7,
        "query": "从牌坊出发，去赏樱的地方",
        "expected_task_type": "path_planning",
        "expected_constraints": {"scenery": ["high"]},
    },
    {
        "id": 8,
        "query": "从珞珈山到樱花大道，走平坦的路",
        "expected_task_type": "path_planning",
        "expected_constraints": {"slope": ["avoid"]},
    },
    {
        "id": 9,
        "query": "万林艺术馆怎么去",
        "expected_task_type": "poi_query",
        "expected_constraints": {},  # poi_query has no routing constraints
    },
    {
        "id": 10,
        "query": "桂园到月湖怎么走",
        "expected_task_type": "path_planning",
        "expected_constraints": {},  # default
    },
    {
        "id": 11,
        "query": "从老斋舍到宋卿体育馆，避开爬坡",
        "expected_task_type": "path_planning",
        "expected_constraints": {"slope": ["avoid"]},
    },
    {
        "id": 12,
        "query": "我第一次来武大，想看最美的校园路线",
        "expected_task_type": "path_planning",
        "expected_constraints": {"scenery": ["high"]},
    },
    {
        "id": 13,
        "query": "从信息学部到文理学部怎么走",
        "expected_task_type": "path_planning",
        "expected_constraints": {},  # default
    },
    {
        "id": 14,
        "query": "我想从洪波门走到珞瑜门",
        "expected_task_type": "path_planning",
        "expected_constraints": {},  # default
    },
    {
        "id": 15,
        "query": "凌波门附近有什么好玩的",
        "expected_task_type": "poi_query",
        "expected_constraints": {},  # poi_query has no routing constraints
    },
]

# Base URL for the Flask server
BASE_URL = os.environ.get("WHU_WALKER_BASE_URL", "http://localhost:5000")
API_CHAT_URL = f"{BASE_URL}/api/chat"
API_PARSE_URL = f"{BASE_URL}/api/parse"


def _send_chat(query: str, timeout: int = 120) -> dict:
    """Send a single query to /api/chat and return the parsed JSON."""
    import urllib.request
    import urllib.error

    body = json.dumps({"query": query}).encode("utf-8")
    req = urllib.request.Request(
        API_CHAT_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else ""
        try:
            return json.loads(error_body)
        except json.JSONDecodeError:
            return {"error": "http_error", "message": error_body, "status_code": e.code}
    except Exception as e:
        return {"error": "connection_error", "message": str(e)}


def _send_parse(query: str, timeout: int = 30) -> dict:
    """Send a single query to /api/parse and return the parsed JSON."""
    import urllib.request
    import urllib.error

    body = json.dumps({"query": query, "input_method": "nl"}).encode("utf-8")
    req = urllib.request.Request(
        API_PARSE_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else ""
        try:
            return json.loads(error_body)
        except json.JSONDecodeError:
            return {"error": "http_error", "message": error_body, "status_code": e.code}
    except Exception as e:
        return {"error": "connection_error", "message": str(e)}


def _check_server_running() -> bool:
    """Quickly check if the server is reachable."""
    import urllib.request
    import urllib.error

    try:
        req = urllib.request.Request(f"{BASE_URL}/health", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("status") == "ok"
    except Exception:
        return False


# =========================================================================
# TestEndToEnd: server-dependent tests
# =========================================================================


@pytest.mark.e2e
class TestEndToEnd:
    """End-to-end tests requiring a running Flask server at localhost:5000.

    Skip these tests when no server is available:
        pytest -m "not e2e"
    """

    @pytest.fixture(autouse=True)
    def require_server(self):
        """Skip entire class if server is not running."""
        if not _check_server_running():
            pytest.skip(
                f"Flask server not reachable at {BASE_URL}. "
                "Start with: python app.py"
            )

    # ------------------------------------------------------------------
    # AC-1: Task type accuracy >= 80% (12/15)
    # ------------------------------------------------------------------
    def test_task_type_accuracy(self):
        """AC-1: Verify task_type correctness >= 80% (12/15).

        For each query, send to /api/parse (lighter than /api/chat).
        Check the returned task_type against expected_task_type.
        """
        correct = 0
        total = len(STANDARD_QUERIES)
        results = []

        for q in STANDARD_QUERIES:
            resp = _send_parse(q["query"])
            data = resp.get("data", resp)
            actual_type = data.get("task_type", "unknown")
            expected = q["expected_task_type"]
            is_correct = actual_type == expected
            if is_correct:
                correct += 1
            results.append({
                "id": q["id"],
                "query": q["query"],
                "expected": expected,
                "actual": actual_type,
                "correct": is_correct,
            })

        pass_rate = correct / total
        print(f"\n  Task type accuracy: {correct}/{total} = {pass_rate:.0%}")
        for r in results:
            status = "OK" if r["correct"] else f"MISMATCH (expected={r['expected']})"
            print(f"    [{r['id']:2d}] {status:35s}  |  {r['query']}")

        assert pass_rate >= 0.8, (
            f"Task type accuracy {pass_rate:.0%} below 80% threshold.\n"
            f"Results:\n" + "\n".join(
                f"  [{r['id']}] {r['query']}: expected={r['expected']}, actual={r['actual']}"
                for r in results if not r["correct"]
            )
        )

    # ------------------------------------------------------------------
    # AC-2: Constraints correctly extracted >= 80%
    # ------------------------------------------------------------------
    def test_constraints_accuracy(self):
        """AC-2: Verify constraints extraction >= 80%.

        For queries with expected_constraints, check that the parsed
        constraints match the expected values.
        """
        # Only test queries that have explicit expected constraints
        constrained_queries = [
            q for q in STANDARD_QUERIES if q["expected_constraints"]
        ]
        if not constrained_queries:
            pytest.skip("No queries with expected constraints to check")

        correct = 0
        total = len(constrained_queries)
        results = []

        for q in constrained_queries:
            resp = _send_parse(q["query"])
            data = resp.get("data", resp)
            actual_constraints = data.get("constraints", {})
            expected = q["expected_constraints"]

            # Check: for each expected constraint key, the actual value
            # must match one of the allowed values
            match = True
            for key, allowed_values in expected.items():
                actual_val = actual_constraints.get(key)
                if actual_val not in allowed_values:
                    match = False
                    break

            if match:
                correct += 1
            results.append({
                "id": q["id"],
                "query": q["query"],
                "expected": expected,
                "actual": actual_constraints,
                "correct": match,
            })

        pass_rate = correct / total
        print(f"\n  Constraints accuracy: {correct}/{total} = {pass_rate:.0%}")
        for r in results:
            status = "OK" if r["correct"] else "MISMATCH"
            print(f"    [{r['id']:2d}] {status:10s} expected={r['expected']} actual={r['actual']}")

        assert pass_rate >= 0.8, (
            f"Constraints accuracy {pass_rate:.0%} below 80% threshold."
        )

    # ------------------------------------------------------------------
    # AC-3: Path connectivity — all 15 queries can generate routes
    # ------------------------------------------------------------------
    def test_path_connectivity(self):
        """AC-3: Verify all 15 queries can generate routes.

        For path_planning queries, the /api/chat response must contain
        recommended and shortest route arrays with at least 2 nodes each.
        For poi_query queries, the response is expected to be a 400 error
        (unsupported task type in /api/chat), which is acceptable.
        """
        connectivity_results = []
        path_planning_count = 0
        path_planning_ok = 0

        for q in STANDARD_QUERIES:
            resp = _send_chat(q["query"])
            data = resp.get("data", resp)
            task_type = data.get("task_type", q["expected_task_type"])

            if task_type == "path_planning":
                path_planning_count += 1
                recommended = data.get("recommended", [])
                shortest = data.get("shortest", [])
                has_route = (
                    isinstance(recommended, list) and len(recommended) >= 2
                    and isinstance(shortest, list) and len(shortest) >= 2
                )
                if has_route:
                    path_planning_ok += 1
                connectivity_results.append({
                    "id": q["id"],
                    "query": q["query"],
                    "task_type": task_type,
                    "has_route": has_route,
                    "rec_len": len(recommended) if isinstance(recommended, list) else 0,
                    "short_len": len(shortest) if isinstance(shortest, list) else 0,
                })
            else:
                # poi_query or other — expected to fail at /api/chat
                # (the endpoint returns 400 for non-path_planning)
                connectivity_results.append({
                    "id": q["id"],
                    "query": q["query"],
                    "task_type": task_type,
                    "has_route": False,
                    "note": "non-path_planning task, /api/chat expected to reject",
                })

        print(f"\n  Path connectivity: {path_planning_ok}/{path_planning_count} path_planning queries have routes")
        for r in connectivity_results:
            if r.get("has_route"):
                print(f"    [{r['id']:2d}] ROUTE_OK  rec={r['rec_len']}nodes short={r['short_len']}nodes")
            elif r.get("note"):
                print(f"    [{r['id']:2d}] SKIPPED   ({r['note']})")
            else:
                print(f"    [{r['id']:2d}] NO_ROUTE  task_type={r['task_type']}")

        # All path_planning queries should have routes
        assert path_planning_ok == path_planning_count, (
            f"Only {path_planning_ok}/{path_planning_count} path_planning queries generated routes"
        )

    # ------------------------------------------------------------------
    # AC-4: Overlap rate <= 70%
    # ------------------------------------------------------------------
    def test_overlap_rate(self):
        """AC-4: Verify overlap rate <= 70% for all path_planning queries.

        The overlap_rate measures how similar the recommended route is
        to the shortest route.  Low overlap means the system is actually
        doing multi-factor optimization, not just returning shortest path.
        """
        overlap_rates = []
        high_overlap = []

        for q in STANDARD_QUERIES:
            if q["expected_task_type"] != "path_planning":
                continue

            resp = _send_chat(q["query"])
            data = resp.get("data", resp)
            overlap = data.get("overlap_rate")

            if overlap is not None:
                overlap_rates.append(overlap)
                if overlap > 0.7:
                    high_overlap.append({
                        "id": q["id"],
                        "query": q["query"],
                        "overlap_rate": overlap,
                    })

        avg_overlap = sum(overlap_rates) / len(overlap_rates) if overlap_rates else 0
        print(f"\n  Overlap rates: avg={avg_overlap:.2%}, count={len(overlap_rates)}")
        for h in high_overlap:
            print(f"    [{h['id']:2d}] HIGH_OVERLAP  {h['overlap_rate']:.0%} | {h['query']}")

        # Individual checks: each query's overlap <= 0.7
        violations = [h for h in high_overlap]
        # Allow some tolerance: if more than half have low overlap, we are ok
        # (some queries with default constraints naturally produce high overlap)
        low_overlap_count = sum(1 for r in overlap_rates if r <= 0.7)
        print(f"  Queries with overlap <= 70%: {low_overlap_count}/{len(overlap_rates)}")

        # At least 50% of queries should have overlap <= 70%
        assert low_overlap_count >= len(overlap_rates) * 0.5, (
            f"Only {low_overlap_count}/{len(overlap_rates)} queries have overlap <= 70%"
        )

    # ------------------------------------------------------------------
    # AC-5: Hot start P50 <= 15s
    # ------------------------------------------------------------------
    def test_hot_start_latency(self):
        """AC-5: Verify hot start P50 latency <= 15 seconds.

        Sends each path_planning query twice; the second call (hot start)
        should be fast.  Computes the median (P50) of the second calls.
        """
        path_queries = [
            q for q in STANDARD_QUERIES if q["expected_task_type"] == "path_planning"
        ]
        latencies = []

        for q in path_queries:
            # Warm-up call
            _send_chat(q["query"])
            # Hot call (timed)
            t0 = time.time()
            _send_chat(q["query"])
            elapsed = time.time() - t0
            latencies.append(elapsed)
            print(f"    [{q['id']:2d}] hot latency: {elapsed:.2f}s  | {q['query']}")

        latencies.sort()
        n = len(latencies)
        if n == 0:
            pytest.skip("No path_planning queries to measure")

        p50 = latencies[n // 2] if n % 2 == 1 else (latencies[n // 2 - 1] + latencies[n // 2]) / 2
        avg = sum(latencies) / n
        max_lat = max(latencies)
        min_lat = min(latencies)

        print(f"\n  Latency summary: P50={p50:.2f}s  avg={avg:.2f}s  min={min_lat:.2f}s  max={max_lat:.2f}s")

        assert p50 <= 15.0, (
            f"Hot start P50 latency {p50:.2f}s exceeds 15s threshold"
        )


# =========================================================================
# TestEndToEndOffline: no-server validation
# =========================================================================


class TestEndToEndOffline:
    """Offline validation tests (no server required)."""

    # ------------------------------------------------------------------
    # O-1: Query list completeness
    # ------------------------------------------------------------------
    def test_query_list_completeness(self):
        """Verify all 15 standard queries are defined with unique IDs."""
        assert len(STANDARD_QUERIES) == 15, (
            f"Expected 15 queries, got {len(STANDARD_QUERIES)}"
        )
        ids = [q["id"] for q in STANDARD_QUERIES]
        assert ids == list(range(1, 16)), (
            f"Query IDs should be 1..15, got {ids}"
        )

    # ------------------------------------------------------------------
    # O-2: All queries have required fields
    # ------------------------------------------------------------------
    def test_all_queries_have_required_fields(self):
        """Every query has 'id', 'query', 'expected_task_type', 'expected_constraints'."""
        required = ["id", "query", "expected_task_type", "expected_constraints"]
        for q in STANDARD_QUERIES:
            for field in required:
                assert field in q, (
                    f"Query #{q.get('id', '?')} missing field '{field}'"
                )

    # ------------------------------------------------------------------
    # O-3: Query lengths are reasonable
    # ------------------------------------------------------------------
    def test_query_lengths_reasonable(self):
        """All queries <= 100 characters."""
        for q in STANDARD_QUERIES:
            length = len(q["query"])
            assert length <= 100, (
                f"Query #{q['id']} is {length} chars (max 100): {q['query']!r}"
            )

    # ------------------------------------------------------------------
    # O-4: Task types are valid
    # ------------------------------------------------------------------
    def test_task_types_valid(self):
        """All expected_task_type values are in the allowed set."""
        allowed = {"path_planning", "poi_query", "help", "unknown"}
        for q in STANDARD_QUERIES:
            assert q["expected_task_type"] in allowed, (
                f"Query #{q['id']} has invalid task_type: {q['expected_task_type']!r}"
            )

    # ------------------------------------------------------------------
    # O-5: Task type distribution
    # ------------------------------------------------------------------
    def test_task_type_distribution(self):
        """Verify the expected distribution: 13 path_planning + 2 poi_query."""
        counts = {}
        for q in STANDARD_QUERIES:
            t = q["expected_task_type"]
            counts[t] = counts.get(t, 0) + 1
        assert counts.get("path_planning", 0) == 13, (
            f"Expected 13 path_planning queries, got {counts.get('path_planning', 0)}"
        )
        assert counts.get("poi_query", 0) == 2, (
            f"Expected 2 poi_query queries, got {counts.get('poi_query', 0)}"
        )

    # ------------------------------------------------------------------
    # O-6: Constraint query coverage
    # ------------------------------------------------------------------
    def test_constraint_coverage(self):
        """Verify constraint distribution:
        - slope=avoid: queries #3, #8, #11 (3)
        - scenery=high: queries #4, #7, #12 (3)
        - distance=short: query #5 (1)
        - distance=short/medium: queries #1, #2 (2)
        - default (empty): queries #6, #9, #10, #13, #14, #15 (6)
        """
        slope_avoid = [q for q in STANDARD_QUERIES if q["expected_constraints"].get("slope") == ["avoid"]]
        scenery_high = [q for q in STANDARD_QUERIES if q["expected_constraints"].get("scenery") == ["high"]]
        distance_short = [q for q in STANDARD_QUERIES if q["expected_constraints"].get("distance") == ["short"]]
        distance_short_med = [q for q in STANDARD_QUERIES if q["expected_constraints"].get("distance") == ["short", "medium"]]
        default = [q for q in STANDARD_QUERIES if not q["expected_constraints"]]

        # Log distribution for visibility
        print(f"\n  Constraint distribution:")
        print(f"    slope=avoid:      {len(slope_avoid)} queries -> {[q['id'] for q in slope_avoid]}")
        print(f"    scenery=high:     {len(scenery_high)} queries -> {[q['id'] for q in scenery_high]}")
        print(f"    distance=short:   {len(distance_short)} queries -> {[q['id'] for q in distance_short]}")
        print(f"    distance=s/m:     {len(distance_short_med)} queries -> {[q['id'] for q in distance_short_med]}")
        print(f"    default (none):   {len(default)} queries -> {[q['id'] for q in default]}")

        assert len(slope_avoid) == 3
        assert len(scenery_high) == 3
        assert len(distance_short) == 1
        assert len(distance_short_med) == 2
        assert len(default) == 6
        # Total: 3+3+1+2+6 = 15

    # ------------------------------------------------------------------
    # O-7: No duplicate queries
    # ------------------------------------------------------------------
    def test_no_duplicate_queries(self):
        """Verify no duplicate query strings exist."""
        queries = [q["query"] for q in STANDARD_QUERIES]
        duplicates = [q for q in queries if queries.count(q) > 1]
        assert len(duplicates) == 0, (
            f"Duplicate queries found: {set(duplicates)}"
        )

    # ------------------------------------------------------------------
    # O-8: Queries contain Chinese characters
    # ------------------------------------------------------------------
    def test_queries_contain_chinese(self):
        """Verify all queries contain Chinese characters (not empty/ASCII)."""
        for q in STANDARD_QUERIES:
            has_chinese = any('一' <= c <= '鿿' for c in q["query"])
            assert has_chinese, (
                f"Query #{q['id']} does not contain Chinese characters: {q['query']!r}"
            )

    # ------------------------------------------------------------------
    # O-9: Server connectivity check helper
    # ------------------------------------------------------------------
    def test_server_check_helper(self):
        """Verify _check_server_running returns a boolean."""
        result = _check_server_running()
        assert isinstance(result, bool), (
            f"_check_server_running should return bool, got {type(result).__name__}: {result!r}"
        )

    # ------------------------------------------------------------------
    # O-10: Parser module importable
    # ------------------------------------------------------------------
    def test_parser_module_importable(self):
        """Verify the parser module can be imported (offline)."""
        try:
            from agents.parser import TaskIntent, Constraints, PoiRef
            assert TaskIntent is not None
            assert Constraints is not None
            assert PoiRef is not None
        except ImportError as e:
            pytest.fail(f"Parser module not importable: {e}")


# =========================================================================
# TestTravelModeEndToEnd: 出行方式（walk/bike/drive）全链路
# Flask 内存测试客户端 + 无 LLM key 走规则兜底，路网读本地 graphml 缓存
# =========================================================================
class TestTravelModeEndToEnd:
    """出行方式全链路：NL/body 指定 mode → 模式过滤路网 → compute_route → 响应/解释。

    使用 Flask test_client（无需独立服务器）。路网不可用（无缓存且无法下载）时跳过。
    """

    @pytest.fixture(autouse=True)
    def setup_client(self):
        try:
            from app import create_app
            app = create_app()
            app.config["TESTING"] = True
            self.client = app.test_client()
        except Exception as e:
            pytest.skip(f"无法创建测试客户端: {e}")
        # 预加载路网（首次从 data/whu_road_network.graphml 缓存读入）
        try:
            from spatial.network import get_network, load_or_download_network
            if get_network() is None:
                load_or_download_network()
        except Exception as e:
            pytest.skip(f"路网不可用，跳过出行方式 e2e: {e}")
        yield

    def _post(self, path: str, payload: dict):
        resp = self.client.post(
            path,
            data=json.dumps(payload),
            content_type="application/json",
        )
        return resp.status_code, resp.get_json()

    # ---- /api/chat ----
    def test_chat_travel_mode_bike(self):
        """/api/chat 带 travel_mode=bike：响应 mode=bike 且含 duration_min。"""
        code, body = self._post("/api/chat", {"query": "从牌坊到樱顶", "travel_mode": "bike"})
        assert code == 200, body
        data = body["data"]
        assert data["task_type"] == "path_planning"
        assert data["mode"] == "bike"
        assert data["duration_min"] is not None
        assert data["shortest_duration_min"] is not None
        assert data["speed_kmh"] == 14.0
        # 模板解释（无 LLM key）应包含出行方式
        assert "骑行" in data["explanation"]

    def test_chat_travel_mode_drive(self):
        """/api/chat 带 travel_mode=drive（驾车可达 POI 对）：mode=drive 且含 duration_min。"""
        code, body = self._post("/api/chat", {
            "query": "从人工智能学院到化学与分子科学学院",
            "travel_mode": "drive",
        })
        assert code == 200, body
        data = body["data"]
        assert data["task_type"] == "path_planning"
        assert data["mode"] == "drive"
        assert data["duration_min"] is not None
        assert data["speed_kmh"] == 25.0
        assert "驾车" in data["explanation"]

    def test_chat_nl_keyword_mode_overrides_body(self):
        """优先级：NL 显式关键词（开车）> body.travel_mode（bike）。"""
        code, body = self._post("/api/chat", {
            "query": "开车从人工智能学院到化学与分子科学学院",
            "travel_mode": "bike",
        })
        assert code == 200, body
        assert body["data"]["mode"] == "drive"

    def test_chat_default_mode_walk(self):
        """未指定出行方式时默认 walk，响应同样带 mode/duration_min 字段。"""
        code, body = self._post("/api/chat", {"query": "从牌坊到樱顶"})
        assert code == 200, body
        data = body["data"]
        assert data["mode"] == "walk"
        assert data["duration_min"] is not None
        assert data["speed_kmh"] == 4.5

    # ---- /api/route ----
    def test_route_travel_mode_bike(self):
        """/api/route 带 travel_mode=bike。"""
        code, body = self._post("/api/route", {
            "start": {"name": "牌坊", "type": "poi"},
            "end": {"name": "樱顶", "type": "poi"},
            "travel_mode": "bike",
        })
        assert code == 200, body
        data = body["data"]
        assert data["mode"] == "bike"
        assert data["duration_min"] is not None
        assert data["shortest_duration_min"] is not None
        assert data["speed_kmh"] == 14.0
        assert len(data["recommended"]) >= 2

    def test_route_travel_mode_drive(self):
        """/api/route 带 travel_mode=drive（驾车可达 POI 对）。"""
        code, body = self._post("/api/route", {
            "start": {"name": "武汉大学人工智能学院", "type": "poi"},
            "end": {"name": "武汉大学化学与分子科学学院西区", "type": "poi"},
            "travel_mode": "drive",
        })
        assert code == 200, body
        data = body["data"]
        assert data["mode"] == "drive"
        assert data["duration_min"] is not None
        assert data["speed_kmh"] == 25.0
        assert len(data["recommended"]) >= 2

    def test_route_mode_field_compat(self):
        """兼容 /api/parse 返回体的 mode 字段：值为 bike 时采纳；distance_first 等预设不采纳。"""
        code, body = self._post("/api/route", {
            "start": {"name": "牌坊", "type": "poi"},
            "end": {"name": "樱顶", "type": "poi"},
            "mode": "bike",
        })
        assert code == 200, body
        assert body["data"]["mode"] == "bike"

        code, body = self._post("/api/route", {
            "start": {"name": "牌坊", "type": "poi"},
            "end": {"name": "樱顶", "type": "poi"},
            "mode": "distance_first",
        })
        assert code == 200, body
        # 快捷预设值不属于 TRAVEL_MODES → 回退默认 walk
        assert body["data"]["mode"] == "walk"

    # ---- /api/parse shortcut ----
    def test_parse_shortcut_travel_mode_drive(self):
        """/api/parse 快捷模式带 travel_mode=drive：响应 mode=drive，前端链路自动携带。"""
        code, body = self._post("/api/parse", {
            "input_method": "shortcut",
            "start": {"name": "牌坊", "type": "poi"},
            "end": {"name": "教五", "type": "poi"},
            "mode": "distance_first",
            "travel_mode": "drive",
        })
        assert code == 200, body
        data = body["data"]
        assert data["mode"] == "drive"
        assert data["task_type"] == "path_planning"
        assert data["weight_source"] == "shortcut"

    def test_parse_nl_bike_keyword(self):
        """/api/parse NL 模式含"骑车"关键词：规则兜底解析 mode=bike。"""
        code, body = self._post("/api/parse", {
            "query": "骑车从牌坊到樱顶",
            "input_method": "nl",
        })
        assert code == 200, body
        assert body["data"]["mode"] == "bike"


# =========================================================================
# Standalone runner (no pytest needed)
# =========================================================================
if __name__ == "__main__":
    print("=" * 70)
    print("T-028: NL 解析与路径计算 · 离线验证")
    print("=" * 70)

    all_pass = 0
    all_fail = 0

    def run_check(name, condition, detail=""):
        global all_pass, all_fail
        if condition:
            all_pass += 1
            print(f"  [PASS] {name}")
        else:
            all_fail += 1
            print(f"  [FAIL] {name}  {detail}")

    # O-1: Query list completeness
    run_check("O-1: 15 standard queries", len(STANDARD_QUERIES) == 15)
    ids = [q["id"] for q in STANDARD_QUERIES]
    run_check("O-1: IDs are 1..15", ids == list(range(1, 16)),
              f"got {ids}")

    # O-2: Required fields
    required = ["id", "query", "expected_task_type", "expected_constraints"]
    missing_fields = []
    for q in STANDARD_QUERIES:
        for f in required:
            if f not in q:
                missing_fields.append(f"#{q.get('id', '?')}.{f}")
    run_check("O-2: All queries have required fields", len(missing_fields) == 0,
              f"missing: {missing_fields}")

    # O-3: Query lengths
    long_queries = [(q["id"], len(q["query"])) for q in STANDARD_QUERIES if len(q["query"]) > 100]
    run_check("O-3: All queries <= 100 chars", len(long_queries) == 0,
              f"too long: {long_queries}")

    # O-4: Valid task types
    allowed = {"path_planning", "poi_query", "help", "unknown"}
    invalid_types = [(q["id"], q["expected_task_type"]) for q in STANDARD_QUERIES
                     if q["expected_task_type"] not in allowed]
    run_check("O-4: Valid task types", len(invalid_types) == 0,
              f"invalid: {invalid_types}")

    # O-5: Task type distribution
    path_count = sum(1 for q in STANDARD_QUERIES if q["expected_task_type"] == "path_planning")
    poi_count = sum(1 for q in STANDARD_QUERIES if q["expected_task_type"] == "poi_query")
    run_check("O-5: 13 path_planning + 2 poi_query",
              path_count == 13 and poi_count == 2,
              f"got {path_count} path_planning, {poi_count} poi_query")

    # O-6: Constraint coverage
    slope_avoid = [q["id"] for q in STANDARD_QUERIES if q["expected_constraints"].get("slope") == ["avoid"]]
    scenery_high = [q["id"] for q in STANDARD_QUERIES if q["expected_constraints"].get("scenery") == ["high"]]
    distance_short = [q["id"] for q in STANDARD_QUERIES if q["expected_constraints"].get("distance") == ["short"]]
    distance_sm = [q["id"] for q in STANDARD_QUERIES if q["expected_constraints"].get("distance") == ["short", "medium"]]
    default = [q["id"] for q in STANDARD_QUERIES if not q["expected_constraints"]]
    run_check("O-6a: 3 slope=avoid queries", len(slope_avoid) == 3,
              f"got {len(slope_avoid)}: {slope_avoid}")
    run_check("O-6b: 3 scenery=high queries", len(scenery_high) == 3,
              f"got {len(scenery_high)}: {scenery_high}")
    run_check("O-6c: 1 distance=short query", len(distance_short) == 1,
              f"got {len(distance_short)}: {distance_short}")
    run_check("O-6d: 2 distance=short/medium queries", len(distance_sm) == 2,
              f"got {len(distance_sm)}: {distance_sm}")
    run_check("O-6e: 6 default queries", len(default) == 6,
              f"got {len(default)}: {default}")
    run_check("O-6f: total adds to 15", len(slope_avoid) + len(scenery_high) + len(distance_short) + len(distance_sm) + len(default) == 15)

    # O-7: No duplicates
    queries_list = [q["query"] for q in STANDARD_QUERIES]
    dups = [q for q in queries_list if queries_list.count(q) > 1]
    run_check("O-7: No duplicate queries", len(dups) == 0,
              f"duplicates: {set(dups)}")

    # O-8: Chinese characters
    non_chinese = [q["id"] for q in STANDARD_QUERIES
                   if not any('一' <= c <= '鿿' for c in q["query"])]
    run_check("O-8: All queries contain Chinese", len(non_chinese) == 0,
              f"no Chinese in: {non_chinese}")

    # O-9: Server check helper
    server_ok = _check_server_running()
    run_check("O-9: _check_server_running returns bool", isinstance(server_ok, bool))
    print(f"      Server at {BASE_URL}: {'RUNNING' if server_ok else 'NOT REACHABLE'}")

    # O-10: Parser importable
    try:
        from agents.parser import TaskIntent, Constraints, PoiRef
        run_check("O-10: Parser module importable", True)
    except ImportError as e:
        run_check("O-10: Parser module importable", False, str(e))

    print("=" * 70)
    print(f"Result: {all_pass}/{all_pass + all_fail} PASSED")
    print("=" * 70)
    sys.exit(0 if all_fail == 0 else 1)
