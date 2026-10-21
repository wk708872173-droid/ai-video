"""V2.5 Ken Burns 视频引擎（CPU 友好的 MVP 方案）。

把静态图片 + Ken Burns 效果（zoom/pan）合成 mp4。这是漫剧平台常用的"伪视频"
方案：AI 不生成帧间运动，但图片在缓慢推拉，给观众"镜头感"。

CPU 上比 CogVideoX 快 100-300 倍：
  - CogVideoX 5s/9 帧 ≈ 30 分钟（CPU）
  - Ken Burns 5s mp4 ≈ 3-5 秒（ffmpeg zoompan filter）

⚠️ 这是 MVP 妥协，不是真 AI 视频。要真 AI 视频需 GPU + MiniMax Max/Ultra 或自购显卡。
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from hailuo_video.local_client import PACKAGE_ROOT
from hailuo_video.concat_engine import (
    ConcatError,
    RunFFmpegFn,
    _default_runner,
    _resolve_ffmpeg_exe,
    _runner,
)


# ---------- 默认参数 ----------

DEFAULT_FPS = 8                          # 8 fps（CogVideoX 默认；CPU 友好）
DEFAULT_DURATION = 5.0                  # 5 秒
DEFAULT_ZOOM_START = 1.0                # 起始缩放
DEFAULT_ZOOM_END = 1.15                 # 结束缩放（15% 推近）
# pan 默认从图片中心点出发，到达 (0.55, 0.45)（轻微右下偏移）

VALID_EFFECTS = ("zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down", "static")


# ---------- 数据类 ----------

@dataclass
class VideoResult:
    output_path: Path
    ok: bool
    source: str = "ok"                   # "ok" | "error" | "disabled"
    error: str = ""
    duration_seconds: float = 0.0        # ffmpeg 调用耗时
    effect: str = "zoom_in"
    fps: int = DEFAULT_FPS
    seconds: float = DEFAULT_DURATION
    bytes: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_ok(self) -> bool:
        return self.ok

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------- Ken Burns 后端 ----------

class KenBurnsBackend:
    """ffmpeg zoompan filter 实现 Ken Burns。

    滤镜：zoompan=z='zoom_expr':x='x_expr':y='y_expr':d=frames
    zoom_expr / x_expr / y_expr 用 on(frame, 0, total) 函数，每帧计算 zoom/x/y。
    """

    def __init__(self, runner: RunFFmpegFn | None = None) -> None:
        # 复用 concat_engine 的 runner（可注入 mock）
        self._runner_override = runner

    def render(
        self,
        image: str | Path,
        output: str | Path,
        *,
        seconds: int | None = None,
        fps: int | None = None,
        effect: str = "zoom_in",
        # 高级：自定义 zoom/x/y 表达式（覆盖 effect）
        zoom_expr: str | None = None,
        x_expr: str | None = None,
        y_expr: str | None = None,
        timeout: int = 60,
    ) -> VideoResult:
        if not Path(image).is_file():
            return VideoResult(
                output_path=Path(output),
                ok=False,
                source="error",
                error=f"image 不存在：{image}",
                effect=effect,
            )
        if effect not in VALID_EFFECTS:
            return VideoResult(
                output_path=Path(output),
                ok=False,
                source="error",
                error=f"effect={effect} 不在白名单 {VALID_EFFECTS}",
                effect=effect,
            )

        sec = seconds if seconds is not None else int(DEFAULT_DURATION)
        fr = fps if fps is not None else DEFAULT_FPS
        total_frames = sec * fr
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # 默认 effect → 表达式
        if zoom_expr is None or x_expr is None or y_expr is None:
            z, x, y = _effect_to_expr(effect, total_frames)
            zoom_expr = zoom_expr or z
            x_expr = x_expr or x
            y_expr = y_expr or y

        # zoompan filter + 裁剪到 720x480（与 CogVideoX 默认一致）
        # 注意：zoompan 的 s= 用 WxH 格式（ffmpeg 语法约定）
        vf = (
            f"scale=1280:720:force_original_aspect_ratio=decrease,"
            f"pad=1280:720:(ow-iw)/2:(oh-ih)/2:black,"
            f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':"
            f"d={total_frames}:s=1280x720:fps={fr},"
            f"format=yuv420p"
        )

        exe = _resolve_ffmpeg_exe()
        args = [
            exe, "-y",
            "-loop", "1",
            "-i", str(image),
            "-t", str(sec),
            "-vf", vf,
            "-r", str(fr),
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            "-an",
            str(out_path),
        ]

        started = time.monotonic()
        runner = self._runner_override or _runner
        try:
            rc, _out, err = runner(args, timeout)
        except ConcatError as exc:
            return VideoResult(
                output_path=out_path,
                ok=False,
                source="error",
                error=f"ffmpeg 调用失败：{exc}",
                effect=effect,
                fps=fr,
                seconds=float(sec),
                duration_seconds=time.monotonic() - started,
            )
        elapsed = time.monotonic() - started

        if rc != 0:
            return VideoResult(
                output_path=out_path,
                ok=False,
                source="error",
                error=f"ffmpeg rc={rc}：{err[-300:]}",
                effect=effect,
                fps=fr,
                seconds=float(sec),
                duration_seconds=elapsed,
            )

        return VideoResult(
            output_path=out_path,
            ok=True,
            source="ok",
            effect=effect,
            fps=fr,
            seconds=float(sec),
            duration_seconds=elapsed,
            bytes=out_path.stat().st_size if out_path.exists() else 0,
        )


def _effect_to_expr(effect: str, total_frames: int) -> tuple[str, str, str]:
    """effect 名 → (zoom, x, y) ffmpeg zoompan 表达式。

    注意：ffmpeg 7.1 的 zoompan 表达式用 on(N, 0, 1) 在某些版本会触发
    "Failed to configure output pad" 错误。改用 min/max + 普通变量更稳。
    ffmpeg 表达式语法：
      - iw/ih 是输入宽高
      - x/y 范围 0 ~ iw-1 / 0 ~ ih-1
    """
    # total_frames 在 7.1 上：表达式引擎有问题时只用静态系数
    # （视频 5s @ 8fps = 40 帧，足够平滑）
    if effect == "zoom_in":
        zoom = "min(zoom+0.003,1.15)"          # 每帧 +0.3% zoom，最高 1.15
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"
    elif effect == "zoom_out":
        zoom = "if(lt(zoom,1.0),1.15,max(zoom-0.003,1.0))"
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"
    elif effect == "pan_left":
        zoom = "1.05"
        x = "iw/2-(iw/zoom/2)+(iw*0.003)*on"  # 起点 = 中心 + 偏移
        y = "ih/2-(ih/zoom/2)"
    elif effect == "pan_right":
        zoom = "1.05"
        x = "iw/2-(iw/zoom/2)-(iw*0.003)*on"
        y = "ih/2-(ih/zoom/2)"
    elif effect == "pan_up":
        zoom = "1.05"
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)+(ih*0.003)*on"
    elif effect == "pan_down":
        zoom = "1.05"
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)-(ih*0.003)*on"
    else:  # static
        zoom = "1.0"
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"
    return zoom, x, y


# ---------- 工厂 ----------

def make_backend() -> KenBurnsBackend:
    return KenBurnsBackend()