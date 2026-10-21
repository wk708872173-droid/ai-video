"""V3.2 自动开发冲刺（轮 12 L2）—— 给 L2 多帧版加音频 + 胶片颗粒，6 个 shot 拼成完整 mp4。

L2 多帧视频没音频也没颗粒（multi_frame_shot.py 暂未集成）。本脚本补完：
  1. 给每个 shot_XXX_l2.mp4 加 ambient audio + 胶片颗粒（ffmpeg）
  2. 6 个 shot 拼成完整 36s mp4（concat filter，保留 audio）

用法：
    python scripts/finalize_l2_story.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import imageio_ffmpeg


# ---------- 6 镜的"专业电影"配置（与 L1 一致）----------

AMBIENT_RECIPES = {
    "shot_001": {  # 晨光·苏醒 — 清晨鸟鸣
        "noise": "pink", "lowpass": 3000, "highpass": 200, "volume": 0.35,
        "desc": "清晨鸟鸣",
    },
    "shot_002": {  # 花径·探索 — 户外微风
        "noise": "pink", "lowpass": 1500, "highpass": 80, "volume": 0.4,
        "desc": "微风",
    },
    "shot_003": {  # 屋檐·凝望 — 黄昏
        "noise": "brown", "lowpass": 800, "highpass": 40, "volume": 0.3,
        "desc": "黄昏远景",
    },
    "shot_004": {  # 月夜·归巢 — 深夜虫鸣
        "noise": "pink", "lowpass": 4000, "highpass": 600, "volume": 0.35,
        "desc": "深夜虫鸣",
    },
    "shot_005": {  # 微光·梦境 — 极静
        "noise": "brown", "lowpass": 400, "highpass": 20, "volume": 0.15,
        "desc": "极静",
    },
    "shot_006": {  # 黎明·迎新
        "noise": "pink", "lowpass": 3000, "highpass": 200, "volume": 0.4,
        "desc": "清晨鸟鸣呼应",
    },
}


def finalize_shot(input_mp4: Path, output_mp4: Path) -> bool:
    """单镜 L2 终版：加 ambient audio + 胶片颗粒。"""
    shot_id = None
    for sid in AMBIENT_RECIPES:
        if f"_{sid}_l2" in input_mp4.name:
            shot_id = sid
            break
    if not shot_id:
        print(f"[FAIL] cannot detect shot_id from {input_mp4.name}", file=sys.stderr)
        return False

    recipe = AMBIENT_RECIPES[shot_id]
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

    audio_input = (
        f"anoisesrc=color={recipe['noise']}:duration=6:sample_rate=44100"
    )
    af = (
        f"highpass=f={recipe['highpass']},lowpass=f={recipe['lowpass']},"
        f"volume={recipe['volume']}"
    )

    # vf chain：rotate（手持呼吸）+ tblend + grain
    vf = (
        f"rotate='0.03*sin(2*PI*t/3-1)':fillcolor=black,"
        f"tblend=all_mode=average:all_opacity=0.05,"
        f"noise=alls=6:allf=t+u"
    )

    import subprocess
    cmd = [
        ffmpeg, "-y",
        "-i", str(input_mp4),
        "-f", "lavfi", "-i", audio_input,
        "-vf", vf,
        "-af", af,
        "-c:v", "libx264", "-preset", "slow", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k",
        "-pix_fmt", "yuv420p", "-color_range", "tv",
        "-shortest",
        "-movflags", "+faststart",
        str(output_mp4),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        print(f"[FAIL] {output_mp4.name}: {proc.stderr[-200:]}", file=sys.stderr)
        return False
    return True


def main() -> int:
    p = argparse.ArgumentParser(prog="finalize_l2_story")
    p.add_argument("--sample-dir", default="sample", help="sample 目录")
    args = p.parse_args()

    sample = Path(args.sample_dir)
    work_dir = sample / "_l2_final_work"
    work_dir.mkdir(parents=True, exist_ok=True)

    print("[INFO] finalizing 6 L2 shots (audio + grain)...", file=sys.stderr)
    final_shots = []
    for shot_id in ["shot_001", "shot_002", "shot_003", "shot_004", "shot_005", "shot_006"]:
        in_mp4 = sample / f"story_arc_orange_cat_4_seasons_{shot_id}_l2.mp4"
        if not in_mp4.is_file():
            print(f"[FAIL] {in_mp4.name} missing", file=sys.stderr)
            return 2
        out_mp4 = work_dir / f"{shot_id}_final.mp4"
        if finalize_shot(in_mp4, out_mp4):
            print(f"  [OK] {out_mp4.name} ({out_mp4.stat().st_size:,} B)", file=sys.stderr)
            final_shots.append(out_mp4)

    # stitch 6 个 shot（用 editing_engine.crossfade_clips —— crossfade 不带 audio 时 OK）
    print("\n[INFO] stitching 6 final shots...", file=sys.stderr)
    sys.path.insert(0, "sandbox")
    from hailuo_video import editing as ed_mod  # V3.2 重构：editing_engine → editing 包
    out_final = sample / "story_orange_cat_4_seasons_L2_FINAL.mp4"
    try:
        result = ed_mod.crossfade_clips(
            video_paths=[str(p) for p in final_shots],
            output_path=str(out_final),
            transition="fade",
            overlap_seconds=0.5,
            audio_crossfade=True,
        )
    except Exception as exc:
        print(f"[WARN] crossfade failed ({exc.__class__.__name__}) → fallback concat", file=sys.stderr)
        # fallback：concat filter 保留 audio
        import subprocess
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        inputs = []
        for p in final_shots:
            inputs.extend(["-i", str(p)])
        n = len(final_shots)
        fc = "".join(f"[{i}:v][{i}:a]" for i in range(n)) + f"concat=n={n}:v=1:a=1[vout][aout]"
        cmd = [
            ffmpeg, "-y", *inputs,
            "-filter_complex", fc,
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "slow", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k",
            "-pix_fmt", "yuv420p", "-shortest",
            "-movflags", "+faststart",
            str(out_final),
        ]
        subprocess.run(cmd, capture_output=True, text=True, timeout=180)

    print(f"\n[OK] final L2: {out_final} ({out_final.stat().st_size:,} B)" if out_final.exists() else "[FAIL] stitch failed")
    return 0 if out_final.exists() else 1


if __name__ == "__main__":
    sys.exit(main())
