"""scripts/midnight_recovery.py — 凌晨 00:00 自动启动脚本。

Python-based 替代 schtasks（避免 admin 权限问题）。

用法：
    python scripts/midnight_recovery.py        # 启动后台进程，等 00:00 触发

行为：
  1. 当前 < 00:00 → 等到 00:00
  2. 当前 >= 00:00 → 立刻跑
  3. 跑 midnight_recovery.bat 里的同样步骤（重跑 4 个 shot + cinematic + stitch）
  4. 写日志到 logs/midnight_recovery_<timestamp>.log
"""
from __future__ import annotations

import datetime as dt
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SAMPLE_DIR = REPO / "sample"
SANDBOX_DIR = REPO / "sandbox"
LOG_DIR = REPO / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def wait_until_midnight() -> None:
    now = dt.datetime.now()
    target = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if now >= target:
        # 已过午夜 → 立刻
        return
    # 等到午夜
    seconds = (target - now).total_seconds()
    print(f"[WAIT] Sleeping {seconds:.0f}s until {target.isoformat()}", file=sys.stderr)
    time.sleep(seconds)


def run_recovery() -> None:
    """跑全部恢复步骤 — 镜像 midnight_recovery.bat 但用 Python subprocess 控制。"""
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOG_DIR / f"midnight_recovery_{timestamp}.log"

    story_dir = SAMPLE_DIR / "story_arc_orange_cat_4_seasons"

    with open(log_file, "w", encoding="utf-8") as logf:
        logf.write(f"=== Midnight Recovery {dt.datetime.now().isoformat()} ===\n")

        env = {
            "MINIMAX_IMAGE_ASPECT_RATIO": "9:16",
            "PYTHONIOENCODING": "utf-8",
            **(__import__("os").environ),
        }

        # Step 1: 重跑 4 个 shot
        logf.write("\n[STEP 1] Re-running 4 shots (shot_003 + shot_004-006)\n")
        for shot in ("shot_003", "shot_004", "shot_005", "shot_006"):
            day = "day1" if shot == "shot_003" else "day2"
            run_dir = SAMPLE_DIR / f"run_story_arc_orange_cat_4_seasons_{shot}"
            out_mp4 = SAMPLE_DIR / f"story_arc_orange_cat_4_seasons_{shot}.mp4"
            logf.write(f"\n--- {shot} ---\n")
            r = subprocess.run(
                [
                    sys.executable, "-m", "hailuo_video", "pipeline",
                    str(story_dir / f"{day}_storyboards" / f"{shot}.json"),
                    "-o", str(out_mp4),
                    "--image-backend", "minimax",
                    "--tts-backend", "mock",
                    "--motion-budget", "3",
                    "--parallel", "1",
                    "--run-dir", str(run_dir),
                ],
                cwd=str(SANDBOX_DIR), env=env,
                capture_output=True, text=True, timeout=180,
            )
            logf.write(f"  exit={r.returncode}\n{r.stderr[-300:]}\n")

        # Step 2: cinematic_upgrade Day 1 + Day 2
        logf.write("\n[STEP 2] Apply T1 cinema filters\n")
        for day in ("day1", "day2"):
            logf.write(f"\n--- cinematic_upgrade {day} ---\n")
            r = subprocess.run(
                [
                    sys.executable, str(REPO / "scripts" / "cinematic_upgrade.py"),
                    str(story_dir / f"{day}_storyboards"),
                    "--suffix", "_realT2",
                ],
                cwd=str(SANDBOX_DIR), env=env,
                capture_output=True, text=True, timeout=300,
            )
            logf.write(f"  exit={r.returncode}\n{r.stderr[-300:]}\n")

        # Step 3: stitch 6 shots 为完整 mp4
        logf.write("\n[STEP 3] Stitch 6 shots → FINAL 6 shots\n")
        shots = [SAMPLE_DIR / f"story_arc_orange_cat_4_seasons_shot_00{i}_realT2.mp4" for i in (1, 2, 3, 4, 5, 6)]
        out = SAMPLE_DIR / "story_orange_cat_hailuo_only_6shots.mp4"
        cmd = [sys.executable, str(REPO / "scripts" / "stitch_story.py"), str(out),
               *[str(s) for s in shots], "--transition", "fade", "--overlap", "0.5"]
        r = subprocess.run(cmd, cwd=str(SANDBOX_DIR), env=env,
                           capture_output=True, text=True, timeout=300)
        logf.write(f"  exit={r.returncode}\n{r.stderr[-300:]}\n")

        # 报告每个 shot 用了真 Hailuo 还是 fallback
        logf.write("\n[REPORT] shot_000_motion.mp4 status:\n")
        for shot in ("shot_001", "shot_002", "shot_003", "shot_004", "shot_005", "shot_006"):
            mp = SAMPLE_DIR / f"run_story_arc_orange_cat_4_seasons_{shot}" / "intermediate" / "shot_000_motion.mp4"
            if mp.exists():
                logf.write(f"  {shot}: REAL HAILUO ({mp.stat().st_size:,} bytes)\n")
            else:
                logf.write(f"  {shot}: Ken Burns fallback\n")
        logf.write(f"\n=== Recovery done at {dt.datetime.now().isoformat()} ===\n")

    print(f"[DONE] Log: {log_file}", file=sys.stderr)


def main() -> int:
    wait_until_midnight()
    run_recovery()
    return 0


if __name__ == "__main__":
    sys.exit(main())
