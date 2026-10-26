"""V3.8 W4 — 负提示词（Negative Prompt）。

## 问题

全仓**零** negative prompt 支持：`image_engine.py` 的请求体只有
model / prompt / aspect_ratio / response_format / n / subject_references / prompt_optimizer。
生成路径里没有任何"**禁止出现什么**"的表达 —— 只能靠正向描述去规避。

## 文献依据

**arXiv:2610.15098「TIDE」**：把昂贵的迭代式提示词优化**蒸馏成可复用的 rule-module
skill** —— source-conditioned 规则模块，**单次 LLM 调用同时产出正/负提示词**，
不做 test-time 视觉搜索、不微调骨干（仅用 100 prompts / 14h 演化）。

为什么成本模型是关键：本项目 Hailuo 每日只有 **3 条 quota** → "生成 → 评分 → 改写"
的迭代式视觉搜索**根本跑不动**。TIDE 的「一次调用出正 + 负提示词」正好匹配这个约束。

## API 现实（重要）

`[UNVERIFIED]` MiniMax image-01 / Gemini `generateContent` 是否有**独立的**
`negative_prompt` 字段 —— 官方文档未列出该参数。

因此实现为**能力声明式**（`ImageBackend.supports_native_negative_prompt`）：

- 后端声明支持 → 发独立字段；
- 否则 → **内联**到正向 prompt 末尾。

内联不是降级凑合 —— 项目**已经**在用同一手法且验证有效：
`asset_prep.py` 的 `_safe_style_anchor` 会追加
`"no illustration no cartoon no anime no CGI"`（V3.4 ADR-003 防漂移守卫），
那正是内联式负提示词。

**绝不发送未确认的字段** —— 会 4xx，而请求已经计费。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------- 禁止出现的内容（视觉层）—— SSOT ----------
# 单一事实源：任何生成路径要加负提示词，都从这里取，不要在别处再写一份。
PROHIBITED_CONTENT: tuple[str, ...] = (
    # 风格漂移（V3.4 ADR-003 同款守卫词）
    "illustration", "cartoon", "anime", "CGI", "3d render",
    # 画面污染
    "text", "watermark", "signature", "logo",
    # 人体畸变（角色一致性场景高频）
    "deformed hands", "extra fingers", "extra limbs", "cropped head",
    # 画质
    "blurry", "low resolution", "jpeg artifacts",
    # 多主体串味（Hailuo 双角色颜色合并的已知症状）
    "duplicate subject", "merged colors",
)

# 动态（视频）镜头的额外禁止项 —— 复杂运动时多角色容易糊成一团
MOTION_PROHIBITED_CONTENT: tuple[str, ...] = (
    "frozen frame", "motion blur smear", "morphing face", "flickering",
)

# 角色一致性专用（配合角色圣经用）
CHARACTER_PROHIBITED_CONTENT: tuple[str, ...] = (
    "changing fur color", "changing species", "costume change",
)


@dataclass(frozen=True)
class NegativePromptSpec:
    """一条负提示词规格（可渲染成内联子句或独立字段）。"""
    items: tuple[str, ...] = field(default_factory=tuple)
    source: str = "base"

    def to_dict(self) -> dict[str, Any]:
        return {"items": list(self.items), "source": self.source}

    def is_empty(self) -> bool:
        return not self.items

    def render(self, *, prefix: str = "no ", joiner: str = ", ") -> str:
        """渲染成内联子句（与 ADR-006 守卫词同款写法）。

        例："no illustration, no cartoon, no text"
        """
        if not self.items:
            return ""
        return joiner.join(f"{prefix}{item}" for item in self.items)


def build_negative_prompt(
    shot: Any = None,
    *,
    include_motion: bool = False,
    include_character: bool = False,
    extra: tuple[str, ...] = (),
    limit: int = 14,
) -> NegativePromptSpec:
    """构造负提示词规格。

    Args:
        shot: Shot 实例或等价 dict（用于按镜头发型化；None = 通用集）
        include_motion: 追加动态禁止项（视频镜头）
        include_character: 追加角色一致性禁止项
        extra: 额外禁止项
        limit: 最多保留多少条

    Returns:
        NegativePromptSpec
    """
    items: list[str] = []
    source = "base"

    complexity = ""
    if shot is not None:
        complexity = str(_get(shot, "motion_complexity", "") or "").strip()

    # 显式追加的项（motion / character / extra）**必须保留** ——
    # limit 只作用在基础集上（否则基础集 18 条会把追加项全挤掉）。
    extras: list[str] = []
    if include_motion or complexity in ("simple", "complex"):
        extras.extend(MOTION_PROHIBITED_CONTENT)
        source = "base+motion"
    if include_character:
        extras.extend(CHARACTER_PROHIBITED_CONTENT)
        source = "base+motion+character" if source != "base" else "base+character"
    if extra:
        extras.extend(extra)
        source += "+extra"

    base_limit = max(0, limit - len(extras))
    items = list(PROHIBITED_CONTENT[:base_limit]) + extras

    # 去重 + 保序
    seen: set[str] = set()
    uniq: list[str] = []
    for it in items:
        key = it.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        uniq.append(it.strip())

    return NegativePromptSpec(items=tuple(uniq[:max(0, limit)]), source=source)


def _get(obj: Any, name: str, default: Any = "") -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


__all__ = [
    "CHARACTER_PROHIBITED_CONTENT",
    "MOTION_PROHIBITED_CONTENT",
    "PROHIBITED_CONTENT",
    "NegativePromptSpec",
    "build_negative_prompt",
]
