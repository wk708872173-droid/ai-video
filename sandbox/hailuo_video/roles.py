"""V5.0 Batch 5 — 角色契约（Role Spec）。

## 问题

图里的四大核心能力第一条是「**角色 + 提示词**」。审计发现：

> agent 层**零角色定义** —— `agents.Agent` 基类只有 `name` + 3 个 `_default_*` 钩子，
> `grep "persona|职责|禁则"` → **零命中**。

"角色"在本项目里只以两种形态存在：
1. 给 LLM 拼的 prompt 字符串（散落在 `prompts.py` / `script_engine.py`）
2. 散落在各 agent docstring 里的自然语言说明

后果：
- **职责边界无处校验** —— 谁该产出什么、越界了怎么办，全靠读代码
- **失败降级策略没有声明** —— `agents.py` 里 `except Exception` 各写各的
- **没有单一来源** —— 加一个 agent 要改 3 个地方（agents.py / orchestrator 注册表 / 文档）

## 设计

`RoleSpec` 把「这个 stage 负责什么」变成**可校验的数据**：

| 字段 | 含义 |
|---|---|
| `mission` | 职责（一句话） |
| `inputs` / `outputs` | 输入 / 输出契约 |
| `forbidden` | **禁则** —— 明确不许做什么 |
| `degradation` | 失败时的降级策略 |
| `deterministic` | 是否**纯确定性**（不调 LLM） |

`deterministic` 这个字段承载了本轮最重要的一个判断：
**8 个 stage 里只有 `script_writer` 调 LLM**，其余全是确定性管道。
把这一点写进数据，而不是散落在各人的脑子里。

## 红线

- **不改任何 stage 的行为** —— 本模块是**声明层**，不是执行层。
- 不引入 LLM 决策（红线：8 个 stage 的确定性语义不变）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RoleSpec:
    """一个 stage 的角色契约。"""

    name: str
    mission: str = ""                                   # 职责
    inputs: tuple[str, ...] = field(default_factory=tuple)
    outputs: tuple[str, ...] = field(default_factory=tuple)
    forbidden: tuple[str, ...] = field(default_factory=tuple)   # 禁则
    degradation: str = ""                               # 失败降级策略
    deterministic: bool = True                          # 纯确定性（不调 LLM）
    cost: str = "none"                                  # 计费影响：none / image / video / tts

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "mission": self.mission,
            "inputs": list(self.inputs),
            "outputs": list(self.outputs),
            "forbidden": list(self.forbidden),
            "degradation": self.degradation,
            "deterministic": self.deterministic,
            "cost": self.cost,
        }


# ---------- 8 个 stage 的契约 ----------

ROLE_REGISTRY: dict[str, RoleSpec] = {
    "script_writer": RoleSpec(
        name="script_writer",
        mission="小说 → 结构化剧本（分场 / 角色 / 分镜）",
        inputs=("novel", "backend"),
        outputs=("storyboard_json",),
        forbidden=(
            "不得输出非 JSON 的内容",
            "不得在本阶段写分镜表（分镜是 storyboard 阶段的事）",
        ),
        degradation="硬失败（无产出即无法继续）",
        deterministic=False,          # 唯一调 LLM 的 stage
        cost="none",
    ),
    "asset_prep": RoleSpec(
        name="asset_prep",
        mission="为每个角色 / 场景生成多角度参考图（视觉锚定）",
        inputs=("storyboard", "image_backend"),
        outputs=("character_ref", "scene_ref"),
        forbidden=(
            "不得修改角色名（改身份会破坏跨项目一致性）",
            "不得使用已过期的参考图 URL",
        ),
        degradation="单张图失败 → 跳过该张（不阻断整个 phase）",
        deterministic=True,
        cost="image",
    ),
    "image_gen": RoleSpec(
        name="image_gen",
        mission="分镜描述 + 参考图 → 首帧画面",
        inputs=("prompt", "subject_references", "aspect_ratio"),
        outputs=("image",),
        forbidden=(
            "不得丢失 style_anchor（画风漂移）",
            "不得静默使用过期 reference",
        ),
        degradation="单镜失败 → 跳过该镜（后续用 Ken Burns 兜底）",
        deterministic=True,
        cost="image",
    ),
    "motion_gen": RoleSpec(
        name="motion_gen",
        mission="首帧 → 真 AI 视频（I2V 运动）",
        inputs=("subject_action", "first_frame_image", "duration"),
        outputs=("video",),
        forbidden=(
            "不得省略 first_frame_image（会走 T2V 自由发挥 → 「小黄变猫」）",
            "不得在配额用尽时静默提交",
        ),
        degradation="配额用尽 → 跳过该镜（Ken Burns 兜底）+ 明确告警",
        deterministic=True,
        cost="video",
    ),
    "video_synth": RoleSpec(
        name="video_synth",
        mission="首帧 → Ken Burns 伪运动视频（无 quota 时的兜底）",
        inputs=("image_path", "seconds"),
        outputs=("video",),
        forbidden=("不得被当成真 AI 运动（必须标明是兜底）",),
        degradation="失败 → 跳过该镜",
        deterministic=True,
        cost="none",
    ),
    "tts": RoleSpec(
        name="tts",
        mission="对白 / 旁白 → 配音 + 字幕",
        inputs=("text", "voice"),
        outputs=("audio", "subtitle"),
        forbidden=(
            "不得给同一角色分配两个音色",
            "不得让旁白占用角色音色池",
        ),
        degradation="失败 → 无音轨（成片照出，但必须可见）",
        deterministic=True,
        cost="tts",
    ),
    "editing": RoleSpec(
        name="editing",
        mission="分镜视频 + 音频 + 字幕 → 成片（调色 / 叠化 / 混音）",
        inputs=("video_paths", "audio_paths", "subtitle_paths"),
        outputs=("final_video",),
        forbidden=(
            "不得让每镜各跑一次全量拼接（会把所有镜头重复拼进去）",
            "不得把成片写到与用户请求不同的路径而不告知",
        ),
        degradation="失败 → 回退到 motion 视频（标记 motion_fallback）",
        deterministic=True,
        cost="none",
    ),
    "qa": RoleSpec(
        name="qa",
        mission="产出后自检（单镜一致性 + 跨镜连续性）",
        inputs=("image_paths",),
        outputs=("consistency_report",),
        forbidden=("不得把不合格结果标记为通过（只报警也要如实报数）",),
        degradation="异常 → 记录但不影响成片",
        deterministic=True,
        cost="none",
    ),
}


def get_role(name: str) -> RoleSpec:
    """按名取角色契约。未知 → KeyError（避免静默用错契约）。"""
    spec = ROLE_REGISTRY.get(name)
    if spec is None:
        raise KeyError(
            f"未知角色：{name!r}；已定义：{sorted(ROLE_REGISTRY)}"
        )
    return spec


def deterministic_roles() -> list[str]:
    """纯确定性的 stage 名（不调 LLM）。"""
    return [n for n, s in ROLE_REGISTRY.items() if s.deterministic]


def llm_roles() -> list[str]:
    """会调 LLM 的 stage 名。"""
    return [n for n, s in ROLE_REGISTRY.items() if not s.deterministic]


def cost_roles() -> list[str]:
    """会消耗额度的 stage 名。"""
    return [n for n, s in ROLE_REGISTRY.items() if s.cost != "none"]


__all__ = [
    "ROLE_REGISTRY",
    "RoleSpec",
    "cost_roles",
    "deterministic_roles",
    "get_role",
    "llm_roles",
]
