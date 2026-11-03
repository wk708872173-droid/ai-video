"""V5.0 Batch 4 — 可观测性（Run 上下文 + 指标聚合）。

## 问题

审计发现三套日志**各记各的、互不关联**：

| 日志 | 记什么 | 粒度 | 问题 |
|---|---|---|---|
| `run_dir/log.jsonl` | phase 级事件 + 降级告警 | phase | **没有 agent 级事件** —— `log.py` 的 docstring 举例 `"agent_completed"`，但全仓从未 emit 过 |
| `logs/{project_id}_llm_audit.jsonl` | LLM request/response | LLM 调用 | V5.0 Batch 1 之前**生产零产出**；且与 log.jsonl **无关联 ID** |
| `sandbox/.state/runs.jsonl` | V2-Local 本地推理 | 本地推理 | 与 V3/V4 生产链路**无关**（V2 遗留） |

后果：**一次 run 发生了什么，无法拼出完整链路**（Request → LLM → agent → artifact）。
客户第 1 优先级是「无人值守生产」—— 无人值守的前提是**能事后复盘**。

## 设计

- **`RunContext`**：`run_id` 贯穿 log.jsonl / audit jsonl / registry.json，
  让三套日志**能串起来**。
- **`Metrics`**：把 `PipelineStep` 聚合成可比较的指标
  （per-phase 耗时 / 成功率 / 成本估算）—— 这是 Batch 6 的 A/B 的基础。
- **`agent_completed` 事件**：补上 agent 级日志（log.py 早就声称有）。

## 不做的事（红线）

- **不引入 OpenTelemetry / LangSmith / Langfuse** —— 本地单用户脚本，
  没有多租户/分布式，`run_id` + jsonl 已经够用。加依赖不划算。
- **不改已有日志文件的格式** —— 只**追加** run_id 字段（老文件仍可读）。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable


def make_run_id(project_id: str = "", seed: str = "") -> str:
    """生成 run_id（短、可读、无外部依赖）。

    组成：project_id 前缀 + 时间戳 + 内容种子的哈希前缀。
    **不含随机源**（同 project + 同输入 → 同 id 便于复现比对）。
    """
    base = f"{project_id}|{seed}|{time.time():.6f}"
    return f"{project_id or 'run'}-{hashlib.sha256(base.encode()).hexdigest()[:8]}"


@dataclass
class RunContext:
    """一次 run 的关联上下文。"""

    run_id: str = ""
    project_id: str = ""
    started_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def log_fields(self) -> dict[str, Any]:
        """写进日志事件的公共字段。"""
        d: dict[str, Any] = {}
        if self.run_id:
            d["run_id"] = self.run_id
        if self.project_id:
            d["project_id"] = self.project_id
        return d


@dataclass
class PhaseMetric:
    """单个 phase 的聚合指标。"""

    name: str
    calls: int = 0
    ok: int = 0
    failed: int = 0
    total_seconds: float = 0.0
    max_seconds: float = 0.0

    @property
    def success_rate(self) -> float:
        return (self.ok / self.calls) if self.calls else 0.0

    @property
    def avg_seconds(self) -> float:
        return (self.total_seconds / self.calls) if self.calls else 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["success_rate"] = round(self.success_rate, 4)
        d["avg_seconds"] = round(self.avg_seconds, 3)
        return d


@dataclass
class Metrics:
    """一次 run 的聚合指标（进 `PipelineResult.metrics`）。"""

    run_id: str = ""
    total_steps: int = 0
    ok_steps: int = 0
    failed_steps: int = 0
    total_seconds: float = 0.0
    phases: dict[str, PhaseMetric] = field(default_factory=dict)
    cost_estimate: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        return (self.ok_steps / self.total_steps) if self.total_steps else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "total_steps": self.total_steps,
            "ok_steps": self.ok_steps,
            "failed_steps": self.failed_steps,
            "success_rate": round(self.success_rate, 4),
            "total_seconds": round(self.total_seconds, 3),
            "phases": {k: v.to_dict() for k, v in self.phases.items()},
            "cost_estimate": dict(self.cost_estimate),
            "extra": dict(self.extra),
        }


# 成本估算（**估算**，不是账单 —— 金额随套餐/汇率变）
#   依据：Hailuo 按秒计，image 按张计；这里只给量级，供 A/B 比较用。
COST_ESTIMATE = {
    "image_per_call": 1.0,     # 相对单位：一张图 = 1
    "video_per_second": 1.0,   # 相对单位：一秒视频 = 1
    "tts_per_call": 0.0,       # edge-tts 不计费
}


def aggregate(steps: Iterable[Any], *, run_id: str = "") -> Metrics:
    """把 `PipelineStep` 列表聚合成 `Metrics`。

    `step.name` 形如 `image_gen_001` / `qa_post` → phase 名取自
    `step.extra["phase"]`（若在）否则从名字前缀推断。
    """
    m = Metrics(run_id=run_id)
    for step in steps or []:
        name = str(getattr(step, "name", "") or "")
        ok = bool(getattr(step, "ok", False))
        dur = float(getattr(step, "duration_seconds", 0.0) or 0.0)
        extra = dict(getattr(step, "extra", {}) or {})
        phase = str(extra.get("phase") or _infer_phase(name))
        pm = m.phases.setdefault(phase, PhaseMetric(name=phase))
        pm.calls += 1
        pm.ok += 1 if ok else 0
        pm.failed += 0 if ok else 1
        pm.total_seconds += dur
        pm.max_seconds = max(pm.max_seconds, dur)
        m.total_steps += 1
        m.ok_steps += 1 if ok else 0
        m.failed_steps += 0 if ok else 1
        m.total_seconds += dur
    return m


def _infer_phase(step_name: str) -> str:
    """从 step 名推断 phase（`image_gen_001` → `image_gen`；`qa_post` → `qa_post`）。"""
    if not step_name:
        return "unknown"
    known = ("script_writer", "asset_prep", "image_gen", "motion_gen",
             "video_synth", "tts", "editing", "qa")
    for k in known:
        if step_name.startswith(k):
            return k
    return step_name


def estimate_cost(
    *,
    image_calls: int = 0,
    video_seconds: float = 0.0,
    tts_calls: int = 0,
) -> dict[str, Any]:
    """成本**估算**（相对单位，非账单）。

    为什么只给相对单位：真实金额随套餐/汇率/地区变，写死会误导。
    相对单位足够做 A/B 比较（"这次比上次贵了多少"）。
    """
    image_cost = image_calls * COST_ESTIMATE["image_per_call"]
    video_cost = video_seconds * COST_ESTIMATE["video_per_second"]
    tts_cost = tts_calls * COST_ESTIMATE["tts_per_call"]
    total = image_cost + video_cost + tts_cost
    return {
        "unit": "relative",
        "image_calls": image_calls,
        "video_seconds": round(video_seconds, 2),
        "tts_calls": tts_calls,
        "image": round(image_cost, 2),
        "video": round(video_cost, 2),
        "tts": round(tts_cost, 2),
        "total": round(total, 2),
    }


def write_run_meta(path: Path | str, meta: dict[str, Any]) -> Path:
    """把 run 元信息写进 registry.json 的 meta 块。

    **向后兼容**：老 registry.json 没有 meta 块 → `Registry.load_json` 不报错。
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    if not isinstance(data, dict):
        data = {}
    data["meta"] = dict(data.get("meta") or {})
    data["meta"].update(meta)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)
    return p


__all__ = [
    "COST_ESTIMATE",
    "Metrics",
    "PhaseMetric",
    "RunContext",
    "aggregate",
    "estimate_cost",
    "make_run_id",
    "write_run_meta",
]
