"""V3.2 重构：editing 子模块拆分 — 色彩归一化。

原 editing_engine.py:
  - L30-62 (ColorStats / EditingResult dataclasses)
  - L116-194 (_ffmpeg_extract_middle_frame / _compute_color_stats_from_png helpers)
  - L197-373 (analyze_video_color / compute_target_stats / _compute_eq_filter / normalize_clip / color_normalize_pipeline)
  - L378-388 (is_available)
"""
from __future__ import annotations

import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from hailuo_video.editing import ffmpeg_runner
from hailuo_video.editing.errors import EditingError
# 通过 editing 包访问 _probe_duration / _resolve_ffmpeg_exe，让 monkeypatch.setattr(editing, ...)
# 在子模块内生效（V3.2 重构后子模块不再单模块共享全局）
from hailuo_video import editing as _editing_pkg


# ---------- Data ----------

@dataclass
class ColorStats:
    """单段视频的色彩基线（从中间帧采样）。"""
    source: str = ""                    # 源视频路径
    mean_r: float = 0.0
    mean_g: float = 0.0
    mean_b: float = 0.0
    brightness: float = 0.0              # 0-1 归一化亮度（与 quality.py 一致）
    frame_sampled_at: float = 0.5        # 采样时间点（0-1 比例）

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EditingResult:
    """单段色彩归一化结果。"""
    output_path: str = ""
    source_path: str = ""
    source_stats: ColorStats = field(default_factory=ColorStats)
    target_stats: ColorStats = field(default_factory=ColorStats)
    eq_filter: str = ""                  # ffmpeg eq filter 表达式（用于审计）
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_path": self.output_path,
            "source_path": self.source_path,
            "source_stats": self.source_stats.to_dict(),
            "target_stats": self.target_stats.to_dict(),
            "eq_filter": self.eq_filter,
            "duration_seconds": self.duration_seconds,
        }


# ---------- 工具：从 ffmpeg stderr 提取采样帧 → PIL 算 mean ----------

def _ffmpeg_extract_middle_frame(video_path: str | Path, tmp_dir: Path) -> Path:
    """从 video 抽取中间帧到 tmp_dir/mid.png（返回 PNG 路径）。

    失败抛 EditingError（不静默）。
    """
    video = str(video_path)
    # 用 ffprobe 取时长；fallback ffmpeg 默认 5s
    duration = _editing_pkg._probe_duration(video)
    if duration is None or duration <= 0:
        mid_t = 0.5  # 兜底用 0.5s（5s 默认）
    else:
        mid_t = duration / 2.0

    out_png = tmp_dir / "mid.png"
    exe = _editing_pkg._resolve_ffmpeg_exe()
    args = [
        exe, "-y",
        "-ss", f"{mid_t:.3f}",
        "-i", video,
        "-frames:v", "1",
        "-q:v", "2",
        str(out_png),
    ]
    rc, _out, err = ffmpeg_runner._runner(args, timeout=30)
    if rc != 0:
        raise EditingError(f"extract middle frame 失败 rc={rc}：{err[-300:]}")
    if not out_png.is_file() or out_png.stat().st_size == 0:
        raise EditingError(f"中间帧未生成：{out_png}")
    return out_png


def _compute_color_stats_from_png(png_path: Path) -> ColorStats:
    """PIL 读 PNG → mean RGB + brightness（与 quality.py 同一风格）。"""
    try:
        from PIL import Image
    except ImportError as exc:
        raise EditingError(
            "PIL 未装；色彩归一化需要 PIL（pip install pillow）"
        ) from exc
    img = Image.open(png_path).convert("RGB").resize((128, 128))
    pixels = list(img.getdata())
    n = len(pixels)
    if n == 0:
        raise EditingError("中间帧无像素")
    r_sum = sum(p[0] for p in pixels)
    g_sum = sum(p[1] for p in pixels)
    b_sum = sum(p[2] for p in pixels)
    mean_r = r_sum / n / 255.0
    mean_g = g_sum / n / 255.0
    mean_b = b_sum / n / 255.0
    # 亮度 = 0.299*R + 0.587*G + 0.114*B
    brightness = (0.299 * mean_r + 0.587 * mean_g + 0.114 * mean_b)
    return ColorStats(
        mean_r=mean_r, mean_g=mean_g, mean_b=mean_b,
        brightness=brightness,
    )


def analyze_video_color(video_path: str | Path, source_label: str = "") -> ColorStats:
    """公开 API：分析单段视频的色彩基线。

    source_label 写到返回 ColorStats.source 字段（方便 pipeline 传 shot_id）。
    """
    video = Path(video_path)
    if not video.is_file():
        raise EditingError(f"video 不存在：{video}")
    with tempfile.TemporaryDirectory(prefix="editing_analyze_") as tmp_str:
        tmp = Path(tmp_str)
        png = _ffmpeg_extract_middle_frame(video, tmp)
        stats = _compute_color_stats_from_png(png)
    stats.source = source_label or str(video)
    return stats


# ---------- 色彩归一化：算 target → 算 eq 参数 → 重编码 ----------

def compute_target_stats(stats_list: list[ColorStats]) -> ColorStats:
    """从多段视频的色彩基线算 target（取均值）。

    空列表 → 兜底中性基线（mean=0.5, brightness=0.5）。
    """
    if not stats_list:
        return ColorStats(
            source="<neutral>",
            mean_r=0.5, mean_g=0.5, mean_b=0.5,
            brightness=0.5,
        )
    n = len(stats_list)
    return ColorStats(
        source=f"<mean of {n}>",
        mean_r=sum(s.mean_r for s in stats_list) / n,
        mean_g=sum(s.mean_g for s in stats_list) / n,
        mean_b=sum(s.mean_b for s in stats_list) / n,
        brightness=sum(s.brightness for s in stats_list) / n,
    )


