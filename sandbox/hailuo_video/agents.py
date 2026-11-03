"""Product Domain Agents。

每个 Agent 继承 Agent，实现 _default_execute / _default_validate / _default_key。
execute 委托现有 engine，返回 AgentResult。DAG 注册表是 ALL_AGENTS（8 个）。
ConcatAgent 仍保留类，供直接拼接；editing phase 用 EditingAgent，不进 ALL_AGENTS。
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
from hailuo_video import editing as ed_mod  # V3.2 REQ-303 EditingAgent (V3.2 重构：editing_engine → editing 包)
from hailuo_video import motion_backend as mb_mod  # V3.2 重构：从 line 717 移至顶部（R1 imports 集中）
from hailuo_video.motion_backend import MotionError, MotionResult
from hailuo_video.script_engine import Script, Storyboard


# ---------- AgentResult + Errors ----------

def _scoped_id(prefix: str, shot_idx: Any, path: Any) -> str:
    """按 shot 隔离的 artifact_id。

    V4.0 BUG-A：Registry 用 artifact_id 做 key（`artifacts[id] = artifact`），
    固定 id（如 "video_out"）会让每个 shot 的产物互相覆盖。

    优先用 shot_idx；没有 shot_idx 时退回输出文件名（仍然唯一）。
    """
    if shot_idx is not None:
        return f"{prefix}_{shot_idx}"
    try:
        stem = Path(str(path)).stem
    except Exception:
        stem = "out"
    return f"{prefix}_{stem}"


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


# ---------- V3.2 重构：Agent 基类 ----------

class Agent:
    """所有 Domain Agent 的基类（V3.2 重构：从 frozen-dataclass 改为 ABC）。

    设计动机：
      - V3.2 之前 9 个 Agent 各自重复 ~40 行模板代码
        （@dataclass(frozen=True) + 3 Callable fields + __post_init__ + 3 个 _default 方法签名）
      - 重构后子类只需实现 3 个 _default 方法体
      - 字段名（name / execute / validate_inputs / idempotency_key）保持不变 → 调用方零改动
      - 不强制 frozen —— 普通 Python 对象允许 `agent.execute = new_fn` 或
        `object.__setattr__(agent, "execute", ex)` 直接替换
        （test_orchestrator_resume.py 6 处依赖此可变性 —— 见 R5 注释）

    字段：
      - name: 类变量；子类覆写为 Agent 名（"script_writer" / "image_gen" / ...）
      - execute / validate_inputs / idempotency_key: 实例属性（bound method），由 __init__ 自动绑定
    """
    name: str = ""  # 子类覆写为类变量

    def __init__(self) -> None:
        # 自动绑定 _default_xxx → execute / validate_inputs / idempotency_key
        # 子类只实现 _default_xxx 方法即可
        self.execute = self._default_execute
        self.validate_inputs = self._default_validate
        self.idempotency_key = self._default_key

    def _default_execute(self, task: dict) -> "AgentResult":
        raise NotImplementedError(
            f"{type(self).__name__}._default_execute() not implemented"
        )

    def _default_validate(self, inputs: dict) -> None:
        raise NotImplementedError(
            f"{type(self).__name__}._default_validate() not implemented"
        )

    def _default_key(self, inputs: dict) -> str | None:
        """默认无缓存（多数 Agent 适用；QA / Editing / MotionGen 都返 None）。"""
        return None


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

class ScriptWriterAgent(Agent):
    """novel text → Storyboard JSON。"""

    name = "script_writer"
    # V5.0 Batch 5：角色契约（见 roles.py）
    role_name = "script_writer"

    def _default_execute(self, task: dict) -> AgentResult:
        novel = task.get("novel", "")
        if not novel:
            return AgentResult(ok=False, error="novel text empty")
        # V3.2 自动开发冲刺（轮 5）：注入 backend（默认 minimax，否则 mock）
        backend = task.get("backend")
        if backend is None:
            from hailuo_video import script_engine as _se_mod
            backend_pref = task.get("backend_prefer", "auto")
            backend = _se_mod.make_backend(backend_pref)
        try:
            # V5.0 Batch 1：透传 project_id → LLM 审计日志真正上线。
            #   此前生产从不传，`audit_llm` 的 `if project_id:` 恒假 → 零审计产出。
            sb = se_mod.novel_to_storyboard(
                novel, backend, project_id=str(task.get("project_id", "") or ""),
            )
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


class AssetPrepAgent(Agent):
    """Storyboard → assets/ 17 reference sheets。"""

    name = "asset_prep"
    # V5.0 Batch 5：角色契约（见 roles.py）
    role_name = "asset_prep"

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


class ImageGenAgent(Agent):
    """prompt + subject_references[] → JPG。"""

    name = "image_gen"
    # V5.0 Batch 5：角色契约（见 roles.py）
    role_name = "image_gen"

    def _default_execute(self, task: dict) -> AgentResult:
        backend = task.get("image_backend")
        prompt = task.get("prompt", "")
        if backend is None:
            return AgentResult(ok=False, error="image_backend missing")
        if not prompt:
            return AgentResult(ok=False, error="prompt empty")
        refs_for_backend = self._reference_urls(task, backend)
        try:
            results = backend.generate(
                prompt,
                aspect_ratio_value=self._resolve_aspect_ratio(task, backend),
                subject_reference=refs_for_backend,
                # V3.8 W4：负提示词（后端不支持独立字段时内联进 prompt）
                negative_prompt=str(task.get("negative_prompt", "") or ""),
            )
            # V3.2 自动开发冲刺（轮 6）：把 bytes 落盘到 intermediate_dir，
            # artifact.path 用文件路径（之前直接传 bytes 给 compute_content_hash 抛 TypeError）。
            artifacts = []
            inter_dir = task.get("intermediate_dir")
            shot_idx = task.get("shot_idx")
            for idx, r in enumerate(results):
                img_path = self._save_image_bytes(r, idx, inter_dir, shot_idx=shot_idx)
                artifacts.append(Artifact(
                    artifact_id=self._image_artifact_id(shot_idx, idx),
                    kind="image",
                    path=img_path,
                    producer=self.name,
                    metadata={"model": r.model, "duration": r.duration_seconds, "shot_idx": shot_idx},
                ))
            return AgentResult(ok=True, artifacts=artifacts, metadata={"results": results})
        except Exception as exc:
            return AgentResult(ok=False, error=f"{type(exc).__name__}: {exc}")

    @staticmethod
    def _image_artifact_id(shot_idx: Any, idx: int) -> str:
        """V3.8 缺陷 15：artifact_id 必须按 shot 隔离。

        旧实现恒为 f"image_{idx}"。n=1 时**每个 shot 都是 "image_0"**，而
        `Registry.add` 用 artifact_id 做 key（`self.artifacts[artifact.artifact_id] = artifact`）
        → 后一个 shot 覆盖前一个；`by_kind` 因 id 去重也只剩一项。
        后果：多镜 storyboard 里所有镜头共用最后一张图，
        且 `_get_image_path_for_shot` 对非首镜永远落到"第一个可用图"兜底。

        新规则：每个 shot 的首张图 = "image_{shot_idx}"；
        同 shot 的第 2..n 张 = "image_{shot_idx}_v{idx}"（不撞名）。
        无 shot_idx（直接单测 agent）→ 退回旧的 f"image_{idx}"。
        """
        if shot_idx is None:
            return f"image_{idx}"
        return f"image_{shot_idx}" if idx == 0 else f"image_{shot_idx}_v{idx}"

    @staticmethod
    def _image_stem(shot_idx: Any, idx: int) -> str:
        """V3.8 缺陷 15：落盘文件名按 shot 隔离（与 _image_artifact_id 同规则）。"""
        if shot_idx is None:
            return f"image_{idx:03d}"
        try:
            base = int(shot_idx)
        except (TypeError, ValueError):
            return f"image_{idx:03d}"
        return f"image_{base:03d}" if idx == 0 else f"image_{base:03d}_v{idx}"

    @staticmethod
    def _resolve_aspect_ratio(task: dict, backend: Any) -> str:
        """V3.8 缺陷 1：画幅解析优先级 = task 显式值 > backend 自己的 env 默认 > "16:9"。

        旧实现硬传 `task.get("aspect_ratio", "16:9")`，backend 的 env 默认永远轮不到，
        导致 .env 的 MINIMAX_IMAGE_ASPECT_RATIO=9:16 在 V3 生产路径失效
        （image_engine.default_aspect_ratio 读的就是这个 env）。
        """
        explicit = str(task.get("aspect_ratio") or "").strip()
        if explicit:
            return explicit
        getter = getattr(backend, "default_aspect_ratio", None)
        if callable(getter):
            try:
                value = getter()
            except Exception:
                value = ""
            if value:
                return str(value)
        return "16:9"

    def _save_image_bytes(self, r, idx, inter_dir, *, shot_idx: Any = None) -> Path:
        """V3.2 自动开发冲刺（轮 6）：把 ImageResult.bytes 落盘成文件。

        V3.2 自动开发冲刺（轮 15 修正）：如果 image_url 是 MiniMax OSS 公网 URL，
        先下载到 inter_dir 落盘，再返回本地路径。原因是 orchestrator 的 _get_image_path_for_shot
        只接受本地路径（URL 不满足 Path.exists()），导致 motion_gen image_path 为空 → Hailuo 不调用。

        V3.2 自动开发冲刺（轮 15 修正 2）：inter_dir 是 Linux-style 路径（/tmp/...）
        时，Windows Python 会建到 cwd 下相对路径——但 artifact.path 仍存 Linux-style，
        导致 Path.exists() 返 False。用 .resolve() 强转绝对 Windows 路径。

        V3.8 缺陷 15：文件名也必须按 shot 隔离，否则多镜 storyboard 里
        每个 shot 都写同一个 image_000.jpg，后一个覆盖前一个。
        """
        from pathlib import Path as _P
        out_dir = _P(inter_dir).resolve() if inter_dir else _P("./intermediate").resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        ext = ".jpg" if "jpeg" in (r.mime_type or "jpeg") else ".png"
        out_path = (out_dir / f"{self._image_stem(shot_idx, idx)}{ext}").resolve()

        # 优先 image_url（MiniMax OSS 公网 URL）→ 下载到 inter_dir
        url_val = getattr(r, "image_url", None)
        if url_val:
            url = url_val[0] if isinstance(url_val, list) and url_val else url_val
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                try:
                    import urllib.request
                    urllib.request.urlretrieve(url, str(out_path))
                    if out_path.is_file() and out_path.stat().st_size > 1000:
                        return str(out_path)
                except Exception:
                    pass  # 下载失败 → 落 fallback to bytes
        # 回退：写 r.bytes 到 inter_dir
        if not r.bytes:
            raise ValueError(f"ImageResult #{idx} 既无 image_url 也无 bytes")
        out_path.write_bytes(r.bytes)
        return str(out_path)

    @staticmethod
    def _reference_urls(task: dict, image_backend: Any = None) -> list[str] | None:
        """合并 subject_references 与 scene_references，最多 4 个。

        角色名（「橘猫」）不会传给 image-01。场景 URL 已在 subject 列表里时不重复追加。

        reference 可能是 http(s) URL（MiniMax 缓存）或本地图片路径（Gemini 缓存）。
        R4：backend 声明 supports_local_refs 时不做 http 过滤；不支持时维持原过滤，
        交给 backend 显式报错——不在这里静默丢弃（那会让角色一致性无声消失）。
        """
        from hailuo_video.asset_prep import MAX_SUBJECT_REFERENCES

        allow_local = bool(getattr(image_backend, "supports_local_refs", False))

        def _urls(value) -> list[str]:
            if not value:
                return []
            items = [value] if isinstance(value, str) else list(value)
            if allow_local:
                return [item for item in items if isinstance(item, str) and item.strip()]
            return [
                item for item in items
                if isinstance(item, str) and item.startswith(("http://", "https://"))
            ]

        merged: list[str] = []
        for url in _urls(task.get("subject_references")) + _urls(task.get("scene_references")):
            if url not in merged and len(merged) < MAX_SUBJECT_REFERENCES:
                merged.append(url)
        return merged or None

    def _default_validate(self, inputs: dict):
        if not inputs.get("prompt", "").strip():
            raise InputValidationError("ImageGenAgent requires 'prompt' input")

    def _default_key(self, inputs: dict) -> str | None:
        # V3.3：idempotency_key 包含 scene_references，避免缓存误命中
        key_parts = [
            inputs.get("prompt", ""),
            str(inputs.get("scene_references", [])),
            str(inputs.get("subject_references", [])),
            inputs.get("aspect_ratio", ""),
        ]
        return hashlib.sha256("|".join(key_parts).encode()).hexdigest()[:16]


class VideoSynthAgent(Agent):
    """JPG → MP4（Ken Burns ffmpeg zoompan）。"""

    name = "video_synth"
    # V5.0 Batch 5：角色契约（见 roles.py）
    role_name = "video_synth"

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
                # V4.0 BUG-A：artifact_id 必须按 shot 隔离。
                #   旧实现恒为 "video_out" → Registry.add 用 id 做 key
                #   （`self.artifacts[artifact.artifact_id] = artifact`）→
                #   **每个 shot 的 Ken Burns 视频互相覆盖**，registry 里只剩最后一镜，
                #   by_kind 也因去重只剩一项 → editing 阶段只拿到 1 段视频。
                #   实测：3 镜 storyboard → by_kind_iter("video") 只返回最后 1 镜。
                shot_idx = task.get("shot_idx")
                artifacts = [Artifact(
                    artifact_id=_scoped_id("video", shot_idx, output_path),
                    kind="video",
                    path=result.output_path,
                    producer=self.name,
                    metadata={
                        "effect": result.effect, "seconds": result.seconds,
                        "fps": result.fps,
                        "shot_idx": shot_idx,
                    },
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


class TTSAgent(Agent):
    """text → MP3 + VTT。"""

    name = "tts"
    # V5.0 Batch 5：角色契约（见 roles.py）
    role_name = "tts"

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
            # V4.0 BUG-A：同 VideoSynthAgent —— 固定 id 会让每个 shot 的配音互相覆盖
            #   （registry 里只剩最后一镜的音频）。
            shot_idx = task.get("shot_idx")
            artifacts = [Artifact(
                artifact_id=_scoped_id("tts_audio", shot_idx, output),
                kind="audio",
                path=output,
                producer=self.name,
                metadata={"voice": task.get("voice", ""), "shot_idx": shot_idx},
            )]
            if result.sub_file:
                artifacts.append(Artifact(
                    artifact_id=_scoped_id("tts_subtitle", shot_idx, result.sub_file),
                    kind="subtitle",
                    path=Path(result.sub_file),
                    producer=self.name,
                    metadata={"shot_idx": shot_idx},
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


class ConcatAgent(Agent):
    """per-shot mp4+mp3+vtt → final.mp4。"""

    name = "concat"

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


class QAAgent(Agent):
    """post-flight 一致性检查（color + face）。

    输入 task 字段：
      - image_paths: list[str | Path] —— 必填，至少 2 张才能算一致性
      - mode: str = "color"  —— 检查模式
          "color"    → 仅色彩一致性（V2.6 原有）
          "face"     → 仅面部相似度（REQ-402）
          "combined" → 色彩 + 面部
      - face_threshold: float = 0.85 —— face mode 阈值
      - face_backend: str = "hash" —— "hash" | "null" | "face_recognition"
    """

    name = "qa"
    # V5.0 Batch 5：角色契约（见 roles.py）
    role_name = "qa"

    def _default_execute(self, task: dict) -> AgentResult:
        image_paths = task.get("image_paths", [])
        mode = task.get("mode", "color")
        metadata: dict = {"mode": mode, "image_count": len(image_paths)}

        if not image_paths:
            return AgentResult(ok=True, metadata={"skipped": True, "reason": "no image_paths"})

        try:
            if mode in ("color", "combined"):
                report = q_mod.compute_color_consistency([Path(p) for p in image_paths])
                metadata["color_report"] = report.to_dict() if hasattr(report, "to_dict") else str(report)
                metadata["mean_similarity"] = getattr(report, "mean_similarity", 1.0)
                metadata["min_similarity"] = getattr(report, "min_similarity", 1.0)
                metadata["color_ok"] = getattr(report, "ok", True)
            if mode in ("face", "combined"):
                backend_name = task.get("face_backend", "hash")
                backend = _make_face_backend(backend_name)
                face_report = q_mod.compute_face_consistency(
                    [Path(p) for p in image_paths],
                    threshold=task.get("face_threshold", 0.85),
                    backend=backend,
                )
                metadata["face_report"] = face_report.to_dict()
                metadata["face_mean_similarity"] = face_report.mean_similarity
                metadata["face_min_similarity"] = face_report.min_similarity
                metadata["face_ok"] = face_report.ok
            ok = metadata.get("color_ok", True) and metadata.get("face_ok", True)
            return AgentResult(
                ok=ok,
                artifacts=[],
                metadata=metadata,
            )
        except Exception as exc:
            return AgentResult(ok=False, error=f"{type(exc).__name__}: {exc}")

    def _default_validate(self, inputs: dict):
        # image_paths 可选（空跳过），无需必填校验
        mode = inputs.get("mode", "color")
        if mode not in ("color", "face", "combined"):
            raise InputValidationError(
                f"QAAgent unknown mode={mode!r}（允许 color/face/combined）"
            )
        backend = inputs.get("face_backend", "hash")
        if mode in ("face", "combined") and backend not in ("hash", "null", "face_recognition"):
            raise InputValidationError(
                f"QAAgent unknown face_backend={backend!r}（允许 hash/null/face_recognition）"
            )

    def _default_key(self, inputs: dict) -> str | None:
        # QA 无副作用，无需 cache
        return None


def _make_face_backend(name: str):
    """内部辅助：根据 name 选 face backend。"""
    if name == "hash":
        return q_mod.HashFaceSimilarityBackend()
    if name == "null":
        return q_mod.NullFaceSimilarityBackend()
    if name == "face_recognition":
        return q_mod.RealFaceRecognitionBackend()
    raise InputValidationError(f"unknown face backend: {name!r}")


# ---------- V3.2 REQ-303 EditingAgent ----------

class EditingAgent(Agent):
    """V3.2 REQ-303：后期处理。

    输入 task 字段：
      - video_paths: list[str | Path]  —— 必填，至少 1 段
      - output_path: str | Path        —— 必填（mode=full 时），最终产物路径
      - output_dir: str | Path         —— 必填（mode=color_normalize 时），归一化产物目录
      - mode: str = "color_normalize"  —— 子功能选择
          "color_normalize" → 色彩归一化（默认）
          "crossfade"       → 仅叠化
          "motion_match"    → 仅算运动峰值
          "apply_lut"       → 仅 LUT
          "full"            → 完整管线（color_normalize → motion_match → crossfade → LUT）
      - lut_path: str | Path | None    —— mode=apply_lut 或 full 时可选
      - transition: str = "fade"       —— mode 含 crossfade 时使用
      - overlap_seconds: float = 0.25  —— mode 含 crossfade 时使用
    """

    name = "editing"
    # V5.0 Batch 5：角色契约（见 roles.py）
    role_name = "editing"

    def _default_execute(self, task: dict) -> AgentResult:
        mode = task.get("mode", "color_normalize")
        try:
            from hailuo_video.artifact import Artifact
            if mode == "color_normalize":
                results = ed_mod.color_normalize_pipeline(
                    video_paths=task["video_paths"],
                    output_dir=task["output_dir"],
                    timeout=task.get("timeout", 180),
                )
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
                        "mode": mode,
                        "clip_count": len(results),
                        "eq_filters": [r.eq_filter for r in results],
                        "target_stats": results[0].target_stats.to_dict() if results else {},
                    },
                )
            elif mode == "crossfade":
                result = ed_mod.crossfade_clips(
                    video_paths=task["video_paths"],
                    output_path=task["output_path"],
                    transition=task.get("transition", "fade"),
                    overlap_seconds=task.get("overlap_seconds", 0.25),
                )
                return AgentResult(
                    ok=True,
                    artifacts=[Artifact(
                        artifact_id="editing_crossfade",
                        kind="video",
                        path=result.output_path,
                        producer=self.name,
                        metadata=result.to_dict(),
                    )],
                    metadata={"mode": mode, **result.to_dict()},
                )
            elif mode == "motion_match":
                peaks = ed_mod.motion_match_cut_points(
                    video_paths=task["video_paths"],
                    sample_count=task.get("sample_count", 8),
                )
                return AgentResult(
                    ok=True,
                    artifacts=[],
                    metadata={
                        "mode": mode,
                        "motion_peaks": [m.to_dict() for m in peaks],
                    },
                )
            elif mode == "apply_lut":
                result = ed_mod.apply_lut(
                    video_path=task["video_path"],
                    output_path=task["output_path"],
                    lut_path=task["lut_path"],
                )
                return AgentResult(
                    ok=True,
                    artifacts=[Artifact(
                        artifact_id="editing_lut",
                        kind="video",
                        path=result.output_path,
                        producer=self.name,
                        metadata=result.to_dict(),
                    )],
                    metadata={"mode": mode, **result.to_dict()},
                )
            elif mode == "full":
                # V3.7 P0-3：SSIM 自检门禁（仅报警，不阻断 — 用户决策保守）
                # 目的：把 daily_review_all_20261009.md 中 5 个 final 视频的 3-4 帧突跳
                #       提前到 editing 阶段暴露（不动 quota，不阻断生产）。
                # 依据：stage A SUMMARY 揭出"拼接层 + hailuo 单段"双层根因；
                #       本 gate 覆盖拼接层（editing 前），单段层由 motion_gen phase 后续扩展。
                # DeepSeek Q5 双锚定（last_frame_image）被 stage B 验证为不支持，本 gate 是替代路径。
                from hailuo_video.quality import compute_frame_quality
                import logging as _logging
                _ssim_log = _logging.getLogger("hailuo_video.editing.ssim_gate")
                _ssim_results: list[dict] = []
                for _vp in task["video_paths"]:
                    try:
                        _rep = compute_frame_quality(_vp, profile="motion")
                        _ssim_results.append({
                            "video": str(_vp),
                            "severity": _rep.severity,
                            "mean_ssim": _rep.mean_ssim,
                            "min_ssim": _rep.min_ssim,
                        })
                        if _rep.severity in ("critical", "major"):
                            _ssim_log.warning(
                                "ssim_gate_fail path=%s severity=%s min_ssim=%.3f mean_ssim=%.3f",
                                _vp, _rep.severity, _rep.min_ssim, _rep.mean_ssim,
                            )
                    except FileNotFoundError:
                        _ssim_log.warning("ssim_gate_skip path=%s reason=not_found", _vp)
                    except Exception as _exc:
                        _ssim_log.warning("ssim_gate_skip path=%s reason=%s: %s", _vp, type(_exc).__name__, _exc)
                result = ed_mod.full_editing_pipeline(
                    # V4.0 BUG-C：把每镜的旁白 / 字幕传进去（此前从不传 → 成片无配音无字幕）
                    audio_paths=list(task.get("audio_paths") or []),
                    subtitle_paths=list(task.get("subtitle_paths") or []),
                    video_paths=task["video_paths"],
                    output_path=task["output_path"],
                    lut_path=task.get("lut_path"),
                    transition=task.get("transition", "fade"),
                    overlap_seconds=task.get("overlap_seconds", 0.25),
                )
                return AgentResult(
                    ok=True,
                    artifacts=[Artifact(
                        artifact_id="editing_full",
                        kind="video",
                        path=result.final_path,
                        producer=self.name,
                        metadata=result.to_dict(),
                    )],
                    metadata={"mode": mode, "ssim_gate": _ssim_results, **result.to_dict()},
                )
            else:
                return AgentResult(ok=False, error=f"EditingAgent unknown mode={mode!r}")
        except ed_mod.EditingError as exc:
            return AgentResult(ok=False, error=f"editing fail：{exc}")
        except Exception as exc:
            return AgentResult(ok=False, error=f"{type(exc).__name__}: {exc}")

    def _default_validate(self, inputs: dict):
        vps = inputs.get("video_paths")
        if not vps or not isinstance(vps, list):
            raise InputValidationError("EditingAgent requires 'video_paths' (non-empty list)")
        mode = inputs.get("mode", "color_normalize")
        if mode == "color_normalize":
            if not inputs.get("output_dir"):
                raise InputValidationError("EditingAgent(mode=color_normalize) requires 'output_dir'")
        elif mode == "crossfade":
            if not inputs.get("output_path"):
                raise InputValidationError("EditingAgent(mode=crossfade) requires 'output_path'")
        elif mode == "apply_lut":
            for k in ("video_path", "output_path", "lut_path"):
                if not inputs.get(k):
                    raise InputValidationError(f"EditingAgent(mode=apply_lut) requires '{k}'")
        elif mode == "full":
            if not inputs.get("output_path"):
                raise InputValidationError("EditingAgent(mode=full) requires 'output_path'")
        elif mode == "motion_match":
            pass  # video_paths 已校验
        else:
            raise InputValidationError(f"EditingAgent unknown mode={mode!r}")

    def _default_key(self, inputs: dict) -> str | None:
        # 归一化不依赖外部 API，但 ffmpeg 重编码有时间成本 → 不缓存
        return None


# ---------- V3.1 MotionGenerationAgent ----------

class MotionGenerationAgent(Agent):
    """V3.1：真 AI 视频生成（Hailuo-2.3）。"""

    name = "motion_gen"
    # V5.0 Batch 5：角色契约（见 roles.py）
    role_name = "motion_gen"

    def _default_execute(self, task: dict) -> AgentResult:
        try:
            from hailuo_video.artifact import Artifact
            backend = task.get("motion_backend")
            if backend is None:
                # V5.0 Batch 3：只有走**真**后端才需要 API key ——
                #   mock 后端不该因为没配 key 就失败。
                mb_mod._api_key()
                backend = mb_mod.make_motion_backend("minimax")
            motion_result: MotionResult = backend.generate(
                task["subject_action"],
                duration=task.get("duration", 6),
                resolution=task.get("resolution", "1080P"),
                output_path=task["output_path"],
                # V3.4 W3 hotfix：传 first_frame_image 走 I2V 路径（否则 T2V 自由发挥 → "小黄变猫"）
                first_frame_image=task.get("first_frame_image") or "",
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
                        # V4.0 BUG-B：editing 阶段按 shot_idx 分组选片，需要它
                        "shot_idx": task.get("shot_idx", task.get("shot_id")),
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

# DAG 上的 8 个 agent。ConcatAgent 不在此列：editing phase 已由 EditingAgent 接管。
ALL_AGENTS = [
    ScriptWriterAgent(),
    AssetPrepAgent(),
    ImageGenAgent(),
    MotionGenerationAgent(),
    VideoSynthAgent(),
    TTSAgent(),
    EditingAgent(),
    QAAgent(),
]


def get_agent(name: str):
    """按 name 查 agent。"""
    for a in ALL_AGENTS:
        if a.name == name:
            return a
    raise KeyError(f"unknown agent: {name}")


__all__ = [
    "Agent",
    "AgentResult",
    "InputValidationError",
    "AgentExecutionError",
    "ScriptWriterAgent",
    "AssetPrepAgent",
    "ImageGenAgent",
    "MotionGenerationAgent",
    "VideoSynthAgent",
    "TTSAgent",
    "ConcatAgent",
    "EditingAgent",
    "QAAgent",
    "ALL_AGENTS",
    "get_agent",
]
