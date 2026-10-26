"""V3.8 Batch 2 — W5 竖屏原生生成 + 主体感知裁剪。

锁住：
  - 画幅贯通（缺陷 1）：`--aspect-ratio` 能把 9:16 传到 image_gen task；
    不传时由 backend 的 env 决定（不再被硬编码 "16:9" 遮蔽）。
  - 主体感知裁剪（缺陷 10）：`to_vertical` 只有 center/left/right 三档，
    偏心主体被裁掉（sample/story_arc_xiaohei_solo/README.md:253-255）。

文献依据：arXiv:2101.04051（H2V：主体选择 → 主体保持式裁剪）、
arXiv:2307.16371（MobileVidFactory：原生竖屏优于事后裁剪）。

R4：所有测试都不打真 API / 不需要真 ffmpeg（几何计算是纯函数）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SANDBOX = REPO / "sandbox"
SCRIPTS = REPO / "scripts"
for _p in (SANDBOX, SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from hailuo_video.crop import (  # noqa: E402
    CropWindow,
    SubjectBox,
    compute_crop_window,
    estimate_subject_box,
)


# ---------- 裁剪窗口几何 ----------

def test_center_anchor_matches_legacy_floor_division():
    """向后兼容：center 锚点必须与旧版 `(scaled_w - target_w) // 2` 逐像素一致。"""
    src_w, src_h, target_w, target_h = 1920, 1080, 1080, 1920
    scaled_w = src_w * target_h // src_h
    expected = (scaled_w - target_w) // 2
    win = compute_crop_window(src_w, src_h, target_w, target_h, anchor="center")
    assert win.crop_x == expected
    assert win.scaled_w == scaled_w


def test_left_and_right_anchors():
    win_l = compute_crop_window(1920, 1080, 1080, 1920, anchor="left")
    win_r = compute_crop_window(1920, 1080, 1080, 1920, anchor="right")
    excess = win_l.scaled_w - 1080
    assert win_l.crop_x == 0
    assert win_r.crop_x == excess


def test_crop_frac_generalizes_anchors():
    """crop_frac 是三档锚点的连续推广（文档里点名要加的这个参数）。"""
    excess = 3413 - 1080
    win = compute_crop_window(1920, 1080, 1080, 1920, crop_frac=0.25)
    assert win.crop_x == int(round(excess * 0.25))
    assert 0 < win.crop_x < excess


def test_crop_frac_is_clamped():
    assert compute_crop_window(1920, 1080, 1080, 1920, crop_frac=-1.0).crop_x == 0
    excess = 3413 - 1080
    assert compute_crop_window(1920, 1080, 1080, 1920, crop_frac=5.0).crop_x == excess


def test_filter_expr_shape():
    win = compute_crop_window(1920, 1080, 1080, 1920, anchor="center")
    assert win.filter_expr == f"scale=-1:1920,crop=1080:1920:{win.crop_x}:0"


def test_source_already_vertical_has_no_excess():
    win = compute_crop_window(1080, 1920, 1080, 1920)
    assert win.crop_x == 0
    assert win.scaled_w == 1080


def test_invalid_resolution_raises():
    with pytest.raises(ValueError):
        compute_crop_window(0, 1080, 1080, 1920)


# ---------- 主体感知裁剪 ----------

def test_subject_box_shifts_crop_toward_subject():
    """主体偏右 → 裁剪窗口右移（否则主体被裁掉）。"""
    win_center = compute_crop_window(1920, 1080, 1080, 1920, anchor="center")
    # 主体在源画面右侧 75% 处
    box = SubjectBox(x0=1400, y0=200, x1=1800, y1=900)
    win_subj = compute_crop_window(1920, 1080, 1080, 1920, subject_box=box)
    assert win_subj.crop_x > win_center.crop_x


def test_subject_box_on_left_shifts_crop_left():
    win_center = compute_crop_window(1920, 1080, 1080, 1920, anchor="center")
    box = SubjectBox(x0=100, y0=200, x1=500, y1=900)
    win_subj = compute_crop_window(1920, 1080, 1080, 1920, subject_box=box)
    assert win_subj.crop_x < win_center.crop_x


def test_subject_box_wider_than_window_falls_back():
    """主体比裁剪窗口还宽 → 无法保住，退回 anchor 兜底（不越界）。"""
    win = compute_crop_window(1920, 1080, 1080, 1920,
                               subject_box=SubjectBox(0, 0, 1920, 1080), anchor="center")
    excess = win.scaled_w - 1080
    assert 0 <= win.crop_x <= excess


def test_crop_x_never_out_of_bounds():
    for frac in (-2.0, 0.0, 0.5, 1.0, 3.0):
        win = compute_crop_window(1920, 1080, 1080, 1920, crop_frac=frac)
        assert 0 <= win.crop_x <= win.scaled_w - win.target_w


# ---------- 显著性 → 主体框 ----------

def _write_image(path: Path, size=(320, 180), box=None, bg=200, fg=20) -> Path:
    """造一张图：背景灰，指定区域画一个深色方块（= 主体）。"""
    from PIL import Image, ImageDraw
    im = Image.new("L", size, bg)
    if box is not None:
        d = ImageDraw.Draw(im)
        d.rectangle(box, fill=fg)
    im.save(path)
    return path


def test_estimate_subject_box_finds_dark_blob_on_right(tmp_path):
    p = _write_image(tmp_path / "r.jpg", box=(240, 40, 310, 150))
    box = estimate_subject_box(p)
    assert box is not None
    cx = box.center_x / 320
    assert cx > 0.6, f"主体应在右侧，实际中心 {cx:.2f}"


def test_estimate_subject_box_finds_dark_blob_on_left(tmp_path):
    p = _write_image(tmp_path / "l.jpg", box=(10, 40, 80, 150))
    box = estimate_subject_box(p)
    assert box is not None
    cx = box.center_x / 320
    assert cx < 0.4, f"主体应在左侧，实际中心 {cx:.2f}"


def test_estimate_subject_box_returns_none_for_missing_file(tmp_path):
    assert estimate_subject_box(tmp_path / "nope.jpg") is None


def test_estimate_subject_box_returns_none_for_flat_image(tmp_path):
    """纯色图没有显著主体 → None（调用方回退中心裁剪）。"""
    p = _write_image(tmp_path / "flat.jpg", box=None)
    assert estimate_subject_box(p) is None


def test_subject_box_geometry_helpers():
    b = SubjectBox(10, 20, 110, 220)
    assert b.width == 100
    assert b.height == 200
    assert b.center_x == 60.0
    assert b.center_y == 120.0
    assert b.to_tuple() == (10, 20, 110, 220)


def test_subject_box_scaled():
    b = SubjectBox(0, 0, 100, 50)
    s = b.scaled(2.0)
    assert s.to_tuple() == (0, 0, 200, 100)


# ---------- to_vertical 接口 ----------

def test_to_vertical_accepts_crop_frac_and_subject_box():
    """to_vertical 必须接受 crop_frac / subject_box（向后兼容：默认行为不变）。"""
    import inspect
    import lib_ffmpeg

    sig = inspect.signature(lib_ffmpeg.to_vertical)
    assert "crop_frac" in sig.parameters
    assert "subject_box" in sig.parameters
    assert sig.parameters["crop_frac"].default is None
    assert sig.parameters["subject_box"].default is None
    assert sig.parameters["anchor"].default == "center"


def test_to_vertical_uses_compute_crop_window(monkeypatch):
    """to_vertical 的 vf 必须来自 compute_crop_window（几何只有一处实现）。"""
    import lib_ffmpeg

    captured: dict = {}

    def _fake_safe_run(cmd, timeout=60):
        captured["cmd"] = cmd
        captured["vf"] = cmd[cmd.index("-vf") + 1]

    monkeypatch.setattr(lib_ffmpeg, "safe_run", _fake_safe_run)
    monkeypatch.setattr(lib_ffmpeg, "probe_resolution", lambda p: (1920, 1080))

    out = Path("/tmp/_v38_vertical.mp4")
    lib_ffmpeg.to_vertical("/tmp/_fake_in.mp4", out, crop_frac=0.25)

    expected = compute_crop_window(1920, 1080, 1080, 1920, crop_frac=0.25).filter_expr
    assert captured["vf"] == expected


def test_to_vertical_with_subject_box_shifts_crop(monkeypatch):
    import lib_ffmpeg

    captured: dict = {}

    def _fake_safe_run(cmd, timeout=60):
        captured["vf"] = cmd[cmd.index("-vf") + 1]

    monkeypatch.setattr(lib_ffmpeg, "safe_run", _fake_safe_run)
    monkeypatch.setattr(lib_ffmpeg, "probe_resolution", lambda p: (1920, 1080))

    lib_ffmpeg.to_vertical(
        "/tmp/_fake_in.mp4", Path("/tmp/_v38_vertical2.mp4"),
        subject_box=(1400, 200, 1800, 900),
    )
    center_vf = compute_crop_window(1920, 1080, 1080, 1920).filter_expr
    assert captured["vf"] != center_vf
    assert "crop=1080:1920:" in captured["vf"]


# ---------- 画幅贯通（缺陷 1 的端到端断言） ----------

def test_pipeline_task_aspect_ratio_is_9x16_when_configured():
    """run_pipeline_v3(aspect_ratio="9:16") → image_gen task 拿到 "9:16"。"""
    from unittest.mock import MagicMock, patch
    from hailuo_video import pipeline as pl
    from hailuo_video.script_engine import SceneSpec, Shot, Storyboard

    sb = Storyboard(
        title="t", characters=[],
        scenes=[SceneSpec(id=1, location="田野", key_elements=[])],
        shots=[Shot(index=1, scene_id=1, shot_type="中景", description="d", voiceover="v")],
    )
    with patch.object(pl, "PipelineAgent") as agent_cls:
        agent_cls.return_value.run.return_value = pl.PipelineResult(output="out.mp4")
        pl.run_pipeline_v3(
            sb, Path("/tmp/_v38_out.mp4"),
            image_backend=MagicMock(),
            tts_backend=MagicMock(),
            video_backend=MagicMock(),
            aspect_ratio="9:16",
            run_dir=Path("/tmp/_v38_run"),
        )
    assert agent_cls.call_args[0][0].aspect_ratio == "9:16"


def test_backend_env_aspect_ratio_is_used_when_config_unset(monkeypatch):
    """不传画幅 → backend 读自己的 env（MINIMAX_IMAGE_ASPECT_RATIO=9:16 生效）。"""
    from hailuo_video import image_engine as ie
    from hailuo_video.orchestrator import PipelineAgent, PipelineConfig

    monkeypatch.setenv("MINIMAX_IMAGE_ASPECT_RATIO", "9:16")
    backend = ie.MockImageBackend()
    agent = PipelineAgent(PipelineConfig(image_backend=backend))
    assert agent._resolve_aspect_ratio() == "9:16"
