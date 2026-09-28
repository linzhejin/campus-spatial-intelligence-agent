"""Run reproducibility gates and create an immutable research snapshot on demand.

The normal command writes a readiness report.  ``--write-snapshot`` additionally
records version hashes, but only when code checks, campus-data readiness, the
slope/scenery master-data gate, and tracked-worktree cleanliness all pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.validate.check_deployment_readiness import AUDIT, ROOT, assess, read_json


CHECKS = {
    "routing_policy": [sys.executable, "-m", "pytest", "tests/test_routing_policy.py", "-q"],
    "route_state": [
        sys.executable, "-m", "pytest", "tests/test_route_state.py",
        "tests/test_replan_api.py", "-q",
    ],
    "profile_telemetry": [
        sys.executable, "-m", "pytest", "tests/test_profile.py",
        "tests/test_telemetry.py", "-q",
    ],
    "routing": [sys.executable, "-m", "pytest", "tests/test_routing.py", "-q"],
    "frontend_state": ["npm.cmd" if sys.platform == "win32" else "npm", "run", "test:js"],
}


def assess_research_readiness(checks, deployment, attributes):
    blockers = {}
    failed = [name for name, row in checks.items() if not row.get("passed")]
    if failed:
        blockers["code_checks"] = failed
    if not deployment or not deployment.get("ready"):
        blockers["campus_data"] = (deployment or {}).get(
            "blockers", [{"code": "deployment_readiness_missing"}]
        )
    if (not attributes or not attributes.get("ready")
            or bool(attributes.get("degraded"))):
        blockers["attribute_master_data"] = (attributes or {}).get(
            "blockers", ["attribute_master_data_report_missing"]
        )
    return {"ready": not blockers, "blockers": blockers}


def _run_checks():
    results = {}
    for name, command in CHECKS.items():
        command = list(command)
        temp_root = None
        if "pytest" in command:
            temp_root = tempfile.mkdtemp(prefix=f"whu-research-{name}-")
            command.append(f"--basetemp={temp_root}")
        completed = subprocess.run(
            command, cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        output = ((completed.stdout or "") + (completed.stderr or "")).strip()
        results[name] = {
            "passed": completed.returncode == 0,
            "command": command,
            "exit_code": completed.returncode,
            "output_tail": output[-4000:],
        }
        if temp_root:
            shutil.rmtree(temp_root, ignore_errors=True)
    return results


def _deployment_result():
    return assess(
        read_json(ROOT / "data_validation_report.json"),
        read_json(AUDIT / "campus_spatial_quality.json"),
        read_json(AUDIT / "amap_comparison_metrics.json"),
        read_json(ROOT / "data" / "route_reference_samples.json"),
        read_json(ROOT / "data" / "campus_review_decisions.json") or {},
        read_json(AUDIT / "amap_comparison_candidate.json"),
    )


def _sha256_paths(paths):
    digest = hashlib.sha256()
    existing = []
    for raw in paths:
        path = ROOT / raw
        if path.is_dir():
            files = sorted(item for item in path.rglob("*") if item.is_file())
        elif path.is_file():
            files = [path]
        else:
            files = []
        for item in files:
            rel = item.relative_to(ROOT).as_posix()
            digest.update(rel.encode("utf-8"))
            digest.update(b"\0")
            digest.update(item.read_bytes())
            existing.append(rel)
    return {"sha256": digest.hexdigest(), "files": existing}


def _git(*args):
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _versions():
    return {
        "git_commit": _git("rev-parse", "HEAD"),
        "model_config": _sha256_paths(["config.py"]),
        "prompts": _sha256_paths(["agents/prompts"]),
        "task_cards": _sha256_paths(["data/task_cards.json"]),
        "poi": _sha256_paths(["data/pois.json"]),
        "network": _sha256_paths(["data/whu_road_network.graphml"]),
        "attributes": _sha256_paths([
            "data/road_annotations.json", "data/edge_overrides.json",
        ]),
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-snapshot", action="store_true")
    args = parser.parse_args(argv)

    checks = _run_checks()
    deployment = _deployment_result()
    attribute_path = AUDIT / "attribute_master_data_readiness.json"
    attributes = read_json(attribute_path)
    result = assess_research_readiness(checks, deployment, attributes)
    report = {
        **result,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "campus_data": deployment,
        "attribute_master_data": attributes,
        "versions": _versions(),
    }
    report_path = AUDIT / "agent_research_readiness.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.write_snapshot:
        dirty = _git("status", "--porcelain", "--untracked-files=no")
        if dirty:
            result["blockers"]["tracked_worktree"] = dirty.splitlines()
            result["ready"] = False
        if result["ready"]:
            output = ROOT / "experiments" / "research_snapshot.json"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"研究快照已写入: {output}")
        else:
            print("拒绝生成研究快照：仍有未通过门槛。")

    print(f"智能体研究就绪: {'通过' if result['ready'] else '未通过'}；报告: {report_path}")
    for name, detail in result["blockers"].items():
        print(f"- {name}: {detail}")
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
