"""V2.5 Pipeline：storyboard → final.mp4 端到端编排。

串联所有引擎：image → video (Ken Burns) → tts → concat。

输入：storyboard.json（来自 script_engine 或手写）
输出：final.mp4 + 中间产物（图片 / 视频 / 音频 / 字幕 / concat manifest）

设计目标：MVP 一条命令跑全流程；失败有清晰错误信息；mock 模式全离线。
"""
from __future__ import annotations

import json
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from hailuo_video import (
    concat_engine as ce,
    image_engine as ie,
    asset_prep as ap,
    tts_engine as te,
    video_engine as ve,
)
from hailuo_video.script_engine import Character, SceneSpec, Shot, Storyboard

# V3.0 新增：orchestrator 包装
from hailuo_video.orchestrator import (  # noqa: E402
    PipelineAgent,
    PipelineConfig,
)
from hailuo_video.log import StructuredLogger  # noqa: E402


# ---------- 数据类 ----------

@dataclass
class PipelineStep:
    name: str                           # "image" / "video" / "tts" / "concat"
    index: int
    ok: bool
    duration_seconds: float = 0.0
    error: str = ""
    output_path: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineResult:
    output: str
    shot_count: int = 0
    ok_shots: int = 0
    failed_shots: int = 0
    duration_seconds: float = 0.0
    bytes: int = 0
    source: str = "ok"                   # "ok" | "error"
    error: str = ""
    steps: list[PipelineStep] = field(default_factory=list)
    intermediate_dir: str = ""

    @property
    def ok(self) -> bool:
        return self.source == "ok"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------- 从 storyboard.json 构造 Storyboard ----------

