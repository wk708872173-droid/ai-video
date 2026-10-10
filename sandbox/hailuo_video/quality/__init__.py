"""V3.2 重构：quality 子模块拆分 —— 公共 re-export。

原 `quality.py` (1012 行) 拆为 7 个文件：
  - logger.py        _qa_logger 单例（避免循环导入）
  - picker.py        warn_picker_fallback
  - validation.py    Issue / ValidationReport / validate_storyboard / check_assets / AssetReport
  - consistency.py   ConsistencyReport / compute_color_consistency + _normalize_hist / _cosine_sim / _variance / assess_pipeline_output
  - face.py          FaceConsistencyReport + FaceSimilarityBackend + 3 后端 + compute_face_consistency +
                     _DEFAULT_BACKEND / get_default_backend / set_default_backend
  - frame_quality.py FrameQualityReport (V3.3 W1) + extract_keyframes + _lite_ssim + compute_frame_quality

公共 API 100% 兼容（re-export）—— 旧路径 `from hailuo_video.quality import X` 已迁移到 `quality.X`。
"""
from __future__ import annotations

# 保留 `quality.ap` 别名（向后兼容：tests/test_quality.py 用 quality.ap.DEFAULT_ANGLES）
from hailuo_video import asset_prep as ap


# ---------- logger ----------
from hailuo_video.quality.logger import get_qa_logger

# ---------- picker ----------
from hailuo_video.quality.picker import warn_picker_fallback

# ---------- validation ----------
from hailuo_video.quality.validation import (
    AssetCheck,
    AssetReport,
    Issue,
    SceneCheck,
    ValidationReport,
    check_assets,
    validate_storyboard,
)

# ---------- consistency ----------
from hailuo_video.quality.consistency import (
    ConsistencyReport,
    _cosine_sim,
    _normalize_hist,
    _variance,
    assess_pipeline_output,
    compute_color_consistency,
)

# ---------- face (V3.2 REQ-402) ----------
from hailuo_video.quality.face import (
    FaceConsistencyReport,
    FaceSimilarityBackend,
    HashFaceSimilarityBackend,
    NullFaceSimilarityBackend,
    RealFaceRecognitionBackend,
    _DEFAULT_BACKEND,
    _DEFAULT_THRESHOLD,
    compute_face_consistency,
    get_default_backend,
    set_default_backend,
)

# ---------- frame_quality (V3.3 W1) ----------
# 注：frame_quality.py 末尾 import consistency._variance；故 consistency 必须先于 frame_quality
from hailuo_video.quality.frame_quality import (
    DEFAULT_BRIGHTNESS_VAR_THRESHOLD,
    DEFAULT_COLOR_THRESHOLD,
    DEFAULT_MOTION_VAR_THRESHOLD,
    DEFAULT_SAMPLE_FRAMES,
    DEFAULT_SSIM_THRESHOLD,
    FrameQualityReport,
    _frame_brightness,
    _frame_color_hist24,
    _lite_ssim,
    compute_frame_quality,
    extract_keyframes,
)


__all__ = [
    # logger
    "get_qa_logger",
    # picker
    "warn_picker_fallback",
    # validation
    "AssetCheck",
    "AssetReport",
    "Issue",
    "SceneCheck",
    "ValidationReport",
    "check_assets",
    "validate_storyboard",
    # consistency
    "ConsistencyReport",
    "_cosine_sim",
    "_normalize_hist",
    "_variance",
    "assess_pipeline_output",
    "compute_color_consistency",
    # face
    "FaceConsistencyReport",
    "FaceSimilarityBackend",
    "HashFaceSimilarityBackend",
    "NullFaceSimilarityBackend",
    "RealFaceRecognitionBackend",
    "_DEFAULT_BACKEND",
    "_DEFAULT_THRESHOLD",
    "compute_face_consistency",
    "get_default_backend",
    "set_default_backend",
    # frame_quality
    "DEFAULT_BRIGHTNESS_VAR_THRESHOLD",
    "DEFAULT_COLOR_THRESHOLD",
    "DEFAULT_MOTION_VAR_THRESHOLD",
    "DEFAULT_SAMPLE_FRAMES",
    "DEFAULT_SSIM_THRESHOLD",
    "FrameQualityReport",
    "_frame_brightness",
    "_frame_color_hist24",
    "_lite_ssim",
    "compute_frame_quality",
    "extract_keyframes",
]
