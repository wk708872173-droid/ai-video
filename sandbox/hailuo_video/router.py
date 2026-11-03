"""V5.0 Batch 2 — 路由层（Router）。

## 问题

图里的「多智能体 → MAS + **路由** + 执行 + **审计**」。

本项目的判定是**不需要 MAS**（8 个 stage 全确定性，DAG 更优 —— 见
`docs/AGENT_FRAMEWORK.md` 的场景可行性评估）。但**需要路由层**，因为现在
唯一的动态决策是**硬编码 if**：

- `orchestrator.py` 的 conditional phase 分派绑定在 **phase 名字符串**上
  （`if phase.conditional and phase.name == "motion_gen"`）；
- 选镜规则写死在 `_run_motion_gen_conditional` 函数体里；
- **决策过程不可解释**（日志里只有结果，没有"为什么"）、不可配置、不可测试。

## 设计

`RoutePolicy` 把"怎么决定"抽成可插拔对象，`RouteDecision` 把"决定了什么 +
为什么"变成**可记录的数据**。orchestrator 只需：

```python
decision = self.router.decide("motion_gen", ctx)
self.logger.event("route_decision", phase=..., target=..., reason=..., evidence=...)
```

决策从此**出现在日志里**，而不是埋在代码里。

## 红线

- **不改变任何现有决策逻辑**：`MotionComplexityPolicy` 是 `_run_motion_gen_conditional`
  的**逐行等价搬移**（同样的排序、同样的截断、同样的 static 排除）。
  等价性由 `tests/test_router.py` 锁定。
- **不引入 LLM 决策**：路由是确定性规则，不是 agent。
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar


@dataclass(frozen=True)
class RouteDecision:
    """一次路由决策 —— **含理由**（这是路由层的核心价值）。

    Attributes:
        target: 决策结果的短标识（如 "motion_gen:3/8"）
        reason: 人可读的理由（会进日志）
        evidence: 支撑证据（数字、索引、配置值）
        confidence: 置信度 0..1（确定性规则 = 1.0；留出给未来的软策略）
        selected: 选中的 shot 索引
        skipped: 被跳过的 shot 索引
    """

    target: str = ""
    reason: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    selected: tuple[int, ...] = ()
    skipped: tuple[int, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "reason": self.reason,
            "evidence": dict(self.evidence),
            "confidence": self.confidence,
            "selected": list(self.selected),
            "skipped": list(self.skipped),
        }


class RoutePolicy(abc.ABC):
    """路由策略基类。

    子类实现 `decide(ctx) -> RouteDecision`。
    `ctx` 是编排上下文（至少含 `storyboard`），由调用方构造。
    """

    name: ClassVar[str] = ""

    @abc.abstractmethod
    def decide(self, ctx: dict[str, Any]) -> RouteDecision:
        """给出一次决策。**不得抛错** —— 拿不准时返回"不选"的决策。"""


class MotionComplexityPolicy(RoutePolicy):
    """按 `motion_complexity` 选哪些镜头值得烧 Hailuo quota。

    这是 `orchestrator._run_motion_gen_conditional` 的**逐行等价搬移**：

    - `motion_complexity == "static"` → 不选（走 Ken Burns 兜底）
    - `motion_complexity == "complex"` → priority 1
    - 其他（`simple`）→ priority 0
    - 按 `(-priority, index)` 排序后取前 `budget` 个

    V3.8 缺陷 2 的告警（static 被排除）也在这里统一产出 ——
    此前它写在 orchestrator 里，与决策逻辑混在一起。
    """

    name = "motion_complexity"

    def decide(self, ctx: dict[str, Any]) -> RouteDecision:
        sb = ctx.get("storyboard")
        shots = list(getattr(sb, "shots", []) or [])
        budget = int(ctx.get("budget", 3) or 0)

        candidates: list[tuple[int, int, Any]] = []
        static_shots: list[int] = []
        for i, shot in enumerate(shots):
            mc = getattr(shot, "motion_complexity", "static") or "static"
            if mc == "static":
                static_shots.append(i)
                continue
            priority = 1 if mc == "complex" else 0
            candidates.append((priority, i, shot))

        candidates.sort(key=lambda t: (-t[0], t[1]))
        chosen = candidates[:budget]
        selected = tuple(i for _, i, _ in chosen)
        skipped = tuple(static_shots)

        reason_bits = [f"候选 {len(candidates)} 镜，budget={budget}"]
        if static_shots:
            reason_bits.append(
                f"{len(static_shots)} 镜 motion_complexity=static → Ken Burns 兜底"
            )
        if len(candidates) > budget:
            reason_bits.append(f"按 priority 截断 {len(candidates) - budget} 镜")
        if not candidates:
            reason_bits.append("无候选 → 本 phase 跳过")

        return RouteDecision(
            target=f"motion_gen:{len(selected)}/{len(shots)}",
            reason="；".join(reason_bits),
            evidence={
                "candidate_count": len(candidates),
                "budget": budget,
                "static_shots": list(static_shots),
                "selected": list(selected),
            },
            selected=selected,
            skipped=skipped,
        )


class Router:
    """按 phase 名分发到对应策略，并记录决策。

    `on_decision` 是可选回调（orchestrator 传 `logger.event`），
    让**决策本身**成为可观测数据 —— 这是「执行审计」的第一层。
    """

    def __init__(self, *, on_decision: Callable[[str, RouteDecision], None] | None = None) -> None:
        self._policies: dict[str, RoutePolicy] = {}
        self._on_decision = on_decision
        self.history: list[tuple[str, RouteDecision]] = []

    def register(self, phase_name: str, policy: RoutePolicy) -> None:
        self._policies[phase_name] = policy

    def has_policy(self, phase_name: str) -> bool:
        return phase_name in self._policies

    def decide(self, phase_name: str, ctx: dict[str, Any]) -> RouteDecision | None:
        """决策；没有对应策略 → None（调用方按默认路径走）。"""
        policy = self._policies.get(phase_name)
        if policy is None:
            return None
        try:
            decision = policy.decide(ctx)
        except Exception as exc:  # 路由出错不该让 pipeline 崩 → 退化为"不选"
            decision = RouteDecision(
                target=f"{phase_name}:error",
                reason=f"路由策略异常：{type(exc).__name__}: {exc}",
                confidence=0.0,
            )
        self.history.append((phase_name, decision))
        if self._on_decision is not None:
            try:
                self._on_decision(phase_name, decision)
            except Exception:
                pass          # 记录失败不该影响决策本身
        return decision


__all__ = [
    "MotionComplexityPolicy",
    "RouteDecision",
    "RoutePolicy",
    "Router",
]