def load_storyboard(path: str | Path) -> Storyboard:
    """读 script_engine 输出的 storyboard.json，转 Storyboard。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    title = data.get("title", "storyboard")
    chars_raw = data.get("characters", [])
    shots_raw = data.get("shots", [])
    characters = [
        Character(
            name=str(c.get("name", "")),
            appearance=str(c.get("appearance", "")),
            personality=list(c.get("personality", []) or []),
            color_palette=list(c.get("color_palette", []) or []),
        )
        for c in chars_raw if isinstance(c, dict)
    ]
    scenes_raw = data.get("scenes", [])
    scenes = [
        SceneSpec(
            id=int(s.get("id", i + 1)),
            location=str(s.get("location", "")),
            atmosphere=str(s.get("atmosphere", "")),
            key_elements=list(s.get("key_elements", []) or []),
        )
        for i, s in enumerate(scenes_raw) if isinstance(s, dict)
    ]
    shots = [
        Shot(
            index=int(s.get("index", i + 1)),
            scene_id=int(s.get("scene_id", 0)),
            shot_type=str(s.get("shot_type", "中景")),
            description=str(s.get("description", "")),
            # V3.2 自动开发冲刺（轮 8）：story_id 形式如 "shot_001"
            shot_id=str(s.get("shot_id", "") or ""),
            characters=list(s.get("characters", []) or []),
            dialogue=str(s.get("dialogue", "")),
            voiceover=str(s.get("voiceover", "")),
            mood=list(s.get("mood", []) or []),
            camera_motion=str(s.get("camera_motion", "")),
            time=str(s.get("time", "")),
            # V3.1 升级路线整合
            subject_action=str(s.get("subject_action", "")),
            # V3.2 自动开发冲刺（轮 6）：duration 兼容两种命名（V3.2 spec / 旧）
            expected_duration=float(s.get("duration", s.get("expected_duration", 5.0)) or 5.0),
            motion_complexity=str(s.get("motion_complexity", "static") or "static"),
            # V3.2 自动开发冲刺（轮 8）：故事弧线字段
            continuity_from_prev=str(s.get("continuity_from_prev", "") or ""),
        )
        for i, s in enumerate(shots_raw) if isinstance(s, dict)
    ]
    return Storyboard(
        title=title,
        characters=characters,
        scenes=scenes,
        shots=shots,
        style_anchor=str(data.get("style_anchor", "")),
        char_sheet_anchor=str(data.get("char_sheet_anchor", "")),
    )


# ---------- 一致性修复：图像 prompt 拼装 ----------

def _build_image_prompt(
    shot_description: str,
    shot_char_names: list[str],
    all_characters: list[Any],
    style_anchor: str,
) -> str:
    """拼装图像 prompt：style_anchor + character appearances + shot description。

    设计目标：
      - 所有 shot 共享同一个风格基线（style_anchor 在最前）
      - 角色外貌描述被注入到 prompt，避免 MiniMax 每次独立生成"农夫"长相
      - shot.description 保留镜头级细节

    顺序很重要：style → characters → scene（一致性由前向后递减）。
    """
    parts: list[str] = []
    if style_anchor and style_anchor.strip():
        parts.append(style_anchor.strip())
    # 注入在场角色的外貌
    if shot_char_names and all_characters:
        char_lines = []
        char_lookup = {c.name: c for c in all_characters}
        for name in shot_char_names:
            ch = char_lookup.get(name)
            if not ch:
                continue
            appearance = getattr(ch, "appearance", "") or ""
            palette = getattr(ch, "color_palette", []) or []
            line = f"character {name} ({appearance})"
            if palette:
                line += f", 配色 {', '.join(palette)}"
            char_lines.append(line)
        if char_lines:
            parts.append("；".join(char_lines))
    # shot 本身的描述
    parts.append(shot_description.strip())
    return "。".join(p for p in parts if p)


# ---------- Pipeline 主入口 ----------

def run_pipeline(
    storyboard: Storyboard,
    output: str | Path,
    *,
    image_backend: ie.ImageBackend | None = None,
    video_backend: ve.KenBurnsBackend | None = None,
    tts_backend: te.TTSBackend | None = None,
    # 视频参数（每镜头共享）
    video_seconds: int = 5,
    video_fps: int = 8,
    video_effect: str = "cycle",         # "cycle" 在图间循环 / 或固定值
    # TTS 参数
    tts_voice: str = "zh-CN-XiaoxiaoNeural",
    # 控制
    keep_intermediate: bool = True,
    skip_video: bool = False,           # 调试用：只跑 image + tts + concat
    skip_tts: bool = False,             # 调试用：跳过配音
    skip_concat: bool = False,           # 调试用：只跑前 3 步
    log_callback: Callable[[str], None] | None = None,
    assets_dir: str | Path = "./assets",
    skip_asset_prep: bool = False,        # V2.5：若已 prep 过可跳过
) -> PipelineResult:
    """端到端跑：storyboard → final.mp4。

    每个 shot 顺序：
      1. image(_build_image_prompt(shot.description + style_anchor + char appearances)) → shot_NNN.jpg
         V2.5: 若角色 reference URL 缓存可用 → 传 subject_reference
      2. video(shot_NNN.jpg) → shot_NNN.mp4  (Ken Burns)
      3. tts(shot.dialogue + voiceover) → shot_NNN.mp3 + shot_NNN.vtt
      4. concat_videos + (可选) burn_subs → final.mp4
    """
    started = time.monotonic()
    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    intermediate_dir = Path(tempfile.mkdtemp(prefix="pipeline_"))

    log = log_callback or (lambda msg: print(msg, flush=True))

    image_backend = image_backend or ie.make_backend("auto")
    video_backend = video_backend or ve.make_backend()
    tts_backend = tts_backend or te.make_backend("auto")

    all_steps: list[PipelineStep] = []
    shots_meta: list[dict[str, Any]] = []

    title = storyboard.title or "storyboard"
    log(f"[pipeline] start: title={title!r} shots={len(storyboard.shots)} -> {out_path}")
    log(f"[pipeline] intermediate dir: {intermediate_dir}")

    # ---- Phase 0: 物料准备（multi-angle + expressions + scene variants） ----
    char_url_lookup: dict[str, str] = {}                       # name → 单 URL（V3 兼容接口）
    char_angle_lookup: dict[str, dict[str, str]] = {}         # name → {angle: url}
    char_expr_lookup: dict[str, dict[str, str]] = {}           # name → {expression: url}
    scene_url_lookup: dict[int, str] = {}                      # scene_id → 单 URL（V3 兼容）
    scene_variant_lookup: dict[int, dict[str, str]] = {}     # scene_id → {variant: url}
    if not skip_asset_prep:
        n_chars = len(storyboard.characters or [])
        n_scenes = len(storyboard.scenes or [])
        if n_chars or n_scenes:
            log(f"[pipeline] Phase 0: preparing {n_chars} char × (4 angles + 3 exprs) + {n_scenes} scene × 3 variants")

        # character angle sheets
        if storyboard.characters:
            char_assets = ap.prepare_all_characters(
                storyboard, image_backend,
                assets_dir=Path(assets_dir),
            )
            char_angle_lookup = ap.build_angle_url_lookup(char_assets)
            for name, angs in char_angle_lookup.items():
                if angs:
                    char_url_lookup[name] = next(iter(angs.values()))
            for name, angle_results in char_assets.items():
                for angle, r in angle_results.items():
                    all_steps.append(PipelineStep(
                        name="asset_prep_char", index=0, ok=r.ok,
                        duration_seconds=r.duration_seconds,
                        error=r.error, output_path=r.image_path,
                        extra={"url": (r.url[:80] + "...") if r.url else "", "angle": angle},
                    ))

        # character expression sheets（V2.5.2 新增）
        if storyboard.characters:
            expr_assets = ap.prepare_all_expressions(
                storyboard, image_backend,
                assets_dir=Path(assets_dir),
            )
            char_expr_lookup = {}
            for name, expr_results in expr_assets.items():
                char_expr_lookup[name] = {e: r.url for e, r in expr_results.items() if r.ok and r.url}
                for expr, r in expr_results.items():
                    all_steps.append(PipelineStep(
                        name="asset_prep_char_expr", index=0, ok=r.ok,
                        duration_seconds=r.duration_seconds,
                        error=r.error, output_path=r.image_path,
                        extra={"url": (r.url[:80] + "...") if r.url else "", "expression": expr},
                    ))

        # scene variant sheets（V2.5.2 新增）
        if storyboard.scenes:
            scene_assets = ap.prepare_all_scene_variants(
                storyboard, image_backend,
                assets_dir=Path(assets_dir),
            )
            scene_variant_lookup = {}
            for sid, var_results in scene_assets.items():
                scene_variant_lookup[sid] = {v: r.url for v, r in var_results.items() if r.ok and r.url}
                # V3 兼容：取第一可用变体（默认 day）
                first = next(iter(scene_variant_lookup[sid].values()), "")
                if first:
                    scene_url_lookup[sid] = first
                for var, r in var_results.items():
                    all_steps.append(PipelineStep(
                        name="asset_prep_scene_var", index=sid, ok=r.ok,
                        duration_seconds=r.duration_seconds,
                        error=r.error, output_path=r.image_path,
                        extra={"url": (r.url[:80] + "...") if r.url else "", "variant": var},
                    ))

        n_angle_urls = sum(len(v) for v in char_angle_lookup.values())
        n_expr_urls = sum(len(v) for v in char_expr_lookup.values())
        n_scene_urls = sum(len(v) for v in scene_variant_lookup.values())
        log(
            f"[pipeline] Phase 0 done: "
            f"{n_angle_urls} char-angle + {n_expr_urls} char-expr"
            f" + {n_scene_urls} scene-variant URLs ready"
        )

    effects_cycle = ["zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down"]

    for i, shot in enumerate(storyboard.shots):
        idx = shot.index if shot.index > 0 else (i + 1)
        effect = effects_cycle[idx % len(effects_cycle)] if video_effect == "cycle" else video_effect
        image_path = intermediate_dir / f"shot_{idx:03d}.jpg"
        video_path = intermediate_dir / f"shot_{idx:03d}.mp4"
        audio_path = intermediate_dir / f"shot_{idx:03d}.mp3"
        sub_path = intermediate_dir / f"shot_{idx:03d}.vtt"

        narration_parts = []
        if shot.dialogue:
            narration_parts.append(shot.dialogue)
        if shot.voiceover:
            narration_parts.append(shot.voiceover)
        narration = "\n".join(narration_parts).strip()

        shot_meta: dict[str, Any] = {"index": idx, "video": None, "audio": None, "subtitle": None}
        ok = True

        # ---- 阶段 1: 文生图 ----
        log(f"[pipeline] shot {idx}/{len(storyboard.shots)}: image")
        t0 = time.monotonic()
        try:
            # 拼装 prompt：style_anchor + character appearances + shot description
            full_prompt = _build_image_prompt(
                shot.description,
                shot.characters or [],
                storyboard.characters or [],
                getattr(storyboard, "style_anchor", "") or "",
            )
            # 与 V3 orchestrator 共用 asset_prep.resolve_shot_reference_urls：
            # 角度 + 表情 + 场景变体，只传 http(s) URL，最多 4 个。
            ref_bundle = ap.resolve_shot_reference_urls(
                shot, assets_dir, shot_index=idx,
            )
            ref_urls = ref_bundle.urls
            used_refs = ref_bundle.labels

            img_results = image_backend.generate(
                full_prompt,
                # V3.4 ADR-001：aspect_ratio 必须读 env，不准硬编码（V3.3 bug：pipeline.py:342 写死 "16:9"，env=9:16 完全无效）
                aspect_ratio_value=ie.aspect_ratio(),
                subject_reference=ref_urls or None,
            )
            img_bytes = img_results[0].bytes
            image_path.write_bytes(img_bytes)
            all_steps.append(PipelineStep(
                name="image", index=idx, ok=True,
                duration_seconds=time.monotonic() - t0,
                output_path=str(image_path),
                extra={"bytes": len(img_bytes), "model": img_results[0].model,
                       "prompt": full_prompt[:200],
                       "subject_references": used_refs},
            ))
        except Exception as exc:
            all_steps.append(PipelineStep(
                name="image", index=idx, ok=False,
                duration_seconds=time.monotonic() - t0,
                error=f"{type(exc).__name__}: {exc}",
            ))
            ok = False

        # ---- 阶段 2: 图生视频（Ken Burns） ----
        if not skip_video and ok and image_path.exists():
            log(f"[pipeline] shot {idx}: video (effect={effect}, sec={video_seconds})")
            t0 = time.monotonic()
            v_result = video_backend.render(
                image_path, video_path,
                seconds=video_seconds, fps=video_fps,
                effect=effect,
            )
            all_steps.append(PipelineStep(
                name="video", index=idx, ok=v_result.ok,
                duration_seconds=time.monotonic() - t0,
                output_path=str(video_path),
                error=v_result.error,
                extra={"effect": effect, "seconds": video_seconds},
            ))
            if v_result.ok:
                shot_meta["video"] = str(video_path)
            else:
                ok = False

        # ---- 阶段 3: 配音 ----
        if not skip_tts and narration:
            log(f"[pipeline] shot {idx}: tts ({len(narration)} chars)")
            t0 = time.monotonic()
            try:
                tts_result = tts_backend.synth(
                    narration, audio_path,
                    voice=tts_voice,
                    write_subtitle=sub_path,
                )
                shot_meta["audio"] = str(audio_path)
                if tts_result.sub_file:
                    shot_meta["subtitle"] = tts_result.sub_file
                all_steps.append(PipelineStep(
                    name="tts", index=idx, ok=True,
                    duration_seconds=time.monotonic() - t0,
                    output_path=str(audio_path),
                    extra={"voice": tts_voice, "chars": len(narration)},
                ))
            except Exception as exc:
                all_steps.append(PipelineStep(
                    name="tts", index=idx, ok=False,
                    duration_seconds=time.monotonic() - t0,
                    error=f"{type(exc).__name__}: {exc}",
                ))
                # tts 失败非致命：保留视频无配音

        shots_meta.append(shot_meta)

    if not skip_concat:
        # ---- 阶段 4: 拼接 ----
        log(f"[pipeline] concat: {len(shots_meta)} shots -> {out_path}")
        t0 = time.monotonic()
        valid_shots = [s for s in shots_meta if s["video"]]
        if not valid_shots:
            elapsed = time.monotonic() - started
            return PipelineResult(
                output=str(out_path),
                shot_count=len(storyboard.shots),
                ok_shots=0,
                failed_shots=len(storyboard.shots),
                duration_seconds=elapsed,
                source="error",
                error="所有 shot 都缺 video，无法 concat",
                steps=all_steps,
                intermediate_dir=str(intermediate_dir) if keep_intermediate else "",
            )
        try:
            ce_result = ce.concat_pipeline(
                shots=[ce.ShotItem(
                    index=s["index"], video=s["video"], audio=s.get("audio"), subtitle=s.get("subtitle"),
                ) for s in valid_shots],
                output=out_path,
                burn_subs=True,
                keep_intermediate=keep_intermediate,
            )
            all_steps.append(PipelineStep(
                name="concat", index=0,
                ok=ce_result.ok,
                duration_seconds=time.monotonic() - t0,
                output_path=str(out_path),
                error=ce_result.error if not ce_result.ok else "",
                extra={"shot_count": ce_result.shot_count, "bytes": ce_result.bytes},
            ))
            if not ce_result.ok:
                elapsed = time.monotonic() - started
                return PipelineResult(
                    output=str(out_path),
                    shot_count=len(storyboard.shots),
                    ok_shots=len(valid_shots),
                    failed_shots=len(storyboard.shots) - len(valid_shots),
                    duration_seconds=elapsed,
                    source="error",
                    error=f"concat 失败：{ce_result.error}",
                    steps=all_steps,
                    intermediate_dir=str(intermediate_dir) if keep_intermediate else "",
                )
        except Exception as exc:
            all_steps.append(PipelineStep(
                name="concat", index=0, ok=False,
                duration_seconds=time.monotonic() - t0,
                error=f"{type(exc).__name__}: {exc}",
            ))
            elapsed = time.monotonic() - started
            return PipelineResult(
                output=str(out_path),
                shot_count=len(storyboard.shots),
                ok_shots=len(valid_shots),
                failed_shots=len(storyboard.shots) - len(valid_shots),
                duration_seconds=elapsed,
                source="error",
                error=f"concat 抛：{exc}",
                steps=all_steps,
                intermediate_dir=str(intermediate_dir) if keep_intermediate else "",
            )

    elapsed = time.monotonic() - started
    ok_shots = sum(1 for s in shots_meta if s["video"])

    # ---- Phase 3: post-flight consistency check（V2.6 Quality Assurance）----
    # 检查相邻镜头色差相似度；过低 → 警告风格断裂
    consistency_report = None
    if ok_shots >= 2 and keep_intermediate:
        try:
            from hailuo_video import quality
            shot_image_paths = [
                    intermediate_dir / f"shot_{s['index']:03d}.jpg"
                    for s in shots_meta if s["video"]
            ]
            shot_image_paths = [p for p in shot_image_paths if p.exists()]
            if len(shot_image_paths) >= 2:
                consistency_report = quality.compute_color_consistency(shot_image_paths)
                log(f"[pipeline] consistency mean_sim={consistency_report.mean_similarity:.3f}, min_sim={consistency_report.min_similarity:.3f}")
                for w in consistency_report.warnings:
                    log(f"[pipeline] {w}")
        except Exception as e:
            log(f"[pipeline] consistency check skipped: {e}")

    result = PipelineResult(
        output=str(out_path),
        shot_count=len(storyboard.shots),
        ok_shots=ok_shots,
        failed_shots=len(storyboard.shots) - ok_shots,
        duration_seconds=elapsed,
        bytes=out_path.stat().st_size if out_path.exists() else 0,
        source="ok",
        steps=all_steps,
        intermediate_dir=str(intermediate_dir) if keep_intermediate else "",
    )
    # 把一致性 extra 附在 result（手动设置属性）
    if consistency_report:
        result.consistency = consistency_report  # type: ignore[attr-defined]
    return result

# ---------- V3.0 入口：run_pipeline_v3 委托给 orchestrator.PipelineAgent ----------

def run_pipeline_v3(
    storyboard: Storyboard,
    output: str | Path,
    *,
    image_backend: ie.ImageBackend | None = None,
    video_backend: ve.KenBurnsBackend | None = None,
    tts_backend: te.TTSBackend | None = None,
    voice: str = "zh-CN-XiaoxiaoNeural",
    video_seconds: int = 5,
    video_fps: int = 8,
    video_effect: str = "cycle",
    assets_dir: str | Path = "./assets",
    skip_asset_prep: bool = False,
    keep_intermediate: bool = True,
    parallel: int = 1,
    motion_budget: int = 3,                      # V3.1：默认 3（MiniMax Max 每天 hailuo-2.3 quota）
    run_dir: str | Path = "./run",
    log_path: str | Path | None = None,
    resume_from: str | None = None,             # V3.2：跳过此 phase 之前的 phase
    load_registry: bool = True,                 # V3.2：是否从 run_dir/registry.json 加载历史
) -> PipelineResult:
    """V3.0 入口：用 orchestrator.PipelineAgent 编排 4 个 phase。

    与 V2 run_pipeline 兼容：返回 PipelineResult + PipelineStep 字段相同。
    区别：并发（parallel>1 时 image_gen/video_synth/tts per-shot 跑 ThreadPoolExecutor）、
          registry.json 落盘（idempotency + 续跑基础）。

    V3.2 新增：resume_from（按 phase 名跳过）+ load_registry（默认 True）。
    """
    cfg = PipelineConfig(
        image_backend=image_backend or ie.make_backend("auto"),
        video_backend=video_backend or ve.make_backend(),
        tts_backend=tts_backend or te.make_backend("auto"),
        voice=voice,
        video_seconds=video_seconds,
        video_fps=video_fps,
        video_effect=video_effect,
        assets_dir=Path(assets_dir),
        skip_asset_prep=skip_asset_prep,
        keep_intermediate=keep_intermediate,
        parallel=parallel,
        motion_budget=motion_budget,
        run_dir=Path(run_dir),
        log=StructuredLogger(Path(log_path)) if log_path else None,
    )
    agent = PipelineAgent(
        cfg,
        resume_from=resume_from,
        load_registry=load_registry,
    )
    return agent.run(storyboard, Path(output))
