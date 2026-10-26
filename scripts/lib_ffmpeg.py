"""V3.4 反回归：scripts/ 共享 helper library。

为什么要抽：
  V3.3 时代 scripts/cinematic_upgrade.py 与 scripts/cinematic_upgrade_hailuo.py 重复 ~60%
  代码；scripts/_run_xiaohuang_pipeline.py 又写一份；scripts/_recover_xiaohuang.py 再一份。
  每个新脚本都得 copy-paste ffmpeg 命令。下次有变种又得新文件。

抽出的公共操作：
  - apply_cinematic_lut(mp4, lut_recipe)  → 给 mp4 加暖色 LUT + 胶片颗粒（保留真运动）
  - to_vertical(mp4, target=1080x1920)    → 转 9:16 竖屏（1920x1080 中心 crop）
  - concat_files(mp4_list, output)          → -c copy 无损拼接
  - safe_run(cmd, timeout=60)                → ffmpeg 调用 + 错误捕获
  - probe_duration(mp4) / probe_resolution(mp4) → 探测元数据

V3.8 W5：to_vertical 支持 **主体感知裁剪**（crop_frac / subject_box）。
  背景：16:9 → 9:16 只留 31.6% 宽度，双主体构图放不下，
  旧的三档锚点（center/left/right）会裁掉偏心主体。
  依据 arXiv:2101.04051（H2V：主体选择 → 主体保持式裁剪）。
  几何计算在 hailuo_video.crop（包内，可测），这里只负责传参。

调用方：
  import sys; sys.path.insert(0, 'scripts')
  from lib_ffmpeg import apply_cinematic_lut, to_vertical, concat_files

LUT_RECIPES：复用 cinematic_upgrade.py 的 6 镜暖色 / 冷色配方
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import imageio_ffmpeg

# V3.8 W5：几何计算复用包内 hailuo_video.crop（scripts 目录不在包路径上，
# 按仓库既有惯例把 sandbox/ 插进 sys.path）。
_SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(_SANDBOX) not in sys.path:
    sys.path.insert(0, str(_SANDBOX))

from hailuo_video.crop import SubjectBox, compute_crop_window  # noqa: E402


# ---------- LUT 配方（V3.2 暖色 / 冷色，6 镜） ----------
# 格式：(eq_brightness, eq_saturation, eq_gamma)
# 复用 cinematic_upgrade.py 配方（Day1 暖 / Day2 冷）
LUT_RECIPES = {
    "shot_001": (0.02, 1.05, 0.98),   # 晨光暖金
    "shot_002": (0.05, 1.10, 1.00),   # 正午饱和
    "shot_003": (-0.02, 1.08, 1.05),  # 黄昏暖橙
    "shot_004": (-0.08, 0.95, 1.10),  # 深夜冷蓝
    "shot_005": (-0.12, 0.90, 1.20),  # 月光银青
    "shot_006": (0.02, 1.00, 0.98),   # 破晓柔暖
}


# ---------- Errors ----------

class FFmpegError(RuntimeError):
    """ffmpeg 调用失败（rc != 0 或超时）。"""


# ---------- Probe ----------

def probe_duration(mp4: Path | str) -> float:
    """读 mp4 时长（秒）。失败 → 6.0 兜底。"""
    mp4 = Path(mp4)
    try:
        proc = subprocess.run(
            [_ffmpeg_exe(), "-i", str(mp4)],
            capture_output=True, text=True, timeout=15,
        )
        m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", proc.stderr or "")
        if m:
            h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
            return h * 3600 + mi * 60 + s
    except (subprocess.TimeoutExpired, OSError):
        pass
    return 6.0


def probe_resolution(mp4: Path | str) -> tuple[int, int] | None:
    """读 mp4 分辨率（W×H）。失败 → None。"""
    mp4 = Path(mp4)
    try:
        proc = subprocess.run(
            [_ffmpeg_exe(), "-i", str(mp4)],
            capture_output=True, text=True, timeout=15,
        )
        m = re.search(r"(\d{3,4})x(\d{3,4})", proc.stderr or "")
        if m:
            w, h = int(m.group(1)), int(m.group(2))
            if 0 < w <= 4096 and 0 < h <= 4096:
                return (w, h)
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


# ---------- ffmpeg 调用 ----------

def _ffmpeg_exe() -> str:
    """imageio-ffmpeg 优先；PATH 兜底。"""
    try:
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        exe = shutil.which("ffmpeg")
        if not exe:
            raise FFmpegError("未找到 ffmpeg：pip install imageio-ffmpeg 或 PATH 包含 ffmpeg")
        return exe


def safe_run(cmd: list[str], timeout: int = 60) -> None:
    """跑 ffmpeg，失败抛 FFmpegError。"""
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg rc={proc.returncode}: {(proc.stderr or '')[-300:]}")


# ---------- Cinema 升级 ----------

def apply_cinematic_lut(
    input_mp4: Path | str,
    output_mp4: Path | str,
    lut: tuple[float, float, float],
    *,
    with_grain: bool = True,
    crf: int = 20,
) -> Path:
    """给 mp4 加 LUT + 颗粒（保留原 24fps 真运动）。

    Args:
        input_mp4: 输入（hailuo 或 ken burns）
        output_mp4: 输出路径
        lut: (eq_brightness, eq_saturation, eq_gamma)
        with_grain: 是否加 noise（默认 True；纯 LUT 可关）
        crf: x264 CRF（默认 20 ≈ 高质量）

    Returns:
        output_mp4 Path
    """
    input_mp4 = Path(input_mp4)
    output_mp4 = Path(output_mp4)
    duration = probe_duration(input_mp4)
    b, sat, gamma = lut
    vf_parts = [f"eq=brightness={b}:saturation={sat}:gamma={gamma}"]
    if with_grain:
        vf_parts.append("noise=alls=6:allf=t+u")
    vf = ",".join(vf_parts)

    cmd = [
        _ffmpeg_exe(), "-y",
        "-i", str(input_mp4),
        "-t", f"{duration:.3f}",
        "-vf", vf,
        "-c:v", "libx264", "-preset", "slow", "-crf", str(crf),
        "-an", "-pix_fmt", "yuv420p", "-color_range", "tv",
        "-movflags", "+faststart",
        str(output_mp4),
    ]
    safe_run(cmd, timeout=180)
    return output_mp4


def apply_cinematic_lut_by_shot_id(
    input_mp4: Path | str,
    output_mp4: Path | str,
    shot_id: str,
    **kwargs,
) -> Path:
    """根据 shot_id 自动选 LUT_RECIPES。未知 shot_id → ValueError。"""
    if shot_id not in LUT_RECIPES:
        raise ValueError(
            f"LUT_RECIPES 无 {shot_id!r}；可选：{list(LUT_RECIPES)}"
        )
    return apply_cinematic_lut(input_mp4, output_mp4, LUT_RECIPES[shot_id], **kwargs)


# ---------- 转 9:16 竖屏 ----------

def to_vertical(
    input_mp4: Path | str,
    output_mp4: Path | str,
    target: tuple[int, int] = (1080, 1920),
    *,
    anchor: str = "center",
    crop_frac: float | None = None,
    subject_box: tuple[int, int, int, int] | SubjectBox | None = None,
) -> Path:
    """16:9 (1920x1080) → 9:16 竖屏。

    实现：scale -1:H（保比例放大到目标高度）→ crop WxH
    副作用：左右两侧 ~ 800px 被裁（主体应居中；偏离主体时不建议用）

    V3.8 W5：新增**主体感知裁剪**（依据 arXiv:2101.04051 H2V）。
      - `subject_box`（源像素坐标 x0,y0,x1,y1）→ 裁剪窗口罩住主体。
      - `crop_frac`（0=最左，0.5=居中，1=最右）→ 通用化旧的三档 anchor。
      - 两者都不给 → 行为与旧版**完全一致**（向后兼容）。

    Args:
        input_mp4: 输入 16:9 横屏
        output_mp4: 输出 9:16 路径
        target: (W, H) 默认 (1080, 1920)
        anchor: 裁剪锚点 "center"（默认）| "left" | "right"
        crop_frac: 水平位置比例 0..1（优先于 anchor）
        subject_box: 主体包围盒（源像素坐标）

    Returns:
        output_mp4 Path
    """
    input_mp4 = Path(input_mp4)
    output_mp4 = Path(output_mp4)
    target_w, target_h = target

    src_res = probe_resolution(input_mp4) or (1920, 1080)
    src_w, src_h = src_res

    box: SubjectBox | None = None
    if subject_box is not None:
        if isinstance(subject_box, SubjectBox):
            box = subject_box
        else:
            box = SubjectBox(*subject_box)

    window = compute_crop_window(
        src_w, src_h, target_w, target_h,
        subject_box=box, anchor=anchor, crop_frac=crop_frac,
    )
    vf = window.filter_expr

    cmd = [
        _ffmpeg_exe(), "-y",
        "-i", str(input_mp4),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "slow", "-crf", "20",
        "-an", "-pix_fmt", "yuv420p", "-color_range", "tv",
        "-movflags", "+faststart",
        str(output_mp4),
    ]
    safe_run(cmd, timeout=180)
    return output_mp4


# ---------- 拼接 ----------

@dataclass
class ConcatResult:
    output_path: Path
    clip_count: int
    duration_seconds: float        # V3.8：产物**视频时长**（探针读回）
    elapsed_seconds: float = 0.0   # V3.8：墙钟耗时（拼接命令跑了多久）


def concat_files(
    mp4_list: list[Path | str],
    output_mp4: Path | str,
    *,
    cleanup_list: bool = True,
) -> ConcatResult:
    """-c copy lossless 拼接。

    Args:
        mp4_list: 输入 mp4 路径列表（顺序 = 时间顺序）
        output_mp4: 输出
        cleanup_list: 是否删除中间 list 文件（默认 True）
    """
    import time
    output_mp4 = Path(output_mp4)
    output_mp4.parent.mkdir(parents=True, exist_ok=True)

    list_file = output_mp4.with_suffix(".concat.txt")
    list_file.write_text(
        "\n".join(f"file '{Path(p).resolve()}'" for p in mp4_list) + "\n",
        encoding="utf-8",
    )

    started = time.monotonic()
    cmd = [
        _ffmpeg_exe(), "-y", "-f", "concat", "-safe", "0",
        "-i", str(list_file), "-c", "copy",
        str(output_mp4),
    ]
    safe_run(cmd, timeout=120)
    if cleanup_list:
        list_file.unlink(missing_ok=True)

    elapsed = time.monotonic() - started
    # V3.8 缺陷 12：旧实现把**墙钟耗时**写进 duration_seconds —— 拼接一个 40.5s 的片子
    # 会报 0.18s（那只是 ffmpeg 命令跑了多久）。字段名承诺的是"视频时长"，
    # 现在用 probe_duration 读回产物真实时长，墙钟耗时另存 elapsed_seconds。
    duration = 0.0
    if output_mp4.is_file():
        try:
            duration = probe_duration(output_mp4)
        except Exception:
            duration = 0.0
    return ConcatResult(
        output_path=output_mp4,
        clip_count=len(mp4_list),
        duration_seconds=duration,
        elapsed_seconds=elapsed,
    )


# ---------- Public API ----------

__all__ = [
    "FFmpegError",
    "LUT_RECIPES",
    "probe_duration",
    "probe_resolution",
    "apply_cinematic_lut",
    "apply_cinematic_lut_by_shot_id",
    "to_vertical",
    "concat_files",
    "ConcatResult",
]