"""frame_quality V3.5 P5 覆盖率补齐。

针对 `compute_frame_quality` 的 profile 解析 / 阈值覆盖 / warning 触发分支
（删 v31_compat.py 后该文件 73%）。

测试策略：**monkeypatch `extract_keyframes` 返回真实落盘的 PNG 路径**
（内部 helper 吃的是 `Path`，不是 PIL 对象），既不依赖真 ffmpeg 抽帧，
也能精确控制亮度方差 / 帧间差 / 色差，稳定命中每条 warning 分支
（构造确定性图，不用随机数，保证可重复）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image

REPO = Path(__file__).resolve().parents[1]
SANDBOX = REPO / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from hailuo_video.quality import frame_quality as fq  # noqa: E402


# ---------- 测试用帧构造器（全部落盘成 PNG） ----------

def _write_solid(rgb, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (128, 72), rgb).save(path)
    return path


def _write_noise(seed: int, path: Path) -> Path:
    """确定性伪噪声图（同一 seed → 同一图，保证测试可重复）。"""
    img = Image.new("RGB", (128, 72))
    px = img.load()
    state = (seed * 2654435761) % (2**32)
    for y in range(72):
        for x in range(128):
            state = (state * 1103515245 + 12345) % (2**32)
            px[x, y] = ((state >> 16) & 0xFF, (state >> 8) & 0xFF, state & 0xFF)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


@pytest.fixture
def fake_video(tmp_path):
    """造一个存在的假 mp4 路径（compute 只检查存在性，不真解码）。"""
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"\x00" * 64)
    return p


def _patch_frames(monkeypatch, frames: list[Path]):
    monkeypatch.setattr(fq, "extract_keyframes", lambda *a, **k: list(frames))


def _frames(tmp_path: Path, maker, count: int, **kw) -> list[Path]:
    d = tmp_path / "_frames"
    return [maker(i, d / f"frame_{i:04d}.png", **kw) for i in range(count)]


# ---------- 文件不存在 ----------

def test_extract_failure_is_soft_warn(monkeypatch, tmp_path):
    """抽帧失败 = 软失败：写 warning 并返回报告，不抛异常。

    实测：compute_frame_quality 捕获 FileNotFoundError/RuntimeError，
    追加 `抽帧失败：...` warning 后提前 return（不是硬失败）。
    """
    def _boom(*a, **k):
        raise RuntimeError("ffmpeg 抽帧后无文件：fake_dir")

    monkeypatch.setattr(fq, "extract_keyframes", _boom)
    rep = fq.compute_frame_quality(tmp_path / "nope.mp4")
    assert rep.sample_count == 0
    assert any("抽帧失败" in w and "RuntimeError" in w for w in rep.warnings)
    assert rep.ok is False


# ---------- profile 解析 ----------

def test_unknown_profile_falls_back_with_warning(monkeypatch, fake_video, tmp_path):
    fr = _frames(tmp_path, lambda i, p: _write_solid((10, 10, 10), p), 3)
    _patch_frames(monkeypatch, fr)
    rep = fq.compute_frame_quality(fake_video, profile="bogus")
    assert rep.profile == "stop_motion"
    assert any("未知 profile" in w for w in rep.warnings)


def test_known_profiles_accepted(monkeypatch, fake_video, tmp_path):
    fr = _frames(tmp_path, lambda i, p: _write_noise(i, p), 3)
    _patch_frames(monkeypatch, fr)
    for name in fq.PROFILE_PRESETS:
        rep = fq.compute_frame_quality(fake_video, profile=name)
        assert not any("未知 profile" in w for w in rep.warnings)


def test_explicit_thresholds_override_profile(monkeypatch, fake_video, tmp_path):
    """显式传阈值 → 覆盖 profile 默认（flag 逻辑随之改变）。"""
    fr = _frames(tmp_path, lambda i, p: _write_noise(i, p), 3)
    _patch_frames(monkeypatch, fr)
    loose = fq.compute_frame_quality(fake_video, ssim_threshold=0.0, color_threshold=0.0)
    assert loose.flagged_pairs == []
    tight = fq.compute_frame_quality(fake_video, ssim_threshold=1.01, color_threshold=1.01)
    assert len(tight.flagged_pairs) == 4  # 2 对 × 2 维度


# ---------- warning 触发分支 ----------

def test_brightness_variance_warning(monkeypatch, fake_video, tmp_path):
    """明暗剧烈跳变 → 亮度方差告警。"""
    vals = [(0, 0, 0), (255, 255, 255), (0, 0, 0), (255, 255, 255)]
    fr = _frames(tmp_path, lambda i, p: _write_solid(vals[i], p), 4)
    _patch_frames(monkeypatch, fr)
    rep = fq.compute_frame_quality(fake_video, brightness_var_threshold=0.001)
    assert any("亮度方差" in w for w in rep.warnings)


def test_motion_variance_warning(monkeypatch, fake_video, tmp_path):
    """相邻帧亮度差忽大忽小 → 运动方差告警。

    运动方差 = 方差(相邻帧亮度差)。若帧亮度等差递增，差值恒定 → 方差 0；
    因此这里刻意用「非等差」的亮度序列制造非零方差。
    """
    vals = [(0, 0, 0), (128, 128, 128), (10, 10, 10), (255, 255, 255)]
    fr = _frames(tmp_path, lambda i, p: _write_solid(vals[i], p), 4)
    _patch_frames(monkeypatch, fr)
    rep = fq.compute_frame_quality(fake_video, motion_var_threshold=0.0001)
    assert rep.motion_variance > 0.0001
    assert any("运动方差" in w for w in rep.warnings)


def test_motion_variance_zero_when_diff_constant(monkeypatch, fake_video, tmp_path):
    """等差递增（相邻差恒定）→ 运动方差 = 0，不告警。"""
    vals = [(0, 0, 0), (64, 64, 64), (128, 128, 128), (192, 192, 192)]
    fr = _frames(tmp_path, lambda i, p: _write_solid(vals[i], p), 4)
    _patch_frames(monkeypatch, fr)
    rep = fq.compute_frame_quality(fake_video, motion_var_threshold=0.0001)
    assert rep.motion_variance == pytest.approx(0.0, abs=1e-9)
    assert not any("运动方差" in w for w in rep.warnings)


def test_single_frame_short_circuit(monkeypatch, fake_video, tmp_path):
    """抽到 1 帧 → 跳过 pair 计算（不崩）。"""
    fr = _frames(tmp_path, lambda i, p: _write_noise(21, p), 1)
    _patch_frames(monkeypatch, fr)
    rep = fq.compute_frame_quality(fake_video)
    assert rep.sample_count == 1
    assert any("采样帧数" in w for w in rep.warnings)
    assert rep.severity == "minor"


def test_static_motion_profile_warning(monkeypatch, fake_video, tmp_path):
    """profile=motion + 帧完全不动 → 提示生成失败/静态帧。"""
    fr = _frames(tmp_path, lambda i, p: _write_noise(7, p), 4)  # 4 张同图 → SSIM≈1.0
    _patch_frames(monkeypatch, fr)
    rep = fq.compute_frame_quality(fake_video, profile="motion")
    assert any("几乎无运动" in w for w in rep.warnings)


def test_static_check_skipped_when_fewer_than_two_frames(monkeypatch, fake_video, tmp_path):
    """帧数 < 2 时不做静态判定（防 pair 数组空）。"""
    fr = _frames(tmp_path, lambda i, p: _write_noise(9, p), 1)
    _patch_frames(monkeypatch, fr)
    rep = fq.compute_frame_quality(fake_video, profile="motion")
    assert not any("几乎无运动" in w for w in rep.warnings)


# ---------- 指标正确性 ----------

def test_identical_frames_have_max_ssim(monkeypatch, fake_video, tmp_path):
    fr = _frames(tmp_path, lambda i, p: _write_noise(11, p), 4)
    _patch_frames(monkeypatch, fr)
    rep = fq.compute_frame_quality(fake_video)
    assert rep.mean_ssim > 0.99
    assert rep.min_ssim > 0.99
    assert rep.mean_color_sim > 0.99


def test_brightness_variance_zero_for_uniform(monkeypatch, fake_video, tmp_path):
    fr = _frames(tmp_path, lambda i, p: _write_solid((120, 120, 120), p), 4)
    _patch_frames(monkeypatch, fr)
    rep = fq.compute_frame_quality(fake_video)
    assert rep.brightness_variance == pytest.approx(0.0, abs=1e-6)
    assert rep.motion_variance == pytest.approx(0.0, abs=1e-6)


def test_per_pair_arrays_length(monkeypatch, fake_video, tmp_path):
    fr = _frames(tmp_path, lambda i, p: _write_noise(i, p), 5)
    _patch_frames(monkeypatch, fr)
    rep = fq.compute_frame_quality(fake_video)
    assert len(rep.per_pair_ssim) == 4
    assert len(rep.per_pair_color_sim) == 4
    assert rep.sample_count == 5


def test_report_records_path_and_profile(monkeypatch, fake_video, tmp_path):
    fr = _frames(tmp_path, lambda i, p: _write_noise(3, p), 3)
    _patch_frames(monkeypatch, fr)
    rep = fq.compute_frame_quality(fake_video, profile="motion")
    assert rep.video_path == str(fake_video)
    assert rep.profile == "motion"


# ---------- 底层 helper（直接吃 PNG 路径） ----------

def test_frame_brightness_black_vs_white(tmp_path):
    black = _write_solid((0, 0, 0), tmp_path / "b.png")
    white = _write_solid((255, 255, 255), tmp_path / "w.png")
    assert fq._frame_brightness(black) < 0.01
    assert fq._frame_brightness(white) > 0.99


def test_frame_color_hist24_sums_to_one(tmp_path):
    """实测实现返回 768 维（3 通道 × 256 bin），总和归一化到 1.0。"""
    p = _write_noise(5, tmp_path / "n.png")
    h = fq._frame_color_hist24(p)
    assert len(h) == 3 * 256
    assert sum(h) == pytest.approx(1.0, abs=1e-6)


def test_frame_color_hist24_black_image_concentrated(tmp_path):
    """纯黑图：每通道首 bin 各占 1/3。"""
    p = _write_solid((0, 0, 0), tmp_path / "blk.png")
    h = fq._frame_color_hist24(p)
    assert sum(h) == pytest.approx(1.0, abs=1e-6)
    assert h[0] == pytest.approx(1 / 3, abs=1e-6)      # R 暗段
    assert h[256] == pytest.approx(1 / 3, abs=1e-6)    # G 暗段
    assert h[512] == pytest.approx(1 / 3, abs=1e-6)    # B 暗段


def test_lite_ssim_same_image_is_one(tmp_path):
    p = _write_noise(13, tmp_path / "s.png")
    assert fq._lite_ssim(p, p) == pytest.approx(1.0, abs=1e-3)


def test_lite_ssim_black_vs_white_is_low(tmp_path):
    b = _write_solid((0, 0, 0), tmp_path / "b.png")
    w = _write_solid((255, 255, 255), tmp_path / "w.png")
    assert fq._lite_ssim(b, w) < 0.1


def test_cosine_sim_identical_is_one(tmp_path):
    p = _write_noise(17, tmp_path / "c.png")
    h = fq._frame_color_hist24(p)
    assert fq._cosine_sim(h, h) == pytest.approx(1.0, abs=1e-6)


def test_cosine_sim_black_vs_white_is_zero(tmp_path):
    hb = fq._frame_color_hist24(_write_solid((0, 0, 0), tmp_path / "b.png"))
    hw = fq._frame_color_hist24(_write_solid((255, 255, 255), tmp_path / "w.png"))
    assert fq._cosine_sim(hb, hw) == pytest.approx(0.0, abs=1e-6)


def test_variance_helper():
    assert fq._variance([1.0, 1.0, 1.0]) == pytest.approx(0.0)
    assert fq._variance([0.0, 2.0]) > 0.9


def test_extract_keyframes_missing_video_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        fq.extract_keyframes(tmp_path / "nope.mp4")