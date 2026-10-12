"""V3.3 W2 增量：cinematic_upgrade 但 input 是 hailuo 真运动 mp4（不是 image_000.jpg）。

与 scripts/cinematic_upgrade.py 的差异：
  - 输入：sample/..._shot_001_hailuo_v33.mp4（保留 24fps 真运动）
  - 输出：sample/..._shot_001_hailuo_v33_cinema.mp4
  - 滤镜：eq（色调 LUT） + tblend（柔化锐利瞬间） + noise（胶片颗粒）
  - 不用 zoompan（已有真运动）、不用 audio（hailuo 短片无音）

为什么不是 cinematic_upgrade.py：
  - 原脚本以 image_000.jpg 为输入 → -loop 1 + zoompan → 重建 Ken Burns（6s 推拉）
  - hailuo V3.3 主线是真 AI 视频生成的 24fps 真运动 → 不应被 Ken Burns 覆盖
  - 本脚本只加"质感滤镜"（LUT + grain），保真 hailuo 视频

输出：3 个 _hailuo_v33_cinema.mp4 + 1 个 _hailuo_v33_cinema_concat.mp4
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg


# 直接复用 cinematic_upgrade.py 的 LUT_RECIPES（暖色 Day1 / 冷色 Day2）
# Day 1（晨 → 黄昏）：暖色 LUT（轻微增加红黄）
LUT_RECIPES = {
    "shot_001": (0.02, 1.05, 0.98, 1.04, 0.98),   # 晨光暖金
    "shot_002": (0.05, 1.10, 1.00, 1.02, 0.96),   # 正午饱和
    "shot_003": (-0.02, 1.08, 1.05, 1.06, 0.92),  # 黄昏暖橙
}


def probe_duration(ffmpeg: str, mp4: Path) -> float:
    """读 mp4 时长（秒）。"""
    proc = subprocess.run([ffmpeg, "-i", str(mp4)], capture_output=True, text=True, timeout=10)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", proc.stderr)
    if m:
        h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        return h * 3600 + mi * 60 + s
    return 6.0


def upgrade_one(ffmpeg: str, input_mp4: Path, output_mp4: Path, lut: tuple) -> None:
    """单镜 hailuo 加 cinema 质感：LUT + tblend + noise（保留真运动）。

    LUT_RECIPES: (eq_brightness, eq_saturation, eq_gamma, _r_bal_unused, _b_bal_unused)
    注：colorbalance 的 rs/bs 范围 [-1, 1]；用 eq 已有三参数足够模拟暖/冷色调。
    """
    duration = probe_duration(ffmpeg, input_mp4)
    b, sat, gamma, _, _ = lut
    vf = (
        f"eq=brightness={b}:saturation={sat}:gamma={gamma},"
        # 柔化锐利瞬间（轻微 tblend 5%）—— 提升电影感不破坏运动
        f"tblend=all_mode=average:all_opacity=0.05,"
        # 胶片颗粒
        f"noise=alls=6:allf=t+u"
    )
    cmd = [
        ffmpeg, "-y",
        "-i", str(input_mp4),
        "-t", f"{duration:.3f}",
        "-vf", vf,
        "-c:v", "libx264", "-preset", "slow", "-crf", "20",
        "-an",                              # hailuo 短片无音轨；TTS 后置编辑阶段处理
        "-pix_fmt", "yuv420p", "-color_range", "tv",
        "-movflags", "+faststart",
        str(output_mp4),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        print(f"[FAIL] {input_mp4.name}: {proc.stderr[-300:]}", file=sys.stderr)
        raise RuntimeError(f"ffmpeg failed for {input_mp4.name}")


def main() -> int:
    # 自动定位 sample/：脚本可能在 sandbox/ 或 project root/ 调用
    here = Path(__file__).resolve().parent
    project_root = here.parent
    for candidate in (here / "sample", project_root / "sample"):
        if candidate.is_dir():
            sample = candidate
            break
    else:
        print("[FAIL] 找不到 sample/ 目录", file=sys.stderr)
        return 2

    story_name = "story_arc_orange_cat_4_seasons"

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    print(f"[INFO] ffmpeg: {ffmpeg}")

    shot_ids = ["shot_001", "shot_002", "shot_003"]
    cin_paths: list[Path] = []
    for sid in shot_ids:
        if sid not in LUT_RECIPES:
            raise ValueError(f"no LUT recipe for {sid}")
        in_mp = sample / f"{story_name}_{sid}_hailuo_v33.mp4"
        out_mp = sample / f"{story_name}_{sid}_hailuo_v33_cinema.mp4"
        if not in_mp.is_file():
            print(f"[FAIL] input not found: {in_mp}", file=sys.stderr)
            return 2
        upgrade_one(ffmpeg, in_mp, out_mp, LUT_RECIPES[sid])
        cin_paths.append(out_mp)
        print(f"[OK] {out_mp.name} ({out_mp.stat().st_size:,} bytes)")

    # Concat（-c copy lossless；与 V3.3 W1 concat 同款）
    list_file = sample / "_cinema_concat_list.txt"
    list_file.write_text(
        "\n".join(f"file '{p.resolve()}'" for p in cin_paths) + "\n",
        encoding="utf-8",
    )
    final = sample / f"{story_name}_day1_hailuo_v33_cinema_concat.mp4"
    cmd = [
        ffmpeg, "-y", "-f", "concat", "-safe", "0",
        "-i", str(list_file), "-c", "copy",
        str(final),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        print(f"[FAIL] concat: {proc.stderr[-300:]}", file=sys.stderr)
        return 1
    print(f"[OK] {final.name} ({final.stat().st_size:,} bytes)")

    # cleanup tmp list
    list_file.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())