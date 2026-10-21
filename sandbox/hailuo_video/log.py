"""V3.0 Phase 1 — StructuredLogger：run_dir/log.jsonl + stderr tail。

每个 Agent 在 execute() 关键点 emit LogEvent（"phase_started" / "agent_completed" /
"fallback_used" / "consistency_low" 等），Orchestrator 汇总到 run_dir/log.jsonl。

CLI 用 `format_for_stderr()` 打印紧凑进度行（每个 phase 一行）。
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class LogEvent:
    """结构化日志事件。"""
    event_type: str                       # "phase_started" / "agent_completed" / "fallback_used" / "consistency_low" 等
    timestamp: str = ""                   # ISO timestamp；空 = 自动填
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LogEvent":
        return cls(
            event_type=data["event_type"],
            timestamp=data.get("timestamp", ""),
            payload=data.get("payload", {}),
        )


class StructuredLogger:
    """Append-only jsonl writer；CI-friendly 错误处理。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else None

    def event(self, event_type: str, **payload: Any) -> LogEvent:
        """Emit 一个 LogEvent。Returns 事件便于 testing。"""
        import datetime
        ts = payload.pop("timestamp", "") or datetime.datetime.now().isoformat(timespec="seconds")
        ev = LogEvent(event_type=event_type, timestamp=ts, payload=payload)
        if self.path is not None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n")
            except OSError:
                pass
        return ev

    def tail(self, n: int = 20) -> list[LogEvent]:
        """读最后 n 行 events。"""
        if self.path is None or not self.path.is_file():
            return []
        events: list[LogEvent] = []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
            for line in lines[-n:]:
                try:
                    events.append(LogEvent.from_dict(json.loads(line)))
                except json.JSONDecodeError:
                    pass
        except OSError:
            pass
        return events

    def format_for_stderr(self, events: list[LogEvent] | None = None) -> str:
        """紧凑单行格式：time | event_type | payload key=val。"""
        if events is None:
            events = self.tail()
        lines: list[str] = []
        for ev in events:
            kv = " ".join(f"{k}={v}" for k, v in ev.payload.items())
            line = f"[log] {ev.timestamp} {ev.event_type}"
            if kv:
                line += f" | {kv}"
            lines.append(line)
        return "\n".join(lines)
