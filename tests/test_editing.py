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
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from hailuo_video import editing as ed_mod  # noqa: E402  # V3.2 重构：editing_engine → editing 包
from hailuo_video.editing import (  # noqa: E402  # V3.2 重构：editing_engine → editing 包
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
# V3.2 自动开发冲刺（轮 5）：_make_min_png 提取到 tests/_png_utils.py
from _png_utils import make_min_png as _make_min_png  # noqa: E402,F401


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


# ============================================================
# V3.2 REQ-303.b — xfade 重叠帧叠化
# ============================================================

from hailuo_video.editing import (  # noqa: E402  # V3.2 重构：editing_engine → editing 包
    CrossfadeResult,
    LUTResult,
    MotionPeak,
    FullEditingResult,
    _xfade_filter_chain,
    _probe_dimensions,
    crossfade_clips,
    apply_lut,
    write_neutral_cube_lut,
    find_motion_peak,
    motion_match_cut_points,
    full_editing_pipeline,
    VALID_XFADE_TRANSITIONS,
)


def test_xfade_filter_chain_basic():
    """3 段 5s，0.25s overlap，fade transition → 2 段 xfade 节点。"""
    chain = _xfade_filter_chain(
        clip_count=3,
        durations=[5.0, 5.0, 5.0],
        transition="fade",
        overlap=0.25,
    )
    parts = chain.split(";")
    assert len(parts) == 2
    assert "xfade=transition=fade" in parts[0]
    assert "xfade=transition=fade" in parts[1]
    # 第二段的 offset 应该是 5+5-0.25-0.25 = 9.5（中间段累加）
    assert "offset=4.750" in parts[0]
    assert "offset=9.500" in parts[1]


def test_xfade_filter_chain_too_few_clips_raises():
    with pytest.raises(EditingError, match="至少需要 2 段"):
        _xfade_filter_chain(clip_count=1, durations=[5.0], transition="fade", overlap=0.25)


def test_xfade_filter_chain_invalid_transition_raises():
    with pytest.raises(EditingError, match="非法 xfade transition"):
        _xfade_filter_chain(
            clip_count=2, durations=[5.0, 5.0],
            transition="nonexistent", overlap=0.25,
        )


def test_xfade_filter_chain_overlap_out_of_range_raises():
    with pytest.raises(EditingError, match="overlap 越界"):
        _xfade_filter_chain(
            clip_count=2, durations=[5.0, 5.0],
            transition="fade", overlap=3.0,  # > 2.0
        )
    with pytest.raises(EditingError, match="overlap 越界"):
        _xfade_filter_chain(
            clip_count=2, durations=[5.0, 5.0],
            transition="fade", overlap=0.01,  # < 0.05
        )


def test_crossfade_clips_too_few_raises(tmp_path):
    with pytest.raises(EditingError, match="至少 2 段"):
        crossfade_clips(
            [str(tmp_path / "a.mp4")],
            str(tmp_path / "out.mp4"),
        )


def test_crossfade_clips_success(fake_runner, tmp_path, monkeypatch):
    """2 段视频叠化：runner 写假 mp4 占位 + monkeypatch _probe_duration/dimensions。"""
    monkeypatch.setattr(ed_mod, "_probe_duration", lambda p: 5.0)
    monkeypatch.setattr(ed_mod, "_probe_dimensions", lambda p: (1920, 1080))

    v1 = tmp_path / "a.mp4"
    v2 = tmp_path / "b.mp4"
    v1.write_bytes(b"x" * 100)
    v2.write_bytes(b"x" * 100)
    out = tmp_path / "out.mp4"

    result = crossfade_clips(
        [str(v1), str(v2)],
        str(out),
        transition="fade",
        overlap_seconds=0.25,
    )
    assert isinstance(result, CrossfadeResult)
    assert result.clip_count == 2
    assert result.transition == "fade"
    assert result.overlap_seconds == 0.25
    assert out.is_file()
    assert out.stat().st_size > 0
    # ffmpeg 命令应包含 -filter_complex 和 xfade=
    ffmpeg_args = fake_runner.calls[0]
    assert any("-filter_complex" in str(a) for a in ffmpeg_args)
    assert any("xfade=transition=fade" in str(a) for a in ffmpeg_args)


def test_crossfade_clips_three_clips(fake_runner, tmp_path, monkeypatch):
    """3 段视频叠化。"""
    monkeypatch.setattr(ed_mod, "_probe_duration", lambda p: 5.0)
    monkeypatch.setattr(ed_mod, "_probe_dimensions", lambda p: (1920, 1080))

    vs = [tmp_path / f"v{i}.mp4" for i in range(3)]
    for v in vs:
        v.write_bytes(b"x" * 50)
    out = tmp_path / "out3.mp4"

    result = crossfade_clips([str(v) for v in vs], str(out), transition="wipeleft", overlap_seconds=0.5)
    assert result.clip_count == 3
    assert result.transition == "wipeleft"


def test_crossfade_clips_ffmpeg_fail_raises(fake_runner, tmp_path, monkeypatch):
    """ffmpeg rc != 0 → EditingError。"""
    monkeypatch.setattr(ed_mod, "_probe_duration", lambda p: 5.0)
    monkeypatch.setattr(ed_mod, "_probe_dimensions", lambda p: (1920, 1080))

    fake_runner.fail_norm = "fake ffmpeg failure"
    v1 = tmp_path / "a.mp4"
    v2 = tmp_path / "b.mp4"
    v1.write_bytes(b"x" * 50)
    v2.write_bytes(b"x" * 50)
    with pytest.raises(EditingError, match="crossfade ffmpeg rc=1"):
        crossfade_clips([str(v1), str(v2)], str(tmp_path / "out.mp4"))


def test_valid_xfade_transitions_not_empty():
    """VALID_XFADE_TRANSITIONS 是非空 tuple。"""
    assert isinstance(VALID_XFADE_TRANSITIONS, tuple)
    assert len(VALID_XFADE_TRANSITIONS) >= 40
    assert "fade" in VALID_XFADE_TRANSITIONS
    assert "wipeleft" in VALID_XFADE_TRANSITIONS


# ============================================================
# V3.2 REQ-303.c — 运动匹配剪切
# ============================================================

def test_motion_peak_video_missing_raises(tmp_path):
    with pytest.raises(EditingError, match="无法探测时长"):
        find_motion_peak(tmp_path / "nonexistent.mp4")


def test_motion_peak_too_few_samples_raises(tmp_path):
    """sample_count < 3 → EditingError。"""
    p = tmp_path / "x.mp4"
    p.write_bytes(b"x")
    with pytest.raises(EditingError, match="sample_count 至少 3"):
        find_motion_peak(p, sample_count=2)


def test_motion_peak_probe_failure_raises(tmp_path, monkeypatch):
    """_probe_duration 返 None → EditingError '无法探测时长'。"""
    p = tmp_path / "x.mp4"
    p.write_bytes(b"x")
    monkeypatch.setattr(ed_mod, "_probe_duration", lambda _: None)
    with pytest.raises(EditingError, match="无法探测时长"):
        find_motion_peak(p, sample_count=4)


def test_motion_peak_success(fake_runner, tmp_path):
    """3 帧采样 → 找 diff 最大的一对。"""
    p = tmp_path / "v.mp4"
    p.write_bytes(b"x")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(ed_mod, "_probe_duration", lambda _: 10.0)
    # _extract_frame_at 走 runner → 写 PNG → _frame_diff_score 算像素差异
    peak = find_motion_peak(p, sample_count=4, search_window=(0.1, 0.9))
    monkeypatch.undo()

    assert isinstance(peak, MotionPeak)
    assert peak.video_path == str(p)
    assert 0 < peak.peak_time_seconds < 10.0
    assert 0.0 <= peak.diff_score <= 1.0


def test_motion_match_cut_points_returns_one_per_video(fake_runner, tmp_path):
    """返回 list[MotionPeak] 与 video_paths 等长。"""
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(ed_mod, "_probe_duration", lambda _: 5.0)
    monkeypatch.setattr(ed_mod, "_probe_dimensions", lambda p: (640, 480))

    vs = [tmp_path / f"v{i}.mp4" for i in range(3)]
    for v in vs:
        v.write_bytes(b"x")
    peaks = motion_match_cut_points([str(v) for v in vs], sample_count=4)
    monkeypatch.undo()

    assert len(peaks) == 3
    assert all(isinstance(p, MotionPeak) for p in peaks)


def test_motion_match_cut_points_empty_raises():
    with pytest.raises(EditingError, match="video_paths 为空"):
        motion_match_cut_points([])


def test_frame_diff_score_identical_is_zero(tmp_path):
    """两张相同 PNG → diff_score == 0。"""
    from PIL import Image
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    _make_min_png(a, rgb=(128, 128, 128))
    _make_min_png(b, rgb=(128, 128, 128))
    score = ed_mod._frame_diff_score(a, b)
    assert score == 0.0


def test_frame_diff_score_different_is_positive(tmp_path):
    """两张不同色 PNG → diff_score > 0。"""
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    _make_min_png(a, rgb=(10, 10, 10))
    _make_min_png(b, rgb=(200, 200, 200))
    score = ed_mod._frame_diff_score(a, b)
    assert score > 0.0


# ============================================================
# V3.2 REQ-303.d — LUT 调色
# ============================================================

def test_write_neutral_cube_lut_creates_file(tmp_path):
    out = tmp_path / "neutral.cube"
    p = write_neutral_cube_lut(out, title="test", size=4)
    assert p == out
    assert out.is_file()
    content = out.read_text(encoding="utf-8")
    assert 'TITLE "test"' in content
    assert "LUT_3D_SIZE 4" in content
    # 4x4x4 = 64 个 RGB 三元组
    data_lines = [
        line for line in content.split("\n")
        if line and not line.startswith(("TITLE", "LUT_3D_SIZE", "DOMAIN_"))
    ]
    assert len(data_lines) == 64


def test_write_neutral_cube_lut_identity_values(tmp_path):
    """neutral cube 应是 identity（R=G=B 对角线）。"""
    out = tmp_path / "neutral.cube"
    write_neutral_cube_lut(out, size=4)
    lines = [
        line for line in out.read_text(encoding="utf-8").split("\n")
        if line and not line.startswith(("TITLE", "LUT_3D_SIZE", "DOMAIN_"))
    ]
    # 第一行应是 (0, 0, 0)，最后一行应是 (1, 1, 1)
    assert lines[0] == "0.000000 0.000000 0.000000"
    assert lines[-1] == "1.000000 1.000000 1.000000"


def test_apply_lut_missing_video_raises(tmp_path):
    cube = tmp_path / "n.cube"
    write_neutral_cube_lut(cube)
    with pytest.raises(EditingError, match="video 不存在"):
        apply_lut(tmp_path / "x.mp4", tmp_path / "out.mp4", cube)


def test_apply_lut_missing_lut_raises(tmp_path):
    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    with pytest.raises(EditingError, match="LUT 不存在"):
        apply_lut(v, tmp_path / "out.mp4", tmp_path / "nope.cube")


def test_apply_lut_success(fake_runner, tmp_path):
    cube = tmp_path / "n.cube"
    write_neutral_cube_lut(cube)
    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    out = tmp_path / "out.mp4"

    result = apply_lut(v, out, cube)
    assert isinstance(result, LUTResult)
    assert result.lut_path == str(cube)
    assert out.is_file()
    # 检查 runner 收到了 lut3d= filter
    last_args = fake_runner.calls[-1]
    assert any("lut3d=" in str(a) for a in last_args)


# ============================================================
# V3.2 REQ-303 — 完整后期管线（full pipeline）
# ============================================================

def test_full_editing_pipeline_empty_raises():
    with pytest.raises(EditingError, match="video_paths 为空"):
        full_editing_pipeline([], "/tmp/out.mp4")


def test_full_editing_pipeline_single_clip(fake_runner, tmp_path, monkeypatch):
    """单段视频 → color_normalize → motion_match → crossfade（仅 1 段也跑通，output = copy）。"""
    monkeypatch.setattr(ed_mod, "_probe_duration", lambda _: 5.0)
    monkeypatch.setattr(ed_mod, "_probe_dimensions", lambda _: (1920, 1080))
    monkeypatch.setattr(ed_mod, "color_normalize_pipeline",
                        lambda video_paths, output_dir, **_kw: [
                            ed_mod.EditingResult(output_path=str(Path(output_dir) / f"normalized_{i:04d}.mp4"))
                            for i in range(len(video_paths))
                        ])
    # 在 color_normalize_pipeline 输出位置写假文件供 crossfade 链使用
    real_pipeline = ed_mod.color_normalize_pipeline

    def fake_pipeline(video_paths, output_dir, **_kw):
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        results = []
        for i, vp in enumerate(video_paths):
            target = out_dir / f"normalized_{i:04d}.mp4"
            target.write_bytes(b"\x00\x00\x00\x18ftyp mp42 fake")
            results.append(ed_mod.EditingResult(output_path=str(target), source_path=str(vp)))
        return results

    monkeypatch.setattr(ed_mod, "color_normalize_pipeline", fake_pipeline)

    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    out = tmp_path / "final.mp4"

    result = full_editing_pipeline([str(v)], str(out), lut_path=None)
    assert isinstance(result, FullEditingResult)
    assert result.final_path == str(out)
    assert Path(out).is_file()
    assert len(result.motion_peaks) == 1


def test_full_editing_pipeline_with_lut(fake_runner, tmp_path, monkeypatch):
    """完整管线 + LUT：2 段视频 → crossfade → apply_lut。"""
    monkeypatch.setattr(ed_mod, "_probe_duration", lambda _: 5.0)
    monkeypatch.setattr(ed_mod, "_probe_dimensions", lambda _: (1920, 1080))

    crossfade_called: dict = {"called": 0}

    def fake_crossfade(video_paths, output_path, **_kw):
        crossfade_called["called"] += 1
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"\x00\x00\x00\x18ftyp mp42 fake")
        return ed_mod.CrossfadeResult(output_path=str(output_path), source_paths=[str(p) for p in video_paths])

    monkeypatch.setattr(ed_mod, "crossfade_clips", fake_crossfade)

    v1 = tmp_path / "v1.mp4"
    v2 = tmp_path / "v2.mp4"
    v1.write_bytes(b"x")
    v2.write_bytes(b"x")
    cube = tmp_path / "n.cube"
    write_neutral_cube_lut(cube)
    out = tmp_path / "final.mp4"

    result = full_editing_pipeline(
        [str(v1), str(v2)], str(out),
        lut_path=str(cube), work_dir=tmp_path / "work",
    )
    assert crossfade_called["called"] == 1
    assert result.lut is not None
    assert result.lut.lut_path == str(cube)
    assert out.is_file()


