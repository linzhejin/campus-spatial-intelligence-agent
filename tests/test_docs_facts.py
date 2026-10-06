"""文档事实一致性：改完代码/数据后忘更新文档时，pytest 直接报错。

对应脚本 scripts/docs/gen_facts.py --check：重算稳定字段（工具/POI/校门/
路网/边覆盖/SW/APK/迁移），与 docs/当前事实.md 及 README/HANDOFF/CLAUDE
标记块比对；git 状态与生成时间不参与校验。
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "docs" / "gen_facts.py"


def test_docs_facts_up_to_date():
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise AssertionError(
            "文档事实与代码/数据不一致，请运行 python scripts/docs/gen_facts.py 更新\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
        )
    assert "校验通过" in proc.stdout