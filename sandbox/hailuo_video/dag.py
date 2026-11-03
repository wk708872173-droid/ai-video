"""V5.0 Batch 2 — DAG 校验（消费 `Phase.inputs_from` 这个死字段）。

## 问题

`orchestrator.Phase` 有个 `inputs_from` 字段（`orchestrator.py:136`），
注释写着"依赖的 phase 名" —— 但**从来没有任何代码消费它**：

- runner 是 `for i, phase in enumerate(DEFAULT_DAG)` 的**顺序循环**
  （`orchestrator.py:836`）；
- 依赖顺序完全由**元组书写顺序**隐含。

于是 `inputs_from` 只是一段**装饰性文档**：改了它，pipeline 行为**不变**。
这类"字段存在但语义未接线"和本轮修的 `knowledge_store` 检索侧、
`audit_llm` 的 project_id 是**同一类缺陷**。

## 设计

本模块把 `inputs_from` 变成**可执行的约束**：

1. **拓扑排序** —— 从 `inputs_from` 推出正确的执行顺序；
2. **环检测** —— 有环 → 报错（现在这种环只会表现为"某个 phase 拿到空产物"）；
3. **依赖缺失** —— `inputs_from` 指向不存在的 phase → 报错；
4. **顺序违规** —— 声明顺序与依赖关系矛盾 → 报错。

关键约束：**校验结果只告警，不 raise**。
现有 `DEFAULT_DAG` 的顺序是**正确的**，改成 raise 会让所有旧调用方炸掉；
本模块的定位是"让漂移可见"，不是"重构 runner"。

## 不重写 runner（红线）

本轮**不改** orchestrator 的顺序执行循环 —— 1306 个测试与所有生产路径都依赖
现有顺序，重写风险远大于收益。本模块提供的是**校验 + 拓扑序**，
让"顺序与依赖不一致"这类漂移在启动时就被发现。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence


@dataclass(frozen=True)
class DagIssue:
    """一条 DAG 问题。"""

    kind: str                      # cycle | missing_dependency | order_violation | unknown_agent
    detail: str
    nodes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "detail": self.detail, "nodes": list(self.nodes)}


@dataclass(frozen=True)
class DagReport:
    """DAG 校验结果。"""

    ok: bool = True
    issues: tuple[DagIssue, ...] = field(default_factory=tuple)
    order: tuple[str, ...] = field(default_factory=tuple)   # 拓扑序

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "issues": [i.to_dict() for i in self.issues],
            "order": list(self.order),
        }

    def summary(self) -> str:
        if self.ok:
            return f"DAG 校验通过（{len(self.order)} 个 phase，拓扑序与声明顺序一致）"
        return "DAG 校验发现问题：" + "；".join(
            f"[{i.kind}] {i.detail}" for i in self.issues
        )


def _phase_attr(phase: Any, name: str, default: Any = "") -> Any:
    if isinstance(phase, dict):
        return phase.get(name, default)
    return getattr(phase, name, default)


def topological_order(
    phases: Sequence[Any],
) -> tuple[tuple[str, ...], tuple[DagIssue, ...]]:
    """从 `inputs_from` 推拓扑序。返回 `(顺序, 问题列表)`。

    环检测用 DFS 三色标记；环上的节点按发现顺序列出。
    """
    names = [str(_phase_attr(p, "name", "")) for p in phases]
    deps: dict[str, list[str]] = {}
    issues: list[DagIssue] = []

    for phase, name in zip(phases, names):
        raw = _phase_attr(phase, "inputs_from", ()) or ()
        dep_list = [str(d) for d in raw]
        for d in dep_list:
            if d not in names:
                issues.append(DagIssue(
                    kind="missing_dependency",
                    detail=f"phase {name!r} 依赖了不存在的 phase {d!r}",
                    nodes=(name, d),
                ))
        deps[name] = [d for d in dep_list if d in names]

    # DFS 三色：0=未访问 1=在栈上 2=已完成
    color: dict[str, int] = {n: 0 for n in names}
    order: list[str] = []
    stack: list[str] = []
    reported_cycles: set[tuple[str, ...]] = set()

    def visit(n: str) -> None:
        color[n] = 1
        stack.append(n)
        for d in deps.get(n, []):
            if color.get(d, 0) == 1:
                # 找到环：截取栈上从 d 到 n 的片段
                idx = stack.index(d)
                cycle = tuple(stack[idx:])
                key = tuple(sorted(cycle))
                if key not in reported_cycles:
                    reported_cycles.add(key)
                    issues.append(DagIssue(
                        kind="cycle",
                        detail="依赖成环：" + " -> ".join(cycle + (d,)),
                        nodes=cycle,
                    ))
            elif color.get(d, 0) == 0:
                visit(d)
        stack.pop()
        color[n] = 2
        order.append(n)

    for n in names:
        if color.get(n, 0) == 0:
            visit(n)

    # 声明顺序 vs 拓扑序：若不一致 → order_violation
    if not any(i.kind == "cycle" for i in issues):
        topo_pos = {n: i for i, n in enumerate(order)}
        for i in range(len(names) - 1):
            a, b = names[i], names[i + 1]
            if topo_pos.get(a, 0) > topo_pos.get(b, 0):
                issues.append(DagIssue(
                    kind="order_violation",
                    detail=(
                        f"声明顺序里 {a!r} 排在 {b!r} 之前，"
                        f"但依赖关系要求 {b!r} 先完成"
                    ),
                    nodes=(a, b),
                ))

    return tuple(order), tuple(issues)


def validate_dag(
    phases: Sequence[Any],
    *,
    known_agents: Iterable[str] | None = None,
) -> DagReport:
    """校验 DAG：拓扑序 + 环 + 依赖缺失 + 顺序违规 + agent 存在性。

    `known_agents` 给了就额外检查每个 phase 的 agent 是否存在。
    """
    order, issues = topological_order(phases)
    issue_list = list(issues)

    if known_agents is not None:
        known = set(known_agents)
        for phase in phases:
            pname = str(_phase_attr(phase, "name", ""))
            agents = _phase_attr(phase, "agent_names", ()) or ()
            for a in agents:
                if str(a) not in known:
                    issue_list.append(DagIssue(
                        kind="unknown_agent",
                        detail=f"phase {pname!r} 引用了不存在的 agent {a!r}",
                        nodes=(pname, str(a)),
                    ))

    return DagReport(
        ok=not issue_list,
        issues=tuple(issue_list),
        order=order,
    )


__all__ = [
    "DagIssue",
    "DagReport",
    "topological_order",
    "validate_dag",
]
