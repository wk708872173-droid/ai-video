"""V3.2 REQ-303.e — AI 编辑 MCP 客户端（CutStudio / SeeCut Stub）。

设计原则：
  - ABC: AIEditingBackend（call_tool + auto_edit + list_tools）
  - Mock: 单测 / 离线演示，不发任何网络请求
  - Real stub: CutStudioMCPBackend（HTTP MCP 端点）/ SeeCutSkillBackend（本地 CLI 调用）
    **任一后端不可用 / 凭据缺失 → 编辑错误，硬失败**（CLAUDE.md R4：不静默 fallback 到 mock）
  - 真接入待用户后续选 CutStudio / SeeCut 并部署对应服务

参考：
  - CutStudio: macOS 剪辑软件，本地暴露 MCP 端点 `http://127.0.0.1:4877/mcp`
    提供 ~90 种剪辑工具（转写 / 跳剪 / 去口误 / 加字幕 / 音乐闪避 等）
  - SeeCut: Coding Agent 的 Skill，能"看"视频（Gemini 多模态），自动编排 + 渲染 + 质检

接口契约（最小可测）：
    backend = make_ai_editing_backend("mock")
    tools = backend.list_tools()                  # → ["trim", "cut_silence", ...]
    result = backend.call_tool("trim", {
        "video": "in.mp4", "start": 1.5, "end": 8.0, "output": "out.mp4",
    })
    # → EditResult(success, output_path, metadata)
"""
from __future__ import annotations

import abc
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, ClassVar


# ---------- Errors / Data ----------

class AIEditingError(RuntimeError):
    """AI 编辑通用错。"""


class AIEditingUnavailable(AIEditingError):
    """AI 编辑后端不可用（服务未启动 / 端点不可达 / 凭据缺失）。

    CLAUDE.md R4：不静默降级到 mock；必须显式提示用户启用服务或选其它后端。
    """


@dataclass
class EditResult:
    """单次 AI 编辑调用结果。"""
    success: bool = False
    output_path: str = ""
    tool_name: str = ""
    duration_seconds: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------- ABC ----------

class AIEditingBackend(abc.ABC):
    """AI 编辑后端抽象基类。"""

    name: ClassVar[str] = ""

    @abc.abstractmethod
    def list_tools(self) -> list[str]:
        """列出后端支持的工具名（如 ["trim", "cut_silence", "auto_edit"]）。"""

    @abc.abstractmethod
    def call_tool(self, tool_name: str, args: dict[str, Any], *, timeout: int = 300) -> EditResult:
        """调用单个工具。失败抛 AIEditingError。"""

    @abc.abstractmethod
    def is_available(self) -> bool:
        """后端是否可用（端点可达 / 凭据齐全 / CLI 在 PATH）。"""

    def auto_edit(
        self,
        video_path: str | Path,
        prompt: str,
        *,
        output_path: str | Path | None = None,
        timeout: int = 1800,
    ) -> EditResult:
        """用自然语言 prompt 描述需要的剪辑，后端自主编排工具调用并产出最终视频。

        默认实现：要求后端暴露 `auto_edit` 工具；否则 raise。
        覆盖：SeeCut 这种 Skill 类后端在内部实现多步编排，不需要单次 MCP 工具调用。
        """
        if "auto_edit" not in self.list_tools():
            raise AIEditingError(
                f"后端 {self.name!r} 不支持 auto_edit；可用工具：{self.list_tools()}"
            )
        return self.call_tool(
            "auto_edit",
            {
                "video": str(video_path),
                "prompt": prompt,
                "output": str(output_path) if output_path else "",
            },
            timeout=timeout,
        )


# ---------- Mock 后端 ----------

class MockAIEditingBackend(AIEditingBackend):
    """Mock 后端：不发任何网络请求，返回 fake EditResult。

    用于：
      - 单测
      - 端到端冒烟（无 CutStudio / SeeCut 时）
      - 演示 EditingAgent → AI 编辑链路的完整性

    不算"DONE"（CLAUDE.md R2：Mocks 不算真实端到端）。
    """

    name: ClassVar[str] = "mock"

    MOCK_TOOLS: ClassVar[list[str]] = [
        "trim", "cut_silence", "add_subtitle", "music_duck",
        "jump_cut", "speed_ramp", "auto_edit",
    ]

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []  # 单测审计

    def list_tools(self) -> list[str]:
        return list(self.MOCK_TOOLS)

    def call_tool(self, tool_name: str, args: dict[str, Any], *, timeout: int = 300) -> EditResult:
        if tool_name not in self.MOCK_TOOLS:
            raise AIEditingError(
                f"mock 后端不支持工具 {tool_name!r}；支持：{self.MOCK_TOOLS}"
            )
        self.calls.append({"tool": tool_name, "args": dict(args), "timeout": timeout})
        # 模拟输出文件路径
        in_video = args.get("video", "input.mp4")
        in_path = Path(str(in_video))
        output = args.get("output") or str(in_path.with_name(f"{in_path.stem}_{tool_name}.mp4"))
        return EditResult(
            success=True,
            output_path=output,
            tool_name=tool_name,
            duration_seconds=0.01,  # mock 极快
            metadata={
                "backend": "mock",
                "call_id": uuid.uuid4().hex[:8],
                "args_echo": dict(args),
            },
        )

    def is_available(self) -> bool:
        return True


