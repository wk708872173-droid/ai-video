"""V3.2 重构：editing 子模块拆分 — 完整后期管线。

原 editing_engine.py:855-968 抽到独立文件：
  - FullEditingResult dataclass
  - full_editing_pipeline（color_normalize → motion_match → xfade → LUT）
"""
from __future__ import annotations

import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hailuo_video.editing.color import EditingResult
from hailuo_video.editing.crossfade import CrossfadeResult
from hailuo_video.editing.errors import EditingError
from hailuo_video.editing.lut import LUTResult, apply_lut
from hailuo_video.editing.motion import MotionPeak
# 通过 editing 包访问，让 monkeypatch.setattr(editing, ...) 在 full.py 内生效
# （V3.2 重构后子模块不再共享全局）
from hailuo_video import editing as _editing_pkg


@dataclass
class FullEditingResult:
    """完整后期管线结果。"""
    final_path: str = ""
    intermediate_paths: list[str] = field(default_factory=list)
    crossfade: CrossfadeResult | None = None
    lut: LUTResult | None = None
    motion_peaks: list[MotionPeak] = field(default_factory=list)
    color_results: list[EditingResult] = field(default_factory=list)
    total_duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "final_path": self.final_path,
            "intermediate_paths": list(self.intermediate_paths),
            "crossfade": self.crossfade.to_dict() if self.crossfade else None,
            "lut": self.lut.to_dict() if self.lut else None,
            "motion_peaks": [m.to_dict() for m in self.motion_peaks],
            "color_results": [r.to_dict() for r in self.color_results],
            "total_duration_seconds": self.total_duration_seconds,
        }


def full_editing_pipeline(
    video_paths: list[str | Path],
    output_path: str | Path,
    *,
    lut_path: str | Path | None = None,
    transition: str = "fade",
    overlap_seconds: float = 0.25,
    work_dir: str | Path | None = None,
    audio_paths: list[str | Path] | None = None,
    subtitle_paths: list[str | Path] | None = None,
) -> FullEditingResult:
    """完整后期管线：色彩归一化 → 运动匹配 → 叠化 → LUT。

    流程：
      1. color_normalize_pipeline → normalized/*.mp4
      2. motion_match_cut_points → peaks（仅记录，不切割原片）
      3. crossfade_clips → final_pre_lut.mp4
      4. 若 lut_path 给定 → apply_lut → final.mp4；否则 final_pre_lut 直接当 final

    Args:
        video_paths: 至少 1 段
        output_path: 最终输出 mp4
        lut_path: 可选 LUT 文件
        transition: xfade transition
        overlap_seconds: 叠化时长
        work_dir: 中间产物目录；None → 系统临时目录
        audio_paths: **每镜**的旁白 mp3（与 video_paths 按下标对应；可空）
        subtitle_paths: **每镜**的字幕 .vtt/.srt（与 video_paths 按下标对应；可空）

    V4.0 BUG-C：此前 audio/subtitle 从不传入 → TTS 的配音与字幕**从未进成片**
    （实测仓库里 9 个成片有 8 个无音轨）。现在在每镜层面烧字幕 + 混旁白，
    再交给 crossfade 的 acrossfade 把各镜音轨叠起来。

    失败抛 EditingError。
    """
    started = time.monotonic()
    if not video_paths:
        raise EditingError("video_paths 为空")

    work = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="editing_full_"))
    work.mkdir(parents=True, exist_ok=True)
    color_dir = work / "color_normalized"
    pre_lut_path = work / "pre_lut.mp4"

    # Step 1: color normalize
    color_results = _editing_pkg.color_normalize_pipeline(
        video_paths, color_dir,
    )
    normalized_paths = [Path(r.output_path) for r in color_results]

    # Step 1.5: 每镜烧字幕 + 混旁白（V4.0 BUG-C）
    #   在**每镜**层面做，时间戳天然正确（每镜 vtt 是相对该镜的）；
    #   混完的片段带音轨 → 后面 crossfade 的 acrossfade 会把音轨叠起来。
    audio_list = list(audio_paths or [])
    subtitle_list = list(subtitle_paths or [])
    if audio_list or subtitle_list:
        merged_dir = work / "merged"
        merged_dir.mkdir(parents=True, exist_ok=True)
        merged_paths: list[Path] = []
        for i, vpath in enumerate(normalized_paths):
            a = audio_list[i] if i < len(audio_list) else None
            s = subtitle_list[i] if i < len(subtitle_list) else None
            out = merged_dir / f"{i:04d}_av.mp4"
            merged_paths.append(
                _editing_pkg.merge_shot_media(vpath, out, audio=a, subtitle=s,
                                              work_dir=merged_dir)
            )
        normalized_paths = merged_paths

    # Step 2: motion peaks（仅记录）
    motion_peaks = _editing_pkg.motion_match_cut_points(normalized_paths)

    # Step 3: crossfade（单段视频跳过）
    if len(normalized_paths) < 2:
        # 单段：直接复制到 pre_lut_path，不叠化
        shutil.copyfile(normalized_paths[0], pre_lut_path)
        crossfade_result = CrossfadeResult(
            output_path=str(pre_lut_path),
            source_paths=[str(normalized_paths[0])],
            clip_count=1,
            transition="(skipped: single clip)",
            overlap_seconds=0.0,
            ffmpeg_filter="(none)",
        )
    else:
        crossfade_result = _editing_pkg.crossfade_clips(
            normalized_paths, pre_lut_path,
            transition=transition,
            overlap_seconds=overlap_seconds,
        )

    # Step 4: optional LUT
    lut_result: LUTResult | None = None
    if lut_path is not None:
        lut_result = apply_lut(pre_lut_path, output_path, lut_path)
    else:
        # 直接把 pre_lut_path 复制到 output_path
        shutil.copyfile(pre_lut_path, output_path)
        lut_result = LUTResult(
            output_path=str(output_path),
            source_path=str(pre_lut_path),
            lut_path="(no lut)",
        )

    return FullEditingResult(
        final_path=str(output_path),
        intermediate_paths=[str(p) for p in normalized_paths],
        crossfade=crossfade_result,
        lut=lut_result,
        motion_peaks=motion_peaks,
        color_results=color_results,
        total_duration_seconds=time.monotonic() - started,
    )
