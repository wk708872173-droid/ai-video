"""V3.2 重构：editing 子模块拆分 — 运动匹配剪切。

原 editing_engine.py:603-741 抽到独立文件：
  - MotionPeak dataclass
  - _extract_frame_at / _frame_diff_score helpers
  - find_motion_peak / motion_match_cut_points
"""
from __future__ import annotations

import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from hailuo_video.editing import ffmpeg_runner
from hailuo_video.editing.errors import EditingError
from hailuo_video import editing as _editing_pkg


@dataclass
class MotionPeak:
    """单段视频的运动峰值点（适合做剪切点）。"""
    video_path: str = ""
    peak_frame_index: int = 0       # 帧索引
    peak_time_seconds: float = 0.0  # 时间点
    diff_score: float = 0.0         # 帧差分得分（越高运动越剧烈）
    frame_sampled_at: float = 0.5   # 采样时间点比例

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _extract_frame_at(video_path: str | Path, time_seconds: float, tmp_path: Path, label: str) -> Path:
    """从 video 在 time_seconds 处抽 1 帧到 tmp_path（label.png）。失败抛 EditingError。"""
    out_png = tmp_path / f"{label}.png"
    exe = _editing_pkg._resolve_ffmpeg_exe()
    args = [
        exe, "-y",
        "-ss", f"{time_seconds:.3f}",
        "-i", str(video_path),
        "-frames:v", "1",
        "-q:v", "2",
        str(out_png),
    ]
    rc, _out, err = ffmpeg_runner._runner(args, timeout=15)
    if rc != 0:
        raise EditingError(f"extract frame@{time_seconds:.2f}s 失败 rc={rc}：{err[-200:]}")
    if not out_png.is_file() or out_png.stat().st_size == 0:
        raise EditingError(f"帧未生成：{out_png}")
    return out_png


def _frame_diff_score(prev_png: Path, curr_png: Path) -> float:
    """PIL 算两帧的像素级 mean abs diff（0..1 归一化）。"""
    try:
        from PIL import Image
    except ImportError as exc:
        raise EditingError("PIL 未装；motion_match 需要 PIL") from exc
    a = Image.open(prev_png).convert("RGB").resize((64, 64))
    b = Image.open(curr_png).convert("RGB").resize((64, 64))
    pa, pb = list(a.getdata()), list(b.getdata())
    if not pa or not pb:
        return 0.0
    s = 0.0
    n = len(pa)
    for (r1, g1, b1), (r2, g2, b2) in zip(pa, pb):
        s += abs(r1 - r2) + abs(g1 - g2) + abs(b1 - b2)
    return s / (n * 3 * 255.0)


def find_motion_peak(
    video_path: str | Path,
    *,
    sample_count: int = 8,
    search_window: tuple[float, float] = (0.1, 0.9),
) -> MotionPeak:
    """在 video_path 的 [search_window] 区间内采 sample_count 帧，找运动峰值（最大帧差分）。

    Args:
        sample_count: 采样帧数（>=3）
        search_window: (start_frac, end_frac) 区间，排除片头/片尾静帧

    Returns:
        MotionPeak：peak_frame_index + peak_time_seconds + diff_score
    失败抛 EditingError。
    """
    if sample_count < 3:
        raise EditingError(f"sample_count 至少 3；当前 {sample_count}")

    duration = _editing_pkg._probe_duration(str(video_path))
    if duration is None or duration <= 0:
        raise EditingError(f"无法探测时长：{video_path}")

    start_t = duration * search_window[0]
    end_t = duration * search_window[1]
    if end_t <= start_t:
        raise EditingError(f"search_window 无效：{search_window}")

    # 等距采样时刻（不含 t=0 与 t=duration）
    times: list[float] = [
        start_t + (end_t - start_t) * (i / (sample_count - 1))
        for i in range(sample_count)
    ]

    with tempfile.TemporaryDirectory(prefix="editing_motion_") as tmp_str:
        tmp = Path(tmp_str)
        frames: list[Path] = []
        for i, t in enumerate(times):
            frames.append(_extract_frame_at(video_path, t, tmp, f"f{i:02d}"))

        # 相邻帧算 diff_score
        scores: list[float] = []
        for i in range(1, len(frames)):
            scores.append(_frame_diff_score(frames[i - 1], frames[i]))

        # peak = 得分最高的一对中的后者（i 索引 = scores 的索引 + 1）
        if not scores:
            raise EditingError("采样帧不足，无法算 diff")
        peak_idx = max(range(len(scores)), key=lambda i: scores[i])
        peak_frame_index = peak_idx + 1  # 后一帧的索引
        peak_time = times[peak_frame_index]
        peak_score = scores[peak_idx]

    return MotionPeak(
        video_path=str(video_path),
        peak_frame_index=peak_frame_index,
        peak_time_seconds=peak_time,
        diff_score=peak_score,
        frame_sampled_at=peak_time / duration,
    )


def motion_match_cut_points(
    video_paths: list[str | Path],
    *,
    sample_count: int = 8,
    search_window: tuple[float, float] = (0.1, 0.9),
) -> list[MotionPeak]:
    """为每段视频找运动峰值（适合在峰值处剪切，匹配相邻段的运动动量）。

    Args:
        video_paths: N 段视频
        sample_count: 每段采样帧数
        search_window: 搜索区间

    Returns:
        list[MotionPeak]（与 video_paths 等长）
    """
    if not video_paths:
        raise EditingError("video_paths 为空")
    return [
        find_motion_peak(v, sample_count=sample_count, search_window=search_window)
        for v in video_paths
    ]