# ============================================================
# V3.2 REQ-303 — EditingAgent 新 mode 覆盖
# ============================================================

def test_editing_agent_validate_crossfade_mode():
    a = EditingAgent()
    with pytest.raises(InputValidationError, match="mode=crossfade.*output_path"):
        a.validate_inputs({"mode": "crossfade", "video_paths": ["/tmp/a.mp4"]})
    # OK 路径不应抛
    a.validate_inputs({
        "mode": "crossfade",
        "video_paths": ["/tmp/a.mp4"],
        "output_path": "/tmp/out.mp4",
    })


def test_editing_agent_validate_apply_lut_mode():
    a = EditingAgent()
    a.validate_inputs({
        "mode": "apply_lut",
        "video_paths": ["/tmp/a.mp4"],
        "video_path": "/tmp/a.mp4",
        "output_path": "/tmp/out.mp4",
        "lut_path": "/tmp/lut.cube",
    })
    with pytest.raises(InputValidationError, match="mode=apply_lut.*lut_path"):
        a.validate_inputs({
            "mode": "apply_lut",
            "video_paths": ["/tmp/a.mp4"],
            "video_path": "/tmp/a.mp4",
            "output_path": "/tmp/out.mp4",
        })


def test_editing_agent_validate_full_mode():
    a = EditingAgent()
    a.validate_inputs({
        "mode": "full",
        "video_paths": ["/tmp/a.mp4"],
        "output_path": "/tmp/out.mp4",
    })
    with pytest.raises(InputValidationError, match="mode=full.*output_path"):
        a.validate_inputs({"mode": "full", "video_paths": ["/tmp/a.mp4"]})