def _compute_eq_filter(src: ColorStats, tgt: ColorStats) -> tuple[str, dict[str, float]]:
    """从源/目标基线算 ffmpeg eq filter 表达式。

    eq filter 用三参数：
      brightness = tgt.brightness - src.brightness  (范围 -1..1)
      saturation 补偿 R/G/B 通道差距：
        dr = tgt.mean_r - src.mean_r
        dg = tgt.mean_g - src.mean_g
        db = tgt.mean_b - src.mean_b
        简单实现：用 saturation * gamma_red/green/blue 三参数
    限制：brightness 限 ±0.5（避免过曝）；gamma 限 [0.5, 2.0]。

    Returns:
        (filter_str, params) — filter_str 直接拼到 ffmpeg -vf，params 给审计/单测。
    """
    delta_b = tgt.brightness - src.brightness
    delta_b = max(-0.5, min(0.5, delta_b))

    def _clamp(x: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, x))

    # gamma_red = 2^(2*(tgt.mean_r - src.mean_r))
    gamma_r = _clamp(2 ** (2 * (tgt.mean_r - src.mean_r)), 0.5, 2.0)
    gamma_g = _clamp(2 ** (2 * (tgt.mean_g - src.mean_g)), 0.5, 2.0)
    gamma_b = _clamp(2 ** (2 * (tgt.mean_b - src.mean_b)), 0.5, 2.0)

    # ffmpeg eq 语法：eq=brightness=VAL:gamma=... 或 saturation=VAL
    # 单个 gamma 参数同时影响 R/G/B → 用 eq=brightness=B:saturation=S:gamma=G
    # 通道独立 gamma 用 eq 不支持；fallback：调整 saturation + 用 curves 滤镜（不引入更复杂滤镜）
    # 这里只用 saturation 补偿整体色偏：saturation = tgt_brightness / src_brightness
    sat = _clamp(tgt.brightness / max(src.brightness, 1e-6), 0.5, 2.0)

    filter_str = f"eq=brightness={delta_b:.4f}:saturation={sat:.4f}:gamma=1.0"
    params = {
        "brightness": delta_b,
        "saturation": sat,
        "gamma_r": gamma_r,
        "gamma_g": gamma_g,
        "gamma_b": gamma_b,
    }
    return filter_str, params


def normalize_clip(
    video_path: str | Path,
    output_path: str | Path,
    *,
    target: ColorStats | None = None,
    source_label: str = "",
    timeout: int = 180,
    extra_args: list[str] | None = None,
) -> EditingResult:
    """单段视频色彩归一化。

    流程：
      1. analyze_video_color → src_stats
      2. 若 target 给定则用 target；否则用 src 自己（pass-through 模式仍写文件，确保 codec 一致）
      3. _compute_eq_filter → 拼 ffmpeg -vf
      4. ffmpeg 重编码到 output_path

    失败抛 EditingError（不静默）。
    """
    started = time.monotonic()
    src_stats = analyze_video_color(video_path, source_label=source_label or str(video_path))
    if target is None:
        target = src_stats  # pass-through
    filter_str, _params = _compute_eq_filter(src_stats, target)

    in_path = str(video_path)
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    exe = _editing_pkg._resolve_ffmpeg_exe()
    args = [
        exe, "-y",
        "-i", in_path,
        "-vf", filter_str,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",                # 音频透传（避免重编码）
    ]
    if extra_args:
        args.extend(extra_args)
    args.append(str(out_path))

    rc, _out, err = ffmpeg_runner._runner(args, timeout=timeout)
    if rc != 0:
        raise EditingError(
            f"normalize_clip ffmpeg rc={rc}：{err[-300:]}"
        )
    if not out_path.is_file() or out_path.stat().st_size == 0:
        raise EditingError(f"归一化产物为空：{out_path}")
    return EditingResult(
        output_path=str(out_path),
        source_path=in_path,
        source_stats=src_stats,
        target_stats=target,
        eq_filter=filter_str,
        duration_seconds=time.monotonic() - started,
    )


def color_normalize_pipeline(
    video_paths: list[str | Path],
    output_dir: str | Path,
    *,
    timeout: int = 180,
    extra_args: list[str] | None = None,
) -> list[EditingResult]:
    """多段视频色彩归一化。

    流程：
      1. 逐段 analyze_video_color → stats_list
      2. compute_target_stats → target（多段均值）
      3. 逐段 normalize_clip（target 共享）→ EditingResult list

    失败抛 EditingError；返回与输入等长 EditingResult 列表。
    """
    if not video_paths:
        raise EditingError("color_normalize_pipeline：video_paths 为空")
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    stats_list = [analyze_video_color(v, source_label=str(v)) for v in video_paths]
    target = compute_target_stats(stats_list)

    results: list[EditingResult] = []
    for i, v in enumerate(video_paths):
        out = out_dir / f"normalized_{i:04d}.mp4"
        result = normalize_clip(
            v, out, target=target,
            source_label=f"shot_{i:04d}",
            timeout=timeout,
            extra_args=extra_args,
        )
        results.append(result)

    return results


# ---------- 可用性检查 ----------

def is_available() -> bool:
    """ffmpeg + PIL 是否齐全（doctor 风格）。"""
    try:
        _editing_pkg._resolve_ffmpeg_exe()
    except EditingError:
        return False
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        return False
    return True
