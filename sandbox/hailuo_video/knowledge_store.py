"""V3.8 W4 — 运动参数知识库（REQ-406）+ 检索增强提示词优化。

## 问题

`docs/PRD.md:428` 写着"运动参数知识库（`knowledge/` 目录存在但 `motion_params.json`
不存在）"。仓库里散落着大量**已验证**的参数经验（各 `sample/*/README.md` 的复盘），
但它们只是散文，**无法被程序消费**。

## 文献依据

- **arXiv:2504.11739「RAPO」/ arXiv:2510.20206「RAPO++」**：检索增强的提示词优化
  —— 双分支（content 分支保语义 / format 分支对齐训练分布），从 **relation graph**
  检索修饰词。→ 本项目的 `knowledge/motion_params.json` 就是那张 relation graph。
- **arXiv:2608.17468「SAGE」**：自演化分镜技能（已部署框架）。三个挑战：
  知识获取（行话隐含在范例里）、知识精炼（写死的知识没对着产出评估）、知识注入
  （全塞进上下文超限，逐组手选不可扩展）→ 方案是 learn / attribute / evolve /
  **route**。→ 本模块的 `search()` + `related()` 就是"路由"。

## 实现取舍（红线）

检索用**关键词 + 关系边**，**纯 stdlib** —— 不引入向量库 / embedding / torch
（本项目主链路依赖只有 pillow / imageio-ffmpeg / edge-tts，见 `pyproject.toml:18-22`）。

CJK 用**字符二元组**切词（无分词器依赖），拉丁文按词切。
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# 知识库默认路径（仓库 knowledge/ 目录，与 decisions.md / lessons.md 同级）
DEFAULT_KNOWLEDGE_PATH = Path("knowledge/motion_params.json")

SCHEMA_VERSION = "1.0"

# 关系类型（SAGE 的"路由"依据）
RELATION_TYPES: tuple[str, ...] = (
    "same_scene",       # 同场景
    "same_character",   # 同角色
    "same_style",       # 同画风
    "same_motion",      # 同运镜
    "same_backend",     # 同生成后端
)


@dataclass
class MotionParamRecord:
    """一条运动参数记录（一次 shot 生成的"配方 + 结果"）。"""
    id: str = ""
    project_id: str = ""
    shot_id: str = ""
    camera_motion: str = ""
    motion_complexity: str = "static"
    duration: float = 6.0
    resolution: str = "1080P"
    aspect_ratio: str = "16:9"
    backend: str = "minimax"
    subject_action: str = ""
    style_anchor_hash: str = ""
    prompt_positive: str = ""
    prompt_negative: str = ""
    color_lock: str = ""
    quality: dict[str, float] = field(default_factory=dict)
    outcome: str = "ok"          # "ok" | "failed" | "stale"
    lesson: str = ""             # 这条记录沉淀下来的经验（一句话）
    created_at: str = ""
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["quality"] = dict(self.quality)
        d["tags"] = list(self.tags)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MotionParamRecord":
        known = {f for f in cls.__dataclass_fields__}          # type: ignore[attr-defined]
        kwargs = {k: v for k, v in data.items() if k in known}
        kwargs.setdefault("tags", [])
        kwargs.setdefault("quality", {})
        return cls(**kwargs)


def _tokenize(text: str) -> set[str]:
    """切词：拉丁按词，CJK 按字符二元组（无分词器依赖）。"""
    if not text:
        return set()
    out: set[str] = set()
    for word in re.findall(r"[a-z0-9]+", str(text).lower()):
        out.add(word)
    cjk = re.findall(r"[一-鿿]+", str(text))
    for run in cjk:
        out.add(run)
        for i in range(len(run) - 1):
            out.add(run[i:i + 2])
    return out


class MotionParamsStore:
    """运动参数知识库（relation graph）。

    用法：
        store = MotionParamsStore()
        hits = store.search("推近 暖光", limit=5)
        near = store.related("mp_0001")
    """

    def __init__(self, path: Path | str = DEFAULT_KNOWLEDGE_PATH) -> None:
        self.path = Path(path)
        self.records: list[MotionParamRecord] = []
        self.relations: list[dict[str, Any]] = []
        self._loaded = False

    # ---------- 读写 ----------

    def load(self, *, force: bool = False) -> "MotionParamsStore":
        """读库。文件不存在 → 空库（**不抛错**：库是可选增强）。

        注意：已经 `add()` 过记录时**不会**被 load 覆盖 —— `add()` 会把
        `_loaded` 置 True（"内存里的是最新的"）。否则"先 add 再 search"
        会把内存记录清空（历史 bug：add 后 search 永远返回空）。
        """
        if self._loaded and not force:
            return self
        self._loaded = True
        self.records = []
        self.relations = []
        if not self.path.is_file():
            return self
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return self
        for raw in data.get("records", []) or []:
            if isinstance(raw, dict):
                self.records.append(MotionParamRecord.from_dict(raw))
        self.relations = [r for r in (data.get("relations") or []) if isinstance(r, dict)]
        return self

    def save(self) -> Path:
        """写库（原子写：tmp + replace）。"""
        payload = {
            "_schema_version": SCHEMA_VERSION,
            "_doc": (
                "运动参数知识库（REQ-406）。relation graph 供检索增强提示词优化使用。"
                "字段说明见 sandbox/hailuo_video/knowledge_store.py。"
            ),
            "records": [r.to_dict() for r in self.records],
            "relations": list(self.relations),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)
        return self.path

    # ---------- 写入 ----------

    def add(self, record: MotionParamRecord) -> MotionParamRecord:
        """加一条记录（id 为空则自动生成）。"""
        if not record.id:
            record.id = self._next_id()
        self.records.append(record)
        self._loaded = True      # 内存里的是最新的 → 后续 load() 不再覆盖
        return record

    def _next_id(self) -> str:
        return f"mp_{len(self.records) + 1:04d}"

    def relate(self, from_id: str, to_id: str, rel_type: str = "same_scene",
               weight: float = 0.8) -> None:
        """加一条关系边（RAPO 的 relation graph）。"""
        self.relations.append({
            "from": from_id, "to": to_id, "type": rel_type, "weight": weight,
        })
        self._loaded = True

    # ---------- 检索 ----------

    def search(self, query: str, *, limit: int = 5) -> list[MotionParamRecord]:
        """关键词检索（纯 stdlib；CJK 字符二元组 + 拉丁词）。

        打分 = 命中 token 数 / sqrt(记录 token 数)，再按 outcome 轻微加权
        （"ok" 的经验优先）。
        """
        self.load()
        q_tokens = _tokenize(query)
        if not q_tokens:
            return []
        scored: list[tuple[float, MotionParamRecord]] = []
        for rec in self.records:
            haystack = " ".join([
                rec.camera_motion, rec.subject_action, rec.lesson,
                rec.color_lock, " ".join(rec.tags), rec.motion_complexity,
                rec.backend, rec.aspect_ratio,
            ])
            r_tokens = _tokenize(haystack)
            if not r_tokens:
                continue
            overlap = len(q_tokens & r_tokens)
            if not overlap:
                continue
            score = overlap / (len(r_tokens) ** 0.5)
            if rec.outcome == "ok":
                score *= 1.15
            elif rec.outcome == "failed":
                score *= 0.85
            scored.append((score, rec))
        scored.sort(key=lambda t: -t[0])
        return [rec for _, rec in scored[:max(0, limit)]]

    def related(self, record_id: str, *, limit: int = 5) -> list[MotionParamRecord]:
        """按 relation graph 取邻居记录（SAGE 的知识路由）。"""
        self.load()
        by_id = {r.id: r for r in self.records}
        out: list[tuple[float, MotionParamRecord]] = []
        for rel in self.relations:
            if rel.get("from") == record_id:
                target = by_id.get(str(rel.get("to", "")))
            elif rel.get("to") == record_id:
                target = by_id.get(str(rel.get("from", "")))
            else:
                continue
            if target is not None:
                out.append((float(rel.get("weight", 0.5)), target))
        out.sort(key=lambda t: -t[0])
        return [rec for _, rec in out[:max(0, limit)]]

    def lessons(self, query: str = "", *, limit: int = 5) -> list[str]:
        """取经验条目（一句话）—— 用于注入 prompt 的"格式分支"。"""
        return [r.lesson for r in self.search(query, limit=limit) if r.lesson]

    def get(self, record_id: str) -> MotionParamRecord | None:
        self.load()
        for r in self.records:
            if r.id == record_id:
                return r
        return None

    def __len__(self) -> int:
        self.load()
        return len(self.records)


def record_result(
    store: MotionParamsStore,
    *,
    shot: Any = None,
    prompt_positive: str = "",
    prompt_negative: str = "",
    quality: dict[str, float] | None = None,
    outcome: str = "ok",
    lesson: str = "",
    tags: list[str] | None = None,
    project_id: str = "",
    backend: str = "",
    save: bool = True,
) -> MotionParamRecord:
    """把一次 shot 生成的结果记进知识库（**纯本地写，无 API 消耗**）。"""
    def _g(obj: Any, name: str, default: Any = "") -> Any:
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(name, default)
        return getattr(obj, name, default)

    rec = MotionParamRecord(
        project_id=project_id,
        shot_id=str(_g(shot, "shot_id", "") or ""),
        camera_motion=str(_g(shot, "camera_motion", "") or ""),
        motion_complexity=str(_g(shot, "motion_complexity", "static") or "static"),
        duration=float(_g(shot, "expected_duration", 6.0) or 6.0),
        subject_action=str(_g(shot, "subject_action", "") or ""),
        color_lock=str(_g(shot, "color_lock", "") or ""),
        prompt_positive=prompt_positive,
        prompt_negative=prompt_negative,
        quality=dict(quality or {}),
        outcome=outcome,
        lesson=lesson,
        tags=list(tags or []),
        backend=backend,
    )
    store.add(rec)
    if save:
        try:
            store.save()
        except OSError:
            pass          # 知识库写失败不该让主流程崩
    return rec


# ---------- 读侧：检索增强提示词注入（V5.0 Batch 1 接线） ----------

def format_lessons(lessons: list[str], *, prefix: str = "[经验]") -> str:
    """把经验条目格式化成可注入 prompt 的一段文本。

    空列表 → 空串（调用方直接跳过，不污染 prompt）。

    **顺序敏感**：经验放在 prompt **最前面**。这不是审美选择 ——
    知识库自己的 `mp_0005` 就是这么写的：
    「色锁写在 description 末尾会失效（模型仍画成两只奶黄）——
    色彩约束必须放在 prompt 开头」。
    """
    items = [str(x).strip() for x in lessons if str(x).strip()]
    if not items:
        return ""
    return " ".join(f"{prefix}{x}" for x in items)


def retrieve_lessons(
    store: MotionParamsStore,
    query: str,
    *,
    limit: int = 3,
    prefix: str = "[经验]",
) -> str:
    """检索经验 → 格式化成 prompt 片段（一步到位）。

    V5.0 Batch 1：此前 `lessons()` / `search()` / `related()` **生产零调用**
    （只有 tests 引用）—— 知识库"只写不读"，积累的经验从不回灌。
    这条读侧通路现在接到 `orchestrator._build_task` 的 image_gen / motion_gen 分支。
    """
    try:
        lessons = store.lessons(query, limit=limit)
    except Exception:
        # 知识库读失败不该让主流程崩（它是**增强**不是**依赖**）
        return ""
    return format_lessons(lessons, prefix=prefix)


__all__ = [
    "DEFAULT_KNOWLEDGE_PATH",
    "RELATION_TYPES",
    "SCHEMA_VERSION",
    "MotionParamRecord",
    "MotionParamsStore",
    "format_lessons",
    "record_result",
    "retrieve_lessons",
]
