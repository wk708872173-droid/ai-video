"""REQ-V2.5 video_engine 单测。注入 mock ffmpeg runner，不依赖真 ffmpeg。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from hailuo_video import image_engine as ie
from hailuo_video import video_engine as ve  # noqa: E402
from hailuo_video.concat_engine import set_runner  # noqa: E402


# ---------- 环境 / ffmpeg fixture ----------

@pytest.fixture
def fake_jpeg(tmp_path):
    img = tmp_path / "in.jpg"
    img.write_bytes(ie._make_minimal_jpeg())
    return img


def _fake_runner_ok(args, timeout):
    """模拟 ffmpeg 成功：创建 args 最后一个文件作为输出（ffmpeg 行为）。"""
    output = Path(args[-1])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"\x00\x00\x00\x18ftypmp42 fake mp4")
    return 0, "", ""


def _fake_runner_fail(args, timeout):
    return 1, "", "ffmpeg test failure"


# ---------- 默认参数 / 常量 ----------

def test_valid_effects_constant():
    assert "zoom_in" in ve.VALID_EFFECTS
    assert "zoom_out" in ve.VALID_EFFECTS
    for e in ("zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down", "static"):
        assert e in ve.VALID_EFFECTS


def test_effect_to_expr_zoom_in():
    z, x, y = ve._effect_to_expr("zoom_in", 40)
    assert "zoom" in z
    assert x.startswith("iw/")
    assert y.startswith("ih/")


def test_effect_to_expr_zoom_out():
    z, _, _ = ve._effect_to_expr("zoom_out", 40)
    assert "zoom" in z


def test_effect_to_expr_pan():
    for e in ("pan_left", "pan_right", "pan_up", "pan_down"):
        z, x, y = ve._effect_to_expr(e, 40)
        assert z == "1.05"
        assert ("iw" in x and "ih" in y) or ("ih" in x and "iw" in y)


def test_effect_to_expr_static():
    z, x, y = ve._effect_to_expr("static", 40)
    assert z == "1.0"


# ---------- KenBurnsBackend.render ----------

def test_render_image_not_found(tmp_path):
    backend = ve.KenBurnsBackend()
    result = backend.render(tmp_path / "missing.jpg", tmp_path / "out.mp4")
    assert result.ok is False
    assert result.source == "error"
    assert "不存在" in result.error


def test_render_invalid_effect(fake_jpeg, tmp_path):
    backend = ve.KenBurnsBackend()
    result = backend.render(fake_jpeg, tmp_path / "out.mp4", effect="bogus")
    assert result.ok is False
    assert "bogus" in result.error
    assert "白名单" in result.error


def test_render_ok_with_fake_runner(fake_jpeg, tmp_path):
    backend = ve.KenBurnsBackend()
    out = tmp_path / "out.mp4"
    result = backend.render(fake_jpeg, out, seconds=3, fps=8, effect="zoom_in")
    assert result.ok is True
    assert result.source == "ok"
    assert out.exists()
    assert result.bytes > 0
    assert result.effect == "zoom_in"
    assert result.seconds == 3
    assert result.fps == 8


def test_render_ffmpeg_failure(fake_jpeg, tmp_path, monkeypatch):
    monkeypatch.setattr(ve, "_runner", _fake_runner_fail)
    backend = ve.KenBurnsBackend()
    result = backend.render(fake_jpeg, tmp_path / "out.mp4")
    assert result.ok is False
    assert result.source == "error"
    assert "ffmpeg test failure" in result.error


def test_render_creates_parent_dirs(fake_jpeg, tmp_path):
    backend = ve.KenBurnsBackend()
    out = tmp_path / "deep" / "nested" / "out.mp4"
    result = backend.render(fake_jpeg, out)
    assert result.ok is True
    assert out.exists()


def test_render_all_effects(fake_jpeg, tmp_path):
    """跑遍所有 effect，确保表达式不报错。"""
    backend = ve.KenBurnsBackend()
    for effect in ve.VALID_EFFECTS:
        out = tmp_path / f"{effect}.mp4"
        result = backend.render(fake_jpeg, out, seconds=2, effect=effect)
        assert result.ok is True, f"effect {effect} failed: {result.error}"


def test_render_with_custom_zoom_expr(fake_jpeg, tmp_path):
    """自定义表达式覆盖 effect。"""
    backend = ve.KenBurnsBackend()
    out = tmp_path / "out.mp4"
    result = backend.render(
        fake_jpeg, out,
        zoom_expr="1.0",
        x_expr="iw/2-(iw/zoom/2)",
        y_expr="ih/2-(ih/zoom/2)",
    )
    assert result.ok is True


def test_video_result_is_ok_property():
    r = ve.VideoResult(output_path=Path("/tmp/x.mp4"), ok=True)
    assert r.is_ok is True
    r2 = ve.VideoResult(output_path=Path("/tmp/x.mp4"), ok=False)
    assert r2.is_ok is False


# ---------- make_backend ----------

def test_make_backend_returns_ken_burns():
    backend = ve.make_backend()
    assert isinstance(backend, ve.KenBurnsBackend)


# ---------- 清理全局 ffmpeg runner ----------

@pytest.fixture(autouse=True)
def _cleanup_runner():
    yield
    set_runner(None)