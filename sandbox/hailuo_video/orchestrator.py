"""V3.0 Phase 3 — PipelineAgent orchestrator。

Sequential orchestrator + thin Blackboard (V3.0 plan 设计原则)。

DAG（8 phases）：
  prep → script → image_gen → motion_gen → video_synth → tts → editing → qa

各 phase 独立失败策略（plan §失败模型）：
  - Phase 0 失败 → best-effort + picker fallback（已有 V2.5.2 机制）
  - Phase 1/2/3 单 shot 失败 → skip-shot
  - Phase 4 失败 → HALT
  - Phase 5 失败 → warning
"""
from __future__ import annotations

import json
import logging
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from hailuo_video.agents import (
    AgentResult,
    AssetPrepAgent,
    EditingAgent,
    ImageGenAgent,
    InputValidationError,
    MotionGenerationAgent,
    QAAgent,
    ScriptWriterAgent,
    TTSAgent,
    VideoSynthAgent,
)
from hailuo_video.artifact import Artifact, Registry
from hailuo_video.log import LogEvent, StructuredLogger
from hailuo_video.retry import RetryPolicy, with_retry


# ---------- Public dataclass 保留旧 run_pipeline 字段兼容 ----------

@dataclass
class PipelineStep:
    """保留与 pipeline.py 旧 PipelineStep 同字段，CLI 兼容。"""
    name: str
    index: int
    ok: bool
    duration_seconds: float = 0.0
    error: str = ""
    output_path: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineResult:
    """保留与 pipeline.py 旧 PipelineResult 同字段 + V3 新增 consistency。"""
    output: str
    shot_count: int = 0
    ok_shots: int = 0
    failed_shots: int = 0
    duration_seconds: float = 0.0
    bytes: int = 0
    source: str = "ok"
    error: str = ""
    steps: list[PipelineStep] = field(default_factory=list)
    intermediate_dir: str = ""
    consistency: dict[str, Any] = field(default_factory=dict)   # V3 新增

    @property
    def ok(self) -> bool:
        """V3.2 自动开发冲刺（轮 6）补：与 pipeline.PipelineResult 接口对齐。"""
        return self.source == "ok"

    def to_dict(self) -> dict[str, Any]:
        return {
            "output": self.output,
            "shot_count": self.shot_count,
            "ok_shots": self.ok_shots,
            "failed_shots": self.failed_shots,
            "duration_seconds": self.duration_seconds,
            "bytes": self.bytes,
            "source": self.source,
            "error": self.error,
            "steps": [s.to_dict() if hasattr(s, "to_dict") else s.__dict__ for s in self.steps],
            "intermediate_dir": self.intermediate_dir,
            "consistency": dict(self.consistency),
        }


# ---------- PipelineConfig ----------

@dataclass
class PipelineConfig:
    """PipelineAgent.run() 的所有可配项。"""
    image_backend: Any = None               # image_engine.IImageBackend
    video_backend: Any = None               # video_engine.KenBurnsBackend
    tts_backend: Any = None                  # tts_engine.TTSBackend
    voice: str = "zh-CN-XiaoxiaoNeural"
    video_seconds: int = 5
    video_fps: int = 8
    video_effect: str = "cycle"
    aspect_ratio: str = "16:9"
    assets_dir: Path = Path("./assets")
    skip_asset_prep: bool = False
    keep_intermediate: bool = True
    parallel: int = 1
    motion_budget: int = 3                     # V3.1：MiniMax Max 默认每天 3 次 hailuo-2.3
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    log: StructuredLogger | None = None
    run_dir: Path = Path("./run")


# ---------- Phase 描述 ----------

@dataclass(frozen=True)
class Phase:
    """Orchestrator 调度单元。"""
    name: str
    agent_names: tuple[str, ...]                          # Agent 名列表（ALL_AGENTS 里查）
    inputs_from: tuple[str, ...] = ()                       # 依赖的 phase 名
    is_parallel: bool = False                                # Per-shot 并发
    halt_on_error: bool = False                              # True = 失败停止整个 pipeline
    conditional: bool = False                                # V3.1：True = per-shot 路由（按 shot.motion_complexity 筛）


