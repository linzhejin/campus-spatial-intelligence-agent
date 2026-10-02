"""Paired C1 pilot against the same live service and spatial-data snapshot.

The full arm keeps one conversation through setup and target turns. The
stateless arm sends the identical target turn in a fresh conversation. This is
an Agent-state ablation, not a road-network comparison or field truth study.
Only projected task fields are saved; route geometry and session cookies are
never written to the result file.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import random
import statistics
import subprocess
import time
from typing import Any
from uuid import uuid4

import requests


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "experiments" / "datasets" / "c1_live_pilot_v1.json"
TERMINAL = {"completed", "partial", "failed", "agent_failed", "needs_input", "cancelled"}
CRITICAL_FIELDS = {"start", "end", "via", "hard_constraints"}


@lru_cache(maxsize=1)
def _poi_aliases() -> dict[str, str]:
    """Resolve only unique aliases in the exact POI snapshot used by both arms."""
    records = json.loads((ROOT / "data" / "pois.json").read_text(encoding="utf-8"))["pois"]
    mapping: dict[str, str] = {}
    ambiguous: set[str] = set()
    for poi in records:
        canonical = poi["name"]
        for name in [canonical, *poi.get("aliases", [])]:
            if name in mapping and mapping[name] != canonical:
                ambiguous.add(name)
            else:
                mapping[name] = canonical
    return {name: canonical for name, canonical in mapping.items()
            if name not in ambiguous}


def _same_poi(actual: Any, expected: Any) -> bool:
    if actual == expected:
        return True
    aliases = _poi_aliases()
    return (isinstance(actual, str) and isinstance(expected, str)
            and actual in aliases and expected in aliases
            and aliases[actual] == aliases[expected])


def _name(ref: Any) -> str | None:
    if isinstance(ref, dict) and ref.get("name"):
        return str(ref["name"]).strip()
    return None


def _via_names(ref: Any) -> list[str]:
    if not isinstance(ref, dict):
        return []
    points = ref.get("points") if ref.get("type") == "multi" else [ref]
    return [name for point in points if (name := _name(point))]


def project_run(run: dict) -> dict:
    """Keep only fields needed for task-state scoring and failure review."""
    result = run.get("result") or {}
    state = result.get("route_state") or {}
    strategy = state.get("strategy") or result.get("strategy") or {}
    return {
        "status": run.get("status"),
        "kind": result.get("response_kind") or result.get("task_type"),
        "start": _name(state.get("start") or result.get("start")),
        "end": _name(state.get("end") or result.get("end")),
        "via": _via_names(state.get("via") or result.get("via")),
        "mode": state.get("travel_mode") or result.get("mode"),
        "strategy": strategy.get("name"),
        "hard_constraints": state.get("hard_constraints") or {},
        "requirements": result.get("requirement_results") or {},
        "data_version": state.get("data_version"),
        "road_condition_version": state.get("road_condition_version"),
        "error": run.get("error"),
    }


def score_target(expected: dict, observed: dict) -> dict:
    """Require every preregistered field; missing evidence never counts as pass."""
    mismatches: list[str] = []
    status = expected.get("status", "completed")
    if observed.get("status") != status:
        mismatches.append("status")
    for field in ("kind", "mode", "strategy"):
        if field in expected and observed.get(field) != expected[field]:
            mismatches.append(field)
    for field in ("start", "end"):
        if field in expected and not _same_poi(observed.get(field), expected[field]):
            mismatches.append(field)
    if "via" in expected:
        actual_via = observed.get("via")
        expected_via = expected["via"]
        if (not isinstance(actual_via, list)
                or len(actual_via) != len(expected_via)
                or not all(_same_poi(a, e) for a, e in zip(actual_via, expected_via))):
            mismatches.append("via")
    for field in ("hard_constraints", "requirements"):
        for key, value in expected.get(field, {}).items():
            if (observed.get(field) or {}).get(key) != value:
                mismatches.append(f"{field}.{key}")
    for key in expected.get("forbidden_constraints", []):
        if (observed.get("hard_constraints") or {}).get(key):
            mismatches.append(f"hard_constraints.{key}")
    return {
        "passed": not mismatches,
        "mismatches": mismatches,
        "critical": observed.get("kind") == "route" and any(
            item.split(".")[0] in CRITICAL_FIELDS for item in mismatches
        ),
    }


def _paired_interval(rows: list[dict], *, seed: int = 20261003) -> list[float]:
    if not rows:
        return [0.0, 0.0]
    effects = [int(row["full"]["passed"]) - int(row["stateless"]["passed"])
               for row in rows]
    rng = random.Random(seed)
    samples = sorted(
        sum(rng.choice(effects) for _ in effects) / len(effects)
        for _ in range(5000)
    )
    return [round(samples[124], 4), round(samples[4874], 4)]


def compare_cases(rows: list[dict]) -> dict:
    full_success = sum(bool(row["full"]["passed"]) for row in rows)
    stateless_success = sum(bool(row["stateless"]["passed"]) for row in rows)
    full_latencies = [row["full_elapsed_s"] for row in rows if "full_elapsed_s" in row]
    return {
        "n_cases": len(rows),
        "full_success": full_success,
        "stateless_success": stateless_success,
        "full_success_rate": round(full_success / len(rows), 4) if rows else 0.0,
        "stateless_success_rate": round(stateless_success / len(rows), 4) if rows else 0.0,
        "paired_difference": round((full_success - stateless_success) / len(rows), 4)
        if rows else 0.0,
        "paired_difference_ci95_bootstrap": _paired_interval(rows),
        "paired_win": sum(row["full"]["passed"] and not row["stateless"]["passed"]
                          for row in rows),
        "paired_loss": sum(row["stateless"]["passed"] and not row["full"]["passed"]
                           for row in rows),
        "full_critical_corruption": sum(bool(row["full"]["critical"]) for row in rows),
        "full_latency_p50_s": round(statistics.median(full_latencies), 3)
        if full_latencies else None,
        "full_latency_p95_s": round(sorted(full_latencies)[
            max(0, int(0.95 * len(full_latencies) + 0.999999) - 1)
        ], 3) if full_latencies else None,
    }


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_head() -> str | None:
    run = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                         capture_output=True, text=True)
    return run.stdout.strip() if run.returncode == 0 else None


def _git_blob(path: Path) -> str | None:
    """Git's canonical blob hash survives Windows/Linux checkout line endings."""
    run = subprocess.run(["git", "hash-object", str(path)], cwd=ROOT,
                         capture_output=True, text=True)
    return run.stdout.strip() if run.returncode == 0 else None


