"""V5.0 Batch 2 — 路由层与 DAG 校验。

## 覆盖三件事

1. **等价性**：路由层是 `_run_motion_gen_conditional` 的**逐行搬移**，
   选镜结果必须与旧逻辑**完全一致**（否则等于悄悄改了生产行为）。
2. **可解释性**：决策现在带 `reason` / `evidence`，并落进 `log.jsonl`。
3. **DAG 校验有效**：`Phase.inputs_from` 从死字段变成可执行约束 ——
   环 / 依赖缺失 / 顺序违规都要被检出。

外加：双份 agent 注册表已收敛为单一来源。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SANDBOX = REPO_ROOT / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from hailuo_video.dag import DagReport, topological_order, validate_dag  # noqa: E402
from hailuo_video.orchestrator import DEFAULT_DAG, PipelineAgent, PipelineConfig  # noqa: E402
from hailuo_video.router import (  # noqa: E402
    MotionComplexityPolicy,
    RouteDecision,
    Router,
)


# ---------- 测试夹具 ----------

class _Phase:
    def __init__(self, name, inputs_from=(), agent_names=()):
        self.name = name
        self.inputs_from = tuple(inputs_from)
        self.agent_names = tuple(agent_names)


class _Shot:
    def __init__(self, index, motion_complexity="simple"):
        self.index = index
        self.motion_complexity = motion_complexity
        self.subject_action = "act"
        self.description = "desc"


class _SB:
    def __init__(self, shots):
        self.shots = shots


# ---------- 1. 等价性：路由层 vs 旧逻辑 ----------

def _legacy_selection(shots, budget):
    """旧逻辑的逐行复刻（来自 V4.0 之前的 `_run_motion_gen_conditional`）。"""
    candidates = []
    static_shots = []
    for i, shot in enumerate(shots):
        mc = getattr(shot, "motion_complexity", "static") or "static"
        if mc == "static":
            static_shots.append(i)
            continue
        priority = 1 if mc == "complex" else 0
        candidates.append((priority, i, shot))
    candidates.sort(key=lambda t: (-t[0], t[1]))
    return [i for _, i, _ in candidates[:budget]], static_shots


@pytest.mark.parametrize("complexities,budget", [
    (["simple", "simple", "simple", "simple"], 3),
    (["simple", "complex", "static", "simple"], 2),
    (["static", "static"], 3),
    (["complex", "complex", "complex"], 1),
    ([], 3),
    (["simple"], 0),
])
def test_router_selection_is_equivalent_to_legacy(complexities, budget):
    """路由层的选镜结果必须与旧逻辑**完全一致**。"""
    shots = [_Shot(i, mc) for i, mc in enumerate(complexities)]
    decision = MotionComplexityPolicy().decide({
        "storyboard": _SB(shots), "budget": budget,
    })
    legacy_selected, legacy_static = _legacy_selection(shots, budget)
    assert list(decision.selected) == legacy_selected
    assert list(decision.skipped) == legacy_static


def test_complex_motion_has_priority_over_simple():
    shots = [_Shot(0, "simple"), _Shot(1, "complex"), _Shot(2, "simple")]
    d = MotionComplexityPolicy().decide({"storyboard": _SB(shots), "budget": 1})
    assert list(d.selected) == [1], "complex 应优先于 simple"


def test_decision_carries_reason_and_evidence():
    """决策**带理由** —— 这是路由层相对旧硬编码 if 的核心增量。"""
    shots = [_Shot(0, "simple"), _Shot(1, "static")]
    d = MotionComplexityPolicy().decide({"storyboard": _SB(shots), "budget": 3})
    assert d.reason
    assert "static" in d.reason
    assert d.evidence["candidate_count"] == 1
    assert d.evidence["static_shots"] == [1]
    assert d.confidence == 1.0


def test_decision_to_dict_is_json_serializable():
    shots = [_Shot(0, "simple")]
    d = MotionComplexityPolicy().decide({"storyboard": _SB(shots), "budget": 3})
    json.dumps(d.to_dict(), ensure_ascii=False)   # 不抛即通过


def test_router_without_policy_returns_none():
    r = Router()
    assert r.decide("unknown_phase", {}) is None


def test_router_swallows_policy_exception():
    """路由出错 → 退化为"不选"，不让 pipeline 崩。"""
    class _Boom:
        def decide(self, ctx):
            raise RuntimeError("boom")

    r = Router()
    r.register("x", _Boom())
    d = r.decide("x", {})
    assert d is not None
    assert d.confidence == 0.0
    assert "boom" in d.reason


def test_router_records_decision_via_callback():
    seen = []
    r = Router(on_decision=lambda phase, d: seen.append((phase, d)))
    r.register("motion_gen", MotionComplexityPolicy())
    r.decide("motion_gen", {"storyboard": _SB([_Shot(0)]), "budget": 1})
    assert len(seen) == 1
    assert seen[0][0] == "motion_gen"
    assert r.history and r.history[0][0] == "motion_gen"


# ---------- 2. 编排器接了路由层 ----------

def test_orchestrator_logs_route_decision(tmp_path):
    """决策进日志 —— 「执行审计」的第一层。"""
    from hailuo_video.script_engine import SceneSpec, Shot, Storyboard

    sb = Storyboard(
        title="t", characters=[],
        scenes=[SceneSpec(id=1, location="x")],
        shots=[Shot(index=1, scene_id=1, shot_type="中景", description="d",
                    motion_complexity="simple")],
    )
    agent = PipelineAgent(PipelineConfig(run_dir=tmp_path / "run"))
    ctx = {"storyboard": sb, "shot_count": 1, "intermediate_dir": tmp_path}
    agent._run_motion_gen_conditional(ctx)

    log_path = tmp_path / "run" / "log.jsonl"
    assert log_path.is_file()
    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    route_events = [e for e in events if e.get("event_type") == "route_decision"]
    assert route_events, "log.jsonl 里没有 route_decision 事件"
    payload = route_events[0].get("payload", {})
    assert payload["phase"] == "motion_gen"
    assert payload["reason"]
    assert "evidence" in payload


def test_orchestrator_static_shots_still_reported(tmp_path):
    """V3.8 缺陷 2 的告警语义不变。"""
    from hailuo_video.script_engine import SceneSpec, Shot, Storyboard

    sb = Storyboard(
        title="t", characters=[],
        scenes=[SceneSpec(id=1, location="x")],
        shots=[Shot(index=1, scene_id=1, shot_type="中景", description="d",
                    motion_complexity="static")],
    )
    agent = PipelineAgent(PipelineConfig(run_dir=tmp_path / "run"))
    ctx = {"storyboard": sb, "shot_count": 1, "intermediate_dir": tmp_path}
    agent._run_motion_gen_conditional(ctx)
    assert ctx.get("static_skipped_shots") == [0]


# ---------- 3. 双份 agent 注册表已收敛 ----------

def test_agent_lookup_is_derived_from_all_agents():
    """`_agent_lookup` 与 `ALL_AGENTS` 必须是**同一份**，不能各写一遍。"""
    from hailuo_video.agents import ALL_AGENTS

    agent = PipelineAgent(PipelineConfig(run_dir=Path("./run")))
    assert set(agent._agent_lookup) == {a.name for a in ALL_AGENTS}
    for name, inst in agent._agent_lookup.items():
        assert inst is next(a for a in ALL_AGENTS if a.name == name), (
            f"{name} 在两处注册表里不是同一个实例"
        )


# ---------- 4. DAG 校验 ----------

def test_default_dag_is_valid():
    """现有 DEFAULT_DAG 应当通过校验（顺序与依赖一致）。"""
    from hailuo_video.agents import ALL_AGENTS

    report = validate_dag(DEFAULT_DAG, known_agents=[a.name for a in ALL_AGENTS])
    assert report.ok, report.summary()
    assert report.order[0] == "prep"
    assert report.order[-1] == "qa"


def test_dag_detects_cycle():
    phases = [
        _Phase("a", inputs_from=("c",)),
        _Phase("b", inputs_from=("a",)),
        _Phase("c", inputs_from=("b",)),
    ]
    report = validate_dag(phases)
    assert not report.ok
    assert any(i.kind == "cycle" for i in report.issues)


def test_dag_detects_missing_dependency():
    phases = [_Phase("a", inputs_from=("nope",))]
    report = validate_dag(phases)
    assert not report.ok
    assert any(i.kind == "missing_dependency" for i in report.issues)


def test_dag_detects_order_violation():
    """声明顺序与依赖矛盾 —— 现在跑会拿到空产物，必须能被检出。"""
    phases = [
        _Phase("a", inputs_from=("b",)),
        _Phase("b"),
    ]
    report = validate_dag(phases)
    assert not report.ok
    assert any(i.kind == "order_violation" for i in report.issues)


def test_dag_detects_unknown_agent():
    phases = [_Phase("a", agent_names=("ghost",))]
    report = validate_dag(phases, known_agents=["real"])
    assert not report.ok
    assert any(i.kind == "unknown_agent" for i in report.issues)


def test_topological_order_respects_dependencies():
    phases = [
        _Phase("c", inputs_from=("b",)),
        _Phase("b", inputs_from=("a",)),
        _Phase("a"),
    ]
    order, issues = topological_order(phases)
    assert order.index("a") < order.index("b") < order.index("c")
    # 声明顺序违反依赖 → 应报 order_violation
    assert any(i.kind == "order_violation" for i in issues)


def test_dag_report_summary_is_readable():
    report = DagReport(ok=True, order=("a", "b"))
    assert "通过" in report.summary()


def test_orchestrator_records_dag_report_at_startup(tmp_path):
    """DAG 校验在启动时就跑（结果挂在 agent 上，便于测试与排查）。"""
    agent = PipelineAgent(PipelineConfig(run_dir=tmp_path / "run"))
    assert agent._dag_report is not None
    assert agent._dag_report.ok, agent._dag_report.summary()
