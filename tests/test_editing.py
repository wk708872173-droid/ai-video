"""V3.2 REQ-303 editing_engine 单测。

设计：
  - 不调真 ffmpeg；用 _runner 注入返回 OK（同时伪造中间帧 PNG 文件）
  - 单元覆盖：compute_target_stats / _compute_eq_filter / normalize_clip / pipeline
  - EditingAgent 集成测试（agents 集成）
"""
from __future__ import annotations

import json
import struct
import sys
import zlib
from pathlib import Path
from unittest.mock import patch

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from hailuo_video import editing_engine as ed_mod  # noqa: E402
from hailuo_video.editing_engine import (  # noqa: E402
    ColorStats,
    EditingError,
    EditingResult,
    analyze_video_color,
    color_normalize_pipeline,
    compute_target_stats,
    _compute_eq_filter,
    normalize_clip,
    set_runner,
)
from hailuo_video import agents  # noqa: E402
from hailuo_video.agents import EditingAgent, InputValidationError  # noqa: E402


# ---------- 测试辅助：造一张最小 PNG ----------

def _make_min_png(path: Path, width: int = 16, height: int = 16,
                   rgb: tuple[int, int, int] = (128, 128, 128)) -> None:
    """生成一张固定颜色 RGB PNG（避免 Pillow 依赖）。"""
    r, g, b = rgb
    # IHDR
    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xffffffff
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    # IDAT - 每行 0 滤镜字节 + RGB triple
    row = b"\x00" + (struct.pack(">BBB", r, g, b) * width)
    raw = row * height
    idat = zlib.compress(raw, 9)
    path.write_bytes(sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b""))


# ---------- 测试辅助：注入 runner，伪造 ffmpeg 行为 ----------