def _new_conversation(session: requests.Session, base: str) -> str:
    response = session.post(f"{base}/api/conversations", json={}, timeout=25)
    response.raise_for_status()
    return response.json()["data"]["conversation_id"]


def _send_turn(session: requests.Session, base: str, conversation: str,
               turn: dict, *, continuation: dict | None = None) -> tuple[dict, float]:
    body = {
        "query": turn["query"], "request_id": str(uuid4()),
        "travel_mode": turn.get("travel_mode", "walk"),
    }
    if continuation:
        body.update({"continuation_task_id": continuation["task_id"],
                     "base_revision": continuation["current_task_revision"]})
    started = time.perf_counter()
    response = session.post(f"{base}/api/conversations/{conversation}/messages",
                            json=body, timeout=30)
    response.raise_for_status()
    run_id = response.json()["data"]["run_id"]
    deadline = started + 100.0
    while time.perf_counter() < deadline:
        status_response = session.get(f"{base}/api/runs/{run_id}",
                                      params={"conversation_id": conversation}, timeout=25)
        status_response.raise_for_status()
        run = status_response.json()["data"]
        if run.get("status") in TERMINAL:
            return run, round(time.perf_counter() - started, 3)
        time.sleep(1.0)
    return {"status": "timeout", "error": "run exceeded 100 seconds"}, 100.0


def _run_full(session: requests.Session, base: str, case: dict) -> tuple[dict, list[dict], float]:
    conversation = _new_conversation(session, base)
    setup_results = []
    continuation = None
    for turn in case.get("setup", []):
        run, elapsed = _send_turn(session, base, conversation, turn,
                                  continuation=continuation)
        setup_results.append({"query": turn["query"], "observed": project_run(run),
                              "elapsed_s": elapsed})
        continuation = run if run.get("status") == "needs_input" else None
    target, elapsed = _send_turn(session, base, conversation, case["target"],
                                 continuation=continuation)
    return project_run(target), setup_results, elapsed


