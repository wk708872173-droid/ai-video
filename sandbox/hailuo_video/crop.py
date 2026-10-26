"""V3.8 W5 — 竖屏主体感知裁剪（Subject-Aware Vertical Crop）。

## 问题

16:9 → 9:16 只保留 **31.6%** 的宽度（1920→1080），双主体构图物理放不下
（`sample/story_arc_xiaohei_solo/README.md:27-28`）。现有的
`scripts/lib_ffmpeg.py:to_vertical` 只有 `center` / `left` / `right` 三档锚点，
偏心主体会被裁掉 —— 文档里已经记着"要根治需给 lib_ffmpeg 加 crop_frac 参数"
（同文件 `:253-255`），本模块就是那个 `crop_frac`。

## 文献依据

- **arXiv:2101.04051「H2V」**：横转竖自动转换 = shot 边界检测 + **主体选择**
  （Rank-SS：位置 / 外观 / 显著性三个线索）+ 多目标跟踪 → **主体保持式裁剪**。
  → 本模块实现"主体选择"这一环：找到主体 → 让裁剪窗口罩住主体。
- **arXiv:2307.16371「MobileVidFactory」**：**原生竖屏生成**优于事后裁剪
  （把生成器直接微调成竖屏，而不是把横屏裁一刀）。
  → 对应本批的另一半：**能原生出 9:16 就别裁**。画幅贯通由 Batch 0 修复
  （`--aspect-ratio` / `MINIMAX_IMAGE_ASPECT_RATIO` 现在在 V3 生产路径生效），
  本模块是"原生竖屏不可得"时的兜底。

## 实现取舍

本项目主链路依赖只有 `pillow` / `imageio-ffmpeg` / `edge-tts`
（`pyproject.toml:18-22`），**不引入 torch / 深度显著性模型**（见 plan 的红线）。
改用纯 PIL 的**中心-周边对比显著性**（center-surround contrast）+ 中心偏置做主体
定位 —— 这是 H2V 里"位置 / 显著性"两项线索的轻量实现，零新依赖。

本模块**不修改视频帧**，只算裁剪窗口；真正的 ffmpeg 调用仍在
`scripts/lib_ffmpeg.py:to_vertical`。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 显著性图工作分辨率（越小越快；64×64 足够定位主体）
WORK_SIZE = 64

# 最小显著度阈值（0-255 灰度尺度）：低于此值视为"无主体"（纯色/低对比图）
MIN_SALIENCY = 4.0


@dataclass(frozen=True)
class SubjectBox:
    """主体包围盒（像素坐标，原图像素空间）。

    x0/y0 = 左上角，x1/y1 = 右下角（**右开区间**，与 PIL crop 约定一致）。
    """
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def width(self) -> int:
        return max(0, self.x1 - self.x0)

    @property
    def height(self) -> int:
        return max(0, self.y1 - self.y0)

    @property
    def center_x(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def center_y(self) -> float:
        return (self.y0 + self.y1) / 2.0

    @property
    def area(self) -> int:
        return self.width * self.height

    def to_tuple(self) -> tuple[int, int, int, int]:
        return (self.x0, self.y0, self.x1, self.y1)

    def scaled(self, factor_x: float, factor_y: float | None = None) -> "SubjectBox":
        """按比例映射到另一坐标系。"""
        fy = factor_x if factor_y is None else factor_y
        return SubjectBox(
            x0=int(round(self.x0 * factor_x)),
            y0=int(round(self.y0 * fy)),
            x1=int(round(self.x1 * factor_x)),
            y1=int(round(self.y1 * fy)),
        )


@dataclass(frozen=True)
class CropWindow:
    """竖屏裁剪窗口（ffmpeg `scale` + `crop` 滤镜的几何参数）。"""
    crop_x: int
    crop_y: int
    scaled_w: int
    scaled_h: int
    target_w: int
    target_h: int

    @property
    def filter_expr(self) -> str:
        """ffmpeg -vf 滤镜串（与旧 to_vertical 的写法一致）。"""
        return (
            f"scale=-1:{self.target_h},"
            f"crop={self.target_w}:{self.target_h}:{self.crop_x}:{self.crop_y}"
        )


# ---------- 显著性 → 主体框 ----------

def estimate_subject_box(
    image_path: Path | str,
    *,
    top_ratio: float = 0.12,
    min_area_ratio: float = 0.01,
    work_size: int = WORK_SIZE,
) -> SubjectBox | None:
    """估计画面主体的包围盒。返回 None = 判定不出（调用方回退到中心裁剪）。

    方法（纯 PIL，零新依赖）：
      1. 灰度 + 缩到 work_size×work_size；
      2. **中心-周边对比显著性** = |原图 - 重模糊图|（高频细节 = 主体候选）；
      3. 乘**中心偏置**（横向容忍更宽 —— 宽银幕主体常偏在左右两侧）；
      4. 取分数最高的前 `top_ratio` 像素作为主体掩膜 → 取其包围盒。

    Args:
        image_path: 图片路径
        top_ratio: 主体掩膜占画面比例上限（越小越严格）
        min_area_ratio: 主体框面积占比下限（低于此值判为"判定不出"）
        work_size: 显著性图工作分辨率

    Returns:
        SubjectBox | None
    """
    try:
        from PIL import Image, ImageChops, ImageFilter
    except ImportError:                       # pragma: no cover - pillow 是硬依赖
        return None

    path = Path(image_path)
    if not path.is_file():
        return None

    try:
        with Image.open(path) as im:
            gray = im.convert("L")
            src_w, src_h = gray.size
            if src_w < 2 or src_h < 2:
                return None
            small = gray.resize((work_size, work_size), Image.BILINEAR)
            blurred = small.filter(ImageFilter.GaussianBlur(radius=max(2, work_size // 8)))
            diff = ImageChops.difference(small, blurred)
            pixels = list(diff.tobytes())
    except Exception:
        return None

    if len(pixels) != work_size * work_size:
        return None

    # 中心偏置：横向 dx 惩罚系数小、纵向 dy 大（宽银幕主体偏左右更常见）
    cx = (work_size - 1) / 2.0
    cy = (work_size - 1) / 2.0
    weighted: list[float] = []
    for y in range(work_size):
        for x in range(work_size):
            dx = (x - cx) / (work_size / 2.0)
            dy = (y - cy) / (work_size / 2.0)
            bias = 1.0 / (1.0 + 2.0 * dx * dx + 4.0 * dy * dy)
            weighted.append(pixels[y * work_size + x] * bias)

    threshold = _percentile(weighted, max(0.0, min(1.0, 1.0 - top_ratio)))
    # 纯色/低对比图没有主体可言（全 0 显著度）→ 判定不出，回退中心裁剪。
    if not weighted or max(weighted) < MIN_SALIENCY:
        return None
    xs: list[int] = []
    ys: list[int] = []
    for y in range(work_size):
        for x in range(work_size):
            # 用严格大于：阈值取到最大值时掩膜为空（纯色图 → 判定不出）
            if weighted[y * work_size + x] > threshold:
                xs.append(x)
                ys.append(y)
    if not xs:
        return None

    bx0, bx1 = min(xs), max(xs) + 1
    by0, by1 = min(ys), max(ys) + 1
    if (bx1 - bx0) * (by1 - by0) < work_size * work_size * min_area_ratio:
        return None

    # 映射回原图坐标
    fx = src_w / work_size
    fy = src_h / work_size
    box = SubjectBox(
        x0=int(round(bx0 * fx)),
        y0=int(round(by0 * fy)),
        x1=int(round(bx1 * fx)),
        y1=int(round(by1 * fy)),
    )
    # 夹回画面内
    box = SubjectBox(
        x0=max(0, min(src_w - 1, box.x0)),
        y0=max(0, min(src_h - 1, box.y0)),
        x1=max(1, min(src_w, box.x1)),
        y1=max(1, min(src_h, box.y1)),
    )
    return box if box.area > 0 else None


def _percentile(values: list[float], q: float) -> float:
    """线性插值分位数（q ∈ [0,1]）。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


