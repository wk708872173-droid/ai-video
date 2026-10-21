"""本地视频 runs.jsonl 解析 + 漂亮打印。

V2-Local：每条记录是单次本地 inference（替代旧 submit/poll/download 三段网络调用）。

R1：解析失败的单行跳过，不抛。runs.jsonl 缺文件 → 返回空列表。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from hailuo_video.local_client import SANDBOX_RUNTIME


@dataclass
class CallRecord:
    ts: str
    op: str
    ok: bool
    source: str
    error: str = ""
    extra: dict | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "CallRecord":
        extra = data.get("extra")
        return cls(
            ts=str(data.get("ts", "")),
            op=str(data.get("op", "")),
            ok=bool(data.get("ok", False)),
            source=str(data.get("source", "")),
            error=str(data.get("error", "")),
            extra=extra if isinstance(extra, dict) else None,
        )


def load_calls(runtime_dir: Path = SANDBOX_RUNTIME) -> list[CallRecord]:
    """读整个 runs.jsonl。文件缺 / 解析坏行跳过。"""
    path = runtime_dir / "runs.jsonl"
    if not path.is_file():
        return []
    out: list[CallRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            out.append(CallRecord.from_dict(data))
    return out


def tail_calls(
    n: int = 20,
    *,
    op: str = "",
    runtime_dir: Path = SANDBOX_RUNTIME,
) -> list[CallRecord]:
    """取最近 n 条（按文件中顺序 = 时间顺序）。可选按 op 过滤（V2 仅有 op=inference）。"""
    if n <= 0:
        return []
    records = load_calls(runtime_dir)
    if op:
        records = [r for r in records if r.op == op]
    return records[-n:]


def format_calls(records: Iterable[CallRecord]) -> str:
    """人类可读。✓ / ✗ 标记 ok，附 source / error。"""
    lines: list[str] = []
    for r in records:
        marker = "✓" if r.ok else "✗"
        extra_summary = ""
        if r.extra:
            keys = sorted(r.extra.keys())
            if keys:
                extra_summary = f" [{', '.join(keys)}]"
        err_part = f"  err={r.error}" if r.error else ""
        lines.append(
            f"{r.ts}  {marker} {r.op:>9}  src={r.source:<14}{extra_summary}{err_part}"
        )
    return "\n".join(lines)