"""V3.2 自动开发冲刺（轮 3-2）face_consistency 单测。

覆盖：
  - HashFaceSimilarityBackend（aHash + dHash）：相同图 1.0 / 不同图 < 1.0
  - NullFaceSimilarityBackend：永远 1.0
  - RealFaceRecognitionBackend：NotImplementedError（明确实装未到位）
  - compute_face_consistency：边界（<2 张 / 缺文件 / 全相同 / 全不同）
  - QAAgent 扩展 mode=color / face / combined
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SANDBOX = REPO / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from hailuo_video.quality import (  # noqa: E402
    FaceConsistencyReport,
    FaceSimilarityBackend,
    HashFaceSimilarityBackend,
    NullFaceSimilarityBackend,
    RealFaceRecognitionBackend,
    compute_face_consistency,
)
from hailuo_video.agents import QAAgent, InputValidationError  # noqa: E402


# ---------- 最小 PNG 工具 ----------
# V3.2 自动开发冲刺（轮 5）：从 tests/_png_utils.py 复用
from _png_utils import (  # noqa: E402,F401
    make_min_png as _make_min_png,
    make_noise_png as _make_noise_png,
    make_pattern_png as _make_pattern_png,
)


# ---------- HashFaceSimilarityBackend ----------

def test_hash_backend_identical_images_similarity_1():
    """两张完全相同的 PNG → similarity == 1.0。"""
    be = HashFaceSimilarityBackend()
    a = Path("/tmp") / "a.png"
    b = Path("/tmp") / "b.png"
    _make_min_png(a, rgb=(100, 100, 100))
    _make_min_png(b, rgb=(100, 100, 100))
    sim = be.similarity(a, b)
    assert sim == 1.0


def test_hash_backend_very_different_images_low_similarity(tmp_path):
    """两张不同噪声 → similarity < 0.7（threshold 更宽松；噪声图 16x16 + LANCZOS 后仍有部分位巧合）。"""
    be = HashFaceSimilarityBackend()
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    _make_noise_png(a, seed=1)
    _make_noise_png(b, seed=42)
    sim = be.similarity(a, b)
    assert sim < 0.7


def test_hash_backend_similarity_in_range(tmp_path):
    """任意两图相似度应在 [0, 1]。"""
    be = HashFaceSimilarityBackend()
    for rgb_pair in [((50, 50, 50), (200, 200, 200)),
                      ((10, 100, 200), (200, 100, 10)),
                      ((128, 128, 128), (128, 128, 128))]:
        a = tmp_path / "a.png"
        b = tmp_path / "b.png"
        _make_min_png(a, rgb=rgb_pair[0])
        _make_min_png(b, rgb=rgb_pair[1])
        sim = be.similarity(a, b)
        assert 0.0 <= sim <= 1.0


def test_hash_backend_name():
    assert HashFaceSimilarityBackend().name == "hash"


# ---------- NullFaceSimilarityBackend ----------

def test_null_backend_always_one(tmp_path):
    be = NullFaceSimilarityBackend()
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    _make_min_png(a, rgb=(0, 0, 0))
    _make_min_png(b, rgb=(255, 255, 255))
    assert be.similarity(a, b) == 1.0
    assert be.similarity(b, a) == 1.0


def test_null_backend_name():
    assert NullFaceSimilarityBackend().name == "null"


# ---------- RealFaceRecognitionBackend（stub 硬失败） ----------

def test_real_backend_raises_not_implemented(tmp_path):
    """RealFaceRecognitionBackend.similarity raise NotImplementedError（明确实装未到位）。"""
    be = RealFaceRecognitionBackend()
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    _make_min_png(a)
    _make_min_png(b)
    with pytest.raises(NotImplementedError, match="face_recognition"):
        be.similarity(a, b)


def test_real_backend_name():
    assert RealFaceRecognitionBackend().name == "face_recognition"


# ---------- compute_face_consistency ----------

def test_compute_face_consistency_less_than_2_raises_warning():
    rep = compute_face_consistency([])
    assert rep.warnings
    assert "≥ 2" in rep.warnings[0]
    assert rep.ok is True  # 无 flagged pair


def test_compute_face_consistency_single_image_warning():
    p = Path("/tmp/x.png")
    rep = compute_face_consistency([p])
    assert rep.warnings


def test_compute_face_consistency_missing_file_warning(tmp_path):
    rep = compute_face_consistency([tmp_path / "a.png", tmp_path / "b.png"])
    assert rep.warnings
    assert "不存在" in rep.warnings[0]


def test_compute_face_consistency_identical_no_flagged(tmp_path):
    """两张完全相同的 PNG → similarity == 1.0，无 flagged_pair。"""
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    _make_min_png(a, rgb=(128, 128, 128))
    _make_min_png(b, rgb=(128, 128, 128))
    rep = compute_face_consistency([a, b])
    assert rep.flagged_pairs == []
    assert rep.min_similarity == 1.0
    assert rep.ok is True
    assert rep.backend_name == "hash"


def test_compute_face_consistency_very_different_flagged(tmp_path):
    """两张不同噪声图 → similarity < 阈值 → flagged。"""
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    _make_noise_png(a, seed=1)
    _make_noise_png(b, seed=42)
    rep = compute_face_consistency([a, b], threshold=0.85)
    assert rep.flagged_pairs
    assert rep.flagged_pairs[0][0] == 0
    assert rep.flagged_pairs[0][1] == 1
    assert rep.ok is False


def test_compute_face_consistency_uses_null_backend(tmp_path):
    """Null backend → 永远 1.0，无 flagged。"""
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    _make_noise_png(a, seed=1)
    _make_noise_png(b, seed=42)
    rep = compute_face_consistency([a, b], backend=NullFaceSimilarityBackend())
    assert rep.backend_name == "null"
    assert rep.flagged_pairs == []
    assert rep.min_similarity == 1.0


def test_compute_face_consistency_custom_threshold(tmp_path):
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    _make_noise_png(a, seed=100)
    _make_noise_png(b, seed=200)
    sim = HashFaceSimilarityBackend().similarity(a, b)
    # 用 threshold=0.99 → 中等相似度会被 flag
    rep_low = compute_face_consistency([a, b], threshold=0.99)
    assert sim < 0.99
    assert rep_low.flagged_pairs


def test_face_consistency_report_to_dict():
    rep = FaceConsistencyReport(mean_similarity=0.9, min_similarity=0.85, backend_name="hash")
    d = rep.to_dict()
    assert d["mean_similarity"] == 0.9
    assert d["min_similarity"] == 0.85
    assert d["backend_name"] == "hash"


# ---------- QAAgent 扩展 ----------

def test_qa_agent_validate_rejects_unknown_mode():
    a = QAAgent()
    with pytest.raises(InputValidationError, match="unknown mode"):
        a.validate_inputs({"mode": "bogus"})


def test_qa_agent_validate_accepts_color():
    a = QAAgent()
    a.validate_inputs({"mode": "color"})  # 不抛


def test_qa_agent_validate_accepts_face_and_combined():
    a = QAAgent()
    a.validate_inputs({"mode": "face", "face_backend": "hash"})
    a.validate_inputs({"mode": "combined", "face_backend": "null"})


def test_qa_agent_validate_rejects_unknown_face_backend():
    a = QAAgent()
    with pytest.raises(InputValidationError, match="face_backend"):
        a.validate_inputs({"mode": "face", "face_backend": "deepface"})


def test_qa_agent_execute_face_mode_uses_null_backend(tmp_path, monkeypatch):
    """mode=face + face_backend=null → 永远 ok=True（Null backend）。"""
    a = QAAgent()
    p = tmp_path / "x.png"
    _make_min_png(p)
    result = a.execute({
        "mode": "face",
        "image_paths": [str(p), str(p)],
        "face_backend": "null",
    })
    assert result.ok is True
    assert "face_report" in result.metadata
    assert result.metadata["mode"] == "face"
    # Null backend 永远 1.0 → 无 flagged
    assert result.metadata["face_report"]["min_similarity"] == 1.0


def test_qa_agent_execute_combined_runs_both(tmp_path):
    """mode=combined → 同时跑 color + face。"""
    a = QAAgent()
    p = tmp_path / "x.png"
    _make_min_png(p)
    result = a.execute({
        "mode": "combined",
        "image_paths": [str(p), str(p)],
        "face_backend": "null",
    })
    assert result.ok is True
    assert "color_report" in result.metadata
    assert "face_report" in result.metadata


def test_qa_agent_execute_color_mode_default(tmp_path):
    """默认 mode=color → 只跑 color（向后兼容）。"""
    a = QAAgent()
    p = tmp_path / "x.png"
    _make_min_png(p)
    result = a.execute({"image_paths": [str(p), str(p)]})
    assert result.ok is True
    assert "color_report" in result.metadata
    # 不应有 face_report（默认 mode=color）
    assert "face_report" not in result.metadata


def test_qa_agent_execute_empty_image_paths_skips():
    """image_paths 空 → skipped=True。"""
    a = QAAgent()
    result = a.execute({"image_paths": []})
    assert result.ok is True
    assert result.metadata.get("skipped") is True


def test_qa_agent_execute_real_face_recognition_warns_not_fails(tmp_path):
    """face_backend=face_recognition + stub → QAAgent 捕获 NotImplementedError 写 warning。

    设计选择：QA agent 对 backend 异常采用 warning 模式（不硬失败，让 pipeline 继续），
    与 edit_agent / motion_agent 硬失败模式不同（CLAUDE.md R4：Auth 域硬失败；QA 域软失败）。
    """
    a = QAAgent()
    p = tmp_path / "x.png"
    _make_min_png(p)
    result = a.execute({
        "mode": "face",
        "image_paths": [str(p), str(p)],
        "face_backend": "face_recognition",
    })
    # QA 域软失败：result.ok 仍 True，但 face_report.warnings 含 NotImplementedError
    warnings = result.metadata["face_report"]["warnings"]
    assert any("NotImplementedError" in w for w in warnings), f"expected NotImplementedError in warnings, got {warnings}"
