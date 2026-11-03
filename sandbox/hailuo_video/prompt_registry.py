"""V5.0 Batch 5 — 提示词版本化（Prompt Registry）。

## 问题

审计发现：

> `grep "prompt_version|PROMPT_VERSION"` → **零命中**；
> `grep "ab_test|golden|snapshot"` → **零命中**。

提示词有 4 个硬编码常量（`prompts.py`），唯一的"版本"概念是一个
`director_mode: bool` 开关（`prompts.py:313`）—— 而且**生产路径从不传 True**。

后果：
- 改了一句提示词 → **无法回答"这条成片是哪个提示词产的"**
- 想比较两版提示词 → **只能靠记忆 + 人工重跑**
- 而 Hailuo **3 条/天**意味着真 API 上根本做不了 A/B

## 设计

`PromptSpec` 给每个提示词一个**稳定身份**：

| 字段 | 含义 |
|---|---|
| `id` | 稳定标识（如 `storyboard`） |
| `version` | 版本号（字符串，可比大小） |
| `template` | 模板正文 |
| `variables` | 占位符集合 |
| `content_hash` | 内容哈希（**写进审计日志** → 事后可追溯） |

`PromptRegistry` 提供 register / get / variants —— `variants()` 是 Batch 6 的 A/B 基础。

## 兼容性

现有 4 个常量**注册进去但不删**（`prompts.py` 的公开名保持不变），
老调用方继续用 `prompts.STORYBOARD_PROMPT`，不受影响。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

_PLACEHOLDER = re.compile(r"(?<!\{)\{([a-zA-Z_][a-zA-Z0-9_]*)\}(?!\})")


def content_hash(template: str) -> str:
    """模板内容哈希（前 12 位）—— 写进审计日志，事后可追溯。"""
    return hashlib.sha256(template.encode("utf-8")).hexdigest()[:12]


def find_variables(template: str) -> tuple[str, ...]:
    """找出模板的占位符集合（按出现顺序去重）。"""
    seen: list[str] = []
    for m in _PLACEHOLDER.finditer(template):
        name = m.group(1)
        if name not in seen:
            seen.append(name)
    return tuple(seen)


@dataclass(frozen=True)
class PromptSpec:
    """一个提示词模板的版本化身份。"""

    id: str
    version: str
    template: str
    variables: tuple[str, ...] = field(default_factory=tuple)
    description: str = ""

    def __post_init__(self) -> None:
        """未显式给 variables 时，从模板自动派生（frozen → object.__setattr__）。"""
        if not self.variables:
            object.__setattr__(self, "variables", find_variables(self.template))

    @property
    def content_hash(self) -> str:
        return content_hash(self.template)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "variables": list(self.variables),
            "description": self.description,
            "content_hash": self.content_hash,
        }


class PromptRegistry:
    """提示词注册表（按 id + version 索引）。"""

    def __init__(self) -> None:
        self._specs: dict[tuple[str, str], PromptSpec] = {}

    def register(self, spec: PromptSpec) -> PromptSpec:
        key = (spec.id, spec.version)
        if key in self._specs:
            raise ValueError(f"提示词版本已注册：{key}")
        self._specs[key] = spec
        return spec

    def add(
        self,
        prompt_id: str,
        version: str,
        template: str,
        *,
        description: str = "",
    ) -> PromptSpec:
        """便捷注册（自动算 variables）。"""
        return self.register(PromptSpec(
            id=prompt_id,
            version=version,
            template=template,
            variables=find_variables(template),
            description=description,
        ))

    def get(self, prompt_id: str, version: str = "") -> PromptSpec | None:
        """取指定版本；不给版本 → 取**最新**（版本号字典序最大）。"""
        if version:
            return self._specs.get((prompt_id, version))
        candidates = [v for (i, v) in self._specs if i == prompt_id]
        if not candidates:
            return None
        return self._specs[(prompt_id, max(candidates))]

    def versions(self, prompt_id: str) -> list[str]:
        return sorted(v for (i, v) in self._specs if i == prompt_id)

    def variants(self, prompt_id: str) -> list[PromptSpec]:
        """某提示词的所有版本 —— **A/B 的基础**。"""
        return [self._specs[(prompt_id, v)] for v in self.versions(prompt_id)]

    def ids(self) -> list[str]:
        return sorted({i for (i, _) in self._specs})

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompts": {
                pid: [s.to_dict() for s in self.variants(pid)]
                for pid in self.ids()
            },
        }

    def __len__(self) -> int:
        return len(self._specs)


# ---------- 默认注册表（把现有 4 个常量登记进来） ----------

def build_default_registry() -> PromptRegistry:
    """把 `prompts.py` 的现有常量登记进版本化注册表。

    **不删不改**原常量 —— 老调用方继续用 `prompts.STORYBOARD_PROMPT`。
    """
    from hailuo_video import prompts as pr

    reg = PromptRegistry()
    reg.add("novel_to_script", "v1", pr.NOVEL_TO_SCRIPT_PROMPT,
            description="小说 → 场景级剧本（分场 / 人物 / 氛围）")
    reg.add("character_extraction", "v1", pr.CHARACTER_EXTRACTION_PROMPT,
            description="剧本 → 角色表（含 AI 生图用视觉描述）")
    reg.add("storyboard", "v1", pr.STORYBOARD_PROMPT,
            description="剧本 → 分镜表（含导演字段 / 白名单 / 三拍结构）")
    reg.add("storyboard", "v2", pr.STORYBOARD_PROMPT_V2,
            description="storyboard 的 director 增强版（额外 schema + 导演指引）")
    return reg


__all__ = [
    "PromptRegistry",
    "PromptSpec",
    "build_default_registry",
    "content_hash",
    "find_variables",
]
