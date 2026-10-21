"""V3.7 P0-3：SSIM 自检门禁（仅报警不阻断）单测。

设计原则（用户决策 2026-10-16）：
  - SSIM gate 仅报警，不阻断，不 retry，不消耗 quota
  - 在 EditingAgent._default_execute 的 mode='full' 分支触发
  - profile='motion' 适配 hailuo 真运动（SSIM 阈值 0.35 而非 0.65）
  - metadata['ssim_gate'] 记录每段视频的 severity / mean_ssim / min_ssim

测试 3 个用例：
  1. test_ssim_gate_warns_on_critical：critical 视频 → logger.warning 被调用 + editing 仍执行
  2. test_ssim_gate_passes_on_ok：ok 视频 → 不报警 + editing 正常
  3. test_ssim_gate_records_metadata：metadata['ssim_gate'] 字段存在且结构正确
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from hailuo_video import agents as agents_mod  # noqa: E402
from hailuo_video.agents import EditingAgent  # noqa: E402
from hailuo_video.quality import FrameQualityReport  # noqa: E402


def _fake_report(severity: str, mean_ssim: float = 0.5, min_ssim: float = 0.3) -> FrameQualityReport:
    """构造一个轻量 FrameQualityReport mock（绕开 ffmpeg 抽帧）。"""
    rep = FrameQualityReport(
        video_path="",
        profile="motion",
        sample_count=6,
        per_pair_ssim=[mean_ssim] * 5,
        per_pair_color_sim=[0.9] * 4,
        mean_ssim=mean_ssim,
        min_ssim=min_ssim,
        mean_color_sim=0.9,
        min_color_sim=0.85,
        brightness_variance=0.01,
        motion_variance=0.001,
        flagged_pairs=[(0, 1, 0.1)] * (4 if severity == "critical" else (2 if severity == "major" else (1 if severity == "minor" else 0))),
    )
    # 强制 severity 属性（不依赖 flag 数）
    rep._severity_override = severity
    type(rep).severity = property(lambda self: self._severity_override)  # type: ignore[assignment]
    return rep


@pytest.fixture
def fake_full_pipeline():
    """mock full_editing_pipeline: 返回一个 FullEditingResult-like 对象。"""
    fake_result = MagicMock()
    fake_result.final_path = Path("/tmp/fake_full.mp4")
    fake_result.to_dict = lambda: {"final_path": str(fake_result.final_path), "clip_count": 1, "lut_applied": False}
    return fake_result


def test_ssim_gate_warns_on_critical(caplog, fake_full_pipeline, tmp_path):
    """critical 视频 → 报警 + editing 仍执行（保守策略）。"""
    a = EditingAgent()
    videos = [tmp_path / f"in_{i}.mp4" for i in range(2)]
    for v in videos:
        v.write_bytes(b"\x00" * 50)

    with caplog.at_level(logging.WARNING, logger="hailuo_video.editing.ssim_gate"):
        with patch.object(agents_mod, "ed_mod") as mock_editing_mod:
            mock_editing_mod.EditingError = Exception
            mock_editing_mod.full_editing_pipeline = MagicMock(return_value=fake_full_pipeline)

            with patch("hailuo_video.quality.compute_frame_quality") as mock_ssim:
                mock_ssim.return_value = _fake_report("critical", mean_ssim=0.333, min_ssim=0.279)
                result = a.execute({
                    "video_paths": [str(v) for v in videos],
                    "output_path": str(tmp_path / "out.mp4"),
                    "mode": "full",
                })

    assert result.ok is True
    assert mock_editing_mod.full_editing_pipeline.called
    assert any("ssim_gate_fail" in rec.message for rec in caplog.records)
    assert any("severity=critical" in rec.message for rec in caplog.records)


def test_ssim_gate_passes_on_ok(fake_full_pipeline, tmp_path, caplog):
    """ok 视频 → 不报警 + editing 正常。"""
    a = EditingAgent()
    videos = [tmp_path / "in.mp4"]
    videos[0].write_bytes(b"\x00" * 50)

    with caplog.at_level(logging.WARNING, logger="hailuo_video.editing.ssim_gate"):
        with patch.object(agents_mod, "ed_mod") as mock_editing_mod:
            mock_editing_mod.EditingError = Exception
            mock_editing_mod.full_editing_pipeline = MagicMock(return_value=fake_full_pipeline)

            with patch("hailuo_video.quality.compute_frame_quality") as mock_ssim:
                mock_ssim.return_value = _fake_report("ok", mean_ssim=0.85, min_ssim=0.78)
                result = a.execute({
                    "video_paths": [str(v) for v in videos],
                    "output_path": str(tmp_path / "out.mp4"),
                    "mode": "full",
                })

    assert result.ok is True
    assert mock_editing_mod.full_editing_pipeline.called
    ssim_gate_warnings = [r for r in caplog.records if "ssim_gate_fail" in r.message]
    assert len(ssim_gate_warnings) == 0


def test_ssim_gate_records_metadata(fake_full_pipeline, tmp_path):
    """ssim_gate 字段必须出现在 metadata，包含每段 severity / ssim 信息。"""
    a = EditingAgent()
    videos = [tmp_path / f"in_{i}.mp4" for i in range(2)]
    for v in videos:
        v.write_bytes(b"\x00" * 50)

    with patch.object(agents_mod, "ed_mod") as mock_editing_mod:
        mock_editing_mod.EditingError = Exception
        mock_editing_mod.full_editing_pipeline = MagicMock(return_value=fake_full_pipeline)

        with patch("hailuo_video.quality.compute_frame_quality") as mock_ssim:
            mock_ssim.side_effect = [
                _fake_report("critical", mean_ssim=0.333, min_ssim=0.279),
                _fake_report("ok", mean_ssim=0.85, min_ssim=0.78),
            ]
            result = a.execute({
                "video_paths": [str(v) for v in videos],
                "output_path": str(tmp_path / "out.mp4"),
                "mode": "full",
            })

    assert "ssim_gate" in result.metadata
    assert isinstance(result.metadata["ssim_gate"], list)
    assert len(result.metadata["ssim_gate"]) == 2
    severities = [entry["severity"] for entry in result.metadata["ssim_gate"]]
    assert severities == ["critical", "ok"]


def test_ssim_gate_handles_missing_file(tmp_path, caplog):
    """mp4 不存在 → ssim_gate_skip，不阻断。"""
    a = EditingAgent()
    missing_video = tmp_path / "missing.mp4"  # 不写文件

    with caplog.at_level(logging.WARNING, logger="hailuo_video.editing.ssim_gate"):
        with patch.object(agents_mod, "ed_mod") as mock_editing_mod:
            mock_editing_mod.EditingError = Exception
            mock_editing_mod.full_editing_pipeline = MagicMock(
                return_value=MagicMock(
                    final_path=Path("/tmp/out.mp4"),
                    to_dict=lambda: {"final_path": "/tmp/out.mp4"},
                )
            )

            result = a.execute({
                "video_paths": [str(missing_video)],
                "output_path": str(tmp_path / "out.mp4"),
                "mode": "full",
            })

    # 不阻断，meta 含 ssim_gate（空 severity 不应是 critical）
    assert mock_editing_mod.full_editing_pipeline.called
    skip_warnings = [r for r in caplog.records if "ssim_gate_skip" in r.message]
    assert len(skip_warnings) >= 1