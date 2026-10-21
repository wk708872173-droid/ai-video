"""V3.0 Phase 1 — 数据层：Artifact / Registry / AgentTask。

Pipeline 多 Agent 架构的状态契约：
  - Artifact: Agent 产出物（文件路径 + 内容哈希 + 元数据）
  - Registry: in-memory dict + JSON 持久化（原子写）
  - AgentTask: Orchestrator → Agent 的入参信封

设计原则：filesystem 是 PRIMARY contract（已有），Registry 是 INDEX。
所有 Agent 走 filesystem 通信；Registry 仅供 Orchestrator 跟踪 "哪些 phase 已完成"。
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


# ---------- Artifact ----------

@dataclass
class Artifact:
    """Agent 产出物：canonical filesystem path + content_hash + producer metadata。"""
    artifact_id: str                # ULID 或 sha256(path)[:16]
    kind: str                       # "image" / "video" / "audio" / "subtitle"
                                    # / "character_ref" / "scene_ref" / "manifest"
    path: Path                      # canonical filesystem path
    content_hash: str = ""          # sha256(file bytes)；空 = 未计算
    producer: str = ""             # 哪个 Agent 产出
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["path"] = str(self.path)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Artifact":
        return cls(
            artifact_id=data["artifact_id"],
            kind=data["kind"],
            path=Path(data["path"]),
            content_hash=data.get("content_hash", ""),
            producer=data.get("producer", ""),
            metadata=data.get("metadata", {}),
        )


def compute_content_hash(path: Path | str) -> str:
    """sha256 of file bytes。接受 Path 或 str。"""
    p = Path(path) if not isinstance(path, Path) else path
    if not p.is_file():
        return ""
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------- AgentTask ----------

@dataclass
class AgentTask:
    """Orchestrator → Agent 的入参信封。"""
    agent_id: str                   # "image_gen" / "video_synth" / etc.
    task_id: str                    # 唯一 ID（一次 run 内）
    inputs: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)   # RunContext 序列化形式


# ---------- Registry ----------

@dataclass
class Registry:
    """In-memory artifact 索引 + JSON 持久化（原子写）。"""
    artifacts: dict[str, Artifact] = field(default_factory=dict)
    by_kind: dict[str, list[str]] = field(default_factory=dict)   # kind → [artifact_id]

    def add(self, artifact: Artifact, *, compute_hash: bool = True) -> None:
        if compute_hash and artifact.content_hash == "":
            artifact.content_hash = compute_content_hash(artifact.path)
        self.artifacts[artifact.artifact_id] = artifact
        self.by_kind.setdefault(artifact.kind, [])
        if artifact.artifact_id not in self.by_kind[artifact.kind]:
            self.by_kind[artifact.kind].append(artifact.artifact_id)

    def get(self, artifact_id: str) -> Artifact | None:
        return self.artifacts.get(artifact_id)

    def by_kind_iter(self, kind: str) -> list[Artifact]:
        ids = self.by_kind.get(kind, [])
        return [self.artifacts[i] for i in ids if i in self.artifacts]

    def exists(self, artifact_id: str) -> bool:
        return artifact_id in self.artifacts

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifacts": {aid: a.to_dict() for aid, a in self.artifacts.items()},
            "by_kind": dict(self.by_kind),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Registry":
        reg = cls()
        for aid, ad in data.get("artifacts", {}).items():
            reg.artifacts[aid] = Artifact.from_dict(ad)
        reg.by_kind = {k: list(v) for k, v in data.get("by_kind", {}).items()}
        return reg

    def save_json(self, path: Path) -> Path:
        """原子写：tmp + os.replace（沿用 state.py 模式）。"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".registry-", suffix=".json", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        return path

    @classmethod
    def load_json(cls, path: Path) -> "Registry":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)

    @classmethod
    def load_json_or_empty(cls, path: Path) -> "Registry":
        if not Path(path).is_file():
            return cls()
        try:
            return cls.load_json(path)
        except (OSError, json.JSONDecodeError):
            return cls()