# V3.2：8 phases（prep → script → {image_gen ∥ motion_gen ∥ video_synth ∥ tts} → editing → qa）。
# - `script` 是新增的 serial phase，依赖 prep 产出（Master Reference），输出 Storyboard JSON
# - `concat` 改名为 `editing`（V3.2 §终极目标 后期处理统一入口）
# - 依赖关系：image_gen / motion_gen / video_synth / tts 都依赖 script；editing 依赖以上 4 个；qa 依赖 editing
DEFAULT_DAG: tuple[Phase, ...] = (
    Phase("prep", ("asset_prep",), halt_on_error=True),
    Phase("script", ("script_writer",), inputs_from=("prep",), halt_on_error=True),
    Phase("image_gen", ("image_gen",), inputs_from=("script",), is_parallel=True),
    Phase("motion_gen", ("motion_gen",), inputs_from=("image_gen",), is_parallel=True, conditional=True),
    Phase("video_synth", ("video_synth",), inputs_from=("motion_gen", "image_gen"), is_parallel=True),
    Phase("tts", ("tts",), inputs_from=("image_gen",), is_parallel=True),
    Phase("editing", ("editing",), inputs_from=("motion_gen", "video_synth", "tts", "image_gen"), halt_on_error=True),
    Phase("qa", ("qa",), inputs_from=("editing",)),
)


# ---------- PipelineAgent ----------

