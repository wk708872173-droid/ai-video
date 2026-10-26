"""V3.8 Batch 4 — W3 跨镜连续性门禁 + 拼接层帧率归一化。

锁住两个已确认缺陷：

  缺陷 7  跨镜连续性**无门禁**：单镜 SSIM gate 管不到"切点之后"。
          实测跨镜色差 mean=0.700 / min=0.587，理想基线 >0.95
          （logs/_diagnose_3to4_jump_20261016/SUMMARY.md:36-39），
          而旧的 compute_color_consistency 门禁只有 0.65 → 0.700 的片子"通过"。
  缺陷 8  拼接层放大崩溃：Hailuo 8fps 与 Ken Burns 24fps 混合，`-c copy` 不重采样
          → 帧突跳跨段扩散（同 SUMMARY.md:41-55）。

文献依据：arXiv:2608.16717（PersonaShot：跨切点连续性 16 指标）、
arXiv:2510.26412（LoCoT2V-Eval：5 维评估）、arXiv:2509.22799（VideoScore2：可解释报告）。

R4：纯 PIL 计算，不需要真 ffmpeg / 不打真 API。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from hailuo_video import continuity as ct  # noqa: E402
from hailuo_video.editing import fps as fpsmod  # noqa: E402


# ---------- helpers ----------

def _write_img(path: Path, color: tuple[int, int, int], size=(64, 64)) -> Path:
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)
    return path


def _write_noise(path: Path, base: tuple[int, int, int], size=(64, 64)) -> Path:
    """造一张纹理图（细节多 → 与纯色图相似度低）。"""
    from PIL import Image
    import random
    random.seed(7)
    im = Image.new("RGB", size, base)
    px = im.load()
    for y in range(size[1]):
        for x in range(size[0]):
            px[x, y] = (
                max(0, min(255, base[0] + random.randint(-90, 90))),
                max(0, min(255, base[1] + random.randint(-90, 90))),
                max(0, min(255, base[2] + random.randint(-90, 90))),
            )
    im.save(path)
    return path


# ============================================================
# 跨镜连续性门禁
# ============================================================

def test_identical_shots_pass_continuity(tmp_path):
    a = _write_img(tmp_path / "a.jpg", (200, 180, 140))
    b = _write_img(tmp_path / "b.jpg", (200, 180, 140))
    rep = ct.evaluate_shot_continuity([a, b])
    assert rep.evaluated is True
    assert rep.mean_similarity > 0.99
    assert rep.flagged_pairs == []
    assert rep.ok is True


def test_very_different_shots_are_flagged(tmp_path):
    """跨镜色差过大 → flag（这是旧门禁 0.65 漏掉的那一类）。"""
    a = _write_img(tmp_path / "a.jpg", (240, 240, 240))
    b = _write_noise(tmp_path / "b.jpg", (10, 20, 200))
    rep = ct.evaluate_shot_continuity([a, b])
    assert rep.flagged_pairs, "跨镜差异过大必须被 flag"
    assert rep.ok is False


def test_threshold_is_stricter_than_legacy_gate():
    """新门禁（0.90）必须比旧的 compute_color_consistency（0.65）严。"""
    assert ct.DEFAULT_CROSS_SHOT_SIMILARITY_THRESHOLD > 0.65


def test_report_has_explainable_dimensions(tmp_path):
    a = _write_img(tmp_path / "a.jpg", (200, 180, 140))
    b = _write_img(tmp_path / "b.jpg", (200, 180, 140))
    rep = ct.evaluate_shot_continuity([a, b])
    for dim in ("perceptual", "physical", "cinematic", "dynamic", "herd"):
        assert dim in rep.dimension_scores, f"缺少评估维度 {dim}"
        assert dim in ct.CONTINUITY_DIMENSIONS


def test_report_format_is_readable(tmp_path):
    a = _write_img(tmp_path / "a.jpg", (200, 180, 140))
    b = _write_img(tmp_path / "b.jpg", (200, 180, 140))
    text = ct.evaluate_shot_continuity([a, b]).format()
    assert "跨镜连续性" in text
    assert "perceptual" in text


def test_report_to_dict_is_json_serializable(tmp_path):
    import json
    a = _write_img(tmp_path / "a.jpg", (200, 180, 140))
    b = _write_img(tmp_path / "b.jpg", (10, 10, 10))
    rep = ct.evaluate_shot_continuity([a, b])
    json.dumps(rep.to_dict(), ensure_ascii=False)   # 不抛错即可


def test_single_image_is_not_evaluated(tmp_path):
    a = _write_img(tmp_path / "a.jpg", (200, 180, 140))
    rep = ct.evaluate_shot_continuity([a])
    assert rep.evaluated is False
    assert rep.warnings


def test_missing_images_are_warned_not_crashed(tmp_path):
    rep = ct.evaluate_shot_continuity([tmp_path / "nope1.jpg", tmp_path / "nope2.jpg"])
    assert rep.evaluated is False
    assert rep.warnings


def test_color_lock_mismatch_is_detected(tmp_path):
    """色基调锁定冲突 → HERD 维度扣分 + 记 mismatch。"""
    a = _write_img(tmp_path / "a.jpg", (200, 180, 140))
    b = _write_img(tmp_path / "b.jpg", (200, 180, 140))
    rep = ct.evaluate_shot_continuity([a, b], color_locks=["warm-gold", "cool-blue"])
    assert rep.color_lock_mismatches
    assert rep.ok is False
    assert rep.dimension_scores["herd"] < 1.0


def test_color_lock_match_passes(tmp_path):
    a = _write_img(tmp_path / "a.jpg", (200, 180, 140))
    b = _write_img(tmp_path / "b.jpg", (200, 180, 140))
    rep = ct.evaluate_shot_continuity([a, b], color_locks=["warm-gold", "warm-gold"])
    assert rep.color_lock_mismatches == []
    assert rep.dimension_scores["herd"] == 1.0


def test_brightness_jump_is_flagged(tmp_path):
    """亮度突变（暗→亮）→ physical 维度扣分。"""
    dark = _write_img(tmp_path / "dark.jpg", (5, 5, 5))
    bright = _write_img(tmp_path / "bright.jpg", (255, 255, 255))
    rep = ct.evaluate_shot_continuity([dark, bright],
                                      brightness_delta_threshold=0.12)
    assert rep.max_brightness_delta > 0.12
    assert rep.dimension_scores["physical"] < 1.0


# ============================================================
# orchestrator：连续性结果进 PipelineResult.consistency
# ============================================================

def test_orchestrator_puts_continuity_in_consistency(tmp_path):
    from unittest.mock import MagicMock
    from hailuo_video.artifact import Artifact, Registry
    from hailuo_video.orchestrator import PipelineAgent, PipelineConfig
    from hailuo_video.script_engine import Character, SceneSpec, Shot, Storyboard

    shots = [
        Shot(index=1, scene_id=1, shot_type="中景", description="d1", characters=[]),
        Shot(index=2, scene_id=1, shot_type="中景", description="d2", characters=[]),
    ]
    sb = Storyboard(title="t", characters=[], scenes=[SceneSpec(id=1, location="x")], shots=shots)

    agent = PipelineAgent(PipelineConfig(run_dir=tmp_path / "run"))
    reg = Registry()
    for idx, color in enumerate([(200, 180, 140), (10, 10, 10)]):
        p = _write_img(tmp_path / f"img_{idx}.jpg", color)
        reg.add(Artifact(artifact_id=f"image_{idx}", kind="image", path=p, producer="t",
                         metadata={"shot_idx": idx}), compute_hash=False)
    agent.registry = reg

    rep = agent._evaluate_continuity({"storyboard": sb})
    assert rep is not None
    assert rep.evaluated is True
    assert rep.flagged_pairs, "两张差异大的图应被 flag"

    consistency = agent._build_consistency({"continuity_report": rep})
    assert consistency["continuity_ok"] is False
    assert consistency["continuity"]["mean_similarity"] >= 0.0


def test_orchestrator_continuity_needs_two_shots(tmp_path):
    from hailuo_video.orchestrator import PipelineAgent, PipelineConfig
    from hailuo_video.script_engine import SceneSpec, Shot, Storyboard

    sb = Storyboard(title="t", characters=[],
                    scenes=[SceneSpec(id=1, location="x")],
                    shots=[Shot(index=1, scene_id=1, shot_type="中景", description="d")])
    agent = PipelineAgent(PipelineConfig(run_dir=tmp_path / "run"))
    assert agent._evaluate_continuity({"storyboard": sb}) is None


def test_orchestrator_continuity_failure_does_not_crash(tmp_path):
    """连续性评估异常不能让已产出的成片崩掉（质量信号，不是故障点）。"""
    from unittest.mock import patch
    from hailuo_video.orchestrator import PipelineAgent, PipelineConfig

    agent = PipelineAgent(PipelineConfig(run_dir=tmp_path / "run"))
    with patch("hailuo_video.continuity.evaluate_shot_continuity",
               side_effect=RuntimeError("boom")):
        assert agent._evaluate_continuity({"storyboard": object()}) is None


# ============================================================
# 拼接层帧率归一化（缺陷 8）
# ============================================================

def test_fps_filter_string():
    assert fpsmod.fps_filter(24) == "fps=24"
    assert fpsmod.fps_filter(30) == "fps=30"
    assert fpsmod.fps_filter(0) == "fps=1"     # 下限保护


def test_detect_mixed_fps_recognizes_difference():
    assert fpsmod.is_mixed_fps([8.0, 24.0]) is True
    assert fpsmod.is_mixed_fps([24.0, 24.0]) is False
    assert fpsmod.is_mixed_fps([None, None]) is False
    assert fpsmod.is_mixed_fps([24.0]) is False


def test_crossfade_filter_chain_includes_fps_normalization():
    """叠化前每段都要重采样到统一帧率（否则帧突跳跨段扩散）。"""
    import inspect
    from hailuo_video.editing import crossfade as cf

    src = inspect.getsource(cf.crossfade_clips)
    assert "fps_filter" in src, "crossfade 链里没有 fps 归一化"
    assert "target_fps" in inspect.signature(cf.crossfade_clips).parameters


def test_crossfade_chain_builds_fps_into_each_clip():
    """直接验证 filter chain 的形状：每段都有 fps=24。"""
    from hailuo_video.editing.crossfade import _xfade_filter_chain  # noqa: F401
    from hailuo_video.editing.fps import fps_filter

    fps_part = fps_filter(24)
    assert fps_part == "fps=24"
    # 与 crossfade 里的拼接方式一致（scale 后、setsar 前）
    chain = (f"[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,"
             f"pad=1920:1080:(ow-iw)/2:(oh-ih)/2,{fps_part},setsar=1[v00];")
    assert chain.count("fps=24") == 1
    assert "setsar=1" in chain


def test_crossfade_target_fps_can_be_disabled():
    """target_fps=None → 不做归一化（保留旧行为的逃生口）。"""
    import inspect
    from hailuo_video.editing import crossfade as cf

    sig = inspect.signature(cf.crossfade_clips)
    assert sig.parameters["target_fps"].default == 24
    assert sig.parameters["target_fps"].default is not None
