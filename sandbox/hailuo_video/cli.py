"""本地视频推理 CLI：generate / ping / status / batch / log / doctor / script / image / tts / concat / pipeline

V2-Local（替代旧 V0.x 的远端 HTTP 客户端）：
    python -m hailuo_video generate "a cat on the moon" -o ./out/cat.mp4
    python -m hailuo_video ping
    python -m hailuo_video status --last
    python -m hailuo_video batch prompts.txt -o ./out/
    python -m hailuo_video log [--tail N] [--op inference]
    python -m hailuo_video doctor
V2.1（脚本引擎）：
    python -m hailuo_video script novel.txt -o storyboard.json [--shots 30] [--backend mock]
V2.2（图像生成）：
    python -m hailuo_video image "a cat on the moon" -o out.jpg [--backend minimax|mock]
V2.3（TTS）：
    python -m hailuo_video tts "你好世界" -o out.mp3 [--voice zh-CN-XiaoxiaoNeural]
V2.4（拼接）：
    python -m hailuo_video concat manifest.json -o final.mp4 [--no-burn-subs] [--keep-intermediate]
V2.5（端到端 pipeline）：
    python -m hailuo_video pipeline storyboard.json -o final.mp4 [--image-backend mock|minimax] [--tts-backend mock|edge]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from hailuo_video import (
    asset_prep as ap,
    calllog,
    doctor,
    state,
)
from hailuo_video import concat_engine as ce
from hailuo_video import editing  # V3.2 重构：editing_engine → editing 包
from hailuo_video import image_engine as ie
from hailuo_video import pipeline as pl
from hailuo_video import quality
from hailuo_video import script_engine as se
from hailuo_video import tts_engine as te
from hailuo_video import video_engine as ve
from hailuo_video.batch import BatchSummary, load_prompts, run_batch
from hailuo_video.local_client import (
    SANDBOX_RUNTIME,
    config_dict,
    generate as client_generate_fn,
    is_environment_ready,
    warnings as config_warnings,
)
from hailuo_video.manifest import write_manifest


def cmd_generate(args: argparse.Namespace) -> int:
    state_path = Path(args.state_path) if args.state_path else state.DEFAULT_STATE_PATH

    # disabled 短路优先（不依赖 env）
    if not args.prompt or not args.prompt.strip():
        print("[FAIL] prompt 为空", file=sys.stderr)
        state.update_last(
            prompt=args.prompt or "",
            output_path=str(args.output),
            status="failed",
            path=state_path,
        )
        return 1

    warns = config_warnings()
    if warns:
        print("[CONFIG] 配置警告：", file=sys.stderr)
        for w in warns:
            print(f"  - {w}", file=sys.stderr)

    if not is_environment_ready() and not _overrides_active():
        print(
            "[FAIL] 环境未就绪：torch / diffusers 未装全。请跑 doctor 看具体缺啥，或：",
            file=sys.stderr,
        )
        print(
            "       pip install torch diffusers transformers accelerate sentencepiece imageio-ffmpeg safetensors",
            file=sys.stderr,
        )
        state.update_last(
            prompt=args.prompt,
            output_path=str(args.output),
            status="failed",
            path=state_path,
        )
        return 2

    cfg = config_dict()
    print(
        f"[GEN] prompt={args.prompt!r} -> {args.output} "
        f"(model={cfg['model']} device={cfg['device']} num_frames={args.num_frames or cfg['num_frames']})",
        file=sys.stderr,
    )
    result = client_generate_fn(
        args.prompt,
        args.output,
        num_frames=args.num_frames,
        height_px=args.height,
        width_px=args.width,
        num_steps=args.steps,
        guidance_scale=args.guidance,
        seed_value=args.seed,
    )
    if not result.ok:
        state.update_last(
            prompt=args.prompt,
            output_path=str(args.output),
            status="failed",
            model=result.model or cfg["model"],
            device=result.device or cfg["device"],
            num_frames=result.num_frames,
            path=state_path,
        )
        print(f"[FAIL] {result.source}: {result.error}", file=sys.stderr)
        return 1

    state.update_last(
        run_id=result.run_id,
        prompt=args.prompt,
        output_path=str(result.output_path),
        status="succeeded",
        model=result.model,
        device=result.device,
        num_frames=result.num_frames,
        duration_seconds=result.duration_seconds,
        path=state_path,
    )
    print(
        f"[OK] run_id={result.run_id} -> {result.output_path} "
        f"({result.duration_seconds:.1f}s, {result.bytes} bytes)",
        file=sys.stderr,
    )
    return 0


def cmd_ping(args: argparse.Namespace) -> int:
    cfg = config_dict()
    print("[PING] 当前配置：", file=sys.stderr)
    for k, v in cfg.items():
        print(f"  {k} = {v}", file=sys.stderr)
    warns = config_warnings()
    if warns:
        print("[PING] 警告：", file=sys.stderr)
        for w in warns:
            print(f"  - {w}", file=sys.stderr)
    if is_environment_ready():
        print("[PING] environment ready=True。可发起 generate。", file=sys.stderr)
        return 0
    print("[PING] environment ready=False。请补全依赖后重试。", file=sys.stderr)
    return 2


def cmd_status(args: argparse.Namespace) -> int:
    state_path = Path(args.state_path) if args.state_path else state.DEFAULT_STATE_PATH
    s = state.load_state(state_path)
    if not s.last_run_id:
        print("[STATUS] state.json 无 last_run_id。请先跑一次 generate。", file=sys.stderr)
        return 2
    print(
        f"[STATUS] run_id={s.last_run_id} status={s.last_status} "
        f"model={s.last_model} device={s.last_device} num_frames={s.last_num_frames} "
        f"output={s.last_output_path} ({s.last_duration_seconds:.1f}s) updated={s.last_updated}",
        file=sys.stderr,
    )
    return 0 if s.last_status == "succeeded" else 1


def cmd_log(args: argparse.Namespace) -> int:
    runtime_dir = Path(args.runtime_dir) if args.runtime_dir else SANDBOX_RUNTIME
    records = calllog.tail_calls(n=args.tail, op=args.op, runtime_dir=runtime_dir)
    if not records:
        print("[LOG] 暂无记录（runs.jsonl 不存在或被过滤为空）", file=sys.stderr)
        return 0
    print(calllog.format_calls(records))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """诊断环境 + 配置是否就绪。"""
    d = doctor.diagnose()
    print(doctor.format_diagnose(d))
    if not d["configured"]:
        return 2
    if all(c.get("ok") for c in d.get("checks", [])):
        return 0
    return 1


def cmd_batch(args: argparse.Namespace) -> int:
    # 文件 / 解析 错误优先（不依赖 env）
    try:
        prompts = load_prompts(args.prompts_file)
    except FileNotFoundError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2

    if not prompts:
        print(f"[FAIL] {args.prompts_file} 没解析到任何 prompt（全是注释 / 空行？）", file=sys.stderr)
        return 2

    if not is_environment_ready() and not _overrides_active():
        print("[FAIL] 环境未就绪，batch 模式无法跑。跑 doctor 看缺啥。", file=sys.stderr)
        return 2

    print(
        f"[BATCH] {len(prompts)} prompts -> {args.output} "
        f"(start_from={args.start_from}, count={args.count}, skip_existing={args.skip_existing}, "
        f"stop_on_failure={args.stop_on_failure})",
        file=sys.stderr,
    )

    summary: BatchSummary = run_batch(
        prompts,
        args.output,
        skip_existing=args.skip_existing,
        stop_on_failure=args.stop_on_failure,
        start_from=args.start_from,
        count=args.count,
        num_frames=args.num_frames,
        height=args.height,
        width=args.width,
        steps=args.steps,
        guidance=args.guidance,
        seed=args.seed,
    )

    print(
        f"\n[BATCH] 总计 {summary.total}，成功 {summary.succeeded}，"
        f"失败 {summary.failed}，跳过 {summary.skipped}",
        file=sys.stderr,
    )
    if summary.failures:
        print("[BATCH] 失败清单：", file=sys.stderr)
        for idx, prompt, err in summary.failures:
            print(f"  [{idx}] {err}", file=sys.stderr)

    if args.manifest:
        manifest_path = write_manifest(summary, args.manifest)
        print(f"[BATCH] manifest -> {manifest_path}", file=sys.stderr)

    return summary.exit_code()


def cmd_script(args: argparse.Namespace) -> int:
    """novel → script → characters → storyboard，落 JSON 到 -o。"""
    novel_path = Path(args.novel_file)
    if not novel_path.is_file():
        print(f"[FAIL] 小说文件不存在：{novel_path}", file=sys.stderr)
        return 2

    try:
        novel = se.read_novel(novel_path)
    except OSError as exc:
        print(f"[FAIL] 读小说失败：{exc}", file=sys.stderr)
        return 2
    if not novel:
        print(f"[FAIL] {novel_path} 为空", file=sys.stderr)
        return 2

    # 配置检查 + warnings
    warns = se.configuration_warnings()
    if warns:
        print("[SCRIPT] LLM 配置警告：", file=sys.stderr)
        for w in warns:
            print(f"  - {w}", file=sys.stderr)

    # 选 backend
    if args.backend == "mock":
        backend = se.make_backend("cli-mock")
        print("[SCRIPT] backend=mock（mock 模式，不会调真 LLM）", file=sys.stderr)
    elif args.backend == "minimax":
        if not se.is_llm_configured():
            print(
                "[FAIL] MINIMAX_API_KEY 未设置或长度不足。先在 .env 填好后重试；"
                "或用 --backend mock 跑通流程（不调真 API）。",
                file=sys.stderr,
            )
            return 2
        try:
            backend = se.make_backend("minimax")
        except se.LLMError as exc:
            print(f"[FAIL] {exc}", file=sys.stderr)
            return 2
    else:
        print(f"[FAIL] 未知 backend: {args.backend}", file=sys.stderr)
        return 2

    print(
        f"[SCRIPT] novel={novel_path} shots={args.shots} backend={args.backend} -> {args.output}",
        file=sys.stderr,
    )

    try:
        sb = se.novel_to_storyboard(novel, backend, n_shots=args.shots)
    except se.LLMError as exc:
        print(f"[FAIL] LLM 失败：{exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — 把任意 backend 异常包成 LLMError-style 报错
        # 典型：MockBackend 没预设响应 → AssertionError；用户没 Key → MiniMax 401
        print(f"[FAIL] pipeline 失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    out_path = se.write_storyboard(sb, args.output)
    print(
        f"[OK] {out_path} (title={sb.title!r} chars={len(sb.characters)} shots={len(sb.shots)})",
        file=sys.stderr,
    )
    return 0


def cmd_image(args: argparse.Namespace) -> int:
    """prompt → MiniMax image API → 落 jpg/png。"""
    if not args.prompt or not args.prompt.strip():
        print("[FAIL] prompt 为空", file=sys.stderr)
        return 1

    warns = (
        ie.gemini_configuration_warnings()
        if args.backend == "gemini"
        else ie.configuration_warnings()
    )
    if warns:
        print("[IMAGE] 配置警告：", file=sys.stderr)
        for w in warns:
            print(f"  - {w}", file=sys.stderr)

    if args.backend == "mock":
        backend = ie.make_backend("mock")
        print("[IMAGE] backend=mock（单测模式，不会调真 API）", file=sys.stderr)
    elif args.backend == "minimax" and not ie.is_image_configured():
        print(
            "[FAIL] MINIMAX_API_KEY 未设置或长度不足。先在 .env 填好，或用 --backend mock。",
            file=sys.stderr,
        )
        return 2
    else:
        # gemini / minimax 都走工厂，构造期缺 Key 会抛 ImageError
        try:
            backend = ie.make_backend(args.backend)
        except ie.ImageError as exc:
            print(f"[FAIL] {exc}", file=sys.stderr)
            return 2

    n = max(1, args.n)
    out_path = Path(args.output)
    # 决定 file vs dir：现存且是目录 → dir；现存且是文件 → file；不存在 → n>1 当 dir，n==1 当 file
    if out_path.is_dir():
        out_dir = out_path
    elif out_path.exists():
        out_dir = None
    elif n > 1:
        out_path.mkdir(parents=True, exist_ok=True)
        out_dir = out_path
    else:
        # n==1，路径不存在 → 当 file（write_image 会创建 parent dir）
        out_dir = None

    print(
        f"[IMAGE] prompt={args.prompt!r} n={n} aspect_ratio={args.aspect_ratio} "
        f"response_format={args.response_format} backend={args.backend}",
        file=sys.stderr,
    )

    try:
        results = backend.generate(
            args.prompt,
            n_value=n,
            aspect_ratio_value=args.aspect_ratio,
            response_format_value=args.response_format,
        )
    except ie.ImageError as exc:
        print(f"[FAIL] image 失败：{exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] pipeline 失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    # 落盘
    written: list[Path] = []
    for i, r in enumerate(results):
        if out_dir is not None:
            ext = ie.image_extension(r.mime_type)
            target = out_dir / f"{i:03d}{ext}"
        else:
            target = out_path
        ie.write_image(r, target)
        written.append(target)

    for i, (r, p) in enumerate(zip(results, written)):
        print(
            f"[OK] [{i+1}/{len(results)}] {p} ({len(r.bytes)} bytes, {r.mime_type}, {r.duration_seconds:.1f}s)",
            file=sys.stderr,
        )
    return 0


def cmd_tts(args: argparse.Namespace) -> int:
    """text → Edge-TTS mp3（+ 可选字幕）。"""
    # list-voices 模式不要求 text，先走
    if args.list_voices:
        try:
            voices = te.list_voices(filter_locale=args.filter_locale)
        except te.TTSError as exc:
            print(f"[FAIL] {exc}", file=sys.stderr)
            return 2
        for v in voices:
            print(
                f"{v['ShortName']:40s} {v['Gender']:6s} {v['Locale']}",
                file=sys.stdout,
            )
        return 0

    if not args.text or not args.text.strip():
        print("[FAIL] text 为空", file=sys.stderr)
        return 1

    if args.backend == "mock":
        backend = te.make_backend("mock")
        print("[TTS] backend=mock（单测模式，不调真 API）", file=sys.stderr)
    elif args.backend == "edge":
        try:
            backend = te.make_backend("edge")
        except te.TTSError as exc:
            print(f"[FAIL] {exc}", file=sys.stderr)
            return 2
    else:
        print(f"[FAIL] 未知 backend: {args.backend}", file=sys.stderr)
        return 2

    print(
        f"[TTS] voice={args.voice} rate={args.rate} volume={args.volume} pitch={args.pitch} -> {args.output}",
        file=sys.stderr,
    )

    try:
        result = backend.synth(
            args.text,
            args.output,
            voice=args.voice,
            rate=args.rate,
            volume=args.volume,
            pitch=args.pitch,
            write_subtitle=args.subtitle,
        )
    except te.TTSError as exc:
        print(f"[FAIL] tts 失败：{exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] pipeline 失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(
        f"[OK] {args.output} ({len(result.bytes)} bytes, {result.duration_seconds:.1f}s)",
        file=sys.stderr,
    )
    if result.sub_file:
        print(f"[OK] subtitle -> {result.sub_file}", file=sys.stderr)
    return 0


def cmd_concat(args: argparse.Namespace) -> int:
    """manifest.json → ffmpeg 拼接 → final mp4。"""
    manifest_path = Path(args.manifest)
    if not manifest_path.is_file():
        print(f"[FAIL] manifest 不存在：{manifest_path}", file=sys.stderr)
        return 2

    if not ce.is_available():
        print(
            "[FAIL] ffmpeg 二进制未找到；先 `pip install imageio-ffmpeg`",
            file=sys.stderr,
        )
        return 2

    try:
        shots = ce.make_concat_result_from_manifest(manifest_path)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[FAIL] manifest 解析失败：{exc}", file=sys.stderr)
        return 2

    if not shots:
        print(f"[FAIL] {manifest_path} shots 列表为空", file=sys.stderr)
        return 2

    print(
        f"[CONCAT] {len(shots)} shots -> {args.output} "
        f"(burn_subs={not args.no_burn_subs}, keep_intermediate={args.keep_intermediate})",
        file=sys.stderr,
    )

    result = ce.concat_pipeline(
        shots,
        args.output,
        burn_subs=not args.no_burn_subs,
        keep_intermediate=args.keep_intermediate,
    )

    if not result.ok:
        print(f"[FAIL] {result.error}", file=sys.stderr)
        return 1

    print(
        f"[OK] {result.output} (shots={result.shot_count}, "
        f"{result.bytes} bytes, {result.duration_seconds:.1f}s)",
        file=sys.stderr,
    )
    return 0


def cmd_prep(args: argparse.Namespace) -> int:
    """V2.5 Phase 0: 跑物料准备（character sheets + scene sheets）。缓存到 assets/。

    用法：先跑 prep 看人眼 review，确认 OK 再跑 pipeline。
    """
    sb_path = Path(args.storyboard)
    if not sb_path.is_file():
        print(f"[FAIL] storyboard 不存在：{sb_path}", file=sys.stderr)
        return 2
    try:
        storyboard = pl.load_storyboard(sb_path)
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        print(f"[FAIL] storyboard 解析失败：{exc}", file=sys.stderr)
        return 2
    if not storyboard.characters and not storyboard.scenes:
        print(f"[FAIL] storyboard.characters 和 scenes 都为空；没法生成 sheet", file=sys.stderr)
        return 2

    try:
        image_backend = ie.make_backend(args.image_backend)
    except ie.ImageError as e:
        print(f"[FAIL] {e}", file=sys.stderr)
        return 2

    assets_dir = Path(args.assets_dir).resolve()
    print(
        f"[PREP] storyboard={sb_path} chars={len(storyboard.characters)} "
        f"scenes={len(storyboard.scenes)} assets_dir={assets_dir} "
        f"force={args.force} backend={args.image_backend}",
        file=sys.stderr,
    )

    all_ok = 0
    all_fail = 0
    # character angle sheets（V2.5.2 多角度：{name: {angle: AssetResult}}）
    if storyboard.characters:
        char_results = ap.prepare_all_characters(
            storyboard, image_backend,
            assets_dir=assets_dir,
            force_regenerate=args.force,
        )
        for name, angle_dict in char_results.items():
            for angle, r in angle_dict.items():
                marker = "✓" if r.ok else "✗"
                suffix = "  [cached]" if r.source == "cached" else ""
                detail = r.error if not r.ok else f"{r.image_path}"
                print(f"  {marker} char {name}/{angle}: {detail}{suffix}", file=sys.stderr)
                all_ok += int(r.ok)
                all_fail += int(not r.ok)
    # character expression sheets（V2.5.2 新增）
    if storyboard.characters:
        expr_results = ap.prepare_all_expressions(
            storyboard, image_backend,
            assets_dir=assets_dir,
            force_regenerate=args.force,
        )
        for name, expr_dict in expr_results.items():
            for expr, r in expr_dict.items():
                marker = "✓" if r.ok else "✗"
                suffix = "  [cached]" if r.source == "cached" else ""
                detail = r.error if not r.ok else f"{r.image_path}"
                print(f"  {marker} char {name}/expr_{expr}: {detail}{suffix}", file=sys.stderr)
                all_ok += int(r.ok)
                all_fail += int(not r.ok)
    # scene variant sheets（V2.5.2 新增）
    if storyboard.scenes:
        scene_var_results = ap.prepare_all_scene_variants(
            storyboard, image_backend,
            assets_dir=assets_dir,
            force_regenerate=args.force,
        )
        for sid, var_dict in scene_var_results.items():
            for var, r in var_dict.items():
                marker = "✓" if r.ok else "✗"
                suffix = "  [cached]" if r.source == "cached" else ""
                detail = r.error if not r.ok else f"{r.image_path}"
                print(f"  {marker} scene_{sid}_{var}: {detail}{suffix}", file=sys.stderr)
                all_ok += int(r.ok)
                all_fail += int(not r.ok)
    # 兼容旧版：scene sheets（V3 single-angle scene）— 已被 scene_variants 替代
    # 但保留以防旧 storyboard 没 scenes[]，旧版会被自动生成 scene_0001.jpg

    print(
        f"[PREP] done: {all_ok} ok / {all_fail} fail（{all_ok + all_fail} total）",
        file=sys.stderr,
    )
    return 0 if all_fail == 0 else 1


def cmd_pipeline(args: argparse.Namespace) -> int:
    """storyboard.json → final.mp4（端到端，V3.0 走 orchestrator）。"""
    sb_path = Path(args.storyboard)
    if not sb_path.is_file():
        print(f"[FAIL] storyboard 不存在：{sb_path}", file=sys.stderr)
        return 2

    # V3.0 dry-run：打印 DAG 后退出
    if getattr(args, "dry_run", False):
        from hailuo_video.orchestrator import DEFAULT_DAG
        print("[DRY-RUN] DAG phases:")
        for p in DEFAULT_DAG:
            print(f"  - {p.name}: agents={p.agent_names} parallel={p.is_parallel} halt={p.halt_on_error}")
        return 0

    # V3.0 resume：读 registry.json，跳过已完成 phase
    from hailuo_video.artifact import Registry
    from hailuo_video.orchestrator import Phase
    registry = Registry.load_json_or_empty(Path(args.run_dir) / "registry.json")
    from_phase_idx = 0
    if getattr(args, "resume_from", None):
        for i, p in enumerate(__import__("hailuo_video").orchestrator.DEFAULT_DAG):
            if p.name == args.resume_from:
                from_phase_idx = i
                break

    try:
        storyboard = pl.load_storyboard(sb_path)
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        print(f"[FAIL] storyboard 解析失败：{exc}", file=sys.stderr)
        return 2
    if not storyboard.shots:
        print(f"[FAIL] {sb_path} shots 列表为空", file=sys.stderr)
        return 2

    # 选 backend
    try:
        image_backend = ie.make_backend(args.image_backend)
    except ie.ImageError as e:
        print(f"[FAIL] image backend：{e}", file=sys.stderr)
        return 2
    try:
        tts_backend = te.make_backend(args.tts_backend)
    except te.TTSError as e:
        print(f"[FAIL] tts backend：{e}", file=sys.stderr)
        return 2
    video_backend = ve.make_backend()

    print(
        f"[PIPELINE] storyboard={sb_path} shots={len(storyboard.shots)} "
        f"image={args.image_backend} tts={args.tts_backend} parallel={args.parallel} "
        f"video_effect={args.video_effect} -> {args.output}",
        file=sys.stderr,
    )

    # V3.0：用 run_pipeline_v3（委托 orchestrator.PipelineAgent）
    result = pl.run_pipeline_v3(
        storyboard,
        args.output,
        image_backend=image_backend,
        tts_backend=tts_backend,
        video_backend=video_backend,
        voice=args.voice,
        video_seconds=args.video_seconds,
        video_fps=args.video_fps,
        video_effect=args.video_effect,
        assets_dir=args.assets_dir,
        skip_asset_prep=args.skip_asset_prep,
        keep_intermediate=args.keep_intermediate,
        parallel=args.parallel,
        motion_budget=args.motion_budget,
        aspect_ratio=getattr(args, "aspect_ratio", None),
        knowledge_path=getattr(args, "knowledge_path", None),
        run_dir=args.run_dir,
        resume_from=getattr(args, "resume_from", None),
    )

    # V3.8 缺陷 2：static 降级镜头必须显式告知（旧行为完全静默）。
    static_skipped = (result.consistency or {}).get("static_skipped_shots") or []
    if static_skipped:
        print(
            f"[WARN] {len(static_skipped)} 个镜头 motion_complexity=static → 走 Ken Burns 兜底"
            f"（无真 AI 运动、不消耗 Hailuo quota）：shot {static_skipped}",
            file=sys.stderr,
        )
    # V3.8 缺陷 13：registry 落盘失败不再静默。
    registry_err = (result.consistency or {}).get("registry_save_error")
    if registry_err:
        print(f"[WARN] registry.json 落盘失败（续跑信息缺失）：{registry_err}", file=sys.stderr)
    # V3.8 W1：过期 reference 被跳过 → 角色一致性锚定缺失，必须显式告知。
    stale_refs = (result.consistency or {}).get("stale_references") or []
    if stale_refs:
        labels = ", ".join(f"shot{r['shot_idx']}:{r['label']}" for r in stale_refs)
        print(
            f"[WARN] {len(stale_refs)} 条 reference 已过期被跳过（角色一致性锚定缺失）：{labels}",
            file=sys.stderr,
        )
    # V3.8 W3：跨镜连续性门禁（默认只报警不阻断）。
    cont = (result.consistency or {}).get("continuity") or {}
    if cont and not cont.get("ok", True):
        print(
            f"[WARN] 跨镜连续性未达标：mean 色差相似度 {cont.get('mean_similarity', 0):.3f}"
            f"（门禁 0.90；日记实测 0.700 / 理想基线 0.95）",
            file=sys.stderr,
        )

    if not result.ok:
        print(f"[FAIL] {result.error}", file=sys.stderr)
        return 1
    print(
        f"[OK] {result.output} (shots={result.shot_count}, "
        f"{result.bytes} bytes, {result.duration_seconds:.1f}s)",
        file=sys.stderr,
    )
    if result.intermediate_dir:
        print(f"[OK] intermediate -> {result.intermediate_dir}", file=sys.stderr)
    return 0


def _overrides_active() -> bool:
    """单测时 _override_pipeline_load/_call 都设了 → 视为环境就绪。"""
    from hailuo_video import local_client
    return local_client._override_pipeline_load is not None and local_client._override_pipeline_call is not None


def cmd_editing(args: argparse.Namespace) -> int:
    """V3.2 REQ-303：后期处理 CLI 子命令（暴露 5 个 mode）。"""
    from hailuo_video import editing as ed_mod  # V3.2 重构：editing_engine → editing 包

    mode = args.mode
    inputs = args.inputs or []
    if mode != "apply_lut" and len(inputs) < 1:
        print(f"[FAIL] mode={mode} 至少需要 1 个 --input", file=sys.stderr)
        return 2
    for inp in inputs:
        if not Path(inp).is_file():
            print(f"[FAIL] 输入文件不存在：{inp}", file=sys.stderr)
            return 2

    try:
        if mode == "color_normalize":
            out_dir = args.output_dir
            if not out_dir:
                print("[FAIL] --output-dir 必填", file=sys.stderr)
                return 2
            results = ed_mod.color_normalize_pipeline(
                video_paths=inputs,
                output_dir=out_dir,
                timeout=args.timeout,
            )
            print(f"[OK] color_normalize → {len(results)} clips → {out_dir}")
            for r in results:
                print(f"  - {r.output_path}  (eq={r.eq_filter})")
            return 0

        elif mode == "crossfade":
            if not args.output:
                print("[FAIL] --output 必填", file=sys.stderr)
                return 2
            if len(inputs) < 2:
                print("[FAIL] crossfade 至少需要 2 段视频", file=sys.stderr)
                return 2
            result = ed_mod.crossfade_clips(
                video_paths=inputs,
                output_path=args.output,
                transition=args.transition,
                overlap_seconds=args.overlap,
            )
            print(f"[OK] crossfade({args.transition}, {args.overlap}s) → {result.output_path}")
            return 0

        elif mode == "motion_match":
            peaks = ed_mod.motion_match_cut_points(
                video_paths=inputs,
                sample_count=args.sample_count,
            )
            print(f"[OK] motion_match → {len(peaks)} peaks:")
            for p in peaks:
                print(f"  - {Path(p.video_path).name}: "
                      f"t={p.peak_time_seconds:.2f}s diff={p.diff_score:.4f}")
            return 0

        elif mode == "apply_lut":
            if not args.video or not args.output or not args.lut:
                print("[FAIL] apply_lut 需要 --video / --output / --lut", file=sys.stderr)
                return 2
            if not Path(args.lut).is_file():
                print(f"[FAIL] LUT 文件不存在：{args.lut}", file=sys.stderr)
                return 2
            result = ed_mod.apply_lut(
                video_path=args.video,
                output_path=args.output,
                lut_path=args.lut,
                timeout=args.timeout,
            )
            print(f"[OK] apply_lut({args.lut}) → {result.output_path}")
            return 0

        elif mode == "full":
            if not args.output:
                print("[FAIL] --output 必填", file=sys.stderr)
                return 2
            result = ed_mod.full_editing_pipeline(
                video_paths=inputs,
                output_path=args.output,
                lut_path=args.lut,
                transition=args.transition,
                overlap_seconds=args.overlap,
                work_dir=args.work_dir,
            )
            print(f"[OK] full_editing_pipeline → {result.final_path}")
            print(f"  - color_normalized: {len(result.color_results)} clips")
            print(f"  - motion_peaks: {len(result.motion_peaks)}")
            print(f"  - crossfade: {result.crossfade.transition if result.crossfade else 'n/a'}")
            print(f"  - lut: {result.lut.lut_path if result.lut else 'n/a'}")
            return 0

        else:
            print(f"[FAIL] 未知 mode：{mode!r}", file=sys.stderr)
            return 2

    except ed_mod.EditingError as exc:
        print(f"[FAIL] editing {mode}：{exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="hailuo_video",
        description="本地视频推理 CLI（CogVideoX 后端，diffusers）",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="prompt → 本地推理 → 落 mp4（CPU 极慢）")
    g.add_argument("prompt", help="英文/中文 prompt 文本")
    g.add_argument("-o", "--output", required=True, help="输出 mp4 路径")
    g.add_argument("--num-frames", type=int, default=None, help="帧数（默认 env LOCAL_VIDEO_NUM_FRAMES）")
    g.add_argument("--height", type=int, default=None, help="高（默认 480）")
    g.add_argument("--width", type=int, default=None, help="宽（默认 720）")
    g.add_argument("--steps", type=int, default=None, help="推理步数（smoke 测试可降到 5）")
    g.add_argument("--guidance", type=float, default=None, help="classifier-free guidance（默认 6.0）")
    g.add_argument("--seed", type=int, default=None, help="随机种子（-1 = 随机）")
    g.add_argument("--state-path", default=None, help="state.json 路径（单测用）")
    g.set_defaults(func=cmd_generate)

    p = sub.add_parser("ping", help="打印当前配置 + 环境就绪状态")
    p.set_defaults(func=cmd_ping)

    s = sub.add_parser("status", help="读 state.json 显示最近一次 generate 状态")
    s.add_argument("--last", action="store_true", help="（默认行为；保留兼容）")
    s.add_argument("--state-path", default=None, help="state.json 路径（单测用）")
    s.set_defaults(func=cmd_status)

    b = sub.add_parser("batch", help="从 prompts.txt 批量推理（顺序）")
    b.add_argument("prompts_file", help=".txt 文件，一行一个 prompt，# 开头是注释")
    b.add_argument("-o", "--output", required=True, help="输出目录（默认 ./out/）")
    b.add_argument("--num-frames", type=int, default=None, help="帧数（默认 env）")
    b.add_argument("--height", type=int, default=None, help="高（默认 480）")
    b.add_argument("--width", type=int, default=None, help="宽（默认 720）")
    b.add_argument("--steps", type=int, default=None, help="推理步数")
    b.add_argument("--guidance", type=float, default=None, help="CFG 强度")
    b.add_argument("--seed", type=int, default=None, help="随机种子")
    b.add_argument("--start-from", type=int, default=0, help="从第 N 个 prompt 开始（断点续跑）")
    b.add_argument("--count", type=int, default=None, help="本批跑几个 prompt（默认 = 全部）")
    b.add_argument("--stop-on-failure", action="store_true", help="遇失败立即停止（默认继续）")
    b.add_argument("--manifest", default=None, help="写 manifest.csv 到此路径")
    skip_group = b.add_mutually_exclusive_group()
    skip_group.add_argument("--skip-existing", dest="skip_existing", action="store_true", default=True, help="跳过已存在的输出文件（默认）")
    skip_group.add_argument("--no-skip-existing", dest="skip_existing", action="store_false", help="覆盖已存在的输出文件")
    b.set_defaults(func=cmd_batch)

    l = sub.add_parser("log", help="查看 runs.jsonl 最近记录")
    l.add_argument("--tail", type=int, default=20, help="看最近 N 条（默认 20）")
    l.add_argument("--op", default="", help="按操作过滤（V2 仅有 inference）")
    l.add_argument("--runtime-dir", default=None, help="runtime 目录（单测用）")
    l.set_defaults(func=cmd_log)

    d = sub.add_parser("doctor", help="诊断 torch / diffusers / 模型缓存 / 磁盘空间")
    d.set_defaults(func=cmd_doctor)

    s = sub.add_parser("script", help="novel.txt → JSON storyboard（LLM 辅助剧本 + 角色 + 分镜）")
    s.add_argument("novel_file", help="小说片段 .txt 文件")
    s.add_argument("-o", "--output", required=True, help="输出 JSON 路径")
    s.add_argument("--shots", type=int, default=30, help="分镜数量（默认 30）")
    s.add_argument("--backend", choices=["minimax", "mock"], default="minimax",
                   help="LLM 后端（minimax=真实，mock=单测假数据；默认 minimax）")
    s.set_defaults(func=cmd_script)

    i = sub.add_parser("image", help="prompt → MiniMax image API → 落 jpg/png")
    i.add_argument("prompt", help="prompt 文本")
    i.add_argument("-o", "--output", required=True, help="输出文件路径（单张）或目录（多张）")
    i.add_argument("--n", type=int, default=1, help="生成数量 1-9（默认 1）")
    i.add_argument("--aspect-ratio", default=None, help="宽高比 16:9 / 1:1 等（默认 env）")
    i.add_argument("--response-format", choices=["base64", "url"], default=None,
                   help="响应格式（默认 env；base64 推荐无过期）")
    i.add_argument("--backend", choices=["minimax", "gemini", "mock"], default="minimax",
                   help="后端（minimax/gemini=真实，mock=单测假数据；默认 minimax）")
    i.set_defaults(func=cmd_image)

    t = sub.add_parser("tts", help="text → Edge-TTS mp3（+ 可选字幕）")
    t.add_argument("text", nargs="?", default="", help="待合成文本")
    t.add_argument("-o", "--output", required=False, default="out.mp3",
                   help="输出 mp3 路径（默认 out.mp3）")
    t.add_argument("--voice", default=None, help="voice 名（默认 EDGE_TTS_VOICE 或 zh-CN-XiaoxiaoNeural）")
    t.add_argument("--rate", default="", help="语速如 +10%% / -20%%（默认 +0%%）")
    t.add_argument("--volume", default="", help="音量如 +10%% / -20%%（默认 +0%%）")
    t.add_argument("--pitch", default="", help="音调如 +5Hz / -10Hz（默认 +0Hz）")
    t.add_argument("--subtitle", default=None, help="同时生成字幕文件（.vtt 或 .srt 路径）")
    t.add_argument("--backend", choices=["edge", "mock"], default="edge",
                   help="后端（edge=真实，mock=单测假数据；默认 edge）")
    t.add_argument("--list-voices", action="store_true", help="列出可用 voices 后退出")
    t.add_argument("--filter-locale", default="", help="--list-voices 时过滤 locale（前缀）")
    t.set_defaults(func=cmd_tts)

    c = sub.add_parser("concat", help="manifest.json → ffmpeg 拼接 → final mp4")
    c.add_argument("manifest", help="manifest.json（含 shots 列表，每 shot 含 video/audio/subtitle）")
    c.add_argument("-o", "--output", required=True, help="最终输出 mp4 路径")
    c.add_argument("--no-burn-subs", action="store_true", help="不烧字幕（默认烧）")
    c.add_argument("--keep-intermediate", action="store_true",
                   help="保留 merge/sub 中间产物（默认删；调试用）")
    c.set_defaults(func=cmd_concat)

    p = sub.add_parser("pipeline", help="storyboard.json → final.mp4（端到端 MVP）")
    p.add_argument("storyboard", help="storyboard.json（来自 script 子命令）")
    p.add_argument("-o", "--output", required=True, help="最终 mp4 输出路径")
    p.add_argument("--image-backend", choices=["minimax", "gemini", "mock"], default="minimax",
                   help="图像后端（默认 minimax；gemini=Nano Banana；mock 不调真 API）")
    p.add_argument("--tts-backend", choices=["edge", "mock"], default="edge",
                   help="TTS 后端（默认 edge；mock 不调真 API）")
    p.add_argument("--voice", default="zh-CN-XiaoxiaoNeural", help="TTS voice 名")
    p.add_argument("--video-seconds", type=int, default=5, help="每镜头视频秒数（默认 5）")
    p.add_argument("--video-fps", type=int, default=8, help="视频帧率（默认 8）")
    p.add_argument("--video-effect", default="cycle",
                   help="视频 Ken Burns 效果：cycle / zoom_in / zoom_out / pan_left / pan_right / pan_up / pan_down / static")
    p.add_argument("--assets-dir", default="./assets", help="V2.5 物料目录（默认 ./assets；与 prep 共用）")
    # V3.8 缺陷 1：pipeline 子命令此前**没有** --aspect-ratio，
    #   画幅被硬编码成 16:9 → 竖屏（小红书 9:16）在生产路径不可达。
    #   不传 = 由 backend 读自己的 env（MINIMAX_IMAGE_ASPECT_RATIO / GEMINI_IMAGE_ASPECT_RATIO）。
    p.add_argument("--aspect-ratio", default=None,
                   help="画幅 16:9 / 9:16 / 1:1 等（默认取 backend 的 env，再退 16:9）")
    p.add_argument("--skip-asset-prep", action="store_true", help="V2.5 跳过 Phase 0（已 prep 过可复用缓存）")
    p.add_argument("--keep-intermediate", action="store_true", help="保留中间产物")
    p.add_argument("--parallel", type=int, default=1,
                   help="V3.0 并发数（per-shot fan-out，>1 启用 ThreadPoolExecutor；默认 1）")
    p.add_argument("--motion-budget", type=int, default=3,
                   help="V3.1 真 AI 视频生成 budget（MiniMax Max 默认 3 hailuo-2.3/天；超额 fallback Ken Burns）")
    p.add_argument("--run-dir", default="./run",
                   help="V3.0 run 目录（log.jsonl + registry.json；默认 ./run）")
    # V3.8 W4：运动参数知识库（REQ-406）。默认**不记录** —— 写库是副作用，
    #   显式传路径才开启（避免污染人工维护的 knowledge/motion_params.json）。
    p.add_argument("--knowledge-path", default=None,
                   help="运动参数知识库 JSON 路径（如 knowledge/motion_params.json；默认不记录）")
    p.add_argument("--dry-run", action="store_true",
                   help="V3.0 仅打印 DAG 后退出（不调任何 API）")
    p.add_argument("--resume-from", default=None,
                   choices=["prep", "image_gen", "video_synth", "tts", "concat", "qa"],
                   help="V3.0 从指定 phase 开始（跳过已完成；与 --keep-intermediate 配合续跑）")
    p.set_defaults(func=cmd_pipeline)

    pp = sub.add_parser("prep", help="V2.5 Phase 0：只生成 character sheet（缓存到 assets/）")
    pp.add_argument("storyboard", help="storyboard.json")
    pp.add_argument("--assets-dir", default="./assets", help="素材目录（默认 ./assets）")
    pp.add_argument("--image-backend", choices=["minimax", "gemini", "mock"], default="minimax",
                    help="图像后端（默认 minimax；gemini=Nano Banana）")
    pp.add_argument("--force", action="store_true", help="强制重新生成（忽略缓存）")
    pp.set_defaults(func=cmd_prep)

    # V3.2 REQ-303 editing 子命令
    e = sub.add_parser("editing", help="V3.2：后期处理（color_normalize / crossfade / motion_match / apply_lut / full）")
    e.add_argument("--mode", required=True,
                   choices=["color_normalize", "crossfade", "motion_match", "apply_lut", "full"],
                   help="后期子功能选择")
    e.add_argument("--input", dest="inputs", action="append", default=[],
                   help="输入视频路径（可多次传；color_normalize/crossfade/motion_match/full 用）")
    e.add_argument("--video", help="单视频输入路径（apply_lut 用）")
    e.add_argument("-o", "--output", help="输出 mp4 路径（crossfade / apply_lut / full 用）")
    e.add_argument("--output-dir", help="输出目录（color_normalize 用）")
    e.add_argument("--lut", help=".cube LUT 文件路径（apply_lut / full 可选）")
    e.add_argument("--work-dir", help="中间产物目录（full 用；默认系统临时目录）")
    e.add_argument("--transition", default="fade",
                   choices=editing.VALID_XFADE_TRANSITIONS,
                   help="xfade transition 名（crossfade / full 用；默认 fade）")
    e.add_argument("--overlap", type=float, default=0.25,
                   help="叠化时长（秒，0.05..2.0；默认 0.25）")
    e.add_argument("--sample-count", type=int, default=8,
                   help="motion_match 每段采样帧数（默认 8）")
    e.add_argument("--timeout", type=int, default=180,
                   help="per-clip ffmpeg timeout 秒（默认 180）")
    e.set_defaults(func=cmd_editing)

    # V2.6 QA subcommands
    v = sub.add_parser("validate", help="V2.6：校验 storyboard.json schema + 引用一致性")
    v.add_argument("storyboard", help="storyboard.json")
    v.set_defaults(func=cmd_validate)

    # V3.8 W6：VLM 画面评审（默认 mock —— 零网络调用）
    rv = sub.add_parser("review", help="V3.8：VLM 画面评审（默认 mock；真 API 需 --enable-real）")
    rv.add_argument("images", nargs="*", help="待评审的图片（按时间顺序）")
    rv.add_argument("--backend", choices=["mock", "gemini"], default="mock",
                    help="评审后端（默认 mock；gemini 需 --enable-real + GEMINI_VLM_MODEL）")
    rv.add_argument("--enable-real", action="store_true",
                    help="允许调用真 API（默认关闭，避免意外计费）")
    rv.add_argument("--context", default="",
                    help="评审上下文（分镜描述 / 旁白 / 角色设定），会进 prompt")
    rv.add_argument("--output", default=None, help="评审报告 JSON 输出路径")
    rv.set_defaults(func=cmd_review)

    ca = sub.add_parser("check-assets", help="V2.6：列出 storyboard 所需但 assets/ 还没的 reference sheets")
    ca.add_argument("storyboard", help="storyboard.json")
    ca.add_argument("--assets-dir", default="./assets", help="素材目录（默认 ./assets）")
    ca.set_defaults(func=cmd_check_assets)

    cc = sub.add_parser("consistency-check", help="V2.6：对生成的 shot jpg 列表跑色差一致性度量")
    cc.add_argument("shots_dir", help="含 shot_NNN.jpg 的目录（一般用 pipeline --keep-intermediate 的 intermediate_dir）")
    cc.set_defaults(func=cmd_consistency_check)

    return ap


def cmd_validate(args: argparse.Namespace) -> int:
    """V2.6：校验 storyboard schema（不依赖具体写作）。"""
    sb_path = Path(args.storyboard)
    if not sb_path.is_file():
        print(f"[FAIL] storyboard 不存在：{sb_path}", file=sys.stderr)
        return 2
    try:
        storyboard = pl.load_storyboard(sb_path)
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        print(f"[FAIL] storyboard 解析失败：{exc}", file=sys.stderr)
        return 2
    rep = quality.validate_storyboard(storyboard)
    print(rep.format(), file=sys.stderr)
    return 0 if rep.ok else 1


def cmd_check_assets(args: argparse.Namespace) -> int:
    """V2.6：pre-flight asset 完整性检查。"""
    sb_path = Path(args.storyboard)
    if not sb_path.is_file():
        print(f"[FAIL] storyboard 不存在：{sb_path}", file=sys.stderr)
        return 2
    try:
        storyboard = pl.load_storyboard(sb_path)
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        print(f"[FAIL] storyboard 解析失败：{exc}", file=sys.stderr)
        return 2
    assets_dir = Path(args.assets_dir).resolve()
    rep = quality.check_assets(storyboard, assets_dir)
    print(rep.format(), file=sys.stderr)
    if rep.all_missing:
        print(f"\n建议跑：python -m hailuo_video prep {sb_path} --assets-dir {assets_dir}", file=sys.stderr)
    return 0 if rep.ok else 1


def cmd_consistency_check(args: argparse.Namespace) -> int:
    """V2.6：对 shots_dir 下所有 shot_NNN.jpg 跑色差一致性度量。"""
    shots_dir = Path(args.shots_dir)
    if not shots_dir.is_dir():
        print(f"[FAIL] shots_dir 不存在：{shots_dir}", file=sys.stderr)
        return 2
    shot_paths = sorted(shots_dir.glob("shot_*.jpg"))
    if len(shot_paths) < 2:
        print(f"[FAIL] 至少需要 2 张 shot_NNN.jpg（找到 {len(shot_paths)} 张）", file=sys.stderr)
        return 2
    rep = quality.compute_color_consistency(shot_paths)
    print(rep.format(), file=sys.stderr)
    return 0 if rep.ok else 1


def cmd_review(args: argparse.Namespace) -> int:
    """V3.8 W6：VLM 画面评审（默认 mock —— 零网络调用、零计费）。

    真 API（Gemini VLM）需 **同时**满足：`--backend gemini` + `--enable-real`
    + env `GEMINI_API_KEY` + env `GEMINI_VLM_MODEL`。
    缺任一项 → 显式报错（鉴权域硬失败，**不静默退回 mock**）。
    """
    from hailuo_video import vlm_review as vr

    images = [str(p) for p in (args.images or [])]
    if not images:
        print("[FAIL] 至少给一张图", file=sys.stderr)
        return 2
    missing = [p for p in images if not Path(p).is_file()]
    if missing:
        print(f"[FAIL] 图片不存在：{missing[:3]}", file=sys.stderr)
        return 2

    try:
        reviewer = vr.make_reviewer(args.backend, enable_real=args.enable_real)
    except vr.VLMReviewError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2

    try:
        report = reviewer.review(images, context=args.context or "")
    except vr.VLMReviewError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"[FAIL] 评审异常：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(report.format(), file=sys.stderr)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[OK] review report → {out}", file=sys.stderr)
    return 0 if report.ok else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())