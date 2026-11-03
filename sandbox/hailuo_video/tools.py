r"""V5.0 Batch 5 — 工具注册表（能力④ 工具集成）。

## 问题

审计发现：

> `agents.py` 顶部 **11 个直接 import**（`from hailuo_video import asset_prep as ap_mod` …），
> 调用方式是硬编码的静态函数调用（`ap_mod.prepare_all_characters(...)`）。
> `grep "tools=\[|function_call|tool_choice|json_schema"` → **零命中** —— 无 function-calling。
> `mcp_client.py`（266 行）**整个模块零引用**（真后端 `raise NotImplementedError`）。

后果：
- 工具**没有统一抽象** —— 加一个新能力要改 agent 的 import 与调用
- **MCP 集成是死代码** —— 写了 266 行，从没被任何生产路径调用过
- 没有"这个工具花不花钱"的声明 → 成本治理只能靠 agent 名硬编码

## 设计

`ToolSpec` 把工具变成**声明式**条目：

| 字段 | 含义 |
|---|---|
| `name` | 唯一标识（如 `image.generate`） |
| `description` | 人 / LLM 可读的说明 |
| `parameters` | JSON schema（参数契约） |
| `handler` | 实际执行体 |
| `cost` | 计费影响：none / image / video / tts |
| `mutating` | 是否**花钱或改文件**（决定要不要先过配额） |
| `timeout` | 超时（秒） |

`ToolRegistry` 提供 register / get / describe / list_by_cost。

## 关于 function-calling

**刻意不做**。本项目 8 个 stage 全确定性（见 `roles.py`），
没有 LLM 需要"选择工具"。引入 function-calling 会：
- 让确定性管道多一层不可复现的模型决策
- 违反本轮红线「不改 8 个 stage 的确定性语义」

工具注册表的真实价值是：**声明化 + 成本标注 + MCP 可插拔**，
不是"让 LLM 调工具"。

## MCP 的处置

`mcp_client` 的实现**不动**，本模块把它当 **provider** 收编：
`McpToolProvider` 从 `AIEditingBackend.list_tools()` 拿工具清单，
**不可用时一个都不注册**（而不是注册一堆调不通的假工具）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable


@dataclass(frozen=True)
class ToolSpec:
    """一个工具的声明式描述。"""

    name: str
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)   # JSON schema
    handler: Callable[..., Any] | None = None
    cost: str = "none"                 # none / image / video / tts
    mutating: bool = False             # 是否花钱或改文件
    timeout: int = 300
    provider: str = "builtin"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": dict(self.parameters),
            "cost": self.cost,
            "mutating": self.mutating,
            "timeout": self.timeout,
            "provider": self.provider,
        }


class ToolRegistry:
    """工具注册表。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> ToolSpec:
        if spec.name in self._tools:
            raise ValueError(f"工具已注册：{spec.name!r}")
        self._tools[spec.name] = spec
        return spec

    def add(self, name: str, handler: Callable[..., Any], **kwargs: Any) -> ToolSpec:
        return self.register(ToolSpec(name=name, handler=handler, **kwargs))

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def describe(self) -> list[dict[str, Any]]:
        """导出工具清单（给 LLM 看 / 给文档用）。"""
        return [self._tools[n].to_dict() for n in self.names()]

    def list_by_cost(self, cost: str) -> list[ToolSpec]:
        return [s for s in self._tools.values() if s.cost == cost]

    def mutating_tools(self) -> list[ToolSpec]:
        return [s for s in self._tools.values() if s.mutating]

    def call(self, name: str, **kwargs: Any) -> Any:
        """按名调用。未知工具 → KeyError（不静默返回 None）。"""
        spec = self._tools.get(name)
        if spec is None:
            raise KeyError(f"未知工具：{name!r}；已注册：{self.names()}")
        if spec.handler is None:
            raise ValueError(f"工具 {name!r} 没有 handler")
        return spec.handler(**kwargs)

    def __len__(self) -> int:
        return len(self._tools)


# ---------- MCP provider（收编 mcp_client，不改其实现） ----------

def register_mcp_tools(
    registry: ToolRegistry,
    backend: Any = None,
) -> list[str]:
    """把 MCP 后端的工具登记进 registry。

    **不可用时一个都不注册** —— 而不是注册一堆调不通的假工具
    （`mcp_client` 的真后端 `raise NotImplementedError`、`is_available()` 恒 False）。

    返回登记成功的工具名列表。
    """
    if backend is None:
        try:
            from hailuo_video import mcp_client
            backend = mcp_client.AIEditingBackend()
        except Exception:
            return []
    try:
        if not backend.is_available():
            return []
        tools = backend.list_tools()
    except Exception:
        # NotImplementedError / 网络问题 → 不注册（不静默注册假工具）
        return []

    registered: list[str] = []
    for tool in tools or []:
        tname = str(tool if isinstance(tool, str) else tool.get("name", ""))
        if not tname:
            continue
        full = f"mcp.{tname}"
        if registry.get(full) is not None:
            continue
        registry.register(ToolSpec(
            name=full,
            description=f"MCP 工具：{tname}",
            parameters={},
            handler=(lambda *a, _b=backend, _n=tname, **kw: _b.call_tool(_n, **kw)),
            cost="none",
            mutating=True,
            provider="mcp",
        ))
        registered.append(full)
    return registered


