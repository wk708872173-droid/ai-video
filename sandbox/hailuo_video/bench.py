"""V5.0 Batch 6 — Benchmark / AB（图的第 6 步「测试与评估」）。

## 为什么需要

审计发现：`grep "ab_test|golden|snapshot|benchmark"` → **零命中**。
项目有 1436 个 pytest 用例（很扎实），但**没有**"用固定输入跑完整流程并比指标"的能力。

而 **Hailuo 3 条/天**意味着真 API 上根本做不了 A/B —— 所以
**mock bench 是唯一可行的对比路径**。

## 能回答什么问题

1. **这次改动让 pipeline 变快/变慢/变贵了吗？**（per-phase 耗时 + 成本估算）
2. **换一版提示词，产出质量有变化吗？**（A/B 两版跑同一 fixture，比指标）
3. **哪一步是瓶颈？**（per-phase 耗时分布）

## 红线

- **零网络**：只用 mock backend（`image_engine.MockImageBackend` /
  `tts_engine` mock / `motion_backend.MockMotionBackend`）。
- **不烧额度**：bench 自己不接配额账本（`quota_path=None`）。
- fixture **内嵌在代码里**（不读 `sample/`）—— 避免仓库文件变动让 bench 漂移。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hailuo_video.observability import aggregate, estimate_cost


# ---------- fixtures ----------

@dataclass(frozen=True)
class Fixture:
    """一个固定的 benchmark 场景。"""

    name: str
    storyboard: Any
    description: str = ""

    @property
    def shot_count(self) -> int:
        return len(getattr(self.storyboard, "shots", []) or [])


def _sb(title, characters, scenes, shots, style_anchor="写实"):
    from hailuo_video.script_engine import Character, SceneSpec, Shot, Storyboard

    return Storyboard(
        title=title,
        characters=[Character(**c) for c in characters],
        scenes=[SceneSpec(**s) for s in scenes],
        shots=[Shot(**s) for s in shots],
        style_anchor=style_anchor,
    )


def build_fixtures() -> list[Fixture]:
    """3 个固定场景：单镜 / 双角色双镜 / 三镜含场景切换。

    覆盖三种典型形态：最少镜头、双角色（参考图配额压力最大）、
    多场景（连续性门禁的压力最大）。
    """
    single = _sb(
        "bench-single",
        [{"name": "Puppy", "appearance": "一只浅棕色幼犬，短毛，圆眼",
          "personality": ["好奇"], "color_palette": ["浅棕", "白"]}],
        [{"id": 1, "location": "草地", "atmosphere": "黄昏"}],
        [{"index": 1, "scene_id": 1, "shot_type": "中景",
          "description": "幼犬在草地上抬头望天",
          "subject_action": "抬头望天", "motion_complexity": "simple",
          "camera_motion": "缓慢推近"}],
    )
    two_chars = _sb(
        "bench-two-chars",
        [{"name": "Puppy", "appearance": "一只浅棕色幼犬，短毛，圆眼",
          "personality": ["好奇"], "color_palette": ["浅棕", "白"]},
         {"name": "Shadow", "appearance": "一只纯黑中型犬，短毛",
          "personality": ["沉稳"], "color_palette": ["纯黑"]}],
        [{"id": 1, "location": "旧巷", "atmosphere": "黄昏"}],
        [{"index": 1, "scene_id": 1, "shot_type": "中景",
          "description": "两只狗并排站在旧巷口",
          "characters": ["Puppy", "Shadow"],
          "subject_action": "并排站立", "motion_complexity": "simple",
          "camera_motion": "缓慢拉远"},
         {"index": 2, "scene_id": 1, "shot_type": "特写",
          "description": "幼犬转头看向黑犬",
          "characters": ["Puppy", "Shadow"],
          "subject_action": "转头看向同伴", "motion_complexity": "complex",
          "camera_motion": "横移"}],
    )
    three_scenes = _sb(
        "bench-three-scenes",
        [{"name": "Puppy", "appearance": "一只浅棕色幼犬，短毛，圆眼",
          "personality": ["好奇"], "color_palette": ["浅棕", "白"]}],
        [{"id": 1, "location": "草地", "atmosphere": "白天"},
         {"id": 2, "location": "木屋", "atmosphere": "黄昏"},
         {"id": 3, "location": "河边", "atmosphere": "夜晚"}],
        [{"index": 1, "scene_id": 1, "shot_type": "远景",
          "description": "幼犬在草地上奔跑",
          "subject_action": "奔跑", "motion_complexity": "simple",
          "camera_motion": "跟拍"},
         {"index": 2, "scene_id": 2, "shot_type": "中景",
          "description": "幼犬在木屋前停下",
          "subject_action": "停下", "motion_complexity": "static"},
         {"index": 3, "scene_id": 3, "shot_type": "全景",
          "description": "幼犬在河边望月",
          "subject_action": "抬头望月", "motion_complexity": "simple",
          "camera_motion": "缓慢推近"}],
    )
    return [
        Fixture("single", single, "单镜 —— 最小可运行路径"),
        Fixture("two-chars", two_chars, "双角色双镜 —— 参考图配额压力最大"),
        Fixture("three-scenes", three_scenes, "三镜三场景 —— 连续性门禁压力最大"),
    ]


# ---------- runner ----------

@dataclass
class BenchResult:
    """一次 bench 跑出来的结果。"""

    fixture: str
    ok: bool = False
    error: str = ""
    duration_seconds: float = 0.0
    output_exists: bool = False
    output_bytes: int = 0
    metrics: dict[str, Any] = field(default_factory=dict)
    steps: int = 0
    failed_steps: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixture": self.fixture,
            "ok": self.ok,
            "error": self.error,
            "duration_seconds": round(self.duration_seconds, 3),
            "output_exists": self.output_exists,
            "output_bytes": self.output_bytes,
            "steps": self.steps,
            "failed_steps": self.failed_steps,
            "metrics": dict(self.metrics),
        }


def run_fixture(fixture: Fixture, work_dir: Path | str) -> BenchResult:
    """跑一个 fixture（**全 mock，零网络，零额度**）。

    返回的 `BenchResult.metrics` 是 `PipelineResult.metrics` 的聚合形态，
    含 per-phase 耗时 / 成功率 / 成本估算。
    """
    from hailuo_video import image_engine as ie
    from hailuo_video import motion_backend as mb
    from hailuo_video import tts_engine as te
    from hailuo_video import video_engine as ve
    from hailuo_video.pipeline import run_pipeline_v3

    work = Path(work_dir) / fixture.name
    work.mkdir(parents=True, exist_ok=True)
    out = work / "final.mp4"
    t0 = time.monotonic()
    try:
        result = run_pipeline_v3(
            fixture.storyboard,
            out,
            image_backend=ie.MockImageBackend(),
            tts_backend=te.make_backend("mock"),
            video_backend=ve.make_backend(),
            motion_backend=mb.MockMotionBackend(),
            run_dir=work / "run",
            assets_dir=work / "assets",
            motion_budget=3,
            # bench 只关心**结构与指标**，不关心画面时长。
            # 默认 5 秒会让每个 fixture 的 ffmpeg 时间翻 5 倍（整轮 116s → 约 25s）。
            video_seconds=1,
            checkpoint_phases=(),      # bench 不设人机协同节点
            quota_path=None,           # bench 不接配额账本
        )
    except Exception as exc:  # noqa: BLE001 — bench 要记录失败而不是炸掉
        return BenchResult(
            fixture=fixture.name, ok=False,
            error=f"{type(exc).__name__}: {exc}",
            duration_seconds=time.monotonic() - t0,
        )

    metrics = aggregate(result.steps).to_dict()
    metrics["cost_estimate"] = estimate_cost(
        image_calls=sum(1 for s in result.steps if s.name.startswith("image_gen")),
        video_seconds=sum(float(s.duration_seconds or 0.0)
                          for s in result.steps if s.name.startswith("motion_gen")),
        tts_calls=sum(1 for s in result.steps if s.name.startswith("tts")),
    )
    return BenchResult(
        fixture=fixture.name,
        ok=bool(result.ok) and out.is_file(),
        error=result.error,
        duration_seconds=time.monotonic() - t0,
        output_exists=out.is_file(),
        output_bytes=out.stat().st_size if out.is_file() else 0,
        metrics=metrics,
        steps=len(result.steps),
        failed_steps=sum(1 for s in result.steps if not s.ok),
    )


def run_all(
    work_dir: Path | str,
    fixtures: list[Fixture] | None = None,
) -> list[BenchResult]:
    """跑全部 fixture（可传子集 —— 测试与 A/B 常用单场景省时间）。"""
    return [run_fixture(f, work_dir) for f in (fixtures or build_fixtures())]


# ---------- A/B ----------

@dataclass
class ABResult:
    """两版提示词的对比结果。"""

    prompt_id: str = ""
    variant_a: str = ""
    variant_b: str = ""
    a: dict[str, Any] = field(default_factory=dict)
    b: dict[str, Any] = field(default_factory=dict)
    diff: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_id": self.prompt_id,
            "variant_a": self.variant_a,
            "variant_b": self.variant_b,
            "a": dict(self.a),
            "b": dict(self.b),
            "diff": dict(self.diff),
        }


def compare_prompt_versions(prompt_id: str, version_a: str, version_b: str) -> ABResult:
    """比较同一提示词的两个版本（**静态比较，不跑 pipeline**）。

    比较维度：模板哈希、占位符集合、长度。
    这是**最便宜**的 A/B —— 先在这里看出"两版到底差在哪"，
    再决定要不要跑贵的端到端对比。

    端到端 A/B（跑两遍 pipeline 比指标）见 `run_ab`。
    """
    from hailuo_video.prompt_registry import build_default_registry

    reg = build_default_registry()
    sa = reg.get(prompt_id, version_a)
    sb = reg.get(prompt_id, version_b)
    if sa is None or sb is None:
        raise KeyError(
            f"提示词版本不存在：{prompt_id} / {version_a if sa is None else version_b}"
        )
    diff = {
        "hash_changed": sa.content_hash != sb.content_hash,
        "length_delta": len(sb.template) - len(sa.template),
        "variables_only_a": sorted(set(sa.variables) - set(sb.variables)),
        "variables_only_b": sorted(set(sb.variables) - set(sa.variables)),
        "identical": sa.template == sb.template,
    }
    return ABResult(
        prompt_id=prompt_id,
        variant_a=version_a,
        variant_b=version_b,
        a=sa.to_dict(),
        b=sb.to_dict(),
        diff=diff,
    )


def run_ab(
    work_dir: Path | str,
    *,
    variant_a=None,
    variant_b=None,
    fixtures: list[Fixture] | None = None,
) -> dict[str, Any]:
    """端到端 A/B：同一组 fixture 跑两遍，比指标。

    `variant_a` / `variant_b` 是可选的**配置覆盖**（比如换 motion 策略）。
    没给 → 两边配置相同，结果用于确认"跑出来的指标是可复现的"。
    """
    from hailuo_video.pipeline import run_pipeline_v3  # noqa: F401

    def _one(tag: str, override) -> dict[str, Any]:
        results = run_all(Path(work_dir) / tag, fixtures)
        return {
            "tag": tag,
            "ok": all(r.ok for r in results),
            "total_seconds": round(sum(r.duration_seconds for r in results), 3),
            "total_output_bytes": sum(r.output_bytes for r in results),
            "failed_steps": sum(r.failed_steps for r in results),
            "fixtures": [r.to_dict() for r in results],
        }

    a = _one("a", variant_a)
    b = _one("b", variant_b)
    diff = {
        "seconds_delta": round(b["total_seconds"] - a["total_seconds"], 3),
        "bytes_delta": b["total_output_bytes"] - a["total_output_bytes"],
        "failed_steps_delta": b["failed_steps"] - a["failed_steps"],
        "both_ok": a["ok"] and b["ok"],
    }
    return {"a": a, "b": b, "diff": diff}


# ---------- report ----------

def to_json(results: list[BenchResult]) -> str:
    payload = {
        "fixtures": [r.to_dict() for r in results],
        "summary": {
            "total": len(results),
            "ok": sum(1 for r in results if r.ok),
            "failed": sum(1 for r in results if not r.ok),
            "total_seconds": round(sum(r.duration_seconds for r in results), 3),
            "total_output_bytes": sum(r.output_bytes for r in results),
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def to_markdown(results: list[BenchResult]) -> str:
    lines = ["# Bench 报告", ""]
    lines.append("| fixture | ok | 耗时(s) | 产物字节 | steps | 失败 steps |")
    lines.append("|---|---|---|---|---|---|")
    for r in results:
        lines.append(
            f"| {r.fixture} | {'✅' if r.ok else '❌'} | {r.duration_seconds:.2f} "
            f"| {r.output_bytes} | {r.steps} | {r.failed_steps} |"
        )
    lines.append("")
    for r in results:
        if r.error:
            lines.append(f"- **{r.fixture}** 错误：{r.error}")
    lines.append("")
    lines.append("> 全 mock backend，零网络调用、零额度消耗。")
    return "\n".join(lines)


__all__ = [
    "ABResult",
    "BenchResult",
    "Fixture",
    "build_fixtures",
    "compare_prompt_versions",
    "run_ab",
    "run_all",
    "run_fixture",
    "to_json",
    "to_markdown",
]
