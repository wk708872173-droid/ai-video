"""V3.7 P1-3：合并 4 个一次性脚本为统一的 finalize 工具。

替代以下 4 个旧脚本（V3.7 后删除）：
  - scripts/_recover_xiaohuang.py        (硬编码拼接顺序)
  - scripts/_finalize_with_rerun.py      (LUT + concat + 9:16 + 封面 + 归档)
  - scripts/_archive_xiaohuang.py        (纯文件 move/copy)
  - scripts/_run_xiaohuang_pipeline.py   (内联 LUTS + concat + 9:16，已被 lib_ffmpeg 覆盖)

8 步流水线：
  1. 校验 3 镜存在
  2. L1 SSIM gate（compute_frame_quality profile=motion；仅报警不阻断）
  3. cinematic LUT to shot_001（按 shot_id 自动选 LUT_RECIPES）
  4. 3 镜 -c copy lossless concat → 16:9
  5. 转 9:16 (1080x1920)
  6. 抽封面帧（imageio_ffmpeg）
  7. 归档 _working/ → archives/<date>_v37_finalize/
  8. 渲染人眼验收 checklist

依赖：
  scripts/lib_ffmpeg.py（V3.4 反回归公共库）

用法：
  # 默认路径（自动找 archives/2026-10-12_v33_w2_first_run/shot_00{2,3}_cinema.mp4）
  python scripts/finalize_with_fallback.py

  # 自定义路径
  python scripts/finalize_with_fallback.py \
    --shot-001 _working/shot_001_rerun.mp4 \
    --shot-002 archives/.../shot_002_cinema.mp4 \
    --shot-003 archives/.../shot_003_cinema.mp4

  # 跳过 SSIM gate / 跳过归档
  python scripts/finalize_with_fallback.py --no-ssim-gate --no-archive

产出：
  final/xiaohuang_16x9.mp4              ~17.5s / 1920x1080
  final/xiaohuang_9x16_xiaohongshu.mp4  ~17.5s / 1080x1920  ⭐ 小红书
  final/cover_9x16.png                  封面候选
  archives/<date>_v37_finalize/          _working/ 副本
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

import lib_ffmpeg  # noqa: E402

# 默认 ROOT（向后兼容旧 4 个脚本的硬编码）
DEFAULT_ROOT = Path("sample/story_arc_xiaohuang_daily")
DEFAULT_V33_ARCH = DEFAULT_ROOT / "archives" / "2026-10-12_v33_w2_first_run"


def _run_ssim_gate(video_paths: list[Path]) -> list[dict]:
    """V3.7 P0-3：L1 SSIM gate（仅报警）。返回每段的报告列表。"""
    from hailuo_video.quality import compute_frame_quality
    import logging

    log = logging.getLogger("finalize_with_fallback.ssim_gate")
    results: list[dict] = []
    for vp in video_paths:
        try:
            rep = compute_frame_quality(vp, profile="motion")
            results.append({
                "video": str(vp),
                "severity": rep.severity,
                "mean_ssim": rep.mean_ssim,
                "min_ssim": rep.min_ssim,
            })
            if rep.severity in ("critical", "major"):
                log.warning(
                    "ssim_gate_fail path=%s severity=%s min_ssim=%.3f mean_ssim=%.3f",
                    vp, rep.severity, rep.min_ssim, rep.mean_ssim,
                )
        except FileNotFoundError:
            log.warning("ssim_gate_skip path=%s reason=not_found", vp)
            results.append({"video": str(vp), "severity": "skip", "reason": "not_found"})
        except Exception as exc:  # pragma: no cover
            log.warning("ssim_gate_skip path=%s reason=%s: %s", vp, type(exc).__name__, exc)
            results.append({"video": str(vp), "severity": "skip", "reason": str(exc)})
    return results


def _extract_cover(concat_916: Path, cover_png: Path, cover_time: float) -> bool:
    """抽封面帧（imageio_ffmpeg + ffmpeg -ss）。"""
    import imageio_ffmpeg

    exe = imageio_ffmpeg.get_ffmpeg_exe()
    proc = subprocess.run(
        [exe, "-y", "-ss", str(cover_time), "-i", str(concat_916),
         "-frames:v", "1", str(cover_png)],
        capture_output=True, encoding="utf-8", errors="replace", timeout=30,
    )
    return proc.returncode == 0 and cover_png.is_file()


def _archive_working(working: Path, arch_root: Path, date_str: str | None = None) -> Path | None:
    """归档 _working/ → archives/<date>_v37_finalize/。"""
    if not working.is_dir():
        return None
    archive_dir = arch_root / f"{(date_str or date.today().isoformat())}_v37_finalize"
    archive_dir.mkdir(parents=True, exist_ok=True)
    moved = 0
    for f in working.iterdir():
        if f.is_file():
            shutil.move(str(f), str(archive_dir / f.name))
            moved += 1
    try:
        working.rmdir()
    except OSError:
        pass
    return archive_dir if moved else None


def main() -> int:
    p = argparse.ArgumentParser(
        prog="finalize_with_fallback",
        description="V3.7 漫剧 finalize 一体化工具（合并旧 4 个一次性脚本）",
    )
    p.add_argument(
        "--shot-001", type=Path,
        default=DEFAULT_ROOT / "_working" / "shot_001_rerun.mp4",
        help="今天 quota 重跑出来的 shot_001 mp4 路径",
    )
    p.add_argument(
        "--shot-002", type=Path,
        default=DEFAULT_V33_ARCH / "shot_002_cinema.mp4",
        help="草地奔跑镜（带 LUT 的 cinema 版；或 raw mp4 都行）",
    )
    p.add_argument(
        "--shot-003", type=Path,
        default=DEFAULT_V33_ARCH / "shot_003_cinema.mp4",
        help="门口等待镜（带 LUT 的 cinema 版；或 raw mp4 都行）",
    )
    p.add_argument(
        "--output-dir", type=Path, default=DEFAULT_ROOT / "final",
        help="输出目录（默认 final/）",
    )
    p.add_argument(
        "--working-dir", type=Path, default=DEFAULT_ROOT / "_working",
        help="_working/ 路径（用于归档；默认 sample/.../_working/）",
    )
    p.add_argument(
        "--cover-time", type=float, default=0.5,
        help="封面帧的 t 秒偏移（默认 0.5s）",
    )
    p.add_argument(
        "--no-archive", action="store_true",
        help="跳过自动归档 _working/（默认归档）",
    )
    p.add_argument(
        "--no-ssim-gate", action="store_true",
        help="跳过 L1 SSIM gate（默认跑；不阻断）",
    )
    args = p.parse_args()

    root = args.output_dir.parent
    arch_root = root / "archives"

    # ---------- 0. 校验输入 ----------
    for label, path in [
        ("shot_001", args.shot_001),
        ("shot_002", args.shot_002),
        ("shot_003", args.shot_003),
    ]:
        if not path.is_file():
            print(f"[FAIL] {label} 不存在：{path}")
            return 1

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.working_dir.mkdir(parents=True, exist_ok=True)

    # ---------- 1. L1 SSIM gate（V3.7 P0-3，仅报警）----------
    if not args.no_ssim_gate:
        print("[step 1] L1 SSIM gate（profile=motion；仅报警）")
        _run_ssim_gate([args.shot_001, args.shot_002, args.shot_003])
        print("  → 详见 logger 'finalize_with_fallback.ssim_gate'")

    # ---------- 2. cinematic LUT to shot_001 ----------
    print("[step 2] apply cinematic LUT → shot_001")
    shot_001_cinema = args.working_dir / "shot_001_rerun_cinema.mp4"
    lib_ffmpeg.apply_cinematic_lut_by_shot_id(
        args.shot_001, shot_001_cinema, "shot_001",
    )
    print(f"  → {shot_001_cinema}")

    # ---------- 3. 拼接 3 镜 (16:9) ----------
    print("[step 3] concat 3 shots → 16:9 (-c copy)")
    concat_169 = args.output_dir / "xiaohuang_16x9.mp4"
    lib_ffmpeg.concat_files(
        [shot_001_cinema, args.shot_002, args.shot_003],
        concat_169,
    )
    print(f"  → {concat_169} ({concat_169.stat().st_size:,} bytes)")

    # ---------- 4. 转 9:16 ----------
    print("[step 4] to_vertical → 9:16 (1080x1920)")
    concat_916 = args.output_dir / "xiaohuang_9x16_xiaohongshu.mp4"
    lib_ffmpeg.to_vertical(concat_169, concat_916)
    print(f"  → {concat_916} ({concat_916.stat().st_size:,} bytes)")

    # ---------- 5. 抽封面帧 ----------
    print(f"[step 5] extract cover at t={args.cover_time}s")
    cover_png = args.output_dir / "cover_9x16.png"
    if not _extract_cover(concat_916, cover_png, args.cover_time):
        print(f"[FAIL] 抽封面失败")
        return 1
    print(f"  → {cover_png} ({cover_png.stat().st_size:,} bytes)")

    # ---------- 6. 归档 _working/（默认） ----------
    if not args.no_archive:
        archive_dir = _archive_working(args.working_dir, arch_root)
        if archive_dir:
            print(f"[step 6] _working/ → {archive_dir.relative_to(root)}/")
        else:
            print("[step 6] _working/ 为空，跳过归档")
    else:
        print("[step 6] --no-archive 模式：保留 _working/ 不动")

    print()
    print("=" * 60)
    print("✅ Finalize 完成。最终产物：")
    for f in sorted(args.output_dir.glob("*")):
        if f.is_file():
            print(f"  {f.relative_to(root)}  ({f.stat().st_size:,} bytes)")
    print("=" * 60)
    print()
    print("人眼验收 checklist（必须做）：")
    print("  [ ] final/cover_9x16.png  是不是奶黄幼犬草地？")
    print("  [ ] final/xiaohuang_9x16_xiaohongshu.mp4  是不是 3 镜全狗？")
    print("  [ ] 视频时长是不是 17.5s 左右？")
    return 0


if __name__ == "__main__":
    sys.exit(main())