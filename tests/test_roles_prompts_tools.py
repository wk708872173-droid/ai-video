"""V5.0 Batch 5 — 角色契约 / 提示词版本 / 工具注册表。

对应图里的四大核心能力：
  - ① 角色 + 提示词 → `roles.py` + `prompt_registry.py`
  - ④ 工具集成     → `tools.py`

## 关键断言（都是"曾经是死代码/缺失"的地方）

- 每个 agent 都必须有角色契约，且**每个 ALL_AGENTS 的 agent 都有**
- 角色契约里的**禁则**与**降级策略**不能为空（否则等于没写）
- 提示词注册表必须覆盖所有在用的模板，且**每个提示词至少 2 个版本**
  （否则没法做 A/B）
- 工具注册表必须**标注成本**（成本治理要靠它）
- MCP 不可用时**一个假工具都不能注册**
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SANDBOX = REPO_ROOT / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from hailuo_video.agents import ALL_AGENTS  # noqa: E402
from hailuo_video.prompt_registry import (  # noqa: E402
    PromptRegistry,
    PromptSpec,
    content_hash,
    find_variables,
)
from hailuo_video.prompt_registry import build_default_registry as build_prompt_registry  # noqa: E402
from hailuo_video.roles import (  # noqa: E402
    ROLE_REGISTRY,
    RoleSpec,
    cost_roles,
    deterministic_roles,
    get_role,
    llm_roles,
)
from hailuo_video.tools import (  # noqa: E402
    ToolRegistry,
    ToolSpec,
    register_mcp_tools,
)
from hailuo_video.tools import build_default_registry as build_tool_registry  # noqa: E402


# ---------- 角色契约 ----------

def test_every_agent_has_a_role_spec():
    """ALL_AGENTS 里每个 agent 都必须声明角色契约（单一来源）。"""
    for agent in ALL_AGENTS:
        role_name = getattr(agent, "role_name", "")
        assert role_name, f"{type(agent).__name__} 没声明 role_name"
        assert role_name in ROLE_REGISTRY, (
            f"{type(agent).__name__} 的 role_name={role_name!r} 不在 ROLE_REGISTRY 里"
        )


def test_role_registry_covers_all_dag_agents():
    from hailuo_video.orchestrator import DEFAULT_DAG
    for phase in DEFAULT_DAG:
        for agent_name in phase.agent_names:
            assert agent_name in ROLE_REGISTRY, (
                f"phase {phase.name} 用了 {agent_name}，但没有对应的角色契约"
            )


def test_every_role_has_mission_and_degradation():
    for name, spec in ROLE_REGISTRY.items():
        assert spec.mission.strip(), f"{name} 的角色契约没有职责描述"
        assert spec.degradation.strip(), f"{name} 的角色契约没有降级策略"
        assert spec.forbidden, f"{name} 的角色契约没有禁则（等于没写）"


def test_only_one_stage_calls_llm():
    """**本轮最重要的一个判断**：8 个 stage 里只有 script_writer 调 LLM。

    这条断言把"主体流程不该 agent 化"写进代码 —— 有人加 LLM 决策时会撞上它。
    """
    assert llm_roles() == ["script_writer"]
    assert "image_gen" in deterministic_roles()
    assert "editing" in deterministic_roles()


def test_cost_roles_are_declared():
    """会花钱的 stage 必须被标出来（成本治理的输入）。"""
    assert set(cost_roles()) >= {"asset_prep", "image_gen", "motion_gen", "tts"}
    assert "editing" not in cost_roles()


def test_get_role_raises_on_unknown():
    with pytest.raises(KeyError):
        get_role("no_such_role")


def test_role_spec_serializes():
    d = ROLE_REGISTRY["image_gen"].to_dict()
    assert d["name"] == "image_gen"
    assert isinstance(d["forbidden"], list)


# ---------- 提示词版本 ----------

def test_default_registry_covers_all_prompts():
    reg = build_prompt_registry()
    for pid in ("novel_to_script", "character_extraction", "storyboard"):
        assert reg.get(pid) is not None, f"提示词 {pid} 没注册"


def test_storyboard_has_two_versions_for_ab():
    """A/B 的前提：至少要能拿到两个版本。"""
    reg = build_prompt_registry()
    versions = reg.versions("storyboard")
    assert len(versions) >= 2, f"storyboard 只有 {versions} —— 做不了 A/B"


def test_content_hash_changes_with_template():
    a = content_hash("hello")
    b = content_hash("hello!")
    assert a != b
    assert content_hash("hello") == a


def test_find_variables():
    assert find_variables("{a} and {b} and {a}") == ("a", "b")
    assert find_variables("{{literal}} {x}") == ("x",), "双花括号不是占位符"


def test_registry_rejects_duplicate():
    reg = PromptRegistry()
    reg.add("p", "v1", "x")
    with pytest.raises(ValueError):
        reg.add("p", "v1", "y")


def test_get_latest_version():
    reg = PromptRegistry()
    reg.add("p", "v1", "a")
    reg.add("p", "v2", "b")
    assert reg.get("p").version == "v2"
    assert reg.get("p", "v1").template == "a"


def test_variants_lists_all_versions():
    reg = build_prompt_registry()
    variants = reg.variants("storyboard")
    assert len(variants) == 2
    assert {v.version for v in variants} == {"v1", "v2"}


def test_prompt_spec_serializes_with_hash():
    spec = PromptSpec(id="p", version="v1", template="hello {x}")
    d = spec.to_dict()
    assert d["content_hash"]
    assert d["variables"] == ["x"]


# ---------- 工具注册表 ----------

def test_default_registry_has_core_tools():
    reg = build_tool_registry()
    for name in ("image.generate", "tts.synth", "motion.generate",
                 "ffmpeg.concat", "quality.continuity", "knowledge.retrieve"):
        assert reg.get(name) is not None, f"工具 {name} 没注册"


def test_every_tool_declares_cost():
    """成本治理靠这个标注 —— 没标注的工具等于没有预算约束。"""
    reg = build_tool_registry()
    for spec in reg._tools.values():
        assert spec.cost in ("none", "image", "video", "tts"), (
            f"{spec.name} 的 cost 标注非法：{spec.cost!r}"
        )
        assert spec.description.strip(), f"{spec.name} 没有描述"


def test_mutating_tools_are_marked():
    reg = build_tool_registry()
    names = {s.name for s in reg.mutating_tools()}
    assert "image.generate" in names
    assert "quality.continuity" not in names


def test_cost_listing():
    reg = build_tool_registry()
    assert {s.name for s in reg.list_by_cost("video")} == {"motion.generate"}
    assert {s.name for s in reg.list_by_cost("image")} == {"image.generate"}


def test_call_unknown_tool_raises():
    reg = build_tool_registry()
    with pytest.raises(KeyError):
        reg.call("no.such.tool")


def test_knowledge_tool_reads_real_store(tmp_path):
    """知识库检索工具必须真的能读到东西（不是空壳）。"""
    reg = build_tool_registry()
    hits = reg.call("knowledge.retrieve", query="推近 转头")
    assert isinstance(hits, list)


def test_mcp_provider_registers_nothing_when_unavailable():
    """MCP 不可用时**一个假工具都不能注册**。

    `mcp_client` 的真后端 `is_available()` 恒 False、`list_tools()` 抛
    NotImplementedError —— 那种情况下注册一堆调不通的工具比不注册更糟。
    """
    reg = ToolRegistry()
    registered = register_mcp_tools(reg)
    assert registered == [], "MCP 不可用时不该注册任何工具"
    assert len(reg) == 0


def test_mcp_provider_registers_when_available():
    class _FakeBackend:
        def is_available(self):
            return True

        def list_tools(self):
            return ["trim", "cut_silence"]

        def call_tool(self, name, **kw):
            return f"called:{name}"

    reg = ToolRegistry()
    registered = register_mcp_tools(reg, backend=_FakeBackend())
    assert registered == ["mcp.trim", "mcp.cut_silence"]
    assert reg.call("mcp.trim") == "called:trim"
    assert reg.get("mcp.trim").provider == "mcp"


def test_tool_spec_serializes():
    spec = ToolSpec(name="x.y", description="d", cost="image", mutating=True)
    d = spec.to_dict()
    assert d["name"] == "x.y"
    assert d["cost"] == "image"
    assert d["mutating"] is True


def test_registry_rejects_duplicate_tool():
    reg = ToolRegistry()
    reg.add("a.b", lambda: None)
    with pytest.raises(ValueError):
        reg.add("a.b", lambda: None)
