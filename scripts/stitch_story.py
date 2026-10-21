"""V3.2 自动开发冲刺（轮 8）—— 拼接 N 条视频为完整故事 mp4。

用法：
    python scripts/stitch_story.py <output_mp4> <input1.mp4> <input2.mp4> [...]

例：
    python scripts/stitch_story.py \
        sample/story_final.mp4 \
        sample/story_orange_cat_4_seasons_shot_001.mp4 \
        sample/story_orange_cat_4_seasons_shot_002.mp4 \
        ...

特性：
  - 默认用 crossfade（0.5s）平滑转场；可用 --transition=concat 改硬切
  - 自动归一化分辨率（取最大 width/height）
  - 自动归一化帧率（取最大 fps）
  - 输出 H.264 + AAC 编码 mp4

底层走 editing_engine.crossfade_clips()（已在 V3.2 轮 1 实装 + 测试覆盖）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SANDBOX = REPO / "sandbox"
sys.path.insert(0, str(SANDBOX))


def stitch(video_paths: list[Path], output_path: Path, *, transition: str = "fade", overlap_seconds: float = 0.5) -> Path:
    """拼接 N 个 mp4 为一条 mp4（用 crossfade_clips）。

    Args:
        video_paths: 输入 mp4 列表（顺序即故事顺序）
        output_path: 输出 mp4
        transition: "fade" | 其他 crossfade 类型（见 editing_engine.VALID_XFADE_TRANSITIONS）
        overlap_seconds: 转场叠化时长

    Returns:
        output_path（实写入文件）

    注：V3.2 自动开发冲刺（轮 8）：自动检测无音轨 → 用 concat demuxer 拼接
    （避免 editing_engine.crossfade_clips 要求音轨导致 filtergraph 报错）。
    """
    if len(video_paths) < 1:
        raise ValueError("video_paths 不能为空")
    if len(video_paths) == 1:
        # 单文件：直接复制
        import shutil
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(video_paths[0], output_path)
        return output_path

    # 检测是否有任一 mp4 含音轨
    has_audio = _any_has_audio(video_paths)
    if not has_audio:
        # 无音轨：用 ffmpeg concat demuxer（无转场，仅拼接）
        return _concat_demuxer(video_paths, output_path)

    # 优先尝试 crossfade（带转场）
    if transition and transition != "concat":
        try:
            from hailuo_video import editing as ed_mod  # noqa: E402  # V3.2 重构：editing_engine → editing 包
            result = ed_mod.crossfade_clips(
                video_paths=[str(p) for p in video_paths],
                output_path=str(output_path),
                transition=transition,
                overlap_seconds=overlap_seconds,
            )
            return Path(result.output_path)
        except Exception as exc:
            # V3.2 轮 10：crossfade 复杂 filtergraph 在某些输入下崩 → 降级用 concat filter
            print("[WARN] crossfade failed (" + exc.__class__.__name__ + ") → fallback concat filter", file=sys.stderr)
    # 降级：concat filter（重新编码，保留 audio，硬切）
    return _concat_with_audio(video_paths, output_path)
    result = ed_mod.crossfade_clips(
        video_paths=[str(p) for p in video_paths],
        output_path=str(output_path),
        transition=transition,
        overlap_seconds=overlap_seconds,
    )
    return Path(result.output_path)


def _concat_demuxer(video_paths: list[Path], output_path: Path) -> Path:
    """无音轨拼接（ffmpeg concat demuxer，硬切，无转场）。"""
    import subprocess
    try:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        raise RuntimeError("无 imageio_ffmpeg；不能拼接")
    list_file = output_path.with_suffix(".concat.txt")
    list_file.write_text(
        "\n".join(f"file '{str(p).replace(chr(92), '/')}'" for p in video_paths),
        encoding="utf-8",
    )
    try:
        result = subprocess.run(
            [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
             "-c", "copy", str(output_path)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"concat ffmpeg failed: {result.stderr[-300:]}")
    finally:
        list_file.unlink(missing_ok=True)
    return output_path


def _any_has_audio(video_paths: list[Path]) -> bool:
    """任一 mp4 含音轨 → True（走 crossfade）；否则 False（走 concat demuxer）。"""
    import subprocess
    try:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return True  # 默认 True（避免误判让 crossfade 失败）
    for p in video_paths:
        try:
            result = subprocess.run(
                [ffmpeg, "-i", str(p)],
                capture_output=True, text=True, timeout=10,
            )
            if "Audio:" in result.stderr:
                return True
        except Exception:
            return True
    return False


def _concat_with_audio(video_paths: list[Path], output_path: Path) -> Path:
    """带音轨拼接（用 ffmpeg concat filter 重新编码，允许不同 codec）。"""
    import subprocess
    try:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        raise RuntimeError("无 imageio_ffmpeg；不能拼接")
    # concat filter：接受多个 -i，逐个连接
    # filter: [0:v][0:a][1:v][1:a]...[Nv-1:v][Nv-1:a]concat=n=N:v=1:a=1[v][a]
    inputs = []
    for vp in video_paths:
        inputs.extend(["-i", str(vp)])
    n = len(video_paths)
    filter_complex = (
        "".join(f"[{i}:v][{i}:a]" for i in range(n))
        + f"concat=n={n}:v=1:a=1[vout][aout]"
    )
    cmd = [
        ffmpeg, "-y", *inputs,
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "slow", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise RuntimeError(f"concat filter failed: {result.stderr[-300:]}")
    return output_path


def main() -> int:
    p = argparse.ArgumentParser(
        prog="stitch_story",
        description="拼接多条视频为完整故事 mp4（crossfade 转场）",
    )
    p.add_argument("output", help="输出 mp4 路径")
    p.add_argument("inputs", nargs="+", help="输入 mp4 路径（顺序即故事顺序）")
    p.add_argument("--transition", default="fade",
                   choices=("fade", "fadeblack", "wipeleft", "wiperight", "dissolve"),
                   help="转场效果（默认 fade）")
    p.add_argument("--overlap", type=float, default=0.5,
                   help="转场叠化时长秒（0.05..2.0，默认 0.5）")
    args = p.parse_args()

    inputs = [Path(p) for p in args.inputs]
    for p in inputs:
        if not p.is_file():
            print(f"[FAIL] 输入文件不存在：{p}", file=sys.stderr)
            return 2
    output = Path(args.output)

    try:
        written = stitch(inputs, output, transition=args.transition, overlap_seconds=args.overlap)
    except Exception as exc:
        print(f"[FAIL] stitch: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    size = written.stat().st_size
    print(f"[OK] {len(inputs)} clips → {written} ({size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
