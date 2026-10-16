"""V3.5 P3：LLM Request → Payload → Response audit log（CLAUDE.md §5）。

CLAUDE.md §5 推荐：
  - Audit logging (Request → LLM Payload → Response)
  - PII smoke scans

设计目标：
  - 每次调用 MiniMax API（image / video / tts）前记录 request
  - 调用完成后记录 response + 耗时 + 状态
  - 自动 PII redaction（API key 字符串脱敏）
  - 按 project_id 隔离的 JSONL 日志（logs/{project_id}_llm_audit.jsonl）

用法：
  from hailuo_video.audit_llm import audit_request, audit_response, get_audit_logger

  audit_request(
      project_id="abc123",
      endpoint="image_generation",
      payload={"prompt": "..."},
  )
  # ... call MiniMax ...
  audit_response(
      project_id="abc123",
      endpoint="image_generation",
      payload={"request": {...}},  # 原始 request（已脱敏）
      response_summary={"task_id": "..."},  # 截断响应（避免大文件）
      duration_seconds=1.4,
      ok=True,
  )

JSONL 格式：
  {"timestamp": "...", "kind": "request"|"response", "project_id": "...",
   "endpoint": "...", "payload_keys": [...], "payload_hash": "...",
   "response_summary": {...}, "duration_seconds": 1.4, "ok": true}
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


# ---------- PII / 密钥 redaction ----------

# 模式：值要 redact（不是 key）。keys 保留（用于 debug）
REDACT_VALUE_PATTERNS = [
    (r"sk-cp-[a-zA-Z0-9_-]{20,}", "[REDACTED_API_KEY]"),
    (r"sk-proj[a-zA-Z0-9]{20,}", "[REDACTED_API_KEY]"),
    (r"xox[abp]-[a-zA-Z0-9-]{20,}", "[REDACTED_TOKEN]"),
    (r"AKIA[0-9A-Z]{16}", "[REDACTED_AWS_KEY]"),
    (r"ghp_[a-zA-Z0-9]{36}", "[REDACTED_GITHUB_TOKEN]"),
    # bearer / authorization 用 lookbehind 处理：值是 1+ 个非空白
    (r"(?i)(bearer|authorization)\s+[a-zA-Z0-9_.\-]{8,}", r"\1 [REDACTED_BEARER]"),
]


def redact(payload: Any) -> Any:
    """递归 redact dict / list / str 中的密钥值。

    - str: regex 替换 REDACT_VALUE_PATTERNS
    - dict: 递归 redact values，keys 保留
    - list: 递归 redact 元素
    - 其他: 原样返回
    """
    if isinstance(payload, str):
        s = payload
        for pat, repl in REDACT_VALUE_PATTERNS:
            s = re.sub(pat, repl, s)
        return s
    if isinstance(payload, dict):
        return {k: redact(v) for k, v in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [redact(x) for x in payload]
    return payload


def hash_short(payload: Any) -> str:
    """Payload 摘要（用于查重 / 调试，不暴露原文）。"""
    canonical = json.dumps(redact(payload), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


# ---------- Audit event dataclass ----------

@dataclass
class AuditEvent:
    """V3.5 P3：单次 LLM 调用事件。"""
    timestamp: str                      # ISO 8601
    kind: str                           # "request" | "response" | "error"
    project_id: str = ""
    endpoint: str = ""                  # e.g. "image_generation"
    payload_keys: list[str] = field(default_factory=list)
    payload_hash: str = ""              # sha256[:16]
    payload_preview: Any = None         # truncated (≤256 chars) redacted payload preview
    response_summary: dict[str, Any] = field(default_factory=dict)
    duration_seconds: float = 0.0
    ok: bool = True
    error_code: str = ""                # for kind="error"

    def to_jsonl(self) -> str:
        """单行 JSON（不含 payload 全文）。"""
        d = asdict(self)
        # truncate preview
        if d.get("payload_preview") is not None:
            d["payload_preview"] = str(d["payload_preview"])[:256]
        return json.dumps(d, ensure_ascii=False, default=str)


# ---------- AuditLogger ----------

class AuditLogger:
    """V3.5 P3：按 project_id 隔离的 JSONL audit logger。

    路径：logs/{project_id}_llm_audit.jsonl
    设计：直接写文件，避开 stdlib logging 的 handler 缓存（跨测试会指向旧 tmp_path）。
    """
    DEFAULT_LOG_DIR = Path("logs")

    def __init__(self, project_id: str, log_dir: Path | None = None):
        if not project_id or len(project_id) < 4:
            raise ValueError(f"project_id 无效（需 ≥4 字符）：{project_id!r}")
        self.project_id = project_id
        self.log_dir = log_dir or self.DEFAULT_LOG_DIR
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.log_dir / f"{project_id}_llm_audit.jsonl"

    def write(self, event: AuditEvent) -> None:
        """追加写一行 JSONL（同步 flush）。"""
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(event.to_jsonl() + "\n")
            f.flush()  # pytest 不等容 — 立即落盘

    def read_all(self) -> list[dict[str, Any]]:
        """读所有历史事件（debug 用）。"""
        if not self.log_path.is_file():
            return []
        events = []
        for line in self.log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return events


# ---------- Convenience helpers ----------

def _now_iso() -> str:
    import datetime
    return datetime.datetime.now().isoformat(timespec="seconds")


def audit_request(
    project_id: str,
    endpoint: str,
    payload: dict[str, Any],
    *,
    log_dir: Path | None = None,
) -> str:
    """记录 request 事件。返回 payload_hash（用于 response 时关联）。"""
    payload_hash = hash_short(payload)
    event = AuditEvent(
        timestamp=_now_iso(),
        kind="request",
        project_id=project_id,
        endpoint=endpoint,
        payload_keys=sorted(payload.keys()) if isinstance(payload, dict) else [],
        payload_hash=payload_hash,
        payload_preview=truncate(redact(payload)),
    )
    AuditLogger(project_id, log_dir).write(event)
    return payload_hash


def audit_response(
    project_id: str,
    endpoint: str,
    payload: dict[str, Any] | None,
    response_summary: dict[str, Any],
    *,
    duration_seconds: float,
    ok: bool = True,
    error_code: str = "",
    log_dir: Path | None = None,
) -> None:
    """记录 response 事件。"""
    payload_hash = hash_short(payload) if payload else ""
    event = AuditEvent(
        timestamp=_now_iso(),
        kind="response" if ok else "error",
        project_id=project_id,
        endpoint=endpoint,
        payload_keys=sorted(payload.keys()) if isinstance(payload, dict) else [],
        payload_hash=payload_hash,
        payload_preview=truncate(redact(payload)) if payload else None,
        response_summary=redact(response_summary) if isinstance(response_summary, dict) else {},
        duration_seconds=duration_seconds,
        ok=ok,
        error_code=error_code,
    )
    AuditLogger(project_id, log_dir).write(event)


def truncate(payload: Any, *, max_chars: int = 200) -> Any:
    """简化版 payload（list[str] 截断）。"""
    if isinstance(payload, dict):
        out = {}
        for k, v in payload.items():
            sv = str(v)
            if len(sv) > max_chars:
                out[k] = sv[:max_chars] + "...(truncated)"
            else:
                out[k] = sv
        return out
    if isinstance(payload, str):
        if len(payload) > max_chars:
            return payload[:max_chars] + "...(truncated)"
        return payload
    return payload


def audit_chat_call(
    project_id: str,
    endpoint: str,
    payload: dict[str, Any],
    *,
    log_dir: Path | None = None,
) -> str:
    """V3.5 P3：便利函数 — 调用 audit_request 并返回 payload_hash。

    用法：在 `MiniMaxChatBackend.chat()` 顶部调一次 → 拿 hash → 调用
    `audit_response(..., ok=True/False, error_code=...)` 时关联同一个 hash。

    Returns:
        payload_hash (sha256[:16])。空字符串表示未打 audit（project_id 为空）。
    """
    if not project_id:
        return ""
    return audit_request(project_id, endpoint, payload, log_dir=log_dir)


__all__ = [
    "AuditEvent",
    "AuditLogger",
    "audit_request",
    "audit_response",
    "audit_chat_call",
    "redact",
    "hash_short",
    "truncate",
]