def test_editing_agent_validate_unknown_mode_raises():
    a = EditingAgent()
    with pytest.raises(InputValidationError, match="unknown mode"):
        a.validate_inputs({
            "mode": "bogus",
            "video_paths": ["/tmp/a.mp4"],
            "output_path": "/tmp/out.mp4",
        })


def test_editing_agent_execute_crossfade_mode(fake_runner, tmp_path, monkeypatch):
    """mode=crossfade → 走 crossfade_clips。"""
    monkeypatch.setattr(ed_mod, "_probe_duration", lambda _: 5.0)
    monkeypatch.setattr(ed_mod, "_probe_dimensions", lambda _: (1920, 1080))

    v1 = tmp_path / "a.mp4"
    v2 = tmp_path / "b.mp4"
    v1.write_bytes(b"x")
    v2.write_bytes(b"x")
    out = tmp_path / "out.mp4"

    a = EditingAgent()
    result = a.execute({
        "mode": "crossfade",
        "video_paths": [str(v1), str(v2)],
        "output_path": str(out),
        "transition": "wipeleft",
        "overlap_seconds": 0.3,
    })
    assert result.ok is True
    assert result.metadata["mode"] == "crossfade"
    assert result.metadata["transition"] == "wipeleft"
    assert result.metadata["overlap_seconds"] == 0.3
    assert len(result.artifacts) == 1