class _FakeRunner:
    """注入 runner：
      - 'extract' 模式：写一个固定色 PNG（让 PIL 能解析）
      - 'normalize' 模式：写一个最小 mp4 占位文件（用零字节即可，size>0 校验通过）
    """
    def __init__(self, png_color: tuple[int, int, int] = (128, 128, 128),
                 fail_norm: str = "") -> None:
        self.png_color = png_color
        self.fail_norm = fail_norm
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], timeout: int) -> tuple[int, str, str]:
        self.calls.append(list(args))
        # 找输出路径（最后无 - 开头的参数）
        out = None
        for a in args:
            if not a.startswith("-"):
                # 跳过 -i 之后的输入
                continue
        # 末位是输出
        out = args[-1]
        if "ffmpeg" not in args[0]:
            return 1, "", "not ffmpeg"
        # extract 模式：有 -frames:v 1
        if "-frames:v" in args:
            p = Path(out)
            _make_min_png(p, rgb=self.png_color)
            return 0, "", ""
        # normalize 模式：写空 mp4 placeholder
        if self.fail_norm:
            return 1, "", self.fail_norm
        p = Path(out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\x00\x00\x00\x18ftyp mp42 fake mp4 content")
        return 0, "", ""


@pytest.fixture
def fake_runner(tmp_path):
    runner = _FakeRunner()
    set_runner(runner)
    yield runner
    set_runner(None)


# ---------- compute_target_stats ----------

def test_compute_target_stats_empty_returns_neutral():
    t = compute_target_stats([])
    assert t.source == "<neutral>"
    assert t.mean_r == 0.5
    assert t.mean_g == 0.5
    assert t.mean_b == 0.5
    assert t.brightness == 0.5


def test_compute_target_stats_average():
    s1 = ColorStats(mean_r=0.4, mean_g=0.5, mean_b=0.6, brightness=0.5)
    s2 = ColorStats(mean_r=0.6, mean_g=0.5, mean_b=0.4, brightness=0.5)
    t = compute_target_stats([s1, s2])
    assert abs(t.mean_r - 0.5) < 1e-9
    assert abs(t.mean_b - 0.5) < 1e-9
    assert t.brightness == 0.5


# ---------- _compute_eq_filter ----------

def test_compute_eq_filter_clamps_extreme_brightness():
    src = ColorStats(mean_r=0.5, mean_g=0.5, mean_b=0.5, brightness=0.0)
    tgt = ColorStats(mean_r=0.5, mean_g=0.5, mean_b=0.5, brightness=1.0)
    filt, params = _compute_eq_filter(src, tgt)
    assert "eq=" in filt
    assert params["brightness"] == 0.5  # 上界 ±0.5


def test_compute_eq_filter_clamps_extreme_gamma():
    src = ColorStats(mean_r=0.0, mean_g=0.0, mean_b=0.0, brightness=0.5)
    tgt = ColorStats(mean_r=1.0, mean_g=1.0, mean_b=1.0, brightness=0.5)
    filt, params = _compute_eq_filter(src, tgt)
    assert params["gamma_r"] == 2.0  # 上界


# ---------- normalize_clip ----------

def test_normalize_clip_success(fake_runner, tmp_path):
    src = tmp_path / "in.mp4"
    src.write_bytes(b"\x00" * 100)  # ffmpeg 不读真内容，runner 注入
    out = tmp_path / "out.mp4"

    result = normalize_clip(src, out)
    assert isinstance(result, EditingResult)
    assert result.output_path == str(out)
    assert "eq=" in result.eq_filter
    assert Path(out).exists()
    assert Path(out).stat().st_size > 0


def test_normalize_clip_missing_source_raises(tmp_path):
    with pytest.raises(EditingError, match="video 不存在"):
        normalize_clip(tmp_path / "no_such.mp4", tmp_path / "out.mp4")


def test_normalize_clip_ffmpeg_fail_raises(fake_runner, tmp_path):
    fake_runner.fail_norm = "ffmpeg crash"
    src = tmp_path / "in.mp4"
    src.write_bytes(b"\x00" * 100)
    with pytest.raises(EditingError, match="normalize_clip ffmpeg rc=1"):
        normalize_clip(src, tmp_path / "out.mp4")


def test_normalize_clip_uses_pass_through_when_no_target(fake_runner, tmp_path):
    """不传 target → target = src → eq filter brightness=0, saturation=1。"""
    src = tmp_path / "in.mp4"
    src.write_bytes(b"\x00" * 100)
    out = tmp_path / "out.mp4"
    result = normalize_clip(src, out)
    assert "brightness=0.0000" in result.eq_filter


# ---------- color_normalize_pipeline ----------

def test_color_normalize_pipeline_empty_raises():
    with pytest.raises(EditingError, match="video_paths 为空"):
        color_normalize_pipeline([], "/tmp")


def test_color_normalize_pipeline_multiple_clips(fake_runner, tmp_path):
    videos = []
    for i in range(3):
        v = tmp_path / f"in_{i}.mp4"
        v.write_bytes(b"\x00" * 50)
        videos.append(v)
    out_dir = tmp_path / "out"

    results = color_normalize_pipeline(videos, out_dir)
    assert len(results) == 3
    for r in results:
        assert Path(r.output_path).exists()
    # target 共享：results[0].target_stats 应 == results[1].target_stats
    assert results[0].target_stats.source == results[1].target_stats.source


def test_color_normalize_pipeline_missing_file_raises(fake_runner, tmp_path):
    with pytest.raises(EditingError):
        color_normalize_pipeline([tmp_path / "ghost.mp4"], tmp_path / "out")


# ---------- analyze_video_color ----------

def test_analyze_video_color_returns_stats(fake_runner, tmp_path):
    src = tmp_path / "in.mp4"
    src.write_bytes(b"\x00" * 50)
    stats = analyze_video_color(src, source_label="shot_001")
    assert stats.source == "shot_001"
    # PNG (128,128,128) → mean 0.5/0.5/0.5
    assert abs(stats.mean_r - 0.5) < 0.05
    assert abs(stats.mean_g - 0.5) < 0.05
    assert abs(stats.mean_b - 0.5) < 0.05


def test_analyze_video_color_missing_raises(tmp_path):
    with pytest.raises(EditingError):
        analyze_video_color(tmp_path / "ghost.mp4")


# ---------- EditingAgent 集成 ----------

def test_editing_agent_in_all_agents():
    """REQ-303：EditingAgent 注册到 ALL_AGENTS。"""
    names = [a.name for a in agents.ALL_AGENTS]
    assert "editing" in names


def test_editing_agent_validate_missing_video_paths():
    a = EditingAgent()
    with pytest.raises(InputValidationError, match="video_paths"):
        a.validate_inputs({"output_dir": "/tmp"})


def test_editing_agent_validate_missing_output_dir():
    a = EditingAgent()
    with pytest.raises(InputValidationError, match="output_dir"):
        a.validate_inputs({"video_paths": ["/tmp/x.mp4"]})


def test_editing_agent_validate_ok():
    a = EditingAgent()
    a.validate_inputs({"video_paths": ["/tmp/x.mp4"], "output_dir": "/tmp/out"})


def test_editing_agent_execute_success(fake_runner, tmp_path):
    """REQ-303：EditingAgent.execute 端到端跑通（fake runner）。"""
    a = EditingAgent()
    videos = []
    for i in range(2):
        v = tmp_path / f"in_{i}.mp4"
        v.write_bytes(b"\x00" * 50)
        videos.append(v)

    result = a.execute({
        "video_paths": [str(v) for v in videos],
        "output_dir": str(tmp_path / "out"),
    })
    assert result.ok is True
    assert len(result.artifacts) == 2
    assert result.metadata["clip_count"] == 2
    assert all("eq=" in f for f in result.metadata["eq_filters"])


def test_editing_agent_execute_empty_video_paths_returns_error(fake_runner):
    a = EditingAgent()
    result = a.execute({"video_paths": [], "output_dir": "/tmp/out"})
    assert result.ok is False
    assert "video_paths 为空" in result.error