# ---------- 默认注册表（把现有确定性能力登记成工具） ----------

def build_default_registry() -> ToolRegistry:
    """登记项目现有的确定性能力。

    这些能力**本来就存在**（散落在各 engine 里），本注册表只是给它们一个
    **声明式的、可查询的、带成本标注的**入口 —— 不改变任何调用路径。
    """
    reg = ToolRegistry()

    def _image_generate(prompt: str, **opts: Any) -> Any:
        from hailuo_video import image_engine as ie
        backend = opts.pop("backend", None) or ie.make_backend("auto")
        return backend.generate(prompt, **opts)

    def _tts_synth(text: str, **opts: Any) -> Any:
        from hailuo_video import tts_engine as te
        backend = opts.pop("backend", None) or te.make_backend("auto")
        return backend.synth(text, **opts)

    def _motion_generate(prompt: str, **opts: Any) -> Any:
        from hailuo_video import motion_backend as mb
        backend = opts.pop("backend", None) or mb.make_motion_backend("minimax")
        return backend.generate(prompt, **opts)

    def _concat(**opts: Any) -> Any:
        from hailuo_video import concat_engine as ce
        return ce.concat_pipeline(**opts)

    def _to_vertical(**opts: Any) -> Any:
        from scripts import lib_ffmpeg  # type: ignore[import-not-found]
        return lib_ffmpeg.to_vertical(**opts)

    def _continuity(**opts: Any) -> Any:
        from hailuo_video import continuity
        return continuity.evaluate_shot_continuity(**opts)

    def _knowledge_retrieve(query: str, **opts: Any) -> Any:
        from hailuo_video import knowledge_store as ks
        path = opts.pop("path", None) or ks.DEFAULT_KNOWLEDGE_PATH
        store = ks.MotionParamsStore(path).load()
        return [r.to_dict() for r in store.search(query, limit=opts.get("limit", 5))]

    reg.register(ToolSpec(
        name="image.generate",
        description="文本 + 参考图 → 首帧画面（计费：按张）",
        parameters={
            "type": "object",
            "required": ["prompt"],
            "properties": {
                "prompt": {"type": "string"},
                "aspect_ratio": {"type": "string"},
                "subject_references": {"type": "array", "items": {"type": "string"}},
            },
        },
        handler=_image_generate,
        cost="image",
        mutating=True,
        timeout=180,
    ))
    reg.register(ToolSpec(
        name="tts.synth",
        description="文本 → 配音 + 字幕（edge-tts，不计费）",
        parameters={
            "type": "object",
            "required": ["text"],
            "properties": {"text": {"type": "string"}, "voice": {"type": "string"}},
        },
        handler=_tts_synth,
        cost="tts",
        mutating=True,
        timeout=120,
    ))
    reg.register(ToolSpec(
        name="motion.generate",
        description="首帧 → 真 AI 视频（计费：按秒，Hailuo 3 条/天）",
        parameters={
            "type": "object",
            "required": ["prompt"],
            "properties": {
                "prompt": {"type": "string"},
                "first_frame_image": {"type": "string"},
                "duration": {"type": "integer"},
            },
        },
        handler=_motion_generate,
        cost="video",
        mutating=True,
        timeout=600,
    ))
    reg.register(ToolSpec(
        name="ffmpeg.concat",
        description="分镜视频 + 音频 + 字幕 → 成片（本地 ffmpeg，不计费）",
        parameters={"type": "object", "properties": {}},
        handler=_concat,
        cost="none",
        mutating=True,
        timeout=900,
    ))
    reg.register(ToolSpec(
        name="ffmpeg.to_vertical",
        description="横屏 → 竖屏（主体感知裁剪，不计费）",
        parameters={"type": "object", "properties": {}},
        handler=_to_vertical,
        cost="none",
        mutating=True,
        timeout=300,
    ))
    reg.register(ToolSpec(
        name="quality.continuity",
        description="跨镜连续性度量（本地计算，不计费）",
        parameters={"type": "object", "properties": {}},
        handler=_continuity,
        cost="none",
        mutating=False,
        timeout=120,
    ))
    reg.register(ToolSpec(
        name="knowledge.retrieve",
        description="检索运动参数知识库（本地读，不计费）",
        parameters={
            "type": "object",
            "required": ["query"],
            "properties": {"query": {"type": "string"}},
        },
        handler=_knowledge_retrieve,
        cost="none",
        mutating=False,
        timeout=30,
    ))
    # V5.0 Batch 5：**收编 mcp_client**（此前整个模块零生产引用）。
    #   不可用时**一个都不注册** —— 不注册调不通的假工具。
    #   这样将来真 MCP 后端可用时，工具会自动出现在清单里，无需改调用方。
    register_mcp_tools(reg)
    return reg


__all__ = [
    "ToolRegistry",
    "ToolSpec",
    "build_default_registry",
    "register_mcp_tools",
]