def test_editing_agent_execute_motion_match_mode(fake_runner, tmp_path, monkeypatch):
    """mode=motion_match → 走 motion_match_cut_points。"""
    monkeypatch.setattr(ed_mod, "_probe_duration", lambda _: 5.0)

    v = tmp_path / "a.mp4"
    v.write_bytes(b"x")

    a = EditingAgent()
    result = a.execute({
        "mode": "motion_match",
        "video_paths": [str(v)],
        "sample_count": 4,
    })
    assert result.ok is True
    assert result.metadata["mode"] == "motion_match"
    assert "motion_peaks" in result.metadata
    assert len(result.metadata["motion_peaks"]) == 1


def test_editing_agent_execute_unknown_mode_returns_error(fake_runner, tmp_path):
    a = EditingAgent()
    result = a.execute({
        "mode": "bogus",
        "video_paths": ["/tmp/a.mp4"],
        "output_path": "/tmp/out.mp4",
    })
    assert result.ok is False
    assert "unknown mode" in result.error


# ---------- V3.3 W2：crossfade 修复回归测试 ----------

def test_probe_has_audio_true_when_audio_stream_exists(fake_runner, monkeypatch):
    """_probe_has_audio 应正确识别 stderr 中的 Audio: 行。"""
    from hailuo_video.editing import ffmpeg_runner

    class _FakeProc:
        stderr = "Stream #0:1(und): Audio: aac, 44100 Hz, stereo"
    monkeypatch.setattr(ffmpeg_runner.subprocess, "run", lambda *a, **kw: _FakeProc())
    assert ffmpeg_runner._probe_has_audio("/tmp/whatever.mp4") is True


