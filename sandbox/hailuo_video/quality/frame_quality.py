"""V3.2 重构：quality 子模块拆分 — 视频帧级质量度量（V3.3 W1）。

原 quality.py:
  - L687-760 (FrameQualityReport)
  - L763-769 (默认阈值)
  - L774-783 (_ffmpeg_exe)
  - L786-851 (extract_keyframes)
  - L854-898 (_lite_ssim)
  - L901-906 (_frame_brightness)
  - L909-919 (_frame_color_hist24)
  - L924-1012 (compute_frame_quality)
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 从 consistency 复用 _cosine_sim / _variance（避免复制）
from hailuo_video.quality.consistency import _cosine_sim, _variance


# ---------- FrameQualityReport ----------

@dataclass
class FrameQualityReport:
    """V3.3 W1：单视频帧级质量度量报告。"""
    video_path: str = ""
    sample_count: int = 0                    # 实际采样帧数
    per_pair_ssim: list[float] = field(default_factory=list)       # 0-1 lite-SSIM
    per_pair_color_sim: list[float] = field(default_factory=list)  # 0-1 cosine of color hist
    mean_ssim: float = 0.0
    min_ssim: float = 0.0
    mean_color_sim: float = 0.0
    min_color_sim: float = 0.0
    brightness_variance: float = 0.0
    motion_variance: float = 0.0              # 帧间亮度差方差 → 卡顿指标
    flagged_pairs: list[tuple[int, int, float]] = field(default_factory=list)  # (i, j, metric)
    warnings: list[str] = field(default_factory=list)
    backend: str = "ffmpeg+pil"

    @property
    def ok(self) -> bool:
        """True = 通过 L1 检查（无需人眼 review）。"""
        return not self.flagged_pairs and not self.warnings

    @property
    def severity(self) -> str:
        """返回 'ok' / 'minor' / 'major' / 'critical'（按 flag 数 + warning 分级）。

        规则（V3.3）：
          - 0 flag + 0 warning → 'ok'（无需 review）
          - 0 flag + ≥1 warning → 'minor'（有警示但通过）
          - 1 flag → 'minor'
          - 2-3 flag → 'major'
          - ≥4 flag → 'critical'
        """
        n = len(self.flagged_pairs)
        if n == 0:
            return "ok" if not self.warnings else "minor"
        if n == 1:
            return "minor"
        if n <= 3:
            return "major"
        return "critical"

    def format(self) -> str:
        lines = [f"[video] {self.video_path} ({self.sample_count} 帧采样)"]
        lines.append(f"  mean lite-SSIM: {self.mean_ssim:.3f}  min: {self.min_ssim:.3f}（阈值 {DEFAULT_SSIM_THRESHOLD}）")
        lines.append(f"  mean 色差 cosine: {self.mean_color_sim:.3f}  min: {self.min_color_sim:.3f}（阈值 {DEFAULT_SSIM_THRESHOLD}）")
        lines.append(f"  亮度方差: {self.brightness_variance:.4f}（阈值 {DEFAULT_BRIGHTNESS_VAR_THRESHOLD}）")
        lines.append(f"  运动方差: {self.motion_variance:.4f}（阈值 {DEFAULT_MOTION_VAR_THRESHOLD}；越高越卡顿）")
        if self.flagged_pairs:
            lines.append(f"  [WARN] {len(self.flagged_pairs)} 对帧被 flag：")
            for i, j, metric in self.flagged_pairs:
                lines.append(f"    - 帧 {i+1} ↔ 帧 {j+1}: metric={metric:.3f}")
        else:
            lines.append("  [OK] 所有相邻帧通过 L1 检查")
        for w in self.warnings:
            lines.append(f"  [WARN] {w}")
        return "\n".join(lines)


# ---------- 默认阈值（可 CLI 覆盖） ----------

DEFAULT_SSIM_THRESHOLD = 0.65           # 帧间 lite-SSIM < 此值 → flagged
DEFAULT_COLOR_THRESHOLD = 0.65          # 帧间色差 cosine < 此值 → flagged
DEFAULT_BRIGHTNESS_VAR_THRESHOLD = 0.15 # 亮度方差 > 此值 → 忽明忽暗
DEFAULT_MOTION_VAR_THRESHOLD = 0.04     # 运动方差 > 此值 → 卡顿/闪烁
DEFAULT_SAMPLE_FRAMES = 8               # 默认采样帧数（视频长 ~6s 取 8 帧合理）


# ---------- 抽帧（ffmpeg） ----------

def _ffmpeg_exe() -> str:
    """获取 ffmpeg 可执行路径（imageio-ffmpeg 自带，零外部依赖）。"""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise RuntimeError(
            f"ffmpeg 不可用：{type(exc).__name__}: {exc}\n"
            "  W1 帧级质量检查需 ffmpeg；安装：pip install imageio-ffmpeg"
        ) from exc


def extract_keyframes(
    video_path: str | Path,
    *,
    sample_count: int = DEFAULT_SAMPLE_FRAMES,
    tmp_dir: Path | None = None,
) -> list[Path]:
    """从视频均匀抽 N 帧到 tmp_dir 落盘，返回 PNG 路径列表。

    Args:
        video_path: 输入 mp4
        sample_count: 采样帧数（默认 8）
        tmp_dir: 临时目录（None = 视频同目录下 _frames 子目录）

    Returns:
        list[Path]: 采样帧 PNG 路径列表（按时间顺序）
    """
    video_path = Path(video_path)
    if not video_path.is_file():
        raise FileNotFoundError(f"视频不存在：{video_path}")

    if tmp_dir is None:
        tmp_dir = video_path.parent / "_frames"
    tmp_dir = Path(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # 探针：先查 duration
    try:
        probe_proc = subprocess.run(
            [_ffmpeg_exe(), "-i", str(video_path)],
            capture_output=True, text=True, timeout=10,
        )
        # ffmpeg -i 错误退出；duration 在 stderr 里
        m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", probe_proc.stderr)
        if m:
            h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
            duration = h * 3600 + mi * 60 + s
        else:
            duration = 6.0  # 兜底
    except Exception:
        duration = 6.0

    # 抽帧：均匀采样（fps = sample_count / duration）
    fps = max(0.5, sample_count / max(duration, 0.1))
    pattern = tmp_dir / "frame_%03d.png"

    cmd = [
        _ffmpeg_exe(), "-y",
        "-i", str(video_path),
        "-vf", f"fps={fps:.3f},scale=128:72",  # 缩到 128x72 省计算（SSIM 不需大图）
        "-vsync", "vfr",
        str(pattern),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg 抽帧失败：{proc.stderr[-200:]}"
        )

    frames = sorted(tmp_dir.glob("frame_*.png"))
    if not frames:
        raise RuntimeError(f"ffmpeg 抽帧后无文件：{tmp_dir}")
    return frames[:sample_count]


# ---------- Lite-SSIM（无 scikit-image 依赖） ----------

def _lite_ssim(img1_path: Path, img2_path: Path) -> float:
    """Lite-SSIM：基于亮度/对比度/结构的三维度相似度（Wang 2004 简化版）。

    Args:
        img1_path, img2_path: 两张图（任意尺寸，内部 resize 到 128x72）

    Returns:
        float: 0-1（1.0 = 完全一致；0.0 = 完全不同）

    实现：
        luminance  = (2*μ1*μ2 + C1) / (μ1² + μ2² + C1)
        contrast   = (2*σ1*σ2 + C2) / (σ1² + σ2² + C2)
        structure  = (σ12 + C3) / (σ1*σ2 + C3)
        SSIM       = luminance * contrast * structure
        其中 C1=0.01², C2=0.03², C3=C2/2（标准参数）
    """
    from PIL import Image

    img1 = Image.open(img1_path).convert("L").resize((128, 72))
    img2 = Image.open(img2_path).convert("L").resize((128, 72))
    a = list(img1.getdata())
    b = list(img2.getdata())
    n = len(a)
    if n != len(b):
        return 0.0

    mu1 = sum(a) / n
    mu2 = sum(b) / n
    var1 = sum((x - mu1) ** 2 for x in a) / n
    var2 = sum((x - mu2) ** 2 for x in b) / n
    sigma1 = var1 ** 0.5
    sigma2 = var2 ** 0.5
    cov12 = sum((a[i] - mu1) * (b[i] - mu2) for i in range(n)) / n

    C1 = (0.01 * 255) ** 2
    C2 = (0.03 * 255) ** 2
    C3 = C2 / 2

    lum = (2 * mu1 * mu2 + C1) / (mu1 * mu1 + mu2 * mu2 + C1)
    con = (2 * sigma1 * sigma2 + C2) / (var1 + var2 + C2)
    struc = (cov12 + C3) / (sigma1 * sigma2 + C3) if sigma1 * sigma2 > 0 else 1.0

    return lum * con * struc


def _frame_brightness(png_path: Path) -> float:
    """单帧亮度（0-1）。"""
    from PIL import Image
    img = Image.open(png_path).convert("L").resize((64, 64))
    px = list(img.getdata())
    return sum(px) / (len(px) * 255.0)


def _frame_color_hist24(png_path: Path) -> list[float]:
    """单帧 24 维颜色直方图（8 bins × RGB），归一化。与 consistency_check 同口径。"""
    from PIL import Image
    img = Image.open(png_path).convert("RGB").resize((64, 64))
    h = (
        img.getchannel("R").histogram()
        + img.getchannel("G").histogram()
        + img.getchannel("B").histogram()
    )
    s = sum(h)
    return [x / s for x in h] if s else [0.0] * len(h)


# ---------- 公开 API ----------

def compute_frame_quality(
    video_path: str | Path,
    *,
    sample_count: int = DEFAULT_SAMPLE_FRAMES,
    ssim_threshold: float = DEFAULT_SSIM_THRESHOLD,
    color_threshold: float = DEFAULT_COLOR_THRESHOLD,
    brightness_var_threshold: float = DEFAULT_BRIGHTNESS_VAR_THRESHOLD,
    motion_var_threshold: float = DEFAULT_MOTION_VAR_THRESHOLD,
    tmp_dir: Path | None = None,
) -> FrameQualityReport:
    """V3.3 W1：单视频帧级质量度量（0 依赖；ffmpeg + PIL）。

    Args:
        video_path: 输入 mp4
        sample_count: 采样帧数（默认 8）
        ssim_threshold: lite-SSIM flag 阈值
        color_threshold: 帧间色差 flag 阈值
        brightness_var_threshold: 亮度方差 flag 阈值
        motion_var_threshold: 运动方差 flag 阈值
        tmp_dir: 抽帧临时目录（None = 视频旁 _frames/）

    Returns:
        FrameQualityReport

    Raises:
        FileNotFoundError: 视频不存在
        RuntimeError: ffmpeg 失败（依赖 imageio-ffmpeg）

    行为：
        - sample_count < 2 → warning（无法计算 pair）
        - 任一维度超阈值 → flagged_pairs 加 (i, j, metric)
        - 不抛错；warnings 字段收集可恢复问题
    """
    rep = FrameQualityReport(video_path=str(video_path))

    try:
        frames = extract_keyframes(video_path, sample_count=sample_count, tmp_dir=tmp_dir)
    except (FileNotFoundError, RuntimeError) as exc:
        rep.warnings.append(f"抽帧失败：{type(exc).__name__}: {exc}")
        return rep

    rep.sample_count = len(frames)
    if len(frames) < 2:
        rep.warnings.append(f"采样帧数 {len(frames)} < 2，跳过 pair 计算")
        return rep

    # 计算每帧的 brightness + 24 维 color hist
    brights = [_frame_brightness(f) for f in frames]
    hists = [_frame_color_hist24(f) for f in frames]
    rep.brightness_variance = _variance(brights)

    # 运动方差：相邻帧亮度差的方差（越大越闪烁）
    motion_diffs = [abs(brights[i + 1] - brights[i]) for i in range(len(brights) - 1)]
    rep.motion_variance = _variance(motion_diffs)

    # pair 计算（lite-SSIM + color cosine）
    ssims: list[float] = []
    color_sims: list[float] = []
    for i in range(len(frames) - 1):
        try:
            s = _lite_ssim(frames[i], frames[i + 1])
        except Exception:
            s = 0.0
        ssims.append(s)
        color_sims.append(_cosine_sim(hists[i], hists[i + 1]))

    rep.per_pair_ssim = ssims
    rep.per_pair_color_sim = color_sims
    rep.mean_ssim = sum(ssims) / len(ssims)
    rep.min_ssim = min(ssims)
    rep.mean_color_sim = sum(color_sims) / len(color_sims)
    rep.min_color_sim = min(color_sims)

    # flag
    for idx, s in enumerate(ssims):
        if s < ssim_threshold:
            rep.flagged_pairs.append((idx, idx + 1, s))
    for idx, c in enumerate(color_sims):
        if c < color_threshold:
            rep.flagged_pairs.append((idx, idx + 1, c))
    if rep.brightness_variance > brightness_var_threshold:
        rep.warnings.append(
            f"亮度方差 {rep.brightness_variance:.4f} > {brightness_var_threshold}（画面忽明忽暗）"
        )
    if rep.motion_variance > motion_var_threshold:
        rep.warnings.append(
            f"运动方差 {rep.motion_variance:.4f} > {motion_var_threshold}（镜头闪烁/卡顿）"
        )

    return rep


__all__ = [
    "DEFAULT_BRIGHTNESS_VAR_THRESHOLD",
    "DEFAULT_COLOR_THRESHOLD",
    "DEFAULT_MOTION_VAR_THRESHOLD",
    "DEFAULT_SAMPLE_FRAMES",
    "DEFAULT_SSIM_THRESHOLD",
    "FrameQualityReport",
    "_frame_brightness",
    "_frame_color_hist24",
    "_lite_ssim",
    "compute_frame_quality",
    "extract_keyframes",
]
