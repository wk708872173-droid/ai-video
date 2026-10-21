"""scripts/check_recovery_status.py — 检查 midnight_recovery.py 状态 + 产物。

用法：python scripts/check_recovery_status.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SAMPLE_DIR = REPO / "sample"
LOG_DIR = REPO / "logs"


def main() -> int:
    # 1. 检查后台 Python 进程
    print("[PROCESS]")
    r = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq python.exe"],
        capture_output=True, text=True, timeout=10,
    )
    for line in r.stdout.split("\n"):
        if "midnight_recovery" in line.lower() or "PID" in line:
            print(f"  {line.rstrip()}")
    found_any = any("midnight_recovery" in line.lower() for line in r.stdout.split("\n"))
    if not found_any:
        print("  (search by wmic skipped; 检查 tasklist 输出判断)")

    # 2. 检查产物
    print("\n[OUTPUT FILES]")
    for f in [
        SAMPLE_DIR / "story_orange_cat_hailuo_only_6shots.mp4",
        SAMPLE_DIR / "story_orange_cat_hailuo_only_2shots.mp4",
    ]:
        if f.exists():
            print(f"  ✓ {f.name} ({f.stat().st_size:,} B)")
        else:
            print(f"  ✗ {f.name} not yet")

    # 3. 检查每个 shot 用了真 Hailuo 还是 fallback
    print("\n[SHOT HAILUO STATUS]")
    shots = ("shot_001", "shot_002", "shot_003", "shot_004", "shot_005", "shot_006")
    for shot in shots:
        mp = SAMPLE_DIR / f"run_story_arc_orange_cat_4_seasons_{shot}" / "intermediate" / "shot_000_motion.mp4"
        if mp.exists():
            print(f"  ⭐ {shot}: REAL HAILUO ({mp.stat().st_size:,} B)")
        else:
            print(f"  ⚠️ {shot}: Ken Burns fallback (no shot_000_motion.mp4)")

    # 4. 检查 log 文件
    print("\n[LOGS]")
    logs = sorted(LOG_DIR.glob("midnight_recovery_*.log"))
    if logs:
        for lg in logs[-3:]:
            print(f"  {lg.name} ({lg.stat().st_size:,} B)")
    else:
        print("  (no log yet — recovery hasn't run)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
