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
    """PipelineAgent.run() 的所有可配项。

    V3.5 ADR-007：可选 project_context 注入，自动 save all
    路径、env、style_anchor 来自 ProjectContext。无 project_context 时
    仍用下面 defaults（向后兼容 V3.4 调用方）。
    """
    image_backend: Any = None               # image_engine.IImageBackend
    video_backend: Any = None               # video_engine.KenBurnsBackend
    tts_backend: Any = None                  # tts_engine.TTSBackend
    # V4.0 BUG-D："" = 未指定 → 由 voice_cast 按说话角色分配（未指定时用
    #   EDGE_TTS_VOICE 兜底）。旧的硬编码默认值遮蔽了 env。
    voice: str = ""
    video_seconds: int = 5
    video_fps: int = 8
    video_effect: str = "cycle"
    # V3.8 缺陷 1：默认 None = "不指定"。旧的 "16:9" 硬默认值会遮蔽
    #   image_engine.default_aspect_ratio()（读 MINIMAX_IMAGE_ASPECT_RATIO /
    #   GEMINI_IMAGE_ASPECT_RATIO），导致 .env 配 9:16 在 V3 生产路径完全失效。
    aspect_ratio: str | None = None
    assets_dir: Path = Path("./assets")
    skip_asset_prep: bool = False
    keep_intermediate: bool = True
    parallel: int = 1
    motion_budget: int = 3                     # V3.1：MiniMax Max 默认每天 3 次 hailuo-2.3
    # V3.8 W4：运动参数知识库落盘路径。None = **不记录**（默认关闭）。
    #   默认关闭的原因：写库是副作用，且默认路径在仓库内（knowledge/motion_params.json）
    #   —— 测试跑一次就会污染人工维护的种子数据。CLI 显式传入才开启。
    knowledge_path: Path | None = None
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    log: StructuredLogger | None = None
    run_dir: Path = Path("./run")
    # V3.5 ADR-007：项目级上下文（optional，None = 旧默认行为）
    project_context: Any = None               # ProjectContext 实例


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
        # V3.9 W-C：角色→音色分配表（整个 run 内同一角色恒定同音色）
        self._voice_cast = None
        self._voice_collision_seen: set[tuple[str, tuple[str, ...]]] = set()
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
        static_shots: list[int] = []
        for i, shot in enumerate(sb.shots):
            mc = getattr(shot, "motion_complexity", "static") or "static"
            if mc == "static":
                static_shots.append(i)
                continue
            priority = 1 if mc == "complex" else 0
            candidates.append((priority, i, shot))
        # V3.8 缺陷 2：static shot 被排除 = 该镜头**不会**调用 Hailuo，
        #   而是走 Ken Burns 兜底（不烧 quota，也没有真 AI 运动，只有慢推慢拉的伪运动）。
        #   旧行为完全静默——CLI 只打一行 "[OK] shot_003.mp4 (0 bytes, 19.1s)"，
        #   要靠人工抽帧才发现（见 sample/story_arc_qingdao_autumn/README.md:81-89）。
        #   现在显式记 event + 落进 PipelineResult.consistency，CLI 打 [WARN]。
        if static_shots:
            self.logger.event(
                "motion_skipped_static",
                shot_indices=static_shots,
                count=len(static_shots),
                reason="motion_complexity=static → Ken Burns 兜底（非真 AI 运动）",
            )
        ctx["static_skipped_shots"] = static_shots
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
            "aspect_ratio": self._resolve_aspect_ratio(),
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
            # V3.8 W2（缺陷 5）：导演字段真正进模型。
            # 旧行为：prompt 只拼 style_anchor + description + shot_type +
            #   camera_motion + subject_action + mood —— 12 个 director 字段
            #   （lens / lighting / depth_of_field / color_grade / ...）
            #   **一个都没进 prompt**，只服务人工审稿。
            # 新行为：用 DirectorBrief 的**静态视觉条件**（DramaDirector 式解耦，
            #   arXiv:2606.24107）—— 角色外观 + 场景 + 构图 + 焦段 + 景深 + 光线 + 色彩。
            brief = self._director_brief(shot, sb, style_anchor)
            static_visual = brief.static_visual
            if static_visual:
                task["prompt"] = static_visual
            else:
                # 兜底：brief 为空（极端异常）→ 回退旧拼法，不让 prompt 空掉
                prompt_parts = [f"[风格:{style_anchor}]", description]
                if getattr(shot, "shot_type", ""):
                    prompt_parts.append(f"[{getattr(shot, 'shot_type', '')}]")
                if getattr(shot, "camera_motion", ""):
                    prompt_parts.append(f"[运镜:{getattr(shot, 'camera_motion', '')}]")
                if getattr(shot, "subject_action", ""):
                    prompt_parts.append(f"[动作:{getattr(shot, 'subject_action', '')}]")
                mood_list = list(getattr(shot, "mood", []) or [])
                if mood_list:
                    prompt_parts.append(f"[氛围:{', '.join(mood_list)}]")
                task["prompt"] = " ".join(prompt_parts)
            task["style_anchor"] = style_anchor      # V3.2：让 image backend 可单独读取
            task["color_lock"] = brief.color_lock    # V3.8：跨镜色基调（连续性门禁用）
            # V3.8 W4：负提示词（TIDE 式「正 + 负提示词一次产出」）。
            #   本项目此前**零**负提示词支持 —— 生成路径只能靠正向描述规避。
            #   后端若无独立 negative_prompt 字段，image_engine 会内联到 prompt 末尾。
            try:
                from hailuo_video.negative_prompt import build_negative_prompt
                neg = build_negative_prompt(
                    shot, include_character=bool(getattr(shot, "characters", None)),
                )
                task["negative_prompt"] = neg.render()
            except Exception:
                task["negative_prompt"] = ""
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
            # V3.8 W1（缺陷 3）：过期 reference 被跳过 —— 记下来，别让角色一致性
            # 悄悄消失（旧行为是直接用过期 URL → 跨天跑 403 → 锚定失效）。
            if refs.stale_labels:
                task["stale_references"] = list(refs.stale_labels)
                stale_all = ctx.setdefault("stale_references", [])
                for label in refs.stale_labels:
                    stale_all.append({"shot_idx": shot_idx, "label": label})
                self.logger.event(
                    "reference_stale",
                    shot_idx=shot_idx,
                    labels=list(refs.stale_labels),
                    reason="reference URL 已过期 → 跳过（角色一致性锚定缺失）",
                )
        elif agent_name == "motion_gen":
            # V3.2 自动开发冲刺（轮 7）：style_anchor 注入 motion_gen subject_action
            # V3.3 P0 修复（2026-10-09）：style_anchor 前置而非后置
            #   原因：Hailuo 对 prompt 前缀权重高于后缀（实测社区反馈 + D-013 注释）
            #   旧版 `f"{base_subject_action}, 风格:{style_anchor}"` 把风格当尾缀，
            #   Hailuo 可能把后半部分当附加修饰忽略 → 视频风格漂移。
            #   新版 `[风格:{style_anchor}] {base_subject_action}` 把风格放最前。
            # V3.4 ADR-006：style_anchor 已 _safe_style_anchor 兜底（非空），无需 if/else 分支
            #
            # V3.8 W2（缺陷 5）：改用 DirectorBrief 的**动态叙事条件**。
            #   旧行为 prompt = "[风格:X] {subject_action}" —— **camera_motion
            #   从来没进过视频模型**（运镜信息全丢）；direction / pacing /
            #   narrative_beat / transition 也同样只服务人工审稿。
            #   新行为：动作 + 运镜 + 屏幕方向 + 节奏 + 氛围 + 节拍 + 转场。
            brief = self._director_brief(shot, sb, style_anchor)
            dynamic = brief.dynamic_narrative
            base_subject_action = getattr(shot, "subject_action", "") or description
            if dynamic:
                task["subject_action"] = f"[风格:{style_anchor}] {dynamic}"
            else:
                task["subject_action"] = f"[风格:{style_anchor}] {base_subject_action}"
            task["style_anchor"] = style_anchor       # 也单独传，方便后端读取
            task["color_lock"] = brief.color_lock
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
            # V3.9 W-C（REQ-403）：**角色语音一致性** —— 此前所有角色、所有旁白
            # 共用一个全局音色。现在按"说话角色"分配，且分配是**确定性**的
            # （同一角色跨天 / 跨项目 / 跨镜头恒定同音色）。
            voice, speaker = self._resolve_voice(shot, sb)
            task["voice"] = voice
            task["speaker"] = speaker
            task["write_subtitle"] = True
        elif agent_name == "editing":
            # V3.2 自动开发冲刺（轮 6）：editing phase 需要 video_paths + output_path
            # 从 registry 中收集 motion_gen / video_synth 的产物（按 shot_idx）
            video_paths = self._collect_video_paths_for_editing(ctx, shot_idx)
            task["video_paths"] = video_paths
            # V4.0 BUG-C：把每镜的旁白 / 字幕一起传下去。
            #   此前 TTS 的产物被丢在 registry 里没人用 → 成片**无配音无字幕**
            #   （实测仓库里 9 个成片有 8 个无音轨）。
            #   列表必须与 video_paths **下标对齐**（缺的留 None）。
            task["audio_paths"] = self._collect_by_shot(ctx, "audio", len(video_paths))
            task["subtitle_paths"] = self._collect_by_shot(ctx, "subtitle", len(video_paths))
            task["output_path"] = str(self.config.run_dir / "final.mp4")
            task["output_dir"] = str(self.config.run_dir / "intermediate")
            task["mode"] = "full"  # 用 full pipeline（color → crossfade → LUT）

        return task

    def _director_brief(self, shot: Any, storyboard: Any = None, style_anchor: str = "") -> Any:
        """V3.8 W2：取 shot 的 DirectorBrief（静态视觉 / 动态叙事解耦）。

        优先用 shot 上已存的 `static_visual` / `dynamic_narrative`（人工编辑过
        storyboard.json 的情况优先）；否则由 director.plan_director_brief 确定性推导。

        `style_anchor` 必须是**已兜底**的值（`_safe_style_anchor`）—— 空画风会让
        image prompt 丢掉 ADR-006 的 anti-cartoon 守卫词。

        异常一律吞掉并返回空 brief —— brief 只是提示词增强，**不该让 pipeline 崩**。
        """
        from hailuo_video.director import DirectorBrief, plan_director_brief

        if shot is None:
            return DirectorBrief()
        stored_static = str(getattr(shot, "static_visual", "") or "").strip()
        stored_dynamic = str(getattr(shot, "dynamic_narrative", "") or "").strip()
        stored_lock = str(getattr(shot, "color_lock", "") or "").strip()
        if stored_static and stored_dynamic:
            return DirectorBrief(
                static_visual=stored_static,
                dynamic_narrative=stored_dynamic,
                color_lock=stored_lock,
            )
        try:
            brief = plan_director_brief(shot, storyboard, style_anchor=style_anchor)
        except Exception:
            return DirectorBrief()
        if stored_static:
            brief = DirectorBrief(
                static_visual=stored_static,
                dynamic_narrative=brief.dynamic_narrative or stored_dynamic,
                color_lock=stored_lock or brief.color_lock,
                lens=brief.lens, lighting=brief.lighting,
                color_grade=brief.color_grade, pacing=brief.pacing,
                screen_direction=brief.screen_direction,
                narrative_beat=brief.narrative_beat,
                used_fields=brief.used_fields,
            )
        elif stored_dynamic:
            brief = DirectorBrief(
                static_visual=brief.static_visual,
                dynamic_narrative=stored_dynamic,
                color_lock=stored_lock or brief.color_lock,
                lens=brief.lens, lighting=brief.lighting,
                color_grade=brief.color_grade, pacing=brief.pacing,
                screen_direction=brief.screen_direction,
                narrative_beat=brief.narrative_beat,
                used_fields=brief.used_fields,
            )
        return brief

    def _resolve_voice(self, shot: Any, storyboard: Any = None) -> tuple[str, str]:
        """V3.9 W-C：解出这一镜的音色与说话角色。返回 (voice, speaker)。

        分配表挂在 agent 上（`self._voice_cast`）→ 整个 run 内同一角色恒定同音色。

        V4.0 BUG-D：`--voice` 显式指定 → **覆盖一切**（保持旧语义：用户明确要
        一个音色就全片用它）；未指定 → 按说话角色分配，
        旁白音色取 `EDGE_TTS_VOICE`（未设则用内置叙述者音色）。
        """
        from hailuo_video.voice_cast import (
            NARRATOR_VOICE,
            VoiceCast,
            normalize_voice,
            resolve_voice_for_shot,
            speaker_of,
        )

        # 显式全局音色（--voice / PipelineConfig.voice）→ 覆盖一切
        explicit_global = normalize_voice(str(getattr(self.config, "voice", "") or ""))
        if explicit_global:
            return explicit_global, speaker_of(shot)

        # 注意：必须读 **os.environ 原始值**，不能用 te.default_voice() ——
        # 后者在 env 未设时返回 DEFAULT_VOICE（Xiaoxiao），会把旁白音色顶掉。
        import os
        env_voice = normalize_voice(os.environ.get("EDGE_TTS_VOICE", "") or "")
        narrator = env_voice or NARRATOR_VOICE

        if getattr(self, "_voice_cast", None) is None:
            self._voice_cast = VoiceCast(
                default_voice=env_voice or "zh-CN-XiaoxiaoNeural",
                narrator_voice=narrator,
            )
        try:
            voice, speaker = resolve_voice_for_shot(
                shot, storyboard, cast=self._voice_cast,
                default_voice=env_voice or "zh-CN-XiaoxiaoNeural",
                narrator_voice=narrator,
            )
            # V3.9 W-C：角色撞音色 → 报出来（"看起来成功、实际有问题"的静默缺陷）
            for v, names in self._voice_cast.collisions():
                key = (v, tuple(names))
                if key not in self._voice_collision_seen:
                    self._voice_collision_seen.add(key)
                    self.logger.event(
                        "voice_collision",
                        voice=v, characters=list(names),
                        reason="多个角色共用同一音色 —— 请在 storyboard 的 characters[].voice 里显式指定",
                    )
            return voice, speaker
        except Exception:
            return env_voice or "zh-CN-XiaoxiaoNeural", ""

    def _resolve_aspect_ratio(self) -> str:
        """V3.8 缺陷 1：画幅解析优先级 = config 显式值 > backend 自己的 env 默认 > "16:9"。

        旧实现硬编码 `getattr(self.config, "aspect_ratio", "16:9")`：
        PipelineConfig 的默认值本身就是 "16:9"，且 run_pipeline_v3 从不接受外部传入，
        于是 image_engine.default_aspect_ratio()（读 MINIMAX_IMAGE_ASPECT_RATIO /
        GEMINI_IMAGE_ASPECT_RATIO）永远轮不到 —— .env 里配 9:16 完全无效。
        """
        explicit = str(getattr(self.config, "aspect_ratio", "") or "").strip()
        if explicit:
            return explicit
        backend = getattr(self.config, "image_backend", None)
        getter = getattr(backend, "default_aspect_ratio", None)
        if callable(getter):
            try:
                value = getter()
            except Exception:
                value = ""
            if value:
                return str(value)
        return "16:9"

    def _collect_by_shot(self, ctx: dict, kind: str, shot_count: int) -> list[str | None]:
        """按 shot_idx 收集某类产物，返回**与镜头下标对齐**的列表。

        V4.0 BUG-C：editing 阶段需要每镜的旁白（audio）与字幕（subtitle）。

        **必须与视频列表下标对齐**（缺的那镜留 None）—— 若返回"只有存在的那几项"
        的紧凑列表，调用方按下标取就会把 shot 1 的旁白安到 shot 0 上（张冠李戴）。

        依赖 BUG-A 修复：artifact_id 按 shot 隔离，否则 registry 里只剩最后一镜。
        """
        by_shot: dict[int, str] = {}
        for art in self.registry.by_kind_iter(kind):
            path = str(art.path or "")
            if not path or not Path(path).exists():
                continue
            meta_idx = art.metadata.get("shot_idx")
            try:
                key = int(meta_idx) if meta_idx is not None else None
            except (TypeError, ValueError):
                key = None
            if key is None:
                digits = "".join(ch for ch in art.artifact_id if ch.isdigit())
                key = int(digits) if digits else 0
            by_shot.setdefault(key, path)
        return [by_shot.get(i) for i in range(shot_count)]

    def _collect_video_paths_for_editing(self, ctx: dict, shot_idx: int) -> list[str]:
        """收集 editing phase 要拼接的**每镜一段**视频（按 shot 顺序）。

        V4.0 BUG-B：旧实现把 registry 里**所有** kind="video" 的产物都收进来 ——
        同一镜的 motion 视频与 Ken Burns 兜底视频**同时**进列表 → 成片里该镜
        出现两次。（V3.8 已删掉其中一段死分支，但"全都收"这个语义还在。）

        V4.0：按 shot_idx 分组，**每镜优先 motion（真 AI 视频）**，没有则用
        Ken Burns 兜底；按 shot_idx 排序保证时间顺序。

        依赖 BUG-A 修复：artifact_id 必须按 shot 隔离，否则 registry 里每镜的
        产物互相覆盖，分组就无从谈起。
        """
        by_shot: dict[int, dict[str, str]] = {}
        for kind in ("video", "video_pending"):
            for art in self.registry.by_kind_iter(kind):
                path = str(art.path or "")
                if not path or not Path(path).exists():
                    continue
                meta_idx = art.metadata.get("shot_idx")
                try:
                    key = int(meta_idx) if meta_idx is not None else None
                except (TypeError, ValueError):
                    key = None
                if key is None:
                    # 没有 shot_idx → 按 artifact_id 里的数字兜底（"video_3" / "motion_3"）
                    digits = "".join(ch for ch in art.artifact_id if ch.isdigit())
                    key = int(digits) if digits else 0
                slot = by_shot.setdefault(key, {})
                is_motion = art.producer == "motion_gen" or art.artifact_id.startswith("motion_")
                # motion 优先；已有 motion 时不被 Ken Burns 覆盖
                slot["motion" if is_motion else "fallback"] = path

        out: list[str] = []
        for key in sorted(by_shot):
            slot = by_shot[key]
            chosen = slot.get("motion") or slot.get("fallback")
            if chosen:
                out.append(chosen)
        return out

    def _get_image_path_for_shot(self, ctx: dict, shot_idx: int) -> str:
        """V3.2 自动开发冲刺（轮 6）：从 registry 中找 shot_idx 对应的 image artifact。

        返回 Path 字符串（video_synth 喂给 ffmpeg zoompan 用）。

        V3.2 自动开发冲刺（轮 15 修正）：用 .resolve() 把 artifact.path 强转绝对路径
        ——避免 Linux-style 路径（/tmp/...）在 Windows 上 .exists() 返 False。

        V3.8 缺陷 15：**优先按 metadata["shot_idx"] 匹配**。
        旧实现只按 artifact_id 命名规则找 f"image_{shot_idx}"，而 ImageGenAgent
        造的是 f"image_{idx}"（结果下标）→ n=1 时所有 shot 都是 "image_0"，
        匹配到的是最后一个 shot 的图；非首镜则落到"第一个可用图"兜底 → 喂错图。
        """
        # 优先：按 metadata.shot_idx（唯一可靠的身份标识）
        for art in self.registry.by_kind_iter("image"):
            if art.metadata.get("shot_idx") == shot_idx and art.path and Path(str(art.path)).resolve().exists():
                return str(Path(str(art.path)).resolve())
        # 次选：按 artifact_id 命名规则（image_NNN）
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
            # V3.8 缺陷 2：static 降级镜头的清单（motion_gen phase 回填）
            "static_skipped_shots": [],
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
                        consistency=self._build_consistency(ctx),
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
                    consistency=self._build_consistency(ctx),
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
        # V3.8 W3：跨镜连续性门禁（单镜 SSIM gate 管不到"切点之后"）
        #   实测跨镜色差 mean=0.700 vs 理想 0.95（logs/_diagnose_3to4_jump_20261016/SUMMARY.md:36-39）
        #   → 默认**只报警不阻断**（与既有 SSIM gate 一致）。
        continuity_result = self._evaluate_continuity(ctx)
        if continuity_result is not None:
            ctx["continuity_report"] = continuity_result
            all_steps.append(PipelineStep(
                name="continuity_post", index=0, ok=continuity_result.ok,
                duration_seconds=0.0,
                error="" if continuity_result.ok else continuity_result.format(),
                extra=continuity_result.to_dict(),
            ))
        # V3.8 W4：把本次结果沉淀进运动参数知识库（纯本地写，无 API 消耗）。
        #   这是 REQ-406 的数据来源 —— 下一轮检索增强（RAPO/TIDE）才能学到东西。
        self._record_knowledge(ctx, all_steps)
        # Persist registry
        # V3.8 缺陷 13：旧代码 `except OSError: pass` —— 落盘失败完全静默，
        # 违反项目 R4「不静默降级」。现在记 event + 写进 PipelineResult.consistency
        # （**不 raise**：registry 落盘失败不该炸掉已经产出的成片，但必须可见）。
        registry_save_error = ""
        try:
            self.registry.save_json(self.config.run_dir / "registry.json")
        except OSError as exc:
            registry_save_error = f"{type(exc).__name__}: {exc}"
            self.logger.event("registry_save_failed", error=registry_save_error)
        consistency = self._build_consistency(ctx, registry_save_error)
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
            consistency=consistency,
        )

    def _record_knowledge(self, ctx: dict, steps: list[PipelineStep]) -> None:
        """V3.8 W4：把本次 shot 结果沉淀进运动参数知识库。

        **默认关闭**（`PipelineConfig.knowledge_path is None`）—— 写库是副作用，
        且默认路径在仓库内，会污染人工维护的种子数据。CLI 显式配置才开启。

        纯本地写，无 API 消耗；失败不影响成片（异常一律吞掉）。
        """
        path = getattr(self.config, "knowledge_path", None)
        if not path:
            return
        try:
            from hailuo_video.knowledge_store import MotionParamsStore, record_result

            sb = ctx.get("storyboard")
            shots = list(getattr(sb, "shots", []) or [])
            if not shots:
                return
            store = MotionParamsStore(Path(path))
            store.load()
            continuity = ctx.get("continuity_report")
            sim = float(getattr(continuity, "mean_similarity", 0.0) or 0.0)
            for shot in shots:
                record_result(
                    store,
                    shot=shot,
                    quality={"color_sim_mean": sim} if sim else {},
                    outcome="ok",
                    save=False,
                    project_id=str(getattr(self.config.project_context, "project_id", "") or ""),
                    backend="minimax",
                )
            store.save()
        except Exception as exc:
            self.logger.event("knowledge_record_failed", error=str(exc))

    def _evaluate_continuity(self, ctx: dict) -> Any:
        """V3.8 W3：跨镜连续性评估（按 shot 顺序取每镜首帧）。

        异常吞掉返回 None —— 连续性是**质量信号**，不该让已产出的成片崩掉。
        """
        from hailuo_video.continuity import evaluate_shot_continuity

        sb = ctx.get("storyboard")
        shots = list(getattr(sb, "shots", []) or [])
        if len(shots) < 2:
            return None
        paths: list[str] = []
        for idx in range(len(shots)):
            p = self._get_image_path_for_shot(ctx, idx)
            if p:
                paths.append(p)
        if len(paths) < 2:
            return None
        locks = [str(getattr(s, "color_lock", "") or "") for s in shots]
        try:
            return evaluate_shot_continuity(paths, color_locks=locks)
        except Exception as exc:
            self.logger.event("continuity_eval_failed", error=str(exc))
            return None

    def _build_consistency(self, ctx: dict, registry_save_error: str = "") -> dict[str, Any]:
        """V3.8：把"降级/失败但不影响成片"的信息集中放进 PipelineResult.consistency。

        R4「不静默降级」：这些情况**不该 raise**（成片已产出），
        但必须让调用方（CLI / daily_cron）看得见。
        """
        consistency: dict[str, Any] = {}
        static_skipped = list(ctx.get("static_skipped_shots") or [])
        if static_skipped:
            consistency["static_skipped_shots"] = static_skipped
            consistency["static_skipped_count"] = len(static_skipped)
        stale_refs = list(ctx.get("stale_references") or [])
        if stale_refs:
            consistency["stale_references"] = stale_refs
            consistency["stale_reference_count"] = len(stale_refs)
        continuity = ctx.get("continuity_report")
        if continuity is not None:
            consistency["continuity"] = continuity.to_dict()
            consistency["continuity_ok"] = continuity.ok
        if registry_save_error:
            consistency["registry_save_error"] = registry_save_error
        return consistency