class PipelineAgent:
    """V3.0 orchestrator：sequential DAG + thin Blackboard + Registry。"""

    def __init__(
        self,
        config: PipelineConfig,
        *,
        resume_from: str | None = None,
        load_registry: bool = True,
    ) -> None:
        """
        Args:
            config: PipelineConfig
            resume_from: 跳过此 phase 之前的所有 phase（DEFAULT_DAG 顺序；None = 从头开始）
            load_registry: 若 True（默认），从 config.run_dir/registry.json 加载已有 registry
                           作为续跑上下文；False 时强制空 registry 从头开始
        """
        self.config = config
        self.resume_from = resume_from
        self.registry = Registry()
        if load_registry:
            reg_path = config.run_dir / "registry.json"
            if reg_path.is_file():
                # 损坏文件硬失败（CLAUDE.md R4：不静默降级）
                existing = Registry.load_json(reg_path)
                for art in existing.artifacts.values():
                    self.registry.add(art)
        self.logger = config.log or StructuredLogger(config.run_dir / "log.jsonl")
        self._agent_lookup = {
            "script_writer": ScriptWriterAgent(),
            "asset_prep": AssetPrepAgent(),
            "image_gen": ImageGenAgent(),
            "motion_gen": MotionGenerationAgent(),
            "video_synth": VideoSynthAgent(),
            "tts": TTSAgent(),
            "editing": EditingAgent(),
            "qa": QAAgent(),
        }

    def _resolve_resume_index(self) -> int:
        """把 self.resume_from 名字解析为 DEFAULT_DAG 索引；不匹配 → 0。"""
        if not self.resume_from:
            return 0
        for i, p in enumerate(DEFAULT_DAG):
            if p.name == self.resume_from:
                return i
        return 0

    def _motion_mp4_fallback(self, intermediate_dir: Path, output: Path) -> Path | None:
        """V3.4 ADR-002：editing 失败时用 motion_gen 产物作为 final。

        寻找顺序（按优先级）：
          a. shot_000_motion.mp4（hailuo 真视频）
          b. shot_000_kenburns.mp4（Ken Burns 兜底）
        找到 → 复制到 output → 返回复制后路径；否则 None。
        """
        candidates = [
            intermediate_dir / "shot_000_motion.mp4",
            intermediate_dir / "shot_000_kenburns.mp4",
        ]
        for src in candidates:
            if src.is_file() and src.stat().st_size > 0:
                output.parent.mkdir(parents=True, exist_ok=True)
                import shutil
                shutil.copy2(src, output)
                return output
        return None

    def _script_already_loaded(self, ctx: dict) -> bool:
        """V3.2 自动开发冲刺（轮 6）：判断 script phase 是否可跳过。

        条件：
          - ctx["storyboard"] 已有 shots（Storyboard 已从 storyboard.json 加载）
          - shots 非空（至少 1 个）
        返回 True 时 script_writer 无需跑（输入就是完整 Storyboard）。
        """
        sb = ctx.get("storyboard")
        if sb is None:
            return False
        shots = getattr(sb, "shots", None)
        return bool(shots) and len(shots) > 0

    def run_phase(self, phase: Phase, ctx: dict[str, Any]) -> list[PipelineStep]:
        """执行一个 phase，返回该 phase 所有 step。

        - conditional=True（V3.1）：per-shot 路由 — 用 _should_skip_shot() 判断；跳过即没 step
        - is_parallel=True + parallel>1 → per-shot ThreadPoolExecutor fan-out
        - is_parallel=True + parallel==1 → per-shot 串行
        - is_parallel=False → 单次（不 per-shot 循环）
        """
        from concurrent.futures import ThreadPoolExecutor
        steps: list[PipelineStep] = []

        # V3.1 conditional phase（按 per-shot 路由）
        if phase.conditional and phase.name == "motion_gen":
            return self._run_motion_gen_conditional(ctx)

        # V3.2 自动开发冲刺（轮 6）：script phase 条件跳过——若 storyboard 已预加载，
        # 且 shots 非空，则 script_writer 无需跑（Storyboard 已是输入）。
        # 行为：跳过整个 script phase（不调 script_writer），并在 log 留 event。
        if phase.name == "script" and self._script_already_loaded(ctx):
            self.logger.event("phase_skipped", phase="script", reason="storyboard_pre_loaded")
            return []

        if not phase.is_parallel:
            for agent_name in phase.agent_names:
                task = self._build_task(agent_name, 0, ctx, phase)
                steps.append(self._run_agent_safe(agent_name, task, phase.name, 0))
            return steps

        if self.config.parallel > 1:
            with ThreadPoolExecutor(max_workers=self.config.parallel) as ex:
                futures = []
                for shot_idx in range(ctx["shot_count"]):
                    for agent_name in phase.agent_names:
                        task = self._build_task(agent_name, shot_idx, ctx, phase)
                        futures.append(ex.submit(self._run_agent_safe, agent_name, task, phase.name, shot_idx))
                for fut in futures:
                    steps.append(fut.result())
        else:
            for shot_idx in range(ctx["shot_count"]):
                for agent_name in phase.agent_names:
                    task = self._build_task(agent_name, shot_idx, ctx, phase)
                    steps.append(self._run_agent_safe(agent_name, task, phase.name, shot_idx))
        return steps

    def _run_motion_gen_conditional(self, ctx: dict) -> list[PipelineStep]:
        """V3.1 motion_gen conditional phase：按 budget 选 top-N shots 跑真 AI 视频。"""
        from hailuo_video import motion_backend as mb_mod
        sb = ctx["storyboard"]
        # 收集所有 candidate shots（motion_complexity != static）
        candidates = []
        for i, shot in enumerate(sb.shots):
            mc = getattr(shot, "motion_complexity", "static") or "static"
            if mc == "static":
                continue
            priority = 1 if mc == "complex" else 0
            candidates.append((priority, i, shot))
        # 按 priority 降序；同 priority 按 index 升序
        candidates.sort(key=lambda t: (-t[0], t[1]))
        # 截取 budget
        budget = getattr(self.config, "motion_budget", 3)
        chosen = candidates[:budget]
        if not chosen:
            return []  # 无 candidate → 此 phase 跳过（normal）
        self.logger.event("phase_started", phase="motion_gen",
                          candidate_count=len(candidates), budget=budget, chosen=len(chosen))
        steps: list[PipelineStep] = []
        for priority, shot_idx, shot in chosen:
            image_art = next((a for a in self.registry.by_kind_iter("image")
                              if a.metadata.get("shot_idx") == shot_idx), None)
            image_path = str(image_art.path) if image_art else ""
            if not image_path or not Path(image_path).exists():
                steps.append(self._make_skip_step(shot_idx, "motion_gen", "no image artifact"))
                continue
            output_path = str(Path(self.config.run_dir) / "intermediate" / f"shot_{shot_idx:03d}_motion.mp4")
            output_path = str(Path(output_path))
            task = {
                "subject_action": getattr(shot, "subject_action", "") or shot.description,
                "image_path": image_path,
                "output_path": output_path,
                "duration": int(getattr(shot, "expected_duration", 6.0) or 6.0),
                "resolution": "1080P",
                "shot_id": shot_idx,
            }
            steps.append(self._run_agent_safe("motion_gen", task, "motion_gen", shot_idx))
        return steps

    def _make_skip_step(self, shot_idx: int, phase_name: str, reason: str) -> PipelineStep:
        """Conditional phase skip step：标记为何不跑（不算 ok 也不算 error）。"""
        return PipelineStep(
            name=f"{phase_name}_{shot_idx + 1:03d}",
            index=shot_idx,
            ok=True,                          # skip 不是失败
            duration_seconds=0.0,
            error="",
            extra={"skipped": True, "reason": reason},
        )

    def _build_task(self, agent_name: str, shot_idx: int, ctx: dict, phase: Phase) -> dict:
        """从 ctx 抽出单个 shot 的 inputs。

        V3.2 自动开发冲刺（轮 5）：把 shot 字段映射到各 agent 需要的 task 字段：
          - image_gen：prompt = shot.description；subject_references = 角度/表情/场景 URL
          - motion_gen：subject_action / image_path（来自 image_gen artifact）
          - tts：text = shot.dialogue / shot.voiceover
          - video_synth / editing：image_path（来自 image_gen artifact）
        不在本表里的 agent（asset_prep / script_writer / qa）走原始通用字段。
        V3.2 自动开发冲刺（轮 7）：image_gen / motion_gen 都会注入 storyboard.style_anchor
        （写实/电影/纪实等风格基线），让生成的画面有真实质感。
        """
        sb = ctx["storyboard"]
        shot = sb.shots[shot_idx] if shot_idx < len(sb.shots) else None
        # V3.2 自动开发冲刺（轮 7）：style_anchor 是风格基线（写实/电影/纪实等）
        # V3.4 ADR-006：空值兜底为写实系（不再放任 prompt 无 anti-cartoon 守卫）
        from hailuo_video.asset_prep import _safe_style_anchor
        style_anchor = _safe_style_anchor(getattr(sb, "style_anchor", "") or "")

        # 基础字段（所有 agent 都看到）
        task: dict = {
            "image_backend": self.config.image_backend,
            "video_backend": self.config.video_backend,
            "tts_backend": self.config.tts_backend,
            "motion_backend": getattr(self.config, "motion_backend", None),    # V3.1
            "storyboard": sb,
            "shot_index": shot_idx,
            "shot_idx": shot_idx,                              # V3.1：让 Agent 可写回 metadata
            "shot": shot,
            "intermediate_dir": ctx.get("intermediate_dir"),
            "registry": self.registry,
            "assets_dir": getattr(self.config, "assets_dir", "./assets"),
            "run_dir": self.config.run_dir,
            "voice": getattr(self.config, "voice", "zh-CN-XiaoxiaoNeural"),
            "video_seconds": getattr(self.config, "video_seconds", 5),
            "video_fps": getattr(self.config, "video_fps", 8),
            "video_effect": getattr(self.config, "video_effect", "cycle"),
            "aspect_ratio": getattr(self.config, "aspect_ratio", "16:9"),
            "keep_intermediate": getattr(self.config, "keep_intermediate", True),
            "parallel": getattr(self.config, "parallel", 1),
        }

        if shot is None:
            return task

        # shot → per-agent 字段映射（V3.2 链式集成）
        description = getattr(shot, "description", "") or ""
        dialogue = getattr(shot, "dialogue", "") or ""
        voiceover = getattr(shot, "voiceover", "") or ""

        if agent_name == "image_gen":
            # 70/30 提示法拼接：style_anchor + description + [shot_type] + [运镜] + [动作] + [氛围]
            # V3.2 自动开发冲刺（轮 7）：style_anchor 必须放在最前（基线优先级）
            # V3.4 ADR-006：style_anchor 已由 _safe_style_anchor 兜底为非空 + 含守卫词
            prompt_parts = []
            prompt_parts.append(f"[风格:{style_anchor}]")
            prompt_parts.append(description)
            camera_motion = getattr(shot, "camera_motion", "") or ""
            subject_action = getattr(shot, "subject_action", "") or ""
            mood = list(getattr(shot, "mood", []) or [])
            shot_type = getattr(shot, "shot_type", "") or ""
            if shot_type:
                prompt_parts.append(f"[{shot_type}]")
            if camera_motion:
                prompt_parts.append(f"[运镜:{camera_motion}]")
            if subject_action:
                prompt_parts.append(f"[动作:{subject_action}]")
            if mood:
                prompt_parts.append(f"[氛围:{', '.join(mood)}]")
            task["prompt"] = " ".join(prompt_parts)
            task["style_anchor"] = style_anchor      # V3.2：让 image backend 可单独读取
            # 角度 + 表情 + 场景变体，与 V2 run_pipeline 共用 resolver。
            # 缓存缺失的轴跳过；subject_references 只含 URL，不再传角色名。
            from hailuo_video import asset_prep as ap
            refs = ap.resolve_shot_reference_urls(
                shot,
                getattr(self.config, "assets_dir", "./assets"),
                shot_index=shot_idx,
            )
            task["subject_references"] = list(refs.urls)
            task["scene_references"] = list(refs.scene_urls)
            task["reference_labels"] = list(refs.labels)
        elif agent_name == "motion_gen":
            # V3.2 自动开发冲刺（轮 7）：style_anchor 注入 motion_gen subject_action
            # V3.3 P0 修复（2026-10-09）：style_anchor 前置而非后置
            #   原因：Hailuo 对 prompt 前缀权重高于后缀（实测社区反馈 + D-013 注释）
            #   旧版 `f"{base_subject_action}, 风格:{style_anchor}"` 把风格当尾缀，
            #   Hailuo 可能把后半部分当附加修饰忽略 → 视频风格漂移。
            #   新版 `[风格:{style_anchor}] {base_subject_action}` 把风格放最前。
            # V3.4 ADR-006：style_anchor 已 _safe_style_anchor 兜底（非空），无需 if/else 分支
            base_subject_action = getattr(shot, "subject_action", "") or description
            task["subject_action"] = f"[风格:{style_anchor}] {base_subject_action}"
            task["style_anchor"] = style_anchor       # 也单独传，方便后端读取
            # V3.4 W3 hotfix（2026-10-13）：motion_gen 必须传 image_path 给 hailuo 作 first_frame_image
            #   旧行为：task 缺 image_path → hailuo 走 T2V（纯文本）→ 自由发挥"小黄=橘猫"
            #   新行为：image_path + first_frame_image 都传 → hailuo 走 I2V → 主体锁定
            # V3.3 「小黄变猫」事件根因（V3.4 W3 已部分修：style_anchor 守卫词 + 删 concept art）
            image_path = self._get_image_path_for_shot(ctx, shot_idx)
            task["image_path"] = image_path
            task["first_frame_image"] = image_path  # motion_backend 走 I2V
            task["duration"] = int(getattr(shot, "expected_duration", 6.0) or 6.0)
            task["resolution"] = "1080P"
            task["shot_id"] = shot_idx
            task["output_path"] = str(
                Path(str(task.get("intermediate_dir", "./intermediate")))
                / f"shot_{shot_idx:03d}_motion.mp4"
            )
        elif agent_name == "video_synth":
            # V3.2 自动开发冲刺（轮 6）：image_path 来自 image_gen artifact；output_path 写到 intermediate_dir
            image_path = self._get_image_path_for_shot(ctx, shot_idx)
            task["image_path"] = image_path
            task["output_path"] = str(
                Path(str(task.get("intermediate_dir", "./intermediate")))
                / f"shot_{shot_idx:03d}_kenburns.mp4"
            )
            task["seconds"] = getattr(shot, "expected_duration", None) or 5
        elif agent_name == "tts":
            # 对白优先，无对白则用旁白；都无则空
            text = dialogue or voiceover
            task["text"] = text
            task["voice"] = getattr(self.config, "voice", "zh-CN-XiaoxiaoNeural")
            task["write_subtitle"] = True
        elif agent_name == "editing":
            # V3.2 自动开发冲刺（轮 6）：editing phase 需要 video_paths + output_path
            # 从 registry 中收集 motion_gen / video_synth 的产物（按 shot_idx）
            video_paths = self._collect_video_paths_for_editing(ctx, shot_idx)
            task["video_paths"] = video_paths
            task["output_path"] = str(self.config.run_dir / "final.mp4")
            task["output_dir"] = str(self.config.run_dir / "intermediate")
            task["mode"] = "full"  # 用 full pipeline（color → crossfade → LUT）

        return task

    def _collect_video_paths_for_editing(self, ctx: dict, shot_idx: int) -> list[str]:
        """V3.2 自动开发冲刺（轮 6）：收集 editing phase 需要的 video_paths。

        优先级：motion_gen > video_synth > image_gen（任一有产物即可）。
        返回 list[str]（路径列表），按 shot_idx 排序。
        """
        paths: list[str] = []
        # 优先 motion_gen（真 AI 视频）
        for art in self.registry.by_kind_iter("video"):
            if art.path and Path(str(art.path)).exists():
                paths.append(str(art.path))
        # 次选 video_pending（motion_gen 还在后台轮询的中间产物）
        if not paths:
            for art in self.registry.by_kind_iter("video_pending"):
                if art.path and Path(str(art.path)).exists():
                    paths.append(str(art.path))
        # 兜底：video_synth 的产物（Ken Burns fallback）
        if not paths:
            for art in self.registry.by_kind_iter("video"):
                if art.path and Path(str(art.path)).exists():
                    paths.append(str(art.path))
        return paths

    def _get_image_path_for_shot(self, ctx: dict, shot_idx: int) -> str:
        """V3.2 自动开发冲刺（轮 6）：从 registry 中找 shot_idx 对应的 image artifact。

        返回 Path 字符串（video_synth 喂给 ffmpeg zoompan 用）。

        V3.2 自动开发冲刺（轮 15 修正）：用 .resolve() 把 artifact.path 强转绝对路径
        ——避免 Linux-style 路径（/tmp/...）在 Windows 上 .exists() 返 False。
        """
        # 优先：按 artifact_id 命名规则（image_NNN）查找
        target_id = f"image_{shot_idx}"
        for art in self.registry.by_kind_iter("image"):
            if art.artifact_id == target_id and art.path and Path(str(art.path)).resolve().exists():
                return str(Path(str(art.path)).resolve())
        # 兜底：返回第一个可用 image
        for art in self.registry.by_kind_iter("image"):
            if art.path and Path(str(art.path)).resolve().exists():
                return str(Path(str(art.path)).resolve())
        return ""

    def _resolve_scene_refs_for_shot(self, ctx: dict, shot_idx: int) -> list[str]:
        """场景变体 URL。与 `_build_task` 共用 `resolve_shot_reference_urls`。"""
        from hailuo_video import asset_prep as ap

        sb = ctx.get("storyboard")
        shots = getattr(sb, "shots", None) or []
        if shot_idx >= len(shots):
            return []
        refs = ap.resolve_shot_reference_urls(
            shots[shot_idx],
            getattr(self.config, "assets_dir", "./assets"),
            shot_index=shot_idx,
        )
        return list(refs.scene_urls)

    def _run_agent_safe(self, agent_name: str, task: dict, phase_name: str, shot_idx: int) -> PipelineStep:
        """调 Agent.execute() + 异常捕获 + 写 step + 写 registry。"""
        from hailuo_video.agents import get_agent
        agent = self._agent_lookup.get(agent_name) or get_agent(agent_name)
        t0 = time.monotonic()
        step_name = f"{agent_name}_{shot_idx + 1:03d}" if shot_idx is not None else agent_name
        try:
            agent.validate_inputs(task)
            # 注：with_retry.__enter__ 返回 result；不在 with 块内再次调用 agent.execute
            with with_retry(lambda: agent.execute(task), self.config.retry_policy) as result:
                pass  # noqa
        except InputValidationError as exc:
            return PipelineStep(
                name=step_name, index=shot_idx or 0, ok=False,
                duration_seconds=time.monotonic() - t0,
                error=f"InputValidationError: {exc}",
            )
        except Exception as exc:
            return PipelineStep(
                name=step_name, index=shot_idx or 0, ok=False,
                duration_seconds=time.monotonic() - t0,
                error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-200:]}",
            )
        elapsed = time.monotonic() - t0
        for art in result.artifacts:
            self.registry.add(art)
        return PipelineStep(
            name=step_name, index=shot_idx or 0, ok=result.ok,
            duration_seconds=elapsed,
            error=result.error,
            output_path=str(result.artifacts[0].path) if result.artifacts else "",
            extra={"agent": agent_name, "phase": phase_name, "artifacts_count": len(result.artifacts)},
        )

    def run(self, storyboard: Any, output: Path) -> PipelineResult:
        """顶层入口：跑完整个 DAG。"""
        started = time.monotonic()
        all_steps: list[PipelineStep] = []
        intermediate_dir = self.config.run_dir / "intermediate"
        if self.config.keep_intermediate:
            intermediate_dir.mkdir(parents=True, exist_ok=True)
        ctx = {
            "storyboard": storyboard,
            "shot_count": len(storyboard.shots),
            "intermediate_dir": intermediate_dir,
        }
        # 简化的 DAG runner：依 DEFAULT_DAG 顺序执行
        from_idx = self._resolve_resume_index()
        skipped_phases: list[str] = [
            DEFAULT_DAG[i].name for i in range(from_idx)
        ]
        if skipped_phases:
            self.logger.event("resume_skipped", phases=skipped_phases)
        for i, phase in enumerate(DEFAULT_DAG):
            if i < from_idx:
                # 跳过：phase 在 resume_from 之前 → 不跑（registry 已加载历史 artifacts）
                phase_steps = []
                self.logger.event("phase_skipped", phase=phase.name, reason="resume_from")
            else:
                phase_steps = self.run_phase(phase, ctx)
            all_steps.extend(phase_steps)
            # V3.4 ADR-002：editing phase 失败时 soft-success（motion.mp4 落盘即视为成功）
            # 旧逻辑：halt_on_error=True → editing 失败返回 hard fail，但 motion_gen 已落盘
            # 新逻辑：editing 失败时若 motion.mp4 存在 → 复制为 final.mp4 → soft success
            if phase.name == "editing" and any(not s.ok for s in phase_steps):
                motion_fallback = self._motion_mp4_fallback(intermediate_dir, output)
                if motion_fallback:
                    self.logger.event("editing_soft_success", fallback_path=str(motion_fallback))
                    elapsed = time.monotonic() - started
                    return PipelineResult(
                        output=str(output),
                        shot_count=len(storyboard.shots),
                        ok_shots=sum(1 for s in all_steps if s.ok and "image_gen" in s.name),
                        failed_shots=sum(1 for s in all_steps if not s.ok),
                        duration_seconds=elapsed,
                        source="motion_fallback",   # V3.4 ADR-002: 标记为降级产出
                        steps=all_steps,
                        intermediate_dir=str(intermediate_dir),
                    )
            # 其他 phase 仍走 halt 策略
            if phase.halt_on_error and any(not s.ok for s in phase_steps):
                elapsed = time.monotonic() - started
                return PipelineResult(
                    output=str(output),
                    shot_count=len(storyboard.shots),
                    ok_shots=sum(1 for s in all_steps if s.ok and "image_gen" in s.name),
                    failed_shots=sum(1 for s in all_steps if not s.ok),
                    duration_seconds=elapsed,
                    source="error",
                    error=f"phase {phase.name} halted with errors",
                    steps=all_steps,
                    intermediate_dir=str(intermediate_dir),
                )
        # Post-flight QA
        image_paths = [
            str(a.path) for a in self.registry.by_kind_iter("image") if a.path
        ]
        if image_paths:
            qa_task = {"image_paths": image_paths}
            try:
                qa_agent = self._agent_lookup["qa"]
                qa_result = qa_agent.execute(qa_task)
                all_steps.append(PipelineStep(
                    name="qa_post", index=0, ok=qa_result.ok,
                    duration_seconds=0.0,
                    extra={
                        "mean_similarity": qa_result.metadata.get("mean_similarity", 1.0),
                        "min_similarity": qa_result.metadata.get("min_similarity", 1.0),
                    },
                ))
            except Exception as exc:
                all_steps.append(PipelineStep(
                    name="qa_post", index=0, ok=False, error=str(exc),
                ))
        # Persist registry
        try:
            self.registry.save_json(self.config.run_dir / "registry.json")
        except OSError:
            pass
        ok_shots = sum(1 for s in all_steps if s.ok and "image_gen" in s.name)
        failed_shots = sum(1 for s in all_steps if not s.ok)
        return PipelineResult(
            output=str(output),
            shot_count=len(storyboard.shots),
            ok_shots=ok_shots,
            failed_shots=failed_shots,
            duration_seconds=time.monotonic() - started,
            source="ok",
            steps=all_steps,
            intermediate_dir=str(intermediate_dir),
        )
