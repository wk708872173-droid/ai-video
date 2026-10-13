"""V3.4 W3 hotfix — Step 4: 用 shot_001 rerun 重拼 final/。

输入：
  - shot_001_rerun.mp4          (明天 quota 重置后跑出来的奶黄幼犬窗边镜头)
  - shot_002_cinema.mp4         (archives/2026-10-12_v33_w2_first_run/，草地狗)
  - shot_003_cinema.mp4         (archives/2026-10-12_v33_w2_first_run/，门口狗)

行为：
  1. 给 shot_001_rerun 应用 cinematic LUT（暖色·晨光）
  2. 3 镜 -c copy lossless 拼接 → 16:9 版
  3. 转 9:16 (1080x1920) → 小红书主推版
  4. 抽 0.5s 帧作为封面
  5. 全部覆盖到 final/ 目录

用法：
  python scripts/_finalize_with_rerun.py
    --shot-001 ../sample/story_arc_xiaohuang_daily/_working/shot_001_rerun.mp4

  # 或：默认路径（自动找 archives/2026-10-12_v33_w2_first_run/shot_00{2,3}_cinema.mp4）
  python scripts/_finalize_with_rerun.py

产出：
  final/xiaohuang_16x9.mp4              17.5s / 1920x1080
  final/xiaohuang_9x16_xiaohongshu.mp4  17.5s / 1080x1920  ⭐ 小红书
  final/cover_9x16.png                  封面候选
  archives/<date>_v34_w3_rerun/          归档本次 _working/ 副本

依赖：
  scripts/lib_ffmpeg.py（已 commit）
  scripts/_recover_xiaohuang.py（昨天写的拼接+9:16，可复用部分）
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

# 让脚本可找到 lib_ffmpeg
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import lib_ffmpeg


ROOT = Path("sample/story_arc_xiaohuang_daily")
ARCH = ROOT / "archives"
FINAL = ROOT / "final"
WORKING = ROOT / "_working"
V33_ARCH = ARCH / "2026-10-12_v33_w2_first_run"


def main() -> int:
    p = argparse.ArgumentParser(prog="_finalize_with_rerun")
    p.add_argument(
        "--shot-001",
        type=Path,
        default=WORKING / "shot_001_rerun.mp4",
        help="今天 quota 重跑出来的 shot_001 mp4 路径",
    )
    p.add_argument(
        "--shot-002",
        type=Path,
        default=V33_ARCH / "shot_002_cinema.mp4",
        help="草地奔跑镜（带 LUT 的 cinema 版；或 raw mp4 都行）",
    )
    p.add_argument(
        "--shot-003",
        type=Path,
        default=V33_ARCH / "shot_003_cinema.mp4",
        help="门口等待镜（带 LUT 的 cinema 版；或 raw mp4 都行）",
    )
    p.add_argument(
        "--output-dir", type=Path, default=FINAL,
        help="输出目录（默认 final/）",
    )
    p.add_argument(
        "--cover-time", type=float, default=0.5,
        help="封面帧的 t 秒偏移（默认 0.5s）",
    )
    p.add_argument(
        "--no-archive", action="store_true",
        help="跳过自动归档 _working/（默认归档）",
    )
    args = p.parse_args()

    # ---------- 0. 校验输入 ----------
    for label, path in [
        ("shot_001_rerun", args.shot_001),
        ("shot_002", args.shot_002),
        ("shot_003", args.shot_003),
    ]:
        if not path.is_file():
            print(f"[FAIL] {label} 不存在：{path}")
            return 1

    args.output_dir.mkdir(parents=True, exist_ok=True)

    # ---------- 1. cinematic LUT to shot_001_rerun ----------
    print(f"[step 1] apply cinematic LUT → {args.shot_001.name}")
    shot_001_cinema = WORKING / "shot_001_rerun_cinema.mp4"
    WORKING.mkdir(parents=True, exist_ok=True)
    lib_ffmpeg.apply_cinematic_lut_by_shot_id(
        args.shot_001, shot_001_cinema, "shot_001",
    )
    print(f"  → {shot_001_cinema}")

    # ---------- 2. 拼接 3 镜 (16:9) ----------
    print("[step 2] concat 3 shots → 16:9")
    concat_169 = args.output_dir / "xiaohuang_16x9.mp4"
    lib_ffmpeg.concat_files(
        [shot_001_cinema, args.shot_002, args.shot_003],
        concat_169,
    )
    print(f"  → {concat_169} ({concat_169.stat().st_size:,} bytes)")

    # ---------- 3. 转 9:16 ----------
    print("[step 3] to_vertical → 9:16 (1080x1920)")
    concat_916 = args.output_dir / "xiaohuang_9x16_xiaohongshu.mp4"
    lib_ffmpeg.to_vertical(concat_169, concat_916)
    print(f"  → {concat_916} ({concat_916.stat().st_size:,} bytes)")

    # ---------- 4. 抽封面帧 ----------
    print(f"[step 4] extract cover at t={args.cover_time}s")
    import imageio_ffmpeg
    cover_png = args.output_dir / "cover_9x16.png"
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    proc = subprocess.run(
        [exe, "-y", "-ss", str(args.cover_time), "-i", str(concat_916),
         "-frames:v", "1", str(cover_png)],
        capture_output=True, encoding="utf-8", errors="replace", timeout=30,
    )
    if proc.returncode != 0 or not cover_png.is_file():
        print(f"[FAIL] 抽封面失败：{proc.stderr[-200:]}")
        return 1
    print(f"  → {cover_png} ({cover_png.stat().st_size:,} bytes)")

    # ---------- 5. 归档 _working/（默认） ----------
    if not args.no_archive and WORKING.is_dir():
        today = date.today().isoformat()
        archive_dir = ARCH / f"{today}_v34_w3_finalize"
        archive_dir.mkdir(parents=True, exist_ok=True)
        for f in WORKING.iterdir():
            if f.is_file():
                shutil.move(str(f), str(archive_dir / f.name))
                print(f"[archive] {f.name} → archives/{archive_dir.name}/")
        # 空 _working/ 删除
        try:
            WORKING.rmdir()
        except OSError:
            pass
    elif WORKING.is_dir():
        print("[skip] --no-archive 模式：保留 _working/ 不动")

    print()
    print("=" * 60)
    print("✅ Finalize 完成。最终产物：")
    for f in sorted(args.output_dir.glob("*")):
        if f.is_file():
            print(f"  {f.relative_to(ROOT)}  ({f.stat().st_size:,} bytes)")
    print("=" * 60)
    print()
    print("人眼验收 checklist（必须做）：")
    print("  [ ] final/cover_9x16.png  是不是奶黄幼犬草地？")
    print("  [ ] final/xiaohuang_9x16_xiaohongshu.mp4  是不是 3 镜全狗？")
    print("  [ ] 视频时长是不是 17.5s 左右？")
    return 0


if __name__ == "__main__":
    sys.exit(main())