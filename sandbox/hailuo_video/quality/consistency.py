"""V3.2 重构：quality 子模块拆分 — 色彩一致性 + 邻接 shot 度量。

原 quality.py:
  - L330-357 (ConsistencyReport)
  - L360-421 (compute_color_consistency)
  - L424-445 (_normalize_hist / _cosine_sim / _variance)
  - L450-467 (assess_pipeline_output)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from hailuo_video.quality.logger import get_qa_logger


@dataclass
class ConsistencyReport:
    """post-flight 一致性度量。"""
    per_pair_similarity: list[float] = field(default_factory=list)   # 0-1，相邻 shot 色差
    mean_similarity: float = 0.0
    min_similarity: float = 0.0
    flagged_pairs: list[tuple[int, int, float]] = field(default_factory=list)  # (i, i+1, sim)
    brightness_variance: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.flagged_pairs

    def format(self) -> str:
        lines = []
        lines.append(f"mean 色差相似度: {self.mean_similarity:.3f}（1.0 = 完全一致）")
        lines.append(f"min 色差相似度: {self.min_similarity:.3f}")
        lines.append(f"亮度方差: {self.brightness_variance:.3f}（越低越稳）")
        if self.flagged_pairs:
            lines.append(f"[WARN] {len(self.flagged_pairs)} 对相邻 shot 色差过低：")
            for i, j, sim in self.flagged_pairs:
                lines.append(f"  - shot {i+1} ↔ shot {j+1}: sim={sim:.3f}")
        else:
            lines.append("[OK] 所有相邻 shot 色差相似度 ≥ 阈值")
        for w in self.warnings:
            lines.append(f"[WARN] {w}")
        return "\n".join(lines)


def compute_color_consistency(
    image_paths: list[Path],
    *,
    similarity_threshold: float = 0.65,  # 邻接 shot 色差 < 0.65 视为风格断裂
    brightness_threshold: float = 0.15,
) -> ConsistencyReport:
    """纯 PIL/numpy：邻接 shot 颜色直方图 cosine 相似度 + 亮度方差。

    不依赖 torch/transformers/CLIP — 通用且快速。
    """
    rep = ConsistencyReport()
    if len(image_paths) < 2:
        rep.warnings.append("需要 ≥ 2 张图才能计算一致性")
        return rep

    try:
        from PIL import Image
    except ImportError:
        rep.warnings.append("PIL 未装，跳过一致性计算（pip install pillow）")
        return rep

    hists = []
    brightnesses = []
    for p in image_paths:
        if not Path(p).is_file():
            rep.warnings.append(f"图不存在：{p}")
            continue
        img = Image.open(p).convert("RGB").resize((64, 64))  # 缩到 64x64 省内存
        # RGB 3 通道 8-bin 直方图（共 24 维）
        h_r = img.getchannel("R").histogram()
        h_g = img.getchannel("G").histogram()
        h_b = img.getchannel("B").histogram()
        hists.append(_normalize_hist(h_r + h_g + h_b))
        # 平均亮度
        gray = img.convert("L")
        px = list(gray.getdata())
        brightnesses.append(sum(px) / (len(px) * 255.0))  # 0-1

    if len(hists) < 2:
        rep.warnings.append("有效图数 < 2")
        return rep

    # 相邻 shot cosine 相似度
    sims = []
    for i in range(len(hists) - 1):
        sim = _cosine_sim(hists[i], hists[i + 1])
        sims.append(sim)
    rep.per_pair_similarity = sims
    rep.mean_similarity = sum(sims) / len(sims)
    rep.min_similarity = min(sims)
    rep.brightness_variance = _variance(brightnesses)

    # 标 flag：sim 低 OR 亮度方差高
    for idx, sim in enumerate(sims):
        if sim < similarity_threshold:
            rep.flagged_pairs.append((idx, idx + 1, sim))
    if rep.brightness_variance > brightness_threshold:
        rep.warnings.append(
            f"亮度方差 {rep.brightness_variance:.3f} > 阈值 {brightness_threshold}（画面忽明忽暗）"
        )

    return rep


def _normalize_hist(hist: list[int]) -> list[float]:
    s = sum(hist)
    if s == 0:
        return [0.0] * len(hist)
    return [x / s for x in hist]


def _cosine_sim(a: list[float], b: list[float]) -> float:
    """cosine 相似度 0-1。"""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _variance(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    mean = sum(xs) / len(xs)
    return sum((x - mean) ** 2 for x in xs) / len(xs)


def assess_pipeline_output(
    shot_image_paths: list[Path],
    storyboard,  # Storyboard（避免循环 import；运行时 type-check）
) -> ConsistencyReport:
    """pipeline 跑完后调用：报告所有 shot 间的视觉一致性。

    返回 ConsistencyReport；非阻塞（pipeline 已成功，只是建议）。
    """
    rep = compute_color_consistency(shot_image_paths)
    if rep.flagged_pairs:
        for i, j, sim in rep.flagged_pairs:
            get_qa_logger().warning(
                f"[consistency] shot {i+1} ↔ shot {j+1}: 色差相似度 {sim:.3f} < 阈值 0.65 → 风格可能断裂"
            )
    if rep.brightness_variance > 0.15:
        get_qa_logger().warning(
            f"[consistency] 亮度方差 {rep.brightness_variance:.3f} > 阈值 0.15 → 画面忽明忽暗"
        )
    return rep
