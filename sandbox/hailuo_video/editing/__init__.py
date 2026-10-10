"""V3.2 重构：editing 子模块拆分 —— 公共 re-export。

原 `editing_engine.py` (968 行) 拆为 7 个文件：
  - errors.py        EditingError
  - ffmpeg_runner.py  _resolve_ffmpeg_exe / _runner / set_runner / _probe_duration / _probe_dimensions
  - color.py         ColorStats / EditingResult / analyze_video_color / compute_target_stats /
                     _compute_eq_filter / normalize_clip / color_normalize_pipeline / is_available
  - crossfade.py     VALID_XFADE_TRANSITIONS / CrossfadeResult / _xfade_filter_chain / crossfade_clips
  - motion.py        MotionPeak / find_motion_peak / motion_match_cut_points
  - lut.py           LUTResult / apply_lut / write_neutral_cube_lut
  - full.py          FullEditingResult / full_editing_pipeline

公共 API 100% 兼容（re-export）—— 旧路径 `from hailuo_video.editing_engine import X` 已迁移到 `editing.X`。
"""
from __future__ import annotations


# ---------- errors ----------
from hailuo_video.editing.errors import EditingError

# ---------- ffmpeg runner ----------
from hailuo_video.editing.ffmpeg_runner import (
    RunFFmpegFn,
    _default_runner,
    _probe_dimensions,
    _probe_duration,
    _resolve_ffmpeg_exe,
    _runner,
    set_runner,
)

# ---------- color (色彩归一化) ----------
from hailuo_video.editing.color import (
    ColorStats,
    EditingResult,
    _compute_eq_filter,
    _compute_color_stats_from_png,
    _ffmpeg_extract_middle_frame,
    analyze_video_color,
    color_normalize_pipeline,
    compute_target_stats,
    is_available,
    normalize_clip,
)

# ---------- crossfade (重叠帧叠化) ----------
from hailuo_video.editing.crossfade import (
    VALID_XFADE_TRANSITIONS,
    CrossfadeResult,
    _xfade_filter_chain,
    crossfade_clips,
)

# ---------- motion (运动匹配剪切) ----------
from hailuo_video.editing.motion import (
    MotionPeak,
    _extract_frame_at,
    _frame_diff_score,
    find_motion_peak,
    motion_match_cut_points,
)

# ---------- lut (统一 LUT 调色) ----------
from hailuo_video.editing.lut import (
    LUTResult,
    apply_lut,
    write_neutral_cube_lut,
)

# ---------- full pipeline (完整后期管线) ----------
from hailuo_video.editing.full import (
    FullEditingResult,
    full_editing_pipeline,
)


__all__ = [
    # errors
    "EditingError",
    # ffmpeg_runner
    "RunFFmpegFn",
    "_default_runner",
    "_probe_dimensions",
    "_probe_duration",
    "_resolve_ffmpeg_exe",
    "_runner",
    "set_runner",
    # color
    "ColorStats",
    "EditingResult",
    "_compute_eq_filter",
    "_compute_color_stats_from_png",
    "_ffmpeg_extract_middle_frame",
    "analyze_video_color",
    "color_normalize_pipeline",
    "compute_target_stats",
    "is_available",
    "normalize_clip",
    # crossfade
    "VALID_XFADE_TRANSITIONS",
    "CrossfadeResult",
    "_xfade_filter_chain",
    "crossfade_clips",
    # motion
    "MotionPeak",
    "_extract_frame_at",
    "_frame_diff_score",
    "find_motion_peak",
    "motion_match_cut_points",
    # lut
    "LUTResult",
    "apply_lut",
    "write_neutral_cube_lut",
    # full
    "FullEditingResult",
    "full_editing_pipeline",
]