# ---------- CutStudio MCP 后端（stub） ----------

class CutStudioMCPBackend(AIEditingBackend):
    """CutStudio 本地 MCP 后端（stub）。

    部署要求：
      - macOS 上跑 CutStudio 软件
      - CutStudio 暴露 MCP 端点 `http://127.0.0.1:4877/mcp`
      - 90+ 剪辑工具（trim / cut_silence / jump_cut / music_duck 等）

    实装策略：
      - 用 `requests` 调 JSON-RPC over HTTP（pip install requests）
      - 或用 `mcp` Python SDK（pip install mcp）
      - 当前未实装，凭 CUTSTUDIO_MCP_URL 配置后接入

    CLAUDE.md R4：服务不可用 → AIEditingUnavailable，不静默 fallback 到 mock。
    """

    name: ClassVar[str] = "cutstudio"
    DEFAULT_URL: ClassVar[str] = "http://127.0.0.1:4877/mcp"

    def __init__(self, url: str | None = None) -> None:
        self.url = url or self.DEFAULT_URL

    def list_tools(self) -> list[str]:
        # TODO: 真接入后通过 MCP protocol 拉工具列表
        raise NotImplementedError(
            "CutStudioMCPBackend.list_tools 实装待 MCP SDK 接入；"
            "Mock 后端用 `mock` 即可跑通端到端。"
        )

    def call_tool(self, tool_name: str, args: dict[str, Any], *, timeout: int = 300) -> EditResult:
        # TODO: 真接入后用 JSON-RPC over HTTP 调用 self.url
        raise NotImplementedError(
            f"CutStudioMCPBackend.call_tool({tool_name!r}) 实装待 MCP SDK 接入；"
            f"目标端点：{self.url}"
        )

    def is_available(self) -> bool:
        # TODO: 真接入后用 HEAD 请求 ping 端点
        return False  # stub 阶段默认不可用


# ---------- SeeCut Skill 后端（stub） ----------

class SeeCutSkillBackend(AIEditingBackend):
    """SeeCut Skill 后端（stub）。

    部署要求：
      - 用户机器上安装 SeeCut skill（给 Coding Agent 用）
      - SeeCut 内部用 Gemini 多模态"看"视频
      - 调用方式：通过 CLI 或内部 Python API（待 seeCut 团队确认）

    当前 stub 状态：未实装。SeeCutSkillBackend 需 SeeCut 团队明确接口。
    """

    name: ClassVar[str] = "seecut"

    def list_tools(self) -> list[str]:
        raise NotImplementedError(
            "SeeCutSkillBackend 实装待 SeeCut 团队公开 Python API。"
        )

    def call_tool(self, tool_name: str, args: dict[str, Any], *, timeout: int = 300) -> EditResult:
        raise NotImplementedError(
            f"SeeCutSkillBackend.call_tool({tool_name!r}) 实装待 SeeCut 团队公开接口。"
        )

    def is_available(self) -> bool:
        return False


# ---------- Factory ----------

_KNOWN_BACKENDS: dict[str, type[AIEditingBackend]] = {
    "mock": MockAIEditingBackend,
    "cutstudio": CutStudioMCPBackend,
    "seecut": SeeCutSkillBackend,
}


def make_ai_editing_backend(name: str, **kwargs: Any) -> AIEditingBackend:
    """按 name 选 backend。未知 name → AIEditingError。"""
    cls = _KNOWN_BACKENDS.get(name)
    if cls is None:
        raise AIEditingError(
            f"未知 AI editing backend：{name!r}；可选：{list(_KNOWN_BACKENDS)}"
        )
    return cls(**kwargs)


def list_ai_editing_backends() -> list[str]:
    return list(_KNOWN_BACKENDS)


def recommend_backend() -> str:
    """根据可用性推荐 backend。Mock 永远可用；真后端按 is_available() 选。

    用法：daily_cron → 自动选可用 backend → 没有真后端则 fallback 到 mock + `[WARN]`。
    注：fallback 到 mock 仍显式 `[WARN]`，不静默（CLAUDE.md R4）。
    """
    for name in ("cutstudio", "seecut", "mock"):
        try:
            b = make_ai_editing_backend(name)
            if b.is_available():
                return name
        except Exception:
            continue
    return "mock"  # 兜底
