"""V3.2 重构：editing 子模块拆分 — 统一 LUT 调色。

原 editing_engine.py:744-852 抽到独立文件：
  - LUTResult dataclass
  - apply_lut
  - write_neutral_cube_lut
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hailuo_video.editing import ffmpeg_runner
from hailuo_video.editing.errors import EditingError
from hailuo_video import editing as _editing_pkg


@dataclass
class LUTResult:
    """LUT 调色结果。"""
    output_path: str = ""
    source_path: str = ""
    lut_path: str = ""
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_path": self.output_path,
            "source_path": self.source_path,
            "lut_path": self.lut_path,
            "duration_seconds": self.duration_seconds,
        }


def apply_lut(
    video_path: str | Path,
    output_path: str | Path,
    lut_path: str | Path,
    *,
    timeout: int = 180,
    extra_args: list[str] | None = None,
) -> LUTResult:
    """对单段视频应用 .cube LUT 调色（ffmpeg lut3d filter）。

    Args:
        video_path: 输入 mp4
        output_path: 输出 mp4
        lut_path: .cube 文件路径（用户准备；或脚本生成 neutral cube）

    Returns:
        LUTResult
    失败抛 EditingError（不静默）。
    """
    started = time.monotonic()
    in_path = str(video_path)
    out_path = Path(output_path)
    lut = Path(lut_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not Path(in_path).is_file():
        raise EditingError(f"video 不存在：{in_path}")
    if not lut.is_file():
        raise EditingError(f"LUT 不存在：{lut}")

    exe = _editing_pkg._resolve_ffmpeg_exe()
    # lut3d 滤镜：lut3d=/path/to/lut.cube
    # Windows 路径含 \ → 用正斜杠并冒号转义（与 concat_engine 一致）
    lut_str = str(lut).replace("\\", "/").replace(":", "\\:")
    args = [
        exe, "-y",
        "-i", in_path,
        "-vf", f"lut3d='{lut_str}'",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
    ]
    if extra_args:
        args.extend(extra_args)
    args.append(str(out_path))

    rc, _out, err = ffmpeg_runner._runner(args, timeout=timeout)
    if rc != 0:
        raise EditingError(
            f"apply_lut ffmpeg rc={rc}：{err[-300:]}"
        )
    if not out_path.is_file() or out_path.stat().st_size == 0:
        raise EditingError(f"LUT 产物为空：{out_path}")

    return LUTResult(
        output_path=str(out_path),
        source_path=in_path,
        lut_path=str(lut),
        duration_seconds=time.monotonic() - started,
    )


def write_neutral_cube_lut(
    output_path: str | Path,
    *,
    title: str = "neutral",
    size: int = 17,
) -> Path:
    """生成 identity .cube LUT（无调色效果，仅作占位 / 测试用）。

    .cube 文件格式见 Adobe / ffmpeg 文档。
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = [
        f'TITLE "{title}"',
        f"LUT_3D_SIZE {size}",
        "DOMAIN_MIN 0.0 0.0 0.0",
        "DOMAIN_MAX 1.0 1.0 1.0",
    ]
    # identity: R=G=B 对角线
    step = 1.0 / (size - 1)
    for b in range(size):
        for g in range(size):
            for r in range(size):
                v = r * step
                lines.append(f"{v:.6f} {v:.6f} {v:.6f}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
