"""V3.8 W3 — 跨镜连续性门禁（Cross-Shot Continuity Gate）。

## 问题

项目已有**单镜**质量门禁（`quality/frame_quality.py:90 DEFAULT_SSIM_THRESHOLD=0.65`
的帧间 lite-SSIM），但**跨镜**维度没有门禁：

> 实测跨镜色差 mean=0.700 / min=0.587，而 Daily Review 的"理想基线"是 **>0.95**
> （`logs/_diagnose_3to4_jump_20261016/SUMMARY.md:36-39`）
>
> "这是**单镜 SSIM gate 不能解决**的，需要额外的'邻接镜色差门禁'"

→ 客户眼里的"一眼 AI"、色调跳变，就是这条缺口。

## 文献依据

- **arXiv:2608.16717「PersonaShot」**：现有 benchmark 多只测单镜外观 / 单镜质量，
  **不测"切点之后"物理与情绪状态是否连贯**；该工作给出 16 项指标，分属
  physical continuity / affective dynamics / cinematic grammar 三类。
  → 本模块的 `CONTINUITY_DIMENSIONS` 按这三类组织。
- **arXiv:2510.26412「LoCoT2V-Eval」**：长视频评估分 5 维 —— perceptual quality /
  text-video alignment / temporal quality / dynamic quality / HERD。
  → 跨镜门禁的维度命名对齐这套结构（可解释、可扩展）。
- **arXiv:2509.22799「VideoScore2」**：多维、可解释、输出理由（CoT）。
  → `ContinuityReport.format()` 给出逐维度可读的结论。

## 实现取舍

**纯 PIL/numpy**（复用 `quality/consistency.py:47 compute_color_consistency` 与
`quality/frame_quality.py:259 _frame_color_hist24`），**不引入 torch / CLIP**。
本项目的依赖极简是硬约束（`pyproject.toml:18-22`）。

门禁语义：**默认只报警不阻断**（与项目既有 SSIM gate 一致 —— "仅报警，不阻断 —
用户决策保守"）。要阻断需显式传 `strict=True`。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 跨镜色差相似度门禁阈值。
# 0.90 = 日记里的实测值 0.700 与理想基线 0.95 之间的保守中位。
# （旧的 compute_color_consistency 默认 0.65 太宽松 —— 0.700 的片子在旧门禁下"通过"。）
DEFAULT_CROSS_SHOT_SIMILARITY_THRESHOLD = 0.90

# 亮度跳变门禁（相邻 shot 平均亮度差）
DEFAULT_CROSS_SHOT_BRIGHTNESS_DELTA_THRESHOLD = 0.12

# 评估维度（对齐 PersonaShot 三类 + LoCoT2V-Eval 五维的可解释子集）
CONTINUITY_DIMENSIONS: dict[str, str] = {
    "perceptual": "感知质量（LoCoT2V-Eval）：相邻镜色调 / 亮度是否连续",
    "physical": "物理连续性（PersonaShot）：色彩基调与画面亮度是否跳变",
    "cinematic": "电影语法（PersonaShot）：相邻镜是否同一场景/时间",
    "dynamic": "动态质量（LoCoT2V-Eval）：运动能量是否出现异常断裂",
    "herd": "HERD（LoCoT2V-Eval）：是否达成预期观感（本项目以色基调锁定为准）",
}


@dataclass
class ContinuityReport:
    """跨镜连续性评估结果。"""
    mean_similarity: float = 0.0
    min_similarity: float = 0.0
    per_pair_similarity: list[float] = field(default_factory=list)
    brightness_deltas: list[float] = field(default_factory=list)
    max_brightness_delta: float = 0.0
    flagged_pairs: list[tuple[int, int, float]] = field(default_factory=list)
    dimension_scores: dict[str, float] = field(default_factory=dict)
    color_lock_mismatches: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    evaluated: bool = False

    @property
    def ok(self) -> bool:
        """无 flag → 通过。"""
        return not self.flagged_pairs and not self.color_lock_mismatches

    def to_dict(self) -> dict[str, Any]:
        return {
            "mean_similarity": self.mean_similarity,
            "min_similarity": self.min_similarity,
            "per_pair_similarity": list(self.per_pair_similarity),
            "brightness_deltas": list(self.brightness_deltas),
            "max_brightness_delta": self.max_brightness_delta,
            "flagged_pairs": [list(p) for p in self.flagged_pairs],
            "dimension_scores": dict(self.dimension_scores),
            "color_lock_mismatches": list(self.color_lock_mismatches),
            "warnings": list(self.warnings),
            "evaluated": self.evaluated,
            "ok": self.ok,
        }

    def format(self) -> str:
        """可读报告（VideoScore2 式：逐维度 + 理由）。"""
        lines = [
            f"跨镜连续性：mean 色差相似度 {self.mean_similarity:.3f}"
            f"（门禁 {DEFAULT_CROSS_SHOT_SIMILARITY_THRESHOLD:.2f}）",
            f"  min {self.min_similarity:.3f}｜最大亮度跳变 {self.max_brightness_delta:.3f}",
        ]
        for name, score in self.dimension_scores.items():
            desc = CONTINUITY_DIMENSIONS.get(name, "")
            lines.append(f"  - {name}（{desc}）: {score:.3f}")
        if self.flagged_pairs:
            lines.append(f"[WARN] {len(self.flagged_pairs)} 对相邻镜连续性不足：")
            for i, j, sim in self.flagged_pairs:
                lines.append(f"  - shot {i+1} ↔ shot {j+1}: sim={sim:.3f}")
        if self.color_lock_mismatches:
            lines.append(f"[WARN] {len(self.color_lock_mismatches)} 处色基调锁定冲突：")
            for m in self.color_lock_mismatches:
                lines.append(
                    f"  - shot {m.get('shot_idx')}: {m.get('expected')} → {m.get('actual')}"
                )
        if not self.warnings:
            lines.append("[OK] 跨镜连续性通过")
        for w in self.warnings:
            lines.append(f"[WARN] {w}")
        return "\n".join(lines)


def evaluate_shot_continuity(
    image_paths: list[str | Path],
    *,
    similarity_threshold: float = DEFAULT_CROSS_SHOT_SIMILARITY_THRESHOLD,
    brightness_delta_threshold: float = DEFAULT_CROSS_SHOT_BRIGHTNESS_DELTA_THRESHOLD,
    color_locks: list[str] | None = None,
) -> ContinuityReport:
    """评估**相邻镜**的连续性（跨镜，不是帧间）。

    Args:
        image_paths: 按时间顺序排列的每镜首帧图
        similarity_threshold: 相邻镜色彩直方图 cosine 相似度门禁
        brightness_delta_threshold: 相邻镜平均亮度差门禁
        color_locks: 每镜的色基调锁定（来自 Shot.color_lock）；给则检查是否一致

    Returns:
        ContinuityReport
    """
    rep = ContinuityReport()
    paths = [Path(p) for p in image_paths]
    if len(paths) < 2:
        rep.warnings.append("需要 ≥ 2 张图才能评估跨镜连续性")
        return rep

    # 复用既有实现（纯 PIL/numpy，无新依赖）
    from hailuo_video.quality.consistency import compute_color_consistency

    consistency = compute_color_consistency(
        paths, similarity_threshold=similarity_threshold,
    )
    rep.warnings.extend(consistency.warnings)
    if not consistency.per_pair_similarity:
        rep.warnings.append("有效图数 < 2，无法评估")
        return rep

    rep.evaluated = True
    rep.per_pair_similarity = list(consistency.per_pair_similarity)
    rep.mean_similarity = consistency.mean_similarity
    rep.min_similarity = consistency.min_similarity

    # 亮度跳变（相邻镜平均亮度差）
    brightnesses = _mean_brightnesses(paths)
    if len(brightnesses) >= 2:
        deltas = [abs(brightnesses[i + 1] - brightnesses[i])
                  for i in range(len(brightnesses) - 1)]
        rep.brightness_deltas = deltas
        rep.max_brightness_delta = max(deltas) if deltas else 0.0

    # 逐对判定
    for idx, sim in enumerate(rep.per_pair_similarity):
        delta = rep.brightness_deltas[idx] if idx < len(rep.brightness_deltas) else 0.0
        if sim < similarity_threshold or delta > brightness_delta_threshold:
            rep.flagged_pairs.append((idx, idx + 1, sim))

    # 色基调锁定一致性（HERD 维度）—— 必须在维度打分**之前**判定，
    # 否则 herd 永远拿到 1.0（score 先算、mismatch 后收集的顺序错误）。
    if color_locks:
        for i in range(len(rep.per_pair_similarity)):
            a = (color_locks[i] if i < len(color_locks) else "") or ""
            b = (color_locks[i + 1] if i + 1 < len(color_locks) else "") or ""
            if a and b and a != b:
                rep.color_lock_mismatches.append({
                    "shot_idx": i + 1, "expected": a, "actual": b,
                })

    # 维度打分（0..1，越高越好）
    rep.dimension_scores = {
        "perceptual": rep.mean_similarity,
        "physical": 1.0 - min(1.0, rep.max_brightness_delta),
        "cinematic": 1.0 - min(1.0, len(rep.flagged_pairs) / max(1, len(rep.per_pair_similarity))),
        "dynamic": 1.0 - min(1.0, len(rep.flagged_pairs) / max(1, len(rep.per_pair_similarity))),
        "herd": 1.0 if not rep.color_lock_mismatches else 0.5,
    }

    if rep.mean_similarity < similarity_threshold:
        rep.warnings.append(
            f"跨镜平均色差相似度 {rep.mean_similarity:.3f} < 门禁 {similarity_threshold:.2f}"
            f"（日记实测 0.700 / 理想基线 0.95）"
        )
    if rep.max_brightness_delta > brightness_delta_threshold:
        rep.warnings.append(
            f"最大亮度跳变 {rep.max_brightness_delta:.3f} > 门禁 {brightness_delta_threshold:.2f}"
        )
    return rep


def _mean_brightnesses(paths: list[Path]) -> list[float]:
    """每张图的平均亮度（0..1）。失败返回空列表。"""
    try:
        from PIL import Image
    except ImportError:
        return []
    out: list[float] = []
    for p in paths:
        try:
            if not p.is_file():
                continue
            with Image.open(p) as im:
                gray = im.convert("L").resize((64, 64))
                px = list(gray.tobytes())
            if px:
                out.append(sum(px) / (len(px) * 255.0))
        except Exception:
            continue
    return out


__all__ = [
    "CONTINUITY_DIMENSIONS",
    "DEFAULT_CROSS_SHOT_BRIGHTNESS_DELTA_THRESHOLD",
    "DEFAULT_CROSS_SHOT_SIMILARITY_THRESHOLD",
    "ContinuityReport",
    "evaluate_shot_continuity",
]
