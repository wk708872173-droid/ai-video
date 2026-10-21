"""V3.2 自动开发冲刺（轮 10）—— Tier 1 电影质感升级。

每个 Ken Burns mp4 加：
  - 色彩空间修复：yuvj420p → yuv420p + bt470bg → bt709（专业电视色彩）
  - Motion blur：ffmpeg tblend 制造运动模糊（消除"PPT 锐利感"）
  - 胶片颗粒：ffmpeg noise 制造有机噪声
  - 环境音：ffmpeg aevalsrc 生成程序化 ambient（鸟鸣 / 虫鸣 / 风声等）
  - 缓动 zoom：Ken Burns 替换为 sin 曲线（不再机械线性）
  - LUT 色调：Day 1 暖色 / Day 2 冷色

用法：
    python scripts/cinematic_upgrade.py <story_dir> <output_suffix>

例：
    python scripts/cinematic_upgrade.py \
        sample/story_arc_orange_cat_4_seasons/day1_storyboards \
        _cinema
    # 生成 6 个 shot_00?_cinema.mp4
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg


# ---------- 6 镜的"专业电影"配置 ----------

# 每镜的 ambient 音频配方：用 lavfi 的 anoisesrc（brown/pink noise）模拟自然氛围
# 比 aevalsrc 数学表达式稳定（避开 ffmpeg argv 引号 / 通配符问题）
# 参数：noise_color (brown/pink/white) + 高通/低通滤波模拟不同环境
AMBIENT_RECIPES = {
    "shot_001": {  # 晨光·苏醒 — 清晨鸟鸣 + 微风
        "noise": "pink",          # 粉噪（柔和，模拟清晨空气）
        "lowpass": 3000,         # 高频限制（鸟鸣 + 风）
        "highpass": 200,
        "volume": 0.35,
        "extra_freq": "sine=frequency=880:duration=6:sample_rate=44100,volume=0.05",  # 远处高音鸟鸣
        "desc": "清晨鸟鸣 + 微风",
    },
    "shot_002": {  # 花径·探索 — 户外微风
        "noise": "pink",
        "lowpass": 1500,         # 中低频（户外风）
        "highpass": 80,
        "volume": 0.4,
        "desc": "户外微风 + 远处鸟",
    },
    "shot_003": {  # 屋檐·凝望 — 黄昏远景
        "noise": "brown",         # 棕噪（低频主导，远处）
        "lowpass": 800,
        "highpass": 40,
        "volume": 0.3,
        "extra_freq": "sine=frequency=120:duration=6:sample_rate=44100,volume=0.03",  # 远处犬吠低频
        "desc": "黄昏远景 + 远处犬吠",
    },
    "shot_004": {  # 月夜·归巢 — 深夜虫鸣
        "noise": "pink",
        "lowpass": 4000,         # 高频（虫鸣）
        "highpass": 600,
        "volume": 0.35,
        "desc": "深夜虫鸣",
    },
    "shot_005": {  # 微光·梦境 — 极静
        "noise": "brown",
        "lowpass": 400,          # 极低频（梦境呼吸）
        "highpass": 20,
        "volume": 0.15,           # 极低音量
        "desc": "极静 + 梦境呼吸",
    },
    "shot_006": {  # 黎明·迎新 — 晨鸟（呼应 shot_001）
        "noise": "pink",
        "lowpass": 3000,
        "highpass": 200,
        "volume": 0.4,
        "extra_freq": "sine=frequency=920:duration=6:sample_rate=44100,volume=0.06",  # 呼应 shot_001 的 880Hz 鸟鸣略变
        "desc": "清晨鸟鸣呼应",
    },
}

# 每镜的 LUT（ffmpeg color filter）
# Day 1（晨 → 黄昏）：暖色 LUT（轻微增加红黄）
# Day 2（夜 → 晨）：冷色 LUT（轻微增加蓝青）
LUT_RECIPES = {
    # shot_id: (eq brightness, eq saturation, eq gamma, color_balance_r, color_balance_b)
    "shot_001": (0.02, 1.05, 0.98, 1.04, 0.98),  # 晨光暖金
    "shot_002": (0.05, 1.10, 1.00, 1.02, 0.96),  # 正午饱和
    "shot_003": (-0.02, 1.08, 1.05, 1.06, 0.92),  # 黄昏暖橙
    "shot_004": (-0.08, 0.95, 1.10, 0.94, 1.10),  # 深夜冷蓝
    "shot_005": (-0.12, 0.90, 1.20, 0.90, 1.18),  # 月光银青
    "shot_006": (0.02, 1.00, 0.98, 1.03, 0.98),  # 破晓柔暖
}

# Ken Burns 缓动（替代机械线性）：sin 曲线 ease-in-out
# z = 1.0 + 0.18 * sin(π * t / duration)
# 这样镜头起止慢、中间快，比线性自然得多


def upgrade_one(
    ffmpeg: str,
    storyboard_json: Path,
    output_mp4: Path,
    work_dir: Path,
    *,
    ambient: str = "off",  # V3.2 轮 21：默认 OFF（用户反馈"吵"）
) -> None:
    """单镜电影质感升级。

    Args:
        ambient: "off" (默认 - 静音) | "soft" (低音量 pink noise) | "full" (原 anoisesrc 配方)
    """
    shot_id = storyboard_json.stem  # shot_001
    work_dir.mkdir(parents=True, exist_ok=True)

    recipe = AMBIENT_RECIPES.get(shot_id)
    lut = LUT_RECIPES.get(shot_id)
    if not recipe or not lut:
        raise ValueError(f"no recipe/lut for {shot_id}")

    # 找 image_gen 产物（input jpg）
    # 路径规则：sample/story_arc_orange_cat_4_seasons/day{1,2}_storyboards/shot_NNN.json
    #         → sample/run_story_arc_orange_cat_4_seasons_shot_NNN/intermediate/image_000.jpg
    sample_root = storyboard_json.parent.parent.parent  # sample/
    run_dir = sample_root / f"run_story_arc_orange_cat_4_seasons_{shot_id}"
    image_jpg = run_dir / "intermediate" / "image_000.jpg"
    if not image_jpg.is_file():
        raise FileNotFoundError(f"image not found: {image_jpg}")

    duration = 6  # V3.2 固定 6s
    width, height = 1280, 720
    fps = 24

    # 缓动 zoompan + L1 微动（ffmpeg trick，0 quota）
    # - 抛物线 ease-in-out 替代线性匀速（z 表达式）
    # V3.2 自动开发冲刺（轮 21）：去 rotate（Hailuo 真视频不需要手持摇晃感——用户反馈）
    # - tblend 多帧累积（消除锐利瞬间）
    N = fps * duration
    vf = (
        # 主 zoompan（抛物线缓动）
        f"zoompan=z=1+0.18*(on/{N})*(1-on/{N})*4:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={duration}:s={width}x{height}:fps={fps},"
        # 色调 LUT
        f"eq=brightness={lut[0]}:saturation={lut[1]}:gamma={lut[2]},"
        # tblend 多帧混合（消除锐利瞬间；avg 模式 + 5% opacity = 柔化）
        f"tblend=all_mode=average:all_opacity=0.05,"
        # 胶片颗粒
        f"noise=alls=6:allf=t+u"
    )

    # Ambient 音频：V3.2 轮 21 — 默认 OFF（用户反馈"吵"）；支持 off / soft / full 三档
    audio_input = None
    af_filter = None
    use_audio = False
    if ambient != "off":
        # 至少生成 anoisesrc，然后按 ambient 档调音量
        audio_input = (
            f"anoisesrc=color={recipe['noise']}:duration={duration}:sample_rate=44100"
        )
        if ambient == "soft":
            # 软档：音量减到 1/3
            af_filter = (
                f"highpass=f={recipe['highpass']},lowpass=f={recipe['lowpass']},"
                f"volume={recipe['volume'] * 0.3:.3f}"
            )
        else:  # "full"
            af_filter = (
                f"highpass=f={recipe['highpass']},lowpass=f={recipe['lowpass']},"
                f"volume={recipe['volume']}"
            )
        use_audio = True

    cmd = [
        ffmpeg, "-y",
        "-loop", "1", "-t", str(duration), "-i", str(image_jpg),
        "-t", str(duration),  # V3.2 轮 21：强制限输出 6s（防 looped input 跑超）
    ]
    if use_audio and audio_input:
        cmd.extend(["-f", "lavfi", "-i", audio_input])
    cmd.extend(["-vf", vf])
    if af_filter:
        cmd.extend(["-af", af_filter])
    cmd.extend([
        "-c:v", "libx264", "-preset", "slow", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k",
        "-pix_fmt", "yuv420p", "-color_range", "tv",
    ])
    if use_audio:
        cmd.extend(["-shortest"])  # V3.2 轮 10：避免 anoisesrc duration 超 6s
    cmd.extend([
        "-movflags", "+faststart",
        str(output_mp4),
    ])
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        print(f"[FAIL] {shot_id}: {proc.stderr[-300:]}", file=sys.stderr)
        raise RuntimeError(f"ffmpeg failed for {shot_id}")


def main() -> int:
    p = argparse.ArgumentParser(prog="cinematic_upgrade")
    p.add_argument("story_dir", help="含 shot_00?.json 的目录")
    p.add_argument("--suffix", default="_cinema", help="输出文件后缀（默认 _cinema）")
    p.add_argument("--out-dir", default=None, help="输出目录（默认 sample/）")
    p.add_argument("--ambient", default="off", choices=("off", "soft", "full"),
                   help="V3.2 轮 21：背景音档（off=静音 / soft=低音量 / full=原配方），默认 off")
    args = p.parse_args()

    story_dir = Path(args.story_dir)
    if not story_dir.is_dir():
        print(f"[FAIL] {story_dir} 不存在", file=sys.stderr)
        return 2
    out_dir = Path(args.out_dir) if args.out_dir else story_dir.parent.parent
    # 默认输出到 sample/
    if out_dir.name == "sandbox":
        out_dir = out_dir.parent / "sample"
    out_dir.mkdir(parents=True, exist_ok=True)

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    print(f"[INFO] ffmpeg: {ffmpeg}")
    print(f"[INFO] story_dir: {story_dir}, out_dir: {out_dir}")

    work_root = out_dir / "_cinema_work"
    work_root.mkdir(parents=True, exist_ok=True)

    count = 0
    for sb in sorted(story_dir.glob("*.json")):
        shot_id = sb.stem
        out_mp4 = out_dir / f"story_arc_orange_cat_4_seasons_{shot_id}{args.suffix}.mp4"
        work_dir = work_root / shot_id
        try:
            upgrade_one(ffmpeg, sb, out_mp4, work_dir, ambient=args.ambient)
            count += 1
            print(f"[OK] {out_mp4.name} ({out_mp4.stat().st_size:,} bytes) ambient={args.ambient}")
        except Exception as exc:
            print(f"[FAIL] {shot_id}: {exc}", file=sys.stderr)
            return 1

    print("\n=== upgraded: " + str(count) + " clips ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
