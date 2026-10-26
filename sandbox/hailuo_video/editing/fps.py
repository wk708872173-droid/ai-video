"""V3.8 W3 — 帧率归一化（FPS Normalization）。

## 问题

Hailuo-2.3 产出 **8fps**，Ken Burns 兜底产出 **24fps**；`-c copy` 拼接**不重采样**
→ 混合帧率的片子在拼接处出现帧突跳，把单段的崩溃概率放大。

证据（`logs/_diagnose_3to4_jump_20261016/SUMMARY.md`）：

> **结论 2：拼接层放大了崩的概率**
> "这说明**拼接 `-c copy` 不重采样**时，hailuo 帧率与 Ken Burns 帧率混合
> （8fps vs 24fps）会让单段的帧突跳跨段扩散"

单段自身就有 ~1/3 的崩溃率（同文件 `:114`），拼接层再放大 → 成片观感崩。

## 修法

叠化（xfade）前把**每段**统一重采样到同一帧率：`fps=24` 滤镜。
8fps 的片段会被补成 24fps（每帧重复 3 次），24fps 的片段原样通过
→ 拼接后帧率一致，帧突跳不再跨段扩散。

不改动 `editing/` 包的其余逻辑（只加一个模块 + 在 crossfade 的 filter chain
里插一个滤镜）。
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

# 统一目标帧率。24 = Ken Burns 兜底段的帧率（也是行业常规播放帧率）。
DEFAULT_TARGET_FPS = 24


def _resolve_ffmpeg_exe() -> str:
    """ffmpeg 可执行文件（imageio-ffmpeg 优先，PATH 兜底）。"""
    try:
        from hailuo_video.editing.ffmpeg_runner import _resolve_ffmpeg_exe as _r
        return _r()
    except Exception:
        import shutil
        import imageio_ffmpeg
        try:
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            exe = shutil.which("ffmpeg")
            if not exe:
                raise RuntimeError("未找到 ffmpeg：pip install imageio-ffmpeg")
            return exe


def probe_fps(video_path: str | Path) -> float | None:
    """探测视频帧率。失败返回 None。

    形如 "24 fps" 或 "30000/1001 fps"（后者取比值）。
    """
    path = str(video_path)
    exe = _resolve_ffmpeg_exe()
    try:
        proc = subprocess.run(
            [exe, "-i", path],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    stderr = proc.stderr or ""
    m = re.search(r"(\d+(?:\.\d+)?)\s*fps", stderr)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    m = re.search(r"(\d+)/(\d+)\s*fps", stderr)
    if m:
        try:
            num, den = int(m.group(1)), int(m.group(2))
            if den > 0:
                return num / den
        except ValueError:
            return None
    return None


def detect_mixed_fps(video_paths: list[str | Path]) -> list[float | None]:
    """探测每段的帧率（None = 探测失败）。"""
    return [probe_fps(p) for p in video_paths]


def is_mixed_fps(fps_list: list[float | None], *, tolerance: float = 0.5) -> bool:
    """帧率是否不统一（探测失败的段忽略）。"""
    known = [f for f in fps_list if f is not None]
    if len(known) < 2:
        return False
    return (max(known) - min(known)) > tolerance


def fps_filter(target_fps: int = DEFAULT_TARGET_FPS) -> str:
    """ffmpeg fps 重采样滤镜串。"""
    fps = max(1, int(target_fps))
    return f"fps={fps}"


__all__ = [
    "DEFAULT_TARGET_FPS",
    "detect_mixed_fps",
    "fps_filter",
    "is_mixed_fps",
    "probe_fps",
]