def subject_box_from_video(
    video_path: Path | str,
    *,
    frame_index: int | None = None,
    sample_count: int = 3,
) -> SubjectBox | None:
    """从视频里抽帧估计主体框（复用 quality.frame_quality.extract_keyframes）。

    Args:
        video_path: 视频路径
        frame_index: 用第几帧（None = 取所有采样帧里主体框面积最大的那张）
        sample_count: 采样帧数

    Returns:
        SubjectBox | None
    """
    from hailuo_video.quality.frame_quality import extract_keyframes

    try:
        frames = extract_keyframes(video_path, sample_count=sample_count)
    except Exception:
        return None
    if not frames:
        return None
    if frame_index is not None:
        if frame_index < 0 or frame_index >= len(frames):
            return None
        return estimate_subject_box(frames[frame_index])

    best: SubjectBox | None = None
    for f in frames:
        box = estimate_subject_box(f)
        if box is not None and (best is None or box.area > best.area):
            best = box
    return best


# ---------- 裁剪窗口计算 ----------

_ANCHOR_FRAC: dict[str, float] = {"center": 0.5, "left": 0.0, "right": 1.0}


def compute_crop_window(
    src_w: int,
    src_h: int,
    target_w: int,
    target_h: int,
    *,
    subject_box: SubjectBox | None = None,
    anchor: str = "center",
    crop_frac: float | None = None,
) -> CropWindow:
    """算竖屏裁剪窗口。

    优先级：
      1. `subject_box` → 裁剪窗口罩住主体（H2V 主体保持式裁剪）；
      2. `crop_frac`（0=最左，0.5=居中，1=最右）→ 通用化旧的三档锚点；
      3. `anchor`（center/left/right）→ 等价于 crop_frac 0.5/0/1（向后兼容）。

    Args:
        src_w/src_h: 源分辨率
        target_w/target_h: 目标（竖屏 1080×1920）
        subject_box: 主体框（源像素坐标）
        anchor: 旧的三档锚点
        crop_frac: 水平位置比例（0..1），优先于 anchor

    Returns:
        CropWindow
    """
    if src_w <= 0 or src_h <= 0 or target_w <= 0 or target_h <= 0:
        raise ValueError(f"分辨率非法：src={src_w}x{src_h} target={target_w}x{target_h}")

    # scale -1:target_h → 高度铺满，宽度按比例
    scaled_h = target_h
    scaled_w = max(target_w, src_w * target_h // src_h)
    excess = max(0, scaled_w - target_w)

    if crop_frac is not None:
        frac = min(1.0, max(0.0, float(crop_frac)))
        crop_x = int(round(excess * frac))
    elif anchor == "left":
        crop_x = 0
    elif anchor == "right":
        crop_x = excess
    else:
        # center：与旧版 `excess // 2` **逐像素一致**（floor，不是 round）
        crop_x = excess // 2

    if subject_box is not None:
        # 主体框映射到 scaled 坐标
        sb = subject_box.scaled(scaled_w / src_w)
        subject_w = sb.width
        if subject_w > 0 and subject_w < target_w:
            # 让主体中心落在裁剪窗口中心（再夹回合法范围）
            desired = int(round(sb.center_x - target_w / 2))
            crop_x = max(0, min(excess, desired))
        # 主体比窗口还宽 → 无法保住主体，退回 frac 兜底
    return CropWindow(
        crop_x=max(0, min(excess, crop_x)),
        crop_y=0,
        scaled_w=scaled_w,
        scaled_h=scaled_h,
        target_w=target_w,
        target_h=target_h,
    )


__all__ = [
    "CropWindow",
    "SubjectBox",
    "WORK_SIZE",
    "compute_crop_window",
    "estimate_subject_box",
    "subject_box_from_video",
]
