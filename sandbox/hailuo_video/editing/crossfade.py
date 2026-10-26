"""V3.2 重构：editing 子模块拆分 — 重叠帧叠化（xfade）。

原 editing_engine.py:391-600 抽到独立文件：
  - VALID_XFADE_TRANSITIONS（47 种 xfade transition 名）
  - CrossfadeResult dataclass
  - _xfade_filter_chain
  - crossfade_clips
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hailuo_video.editing import ffmpeg_runner
from hailuo_video.editing.errors import EditingError
from hailuo_video.editing.fps import DEFAULT_TARGET_FPS, fps_filter
from hailuo_video import editing as _editing_pkg


# ffmpeg xfade 支持的 transition 名（与 ffmpeg 7.x 一致）
VALID_XFADE_TRANSITIONS = (
    "fade", "fadeblack", "fadewhite", "wipeleft", "wiperight",
    "wipeup", "wipedown", "slideleft", "slideright", "slideup",
    "slidedown", "circlecrop", "rectcrop", "distance", "fadegrays",
    "pixelize", "radial", "smoothleft", "smoothright", "smoothup",
    "smoothdown", "circleopen", "circleclose", "vertopen", "vertclose",
    "horzopen", "horzclose", "dissolve", "hlslice", "hrslice",
    "vuslice", "vdslice", "hblur", "fadefast", "fadeslow",
    "hlwind", "hrwind", "vuwind", "vdwind", "coverleft", "coverright",
    "coverup", "coverdown", "revealleft", "revealright", "revealup",
    "revealdown",
)


@dataclass
class CrossfadeResult:
    """多段视频叠化结果。"""
    output_path: str = ""
    source_paths: list[str] = field(default_factory=list)
    clip_count: int = 0
    transition: str = "fade"
    overlap_seconds: float = 0.25
    duration_seconds: float = 0.0
    ffmpeg_filter: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_path": self.output_path,
            "source_paths": list(self.source_paths),
            "clip_count": self.clip_count,
            "transition": self.transition,
            "overlap_seconds": self.overlap_seconds,
            "duration_seconds": self.duration_seconds,
            "ffmpeg_filter": self.ffmpeg_filter,
        }


def _xfade_filter_chain(
    clip_count: int,
    durations: list[float],
    transition: str,
    overlap: float,
) -> str:
    """拼 ffmpeg xfade 链路 filter 表达式。

    思路：N 个 input → N-1 个 xfade 节点 → output。
    ffmpeg 语法：
      [0][1]xfade=transition=T:duration=D:offset=O1[v01];
      [v01][2]xfade=transition=T:duration=D:offset=O2[v012];
      ...
    offset = cumulative_duration - overlap_per_junction
    """
    if clip_count < 2:
        raise EditingError("xfade 至少需要 2 段视频")
    if transition not in VALID_XFADE_TRANSITIONS:
        raise EditingError(
            f"非法 xfade transition={transition!r}；"
            f"允许：{', '.join(VALID_XFADE_TRANSITIONS[:10])}..."
        )
    if overlap < 0.05 or overlap > 2.0:
        raise EditingError(f"overlap 越界：{overlap}（允许 0.05..2.0 秒）")

    chain_parts: list[str] = []
    cumulative = durations[0]
    # V3.3 W2：修复 — prev_label 用 scaled label "v00"，避免与 scale chain 冲突
    # 旧逻辑用 "0"（原始输入），但 scale chain 已消耗 [0:v]，导致悬空 filter + "setsar unconnected"
    prev_label = "v00"
    for i in range(1, clip_count):
        # offset: 在 cumulative 时刻开始叠化；下一次的 cumulative 加上当前段时长并减 overlap
        offset = cumulative - overlap
        if offset < 0:
            offset = 0.0
        next_label = f"v{i:02d}{i+1:02d}" if i < clip_count - 1 else "vout"
        chain_parts.append(
            f"[{prev_label}][v{i:02d}]xfade=transition={transition}:duration={overlap:.3f}:offset={offset:.3f}[{next_label}]"
        )
        prev_label = next_label
        # 下一次 offset 的累计：cumulative += durations[i] - overlap
        cumulative += durations[i] - overlap

    return ";".join(chain_parts)


def crossfade_clips(
    video_paths: list[str | Path],
    output_path: str | Path,
    *,
    transition: str = "fade",
    overlap_seconds: float = 0.25,
    audio_crossfade: bool = True,
    timeout: int = 300,
    extra_args: list[str] | None = None,
    target_fps: int | None = DEFAULT_TARGET_FPS,
) -> CrossfadeResult:
    """多段视频叠化为一条（REQ-303.b 重叠帧叠化）。

    Args:
        video_paths: 至少 2 段
        output_path: 输出 mp4 路径
        transition: xfade transition 名（VALID_XFADE_TRANSITIONS）
        overlap_seconds: 相邻段叠化时长（0.05..2.0 秒）
        audio_crossfade: True 用 acrossfade 滤镜叠音轨；False 静音叠化段
        target_fps: V3.8 W3：统一目标帧率（None = 不归一化，保持旧行为）。
            Hailuo 出 8fps、Ken Burns 出 24fps，混合帧率会让帧突跳跨段扩散
            （logs/_diagnose_3to4_jump_20261016/SUMMARY.md:41-55）。
    失败抛 EditingError。
    """
    started = time.monotonic()
    if len(video_paths) < 2:
        raise EditingError(f"crossfade_clips 至少 2 段；当前 {len(video_paths)} 段")

    # 探测时长（用于算 offset）；失败兜底 5s/段
    durations: list[float] = []
    for vp in video_paths:
        d = _editing_pkg._probe_duration(str(vp))
        if d is None or d <= 0:
            d = 5.0
        durations.append(d)

    # 探测分辨率（用于 scale 归一化）；失败兜底 1920x1080
    w, h = 1920, 1080
    first_dim = _editing_pkg._probe_dimensions(str(video_paths[0]))
    if first_dim:
        w, h = first_dim

    # 拼 filter chain：先 scale → 再 xfade → 再 acrossfade（可选）
    # V3.8 W3：在 scale 之后插 fps 归一化 —— 各段帧率统一（Hailuo 8fps / Ken Burns 24fps
    #   混合会让帧突跳跨段扩散，见 logs/_diagnose_3to4_jump_20261016/SUMMARY.md:41-55）。
    fps_chain = f",{fps_filter(target_fps)}" if target_fps else ""
    scale_chain = "".join(
        f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2{fps_chain},setsar=1[v{i:02d}];"
        for i in range(len(video_paths))
    )
    xfade_chain = _xfade_filter_chain(
        clip_count=len(video_paths),
        durations=durations,
        transition=transition,
        overlap=overlap_seconds,
    )

    audio_chain = ""
    if audio_crossfade:
        # V3.3 W2：探测所有输入是否有音频轨 → 无音频时强制跳过 acrossfade
        # 否则 ffmpeg filter graph 报"matches no streams"（hailuo 真视频无音轨）
        has_any_audio = any(
            ffmpeg_runner._probe_has_audio(str(p)) for p in video_paths
        )
        if not has_any_audio:
            audio_crossfade = False  # 退化到纯视频叠化
        else:
            # acrossfade: [0:a][1:a]acrossfade=d=DURATION[a01];[a01][2:a]acrossfade=d=DURATION[aout]
            a_parts: list[str] = []
            prev_a = "0:a"
            for i in range(1, len(video_paths)):
                next_a = f"a{i:02d}{i+1:02d}" if i < len(video_paths) - 1 else "aout"
                a_parts.append(
                    f"[{prev_a}][{i}:a]acrossfade=d={overlap_seconds:.3f}[{next_a}]"
                )
                prev_a = next_a
            audio_chain = "".join(p + ";" for p in a_parts)

    full_filter = scale_chain + xfade_chain
    if audio_chain:
        full_filter += ";" + audio_chain

    # 拼 ffmpeg 命令
    exe = _editing_pkg._resolve_ffmpeg_exe()
    args: list[str] = [exe, "-y"]
    for vp in video_paths:
        args.extend(["-i", str(vp)])
    args.extend(["-filter_complex", full_filter])
    args.extend(["-map", "[vout]"])
    if audio_chain:
        args.extend(["-map", "[aout]"])
    else:
        args.extend(["-map", "0:a?"])  # 兜底用第一段音轨
    args.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"])
    if extra_args:
        args.extend(extra_args)
    args.append(str(output_path))

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rc, _out, err = ffmpeg_runner._runner(args, timeout=timeout)
    if rc != 0:
        raise EditingError(
            f"crossfade ffmpeg rc={rc}：{err[-300:]}"
        )
    if not out_path.is_file() or out_path.stat().st_size == 0:
        raise EditingError(f"叠化产物为空：{out_path}")

    return CrossfadeResult(
        output_path=str(out_path),
        source_paths=[str(p) for p in video_paths],
        clip_count=len(video_paths),
        transition=transition,
        overlap_seconds=overlap_seconds,
        duration_seconds=time.monotonic() - started,
        ffmpeg_filter=full_filter,
    )