def test_probe_has_audio_false_when_no_audio_stream(fake_runner):
    """hailuo 短片无音频轨 → _probe_has_audio 应返 False。"""
    from hailuo_video.editing.ffmpeg_runner import _probe_has_audio
    # 不 mock：传真正不存在的文件 → ffmpeg 报错，stderr 无 Audio:
    result = _probe_has_audio("/tmp/_no_such_file_xyz.mp4")
    assert result is False


def test_crossfade_clips_skips_audio_when_inputs_have_no_audio(
    fake_runner, tmp_path, monkeypatch
):
    """V3.3 W2：audio_crossfade=True 但输入无音频 → 自动跳过 acrossfade。

    背景：旧实现无论输入有无音频都构造 acrossfade filter，hailuo 短片（无音轨）
    会触发 ffmpeg "matches no streams" 错误，导致 editing phase FAIL。
    """
    from hailuo_video.editing import crossfade as cf

    monkeypatch.setattr(cf.ffmpeg_runner, "_probe_has_audio", lambda _: False)
    monkeypatch.setattr(cf._editing_pkg, "_probe_duration", lambda _: 5.0)
    monkeypatch.setattr(cf._editing_pkg, "_probe_dimensions", lambda _: (1920, 1080))

    v1 = tmp_path / "a.mp4"; v1.write_bytes(b"x")
    v2 = tmp_path / "b.mp4"; v2.write_bytes(b"x")
    out = tmp_path / "out.mp4"

    # audio_crossfade=True（默认）但无音频 → 应自动退化，filter chain 不含 acrossfade
    result = cf.crossfade_clips(
        video_paths=[str(v1), str(v2)],
        output_path=str(out),
        transition="fade",
        overlap_seconds=0.25,
        audio_crossfade=True,
    )
    assert "acrossfade" not in result.ffmpeg_filter, (
        f"无音频时 filter chain 不应包含 acrossfade：{result.ffmpeg_filter}"
    )


def test_xfade_filter_chain_uses_scaled_labels():
    """V3.3 W2：_xfade_filter_chain 第一段 prev_label 必须是 'v00'（不是 '0'）。

    背景：scale chain 输出 [v00] / [v01]，旧代码用 [0][1:v] 引用原始输入，
    但 [0:v] 已被 scale 消耗，导致 ffmpeg "setsar unconnected" 报错。
    """
    from hailuo_video.editing.crossfade import _xfade_filter_chain

    chain = _xfade_filter_chain(
        clip_count=2, durations=[5.0, 5.0], transition="fade", overlap=0.25,
    )
    # 第一段必须用 [v00][v01]，不是 [0][1:v]
    assert "[v00][v01]" in chain, f"期望 scaled label v00/v01，实际：{chain}"
    assert "[0][1:v]" not in chain, f"不应引用未定义 label [0]：{chain}"
    # 3 镜：链式衔接 v00→v0102→vout
    chain3 = _xfade_filter_chain(
        clip_count=3, durations=[5.0, 5.0, 5.0], transition="fade", overlap=0.25,
    )
    assert "[v00][v01]" in chain3
    assert "[v0102][v02]" in chain3
    assert "[vout]" in chain3