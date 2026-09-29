"""V3.0 Phase 2 — 7 个 Product Domain Agent 描述符。

设计原则（plan）：
  - structural typing（非 ABC 继承）—— 每个 Agent 是 frozen @dataclass + 3 callables
  - execute 委托现有 module 函数（零重构）
  - execute 返回 AgentResult（ok / artifacts / error）
  - validate_inputs 在 execute 前调，失败抛 InputValidationError
  - idempotency_key 给 Orchestrator 用于 cache lookup

Agents:
  1. ScriptWriterAgent  ← script_engine.novel_to_storyboard
  2. AssetPrepAgent     ← asset_prep.prepare_all_characters + ... + prepare_all_scene_variants
  3. ImageGenAgent      ← image_engine.MiniMaxImageBackend.generate
  4. VideoSynthAgent    ← video_engine.KenBurnsBackend.render
  5. TTSAgent           ← tts_engine.EdgeTTSBackend.synth
  6. ConcatAgent        ← concat_engine.concat_pipeline
  7. QAAgent            ← quality.compute_color_consistency
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from hailuo_video.artifact import Artifact
from hailuo_video import script_engine as se_mod
from hailuo_video import asset_prep as ap_mod
from hailuo_video import image_engine as ie_mod
from hailuo_video import video_engine as ve_mod
from hailuo_video import tts_engine as te_mod
from hailuo_video import concat_engine as ce_mod
from hailuo_video import quality as q_mod
from hailuo_video import editing_engine as ed_mod  # V3.2 REQ-303 EditingAgent
from hailuo_video.script_engine import Script, Storyboard


# ---------- AgentResult + Errors ----------

@dataclass
class AgentResult:
    """Agent execute() 的返回值。"""
    ok: bool
    artifacts: list[Artifact] = field(default_factory=list)
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class InputValidationError(ValueError):
    """Agent.validate_inputs() 失败时抛。"""


class AgentExecutionError(RuntimeError):
    """Agent.execute() 失败时抛（区别于网络瞬态错误，不重试）。"""


# ---------- AgentResult helpers ----------

def artifact_from_path(path: Path, kind: str, producer: str, metadata: dict | None = None) -> Artifact:
    """从 path 创建 Artifact + 自动算 content_hash。"""
    return Artifact(
        artifact_id=hashlib.sha256(str(path).encode()).hexdigest()[:16],
        kind=kind,
        path=Path(path),
        producer=producer,
        metadata=metadata or {},
    )


# ---------- 7 Domain Agents ----------

@dataclass(frozen=True)
class ScriptWriterAgent:
    """novel text → Storyboard JSON。"""
    name: str = "script_writer"
    execute: Callable[..., AgentResult] = field(default=None)
    validate_inputs: Callable[[dict], None] = field(default=None)
    idempotency_key: Callable[[dict], str | None] = field(default=None)

    def __post_init__(self):
        object.__setattr__(self, "execute", self._default_execute)
        object.__setattr__(self, "validate_inputs", self._default_validate)
        object.__setattr__(self, "idempotency_key", self._default_key)

    def _default_execute(self, task: dict) -> AgentResult:
        novel = task.get("novel", "")
        if not novel:
            return AgentResult(ok=False, error="novel text empty")
        try:
            sb = se_mod.novel_to_storyboard(novel)
            artifacts = [
                Artifact(
                    artifact_id="storyboard_json",
                    kind="storyboard",
                    path=task.get("storyboard_output", Path("storyboard.json")),
                    producer=self.name,
                    metadata={"shots": len(sb.shots)},
                )
            ]
            return AgentResult(ok=True, artifacts=artifacts, metadata={"storyboard": sb})
        except Exception as exc:
            return AgentResult(ok=False, error=f"{type(exc).__name__}: {exc}")

    def _default_validate(self, inputs: dict):
        if not inputs.get("novel", "").strip():
            raise InputValidationError("ScriptWriterAgent requires 'novel' input (non-empty)")

    def _default_key(self, inputs: dict) -> str | None:
        return hashlib.sha256(inputs.get("novel", "").encode()).hexdigest()[:16]


@dataclass(frozen=True)
class AssetPrepAgent:
    """Storyboard → assets/ 17 reference sheets。"""
    name: str = "asset_prep"
    execute: Callable[..., AgentResult] = field(default=None)
    validate_inputs: Callable[[dict], None] = field(default=None)
    idempotency_key: Callable[[dict], str | None] = field(default=None)

    def __post_init__(self):
        object.__setattr__(self, "execute", self._default_execute)
        object.__setattr__(self, "validate_inputs", self._default_validate)
        object.__setattr__(self, "idempotency_key", self._default_key)

    def _default_execute(self, task: dict) -> AgentResult:
        sb: Storyboard | None = task.get("storyboard")
        backend = task.get("image_backend")
        assets_dir: Path = Path(task.get("assets_dir", "./assets"))
        if sb is None or backend is None:
            return AgentResult(ok=False, error="storyboard or image_backend missing")
        try:
            char_assets = ap_mod.prepare_all_characters(sb, backend, assets_dir=assets_dir)
            expr_assets = ap_mod.prepare_all_expressions(sb, backend, assets_dir=assets_dir)
            scene_assets = ap_mod.prepare_all_scene_variants(sb, backend, assets_dir=assets_dir)
            artifacts = []
            for name, angles in char_assets.items():
                for a, r in angles.items():
                    if r.ok:
                        artifacts.append(Artifact(
                            artifact_id=f"char_{name}_{a}",
                            kind="character_ref",
                            path=r.image_path,
                            producer=self.name,
                            metadata={"angle": a, "kind_sub": "char"},
                        ))
            for name, exprs in expr_assets.items():
                for e, r in exprs.items():
                    if r.ok:
                        artifacts.append(Artifact(
                            artifact_id=f"expr_{name}_{e}",
                            kind="character_ref",
                            path=r.image_path,
                            producer=self.name,
                            metadata={"expression": e, "kind_sub": "expr"},
                        ))
            for sid, vars_ in scene_assets.items():
                for v, r in vars_.items():
                    if r.ok:
                        artifacts.append(Artifact(
                            artifact_id=f"scene_{sid}_{v}",
                            kind="scene_ref",
                            path=r.image_path,
                            producer=self.name,
                            metadata={"variant": v},
                        ))
            return AgentResult(ok=True, artifacts=artifacts)
        except Exception as exc:
            return AgentResult(ok=False, error=f"{type(exc).__name__}: {exc}")

    def _default_validate(self, inputs: dict):
        if inputs.get("storyboard") is None:
            raise InputValidationError("AssetPrepAgent requires 'storyboard' input")
        if inputs.get("image_backend") is None:
            raise InputValidationError("AssetPrepAgent requires 'image_backend' input")

    def _default_key(self, inputs: dict) -> str | None:
        sb: Storyboard | None = inputs.get("storyboard")
        if sb is None:
            return None
        # key by storyboard content hash + assets_dir
        try:
            sb_json = json.dumps(sb.to_dict(), sort_keys=True, ensure_ascii=False)
        except Exception:
            sb_json = str(id(sb))
        return hashlib.sha256((sb_json + str(inputs.get("assets_dir", ""))).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class ImageGenAgent:
    """prompt + subject_references[] → JPG。"""
    name: str = "image_gen"
    execute: Callable[..., AgentResult] = field(default=None)
    validate_inputs: Callable[[dict], None] = field(default=None)
    idempotency_key: Callable[[dict], str | None] = field(default=None)

    def __post_init__(self):
        object.__setattr__(self, "execute", self._default_execute)
        object.__setattr__(self, "validate_inputs", self._default_validate)
        object.__setattr__(self, "idempotency_key", self._default_key)

    def _default_execute(self, task: dict) -> AgentResult:
        backend = task.get("image_backend")
        prompt = task.get("prompt", "")
        if backend is None:
            return AgentResult(ok=False, error="image_backend missing")
        if not prompt:
            return AgentResult(ok=False, error="prompt empty")
        try:
            results = backend.generate(
                prompt,
                aspect_ratio_value=task.get("aspect_ratio", "16:9"),
                subject_reference=task.get("subject_references"),
            )
            artifacts = []
            for idx, r in enumerate(results):
                artifacts.append(Artifact(
                    artifact_id=f"image_{idx}",
                    kind="image",
                    path=r.bytes if r.bytes else r.image_url,  # placeholder
                    producer=self.name,
                    metadata={"model": r.model, "duration": r.duration_seconds, "shot_idx": task.get("shot_idx")},
                ))
            return AgentResult(ok=True, artifacts=artifacts, metadata={"results": results})
        except Exception as exc:
            return AgentResult(ok=False, error=f"{type(exc).__name__}: {exc}")

    def _default_validate(self, inputs: dict):
        if not inputs.get("prompt", "").strip():
            raise InputValidationError("ImageGenAgent requires 'prompt' input")

    def _default_key(self, inputs: dict) -> str | None:
        key_parts = [
            inputs.get("prompt", ""),
            str(inputs.get("subject_references", [])),
            inputs.get("aspect_ratio", ""),
        ]
        return hashlib.sha256("|".join(key_parts).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class VideoSynthAgent:
    """JPG → MP4（Ken Burns ffmpeg zoompan）。"""
    name: str = "video_synth"
    execute: Callable[..., AgentResult] = field(default=None)
    validate_inputs: Callable[[dict], None] = field(default=None)
    idempotency_key: Callable[[dict], str | None] = field(default=None)

    def __post_init__(self):
        object.__setattr__(self, "execute", self._default_execute)
        object.__setattr__(self, "validate_inputs", self._default_validate)
        object.__setattr__(self, "idempotency_key", self._default_key)

    def _default_execute(self, task: dict) -> AgentResult:
        video_backend = task.get("video_backend")
        image_path = task.get("image_path")
        output_path = task.get("output_path")
        if video_backend is None or image_path is None or output_path is None:
            return AgentResult(ok=False, error="video_backend / image_path / output_path missing")
        try:
            result = video_backend.render(
                image_path,
                output_path,
                seconds=task.get("seconds", 5),
                fps=task.get("fps", 8),
                effect=task.get("effect", "zoom_in"),
            )
            if result.ok:
                artifacts = [Artifact(
                    artifact_id="video_out",
                    kind="video",
                    path=result.output_path,
                    producer=self.name,
                    metadata={"effect": result.effect, "seconds": result.seconds, "fps": result.fps},
                )]
                return AgentResult(ok=True, artifacts=artifacts, metadata={"video_result": result})
            return AgentResult(ok=False, error=result.error)
        except Exception as exc:
            return AgentResult(ok=False, error=f"{type(exc).__name__}: {exc}")

    def _default_validate(self, inputs: dict):
        for key in ("image_path", "output_path"):
            if not inputs.get(key):
                raise InputValidationError(f"VideoSynthAgent requires '{key}' input")

    def _default_key(self, inputs: dict) -> str | None:
        return hashlib.sha256(
            f"{inputs.get('image_path','')}|{inputs.get('effect','')}|{inputs.get('seconds',5)}".encode()
        ).hexdigest()[:16]


@dataclass(frozen=True)
class TTSAgent:
    """text → MP3 + VTT。"""
    name: str = "tts"
    execute: Callable[..., AgentResult] = field(default=None)
    validate_inputs: Callable[[dict], None] = field(default=None)
    idempotency_key: Callable[[dict], str | None] = field(default=None)

    def __post_init__(self):
        object.__setattr__(self, "execute", self._default_execute)
        object.__setattr__(self, "validate_inputs", self._default_validate)
        object.__setattr__(self, "idempotency_key", self._default_key)

    def _default_execute(self, task: dict) -> AgentResult:
        tts_backend = task.get("tts_backend")
        text = task.get("text", "")
        output = task.get("output")
        if tts_backend is None or not text or not output:
            return AgentResult(ok=False, error="tts_backend / text / output missing")
        try:
            result = tts_backend.synth(
                text,
                output,
                voice=task.get("voice"),
                rate=task.get("rate", ""),
                volume=task.get("volume", ""),
                pitch=task.get("pitch", ""),
                write_subtitle=task.get("subtitle"),
            )
            artifacts = [Artifact(
                artifact_id="tts_audio",
                kind="audio",
                path=output,
                producer=self.name,
                metadata={"voice": task.get("voice", "")},
            )]
            if result.sub_file:
                artifacts.append(Artifact(
                    artifact_id="tts_subtitle",
                    kind="subtitle",
                    path=Path(result.sub_file),
                    producer=self.name,
                ))
            return AgentResult(ok=True, artifacts=artifacts, metadata={"tts_result": result})
        except Exception as exc:
            return AgentResult(ok=False, error=f"{type(exc).__name__}: {exc}")

    def _default_validate(self, inputs: dict):
        if not inputs.get("text", "").strip():
            raise InputValidationError("TTSAgent requires 'text' input")
        if not inputs.get("output"):
            raise InputValidationError("TTSAgent requires 'output' path")

    def _default_key(self, inputs: dict) -> str | None:
        return hashlib.sha256(
            f"{inputs.get('text','')}|{inputs.get('voice','')}".encode()
        ).hexdigest()[:16]


@dataclass(frozen=True)
class ConcatAgent:
    """per-shot mp4+mp3+vtt → final.mp4。"""
    name: str = "concat"
    execute: Callable[..., AgentResult] = field(default=None)
    validate_inputs: Callable[[dict], None] = field(default=None)
    idempotency_key: Callable[[dict], str | None] = field(default=None)

    def __post_init__(self):
        object.__setattr__(self, "execute", self._default_execute)
        object.__setattr__(self, "validate_inputs", self._default_validate)
        object.__setattr__(self, "idempotency_key", self._default_key)

    def _default_execute(self, task: dict) -> AgentResult:
        shots = task.get("shots", [])
        output = task.get("output")
        keep_intermediate = task.get("keep_intermediate", False)
        if not shots or not output:
            return AgentResult(ok=False, error="shots / output missing")
        try:
            shot_items = [
                ce_mod.ShotItem(
                    index=s.get("index", i),
                    video=s.get("video"),
                    audio=s.get("audio"),
                    subtitle=s.get("subtitle"),
                ) for i, s in enumerate(shots)
            ]
            result = ce_mod.concat_pipeline(
                shot_items,
                Path(output),
                burn_subs=task.get("burn_subs", True),
                keep_intermediate=keep_intermediate,
            )
            if result.ok:
                artifacts = [Artifact(
                    artifact_id="final_video",
                    kind="video",
                    path=result.output,
                    producer=self.name,
                    metadata={"shot_count": result.shot_count},
                )]
                return AgentResult(ok=True, artifacts=artifacts, metadata={"concat_result": result})
            return AgentResult(ok=False, error=result.error)
        except Exception as exc:
            return AgentResult(ok=False, error=f"{type(exc).__name__}: {exc}")

    def _default_validate(self, inputs: dict):
        if not inputs.get("shots"):
            raise InputValidationError("ConcatAgent requires 'shots' list")
        if not inputs.get("output"):
            raise InputValidationError("ConcatAgent requires 'output' path")

    def _default_key(self, inputs: dict) -> str | None:
        return hashlib.sha256(str(inputs.get("shots", "")).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class QAAgent:
    """post-flight color consistency 检查。"""
    name: str = "qa"
    execute: Callable[..., AgentResult] = field(default=None)
    validate_inputs: Callable[[dict], None] = field(default=None)
    idempotency_key: Callable[[dict], str | None] = field(default=None)

    def __post_init__(self):
        object.__setattr__(self, "execute", self._default_execute)
        object.__setattr__(self, "validate_inputs", self._default_validate)
        object.__setattr__(self, "idempotency_key", self._default_key)

    def _default_execute(self, task: dict) -> AgentResult:
        image_paths = task.get("image_paths", [])
        if not image_paths:
            return AgentResult(ok=True, metadata={"skipped": True, "reason": "no image_paths"})
        try:
            report = q_mod.compute_color_consistency([Path(p) for p in image_paths])
            return AgentResult(
                ok=report.ok,
                artifacts=[],
                metadata={
                    "consistency_report": report,
                    "mean_similarity": report.mean_similarity,
                    "min_similarity": report.min_similarity,
                    "warnings": report.warnings,
                },
            )
        except Exception as exc:
            return AgentResult(ok=False, error=f"{type(exc).__name__}: {exc}")

    def _default_validate(self, inputs: dict):
        # image_paths 可选（空跳过），无需必填校验
        pass

    def _default_key(self, inputs: dict) -> str | None:
        # QA 无副作用，无需 cache
        return None


# ---------- V3.2 REQ-303 EditingAgent ----------

@dataclass(frozen=True)
class EditingAgent:
    """V3.2 REQ-303：后期处理（色彩归一化先行）。

    输入 task 字段：
      - video_paths: list[str | Path]  —— 必填，至少 1 段
      - output_dir: str | Path         —— 必填，归一化产物目录
      - timeout: int = 180             —— 可选，per-clip ffmpeg timeout
    """

    name: str = "editing"
    execute: Callable[..., AgentResult] = field(default=None)
    validate_inputs: Callable[[dict], None] = field(default=None)
    idempotency_key: Callable[[dict], str | None] = field(default=None)

    def __post_init__(self):
        object.__setattr__(self, "execute", self._default_execute)
        object.__setattr__(self, "validate_inputs", self._default_validate)
        object.__setattr__(self, "idempotency_key", self._default_key)

    def _default_execute(self, task: dict) -> AgentResult:
        try:
            results = ed_mod.color_normalize_pipeline(
                video_paths=task["video_paths"],
                output_dir=task["output_dir"],
                timeout=task.get("timeout", 180),
            )
            from hailuo_video.artifact import Artifact
            artifacts = [
                Artifact(
                    artifact_id=f"editing_{i:04d}",
                    kind="video",
                    path=r.output_path,
                    producer=self.name,
                    metadata=r.to_dict(),
                )
                for i, r in enumerate(results)
            ]
            return AgentResult(
                ok=True,
                artifacts=artifacts,
                metadata={
                    "clip_count": len(results),
                    "eq_filters": [r.eq_filter for r in results],
                    "target_stats": results[0].target_stats.to_dict() if results else {},
                },
            )
        except ed_mod.EditingError as exc:
            return AgentResult(ok=False, error=f"editing fail：{exc}")
        except Exception as exc:
            return AgentResult(ok=False, error=f"{type(exc).__name__}: {exc}")

    def _default_validate(self, inputs: dict):
        vps = inputs.get("video_paths")
        if not vps or not isinstance(vps, list):
            raise InputValidationError("EditingAgent requires 'video_paths' (non-empty list)")
        if not inputs.get("output_dir"):
            raise InputValidationError("EditingAgent requires 'output_dir'")

    def _default_key(self, inputs: dict) -> str | None:
        # 归一化不依赖外部 API，但 ffmpeg 重编码有时间成本 → 不缓存
        return None


from hailuo_video import motion_backend as mb_mod
from hailuo_video.motion_backend import MotionError, MotionResult


# ---------- V3.1 MotionGenerationAgent ----------

@dataclass(frozen=True)
class MotionGenerationAgent:
    """V3.1：真 AI 视频生成（Hailuo-2.3）。"""

    name: str = "motion_gen"
    execute: Callable[..., AgentResult] = field(default=None)
    validate_inputs: Callable[[dict], None] = field(default=None)
    idempotency_key: Callable[[dict], str | None] = field(default=None)

    def __post_init__(self):
        object.__setattr__(self, "execute", self._default_execute)
        object.__setattr__(self, "validate_inputs", self._default_validate)
        object.__setattr__(self, "idempotency_key", self._default_key)

    def _default_execute(self, task: dict) -> AgentResult:
        try:
            mb_mod._api_key()
            from hailuo_video.artifact import Artifact
            backend = task.get("motion_backend")
            if backend is None:
                backend = mb_mod.make_motion_backend("minimax")
            motion_result: MotionResult = backend.generate(
                task["subject_action"],
                duration=task.get("duration", 6),
                resolution=task.get("resolution", "1080P"),
                output_path=task["output_path"],
            )
            if motion_result.status in ("ok", "submitted"):
                artifacts = [Artifact(
                    artifact_id=f"motion_{task.get('shot_id','?')}",
                    kind="video" if motion_result.status == "ok" else "video_pending",
                    path=task["output_path"],
                    producer=self.name,
                    metadata={
                        "task_id": motion_result.task_id,
                        "model": motion_result.model,
                        "status": motion_result.status,
                    },
                )]
                return AgentResult(ok=True, artifacts=artifacts, metadata={"motion": motion_result})
            return AgentResult(ok=False, error=f"motion backend status={motion_result.status}")
        except MotionError as exc:
            return AgentResult(ok=False, error=f"motion fail：{exc}")
        except Exception as exc:
            return AgentResult(ok=False, error=f"{type(exc).__name__}: {exc}")

    def _default_validate(self, inputs: dict):
        for k in ("subject_action", "image_path", "output_path"):
            if not inputs.get(k):
                raise InputValidationError(f"MotionGenerationAgent requires '{k}' input")

    def _default_key(self, inputs: dict) -> str | None:
        return None  # 真 AI 视频生成不是 idempotent → 不缓存


# ---------- 8 Agent 注册表（V3.1 加 MotionGenerationAgent） ----------

ALL_AGENTS = [
    ScriptWriterAgent(),
    AssetPrepAgent(),
    ImageGenAgent(),
    MotionGenerationAgent(),      # V3.1
    VideoSynthAgent(),
    TTSAgent(),
    ConcatAgent(),
    EditingAgent(),                # V3.2 REQ-303
    QAAgent(),
]


def get_agent(name: str):
    """按 name 查 agent。"""
    for a in ALL_AGENTS:
        if a.name == name:
            return a
    raise KeyError(f"unknown agent: {name}")


__all__ = [
    "AgentResult",
    "InputValidationError",
    "AgentExecutionError",
    "ScriptWriterAgent",
    "AssetPrepAgent",
    "ImageGenAgent",
    "MotionGenerationAgent",   # V3.1
    "VideoSynthAgent",
    "TTSAgent",
    "ConcatAgent",
    "QAAgent",
    "ALL_AGENTS",
    "get_agent",
]
