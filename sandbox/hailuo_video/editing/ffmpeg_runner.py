"""V3.2 重构：editing 子模块拆分 — ffmpeg runner。

原 editing_engine.py:67-167, 433-452 抽到独立文件：
  - _resolve_ffmpeg_exe
  - _default_runner / _runner 全局变量 / set_runner()
  - _probe_duration / _probe_dimensions

注：`_compute_color_stats_from_png` / `_ffmpeg_extract_middle_frame` 是 color-specific 辅助函数，
     留在 color.py（仅 analyze_video_color 用）。
"""
from __future__ import annotations

import re
import shutil
import subprocess
from typing import Callable


from hailuo_video.editing.errors import EditingError


# ---------- ffmpeg 探测 ----------

def _resolve_ffmpeg_exe() -> str:
    """imageio-ffmpeg 优先；PATH 兜底（与 concat_engine 一致）。"""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        pass
    exe = shutil.which("ffmpeg")
    if not exe:
        raise EditingError(
            "未找到 ffmpeg 二进制；先 `pip install imageio-ffmpeg` "
            "或确保系统 ffmpeg 在 PATH"
        )
    return exe


# ---------- 注入式 runner（单测可 mock） ----------

# 签名：run_ffmpeg(args_list, timeout) -> (returncode, stdout, stderr)
RunFFmpegFn = Callable[[list[str], int], tuple[int, str, str]]


def _default_runner(args: list[str], timeout: int) -> tuple[int, str, str]:
    """真网跑 ffmpeg（concat_engine._default_runner 的子集复制，避免循环依赖）。"""
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EditingError(f"ffmpeg 子进程失败：{type(exc).__name__}: {exc}") from exc
    return proc.returncode, proc.stdout, proc.stderr


_runner: RunFFmpegFn = _default_runner


def set_runner(fn: RunFFmpegFn | None) -> None:
    """注入 / 还原 runner（单测）。None → 还原 default。"""
    global _runner
    _runner = fn if fn is not None else _default_runner


# ---------- probe helpers ----------

_STDERR_TIME_LINE = re.compile(r"time=\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def _probe_duration(video_path: str) -> float | None:
    """用 ffmpeg 直接跑一个空命令取 stderr 里的 Duration 字段（无需 ffprobe）。"""
    exe = _resolve_ffmpeg_exe()
    args = [exe, "-i", video_path]
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    stderr = proc.stderr or ""
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr)
    if not m:
        return None
    h, mm, s = (float(x) for x in m.groups())
    return h * 3600 + mm * 60 + s


def _probe_dimensions(video_path: str) -> tuple[int, int] | None:
    """用 ffmpeg stderr 解析分辨率（Stream #0:0 ... WxH）。失败返 None。"""
    exe = _resolve_ffmpeg_exe()
    args = [exe, "-i", video_path]
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    stderr = proc.stderr or ""
    # 形如 "Stream #0:0 ... Video: h264 ... 1920x1080 ..."
    m = re.search(r"(\d{3,4})x(\d{3,4})", stderr)
    if not m:
        return None
    w, h = int(m.group(1)), int(m.group(2))
    if w <= 0 or h <= 0 or w > 4096 or h > 4096:
        return None
    return w, h


def _probe_has_audio(video_path: str) -> bool:
    """V3.3 W2：探测视频是否含音频轨（hailuo 真视频无音 → 不能用 acrossfade）。

    Returns:
        True = 至少 1 条 audio stream；False = 无音轨
    """
    exe = _resolve_ffmpeg_exe()
    args = [exe, "-i", video_path]
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    stderr = proc.stderr or ""
    # ffmpeg stderr 形如 "Stream #0:1(und): Audio: aac ..."；无音频则无 Audio: 行
    return bool(re.search(r"Stream #\d+:\d+.*Audio:", stderr))