def _run_stateless(session: requests.Session, base: str, case: dict) -> tuple[dict, float]:
    conversation = _new_conversation(session, base)
    run, elapsed = _send_turn(session, base, conversation, case["target"])
    return project_run(run), elapsed


def run_comparison(dataset: dict, base: str, *, case_ids: set[str] | None = None) -> list[dict]:
    cases = [case for case in dataset["cases"]
             if case_ids is None or case["id"] in case_ids]
    session = requests.Session()
    rows = []
    try:
        for index, case in enumerate(cases):
            row = {"id": case["id"], "category": case["category"],
                   "target": case["target"]["query"], "expected": case["expected"]}
            try:
                if index % 2:
                    baseline, baseline_elapsed = _run_stateless(session, base, case)
                    full, setup_results, full_elapsed = _run_full(session, base, case)
                else:
                    full, setup_results, full_elapsed = _run_full(session, base, case)
                    baseline, baseline_elapsed = _run_stateless(session, base, case)
                row.update({
                    "full_observed": full, "full": score_target(case["expected"], full),
                    "full_setup": setup_results, "full_elapsed_s": full_elapsed,
                    "stateless_observed": baseline,
                    "stateless": score_target(case["expected"], baseline),
                    "stateless_elapsed_s": baseline_elapsed,
                })
            except (requests.RequestException, KeyError, ValueError) as error:
                row.update({"transport_error": f"{type(error).__name__}: {error}",
                            "full": {"passed": False, "critical": False},
                            "stateless": {"passed": False, "critical": False}})
            rows.append(row)
            print(json.dumps({"id": row["id"], "full": row["full"],
                              "stateless": row["stateless"],
                              "transport_error": row.get("transport_error")},
                             ensure_ascii=False), flush=True)
            if sum("transport_error" in recent for recent in rows[-3:]) == 3:
                raise RuntimeError("three consecutive transport errors; aborting without a model score")
    finally:
        session.close()
    return rows


def _validate_dataset(dataset: dict) -> None:
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("dataset needs nonempty cases")
    ids = [case.get("id") for case in cases]
    if len(ids) != len(set(ids)) or any(not item for item in ids):
        raise ValueError("case IDs must be unique and nonempty")
    for case in cases:
        if not isinstance(case.get("setup", []), list) or not case.get("target", {}).get("query"):
            raise ValueError(f"invalid turns in {case['id']}")
        if case.get("expected", {}).get("kind") not in {"route", "chat", "clarify", "candidates"}:
            raise ValueError(f"missing target kind in {case['id']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--base-url", default="https://whuspati.online")
    parser.add_argument("--server-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case-id", action="append", default=[])
    args = parser.parse_args(argv)
    if not args.base_url.startswith("https://"):
        parser.error("production comparison requires an https URL")
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    _validate_dataset(dataset)
    selected = set(args.case_id) or None
    if selected and not selected <= {case["id"] for case in dataset["cases"]}:
        parser.error("unknown case ID")
    rows = run_comparison(dataset, args.base_url.rstrip("/"), case_ids=selected)
    summary = compare_cases(rows)
    report = {
        "status": "diagnostic_subset" if selected else "pilot_complete",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": dataset.get("scope"), "dataset_version": dataset.get("version"),
        "dataset_sha256": _sha(args.dataset), "runner_git_head": _git_head(),
        "server_commit_declared": args.server_commit,
        "poi_sha256": _sha(ROOT / "data" / "pois.json"),
        "network_sha256": _sha(ROOT / "data" / "whu_road_network.graphml"),
        "poi_git_blob": _git_blob(ROOT / "data" / "pois.json"),
        "network_git_blob": _git_blob(ROOT / "data" / "whu_road_network.graphml"),
        "summary": summary, "cases": rows,
        "limitations": [
            "Pilot tasks are authored and scored by the project; no independent holdout or human study.",
            "A matching Git/data fingerprint must be checked before interpreting paired effects.",
            "Selected routes are not a census or field proof of campus-wide passability.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"report={args.output}")
    return 0 if not any("transport_error" in row for row in rows) else 2


if __name__ == "__main__":
    raise SystemExit(main())
