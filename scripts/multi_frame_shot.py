"""V3.2 自动开发冲刺（轮 11 L2）—— 多帧定格动画生成。

每镜生成 5 张"动作阶段"图（同一橘猫不同姿态），用 ffmpeg 拼成 6 秒定格动画。

用法：
    python scripts/multi_frame_shot.py shot_001 <story_arc.json>
    # 生成 5 张图 + 拼成 shot_001_l2.mp4

每个 shot 内部硬编码 5 个 motion_frames（story-arc 级配置）。
需 image-01 quota（MiniMax image API），不耗 Hailuo quota。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SANDBOX = REPO / "sandbox"
sys.path.insert(0, str(SANDBOX))

import imageio_ffmpeg  # noqa: E402

from hailuo_video.script_engine import StoryArc  # noqa: E402


# ---------- 6 镜的 5 帧 motion 描述（V3.2 轮 11 L2）----------
# 每帧含 description 增量（动作阶段）+ camera_motion 微调 + duration
# 主 description / style_anchor / character 来自 storyboard.json（保证跨帧一致性）

MOTION_FRAMES = {
    "shot_001": [
        {"frame_id": "F1", "delta": "fully curled sleeping on linen cushion, eyes closed, peaceful rest, morning light just beginning to warm the window", "duration": 1.2},
        {"frame_id": "F2", "delta": "eyes slightly open, ears perk up, head begins to lift from cushion", "duration": 1.2},
        {"frame_id": "F3", "delta": "front paws extending forward in stretch, back beginning to arch, mouth opening for yawn", "duration": 1.2},
        {"frame_id": "F4", "delta": "back fully arched mid-stretch, yawn at peak, eyes squinted shut", "duration": 1.2},
        {"frame_id": "F5", "delta": "stretch finished, body relaxed, head turning to look out the window, eyes wide open", "duration": 1.2},
    ],
    "shot_002": [
        {"frame_id": "F1", "delta": "orange tabby cat enters from left side of stone garden path, mid-stride, body leaning forward", "duration": 1.2},
        {"frame_id": "F2", "delta": "continues walking forward, right paw stepping, profile view, body in motion", "duration": 1.2},
        {"frame_id": "F3", "delta": "approaching yellow daisy cluster, slowing down, head lowering toward flowers", "duration": 1.2},
        {"frame_id": "F4", "delta": "close to daisy, head down, nose 2cm from flower, whiskers extended", "duration": 1.2},
        {"frame_id": "F5", "delta": "nose touching flower center, eyes closed in concentration, whiskers forward, body still", "duration": 1.2},
    ],
    "shot_003": [
        {"frame_id": "F1", "delta": "crouching on grey tile roof edge, looking out at orange sunset sky, ears back", "duration": 1.2},
        {"frame_id": "F2", "delta": "sits up slightly straighter, ears forward, alert to distant sound", "duration": 1.2},
        {"frame_id": "F3", "delta": "head turns slowly to the right, eyes following movement in valley below", "duration": 1.2},
        {"frame_id": "F4", "delta": "head returns to forward-facing position, gaze settles back on horizon", "duration": 1.2},
        {"frame_id": "F5", "delta": "stillness returns, tail tip gives single slow flick, body otherwise motionless", "duration": 1.2},
    ],
    "shot_004": [
        {"frame_id": "F1", "delta": "walks onto old cotton cushion, looking around the room, body low", "duration": 1.2},
        {"frame_id": "F2", "delta": "circles once on cushion, paws adjusting position, body curled mid-rotation", "duration": 1.2},
        {"frame_id": "F3", "delta": "settles down onto cushion, body lying, head up and alert", "duration": 1.2},
        {"frame_id": "F4", "delta": "puts front paw gently over nose, eyes half-open, ears relaxed", "duration": 1.2},
        {"frame_id": "F5", "delta": "eyes fully closed, body still, breathing visible in chest rise", "duration": 1.2},
    ],
    "shot_005": [
        {"frame_id": "F1", "delta": "extreme macro close-up of cat's open eye, amber iris fibers visible, moonlight reflection of distant mountains", "duration": 1.2},
        {"frame_id": "F2", "delta": "eyelid begins lowering from above, eye half-open, iris partially covered", "duration": 1.2},
        {"frame_id": "F3", "delta": "eye closing further, eyelash detail visible against dark fur, reflection fading", "duration": 1.2},
        {"frame_id": "F4", "delta": "eye fully closed, eyelash resting on cheek fur, fine hair detail", "duration": 1.2},
        {"frame_id": "F5", "delta": "eye remains closed and relaxed, breathing motion subtle in surrounding fur", "duration": 1.2},
    ],
    "shot_006": [
        {"frame_id": "F1", "delta": "still sleeping in same curled position, soft dawn light warming the room", "duration": 1.2},
        {"frame_id": "F2", "delta": "slowly opens eyes to slits, dawn light catching amber iris, ears perk", "duration": 1.2},
        {"frame_id": "F3", "delta": "yawns widely, jaw open, tongue visible, eyes squeezed shut", "duration": 1.2},
        {"frame_id": "F4", "delta": "sits up, body upright, looking toward window where dawn light comes in", "duration": 1.2},
        {"frame_id": "F5", "delta": "head turns to look outside window, sees small bird on branch outside, alert expression", "duration": 1.2},
    ],
}


def assemble_frame_prompt(frame_delta: str, shot_description: str, style_anchor: str) -> str:
    """V3.3（2026-10-09）：每帧 prompt = style_anchor + shot 主描述 + frame 动作 delta。

    强化点：
      1. style_anchor 前置（与 orchestrator Change D 一致；image-01 偏好 prompt 前缀）
      2. 在 prompt 末尾追加"negative"短语（image-01 当前无 negative_prompt 字段，
         但有 prompt 优化关闭时，尾部"避免 X"短语能起到部分抑制作用）。
    """
    parts = []
    if style_anchor:
        parts.append(f"[风格:{style_anchor}]")
    if shot_description:
        parts.append(shot_description)
    if frame_delta:
        parts.append(f"[动作阶段:{frame_delta}]")
    # 末尾兜底：声明避免 AI 痕迹（即使 image-01 不识别 negative_prompt，
    # prompt_optimizer=False 时尾部声明仍有部分抑制作用）
    parts.append("photorealistic, no illustration, no text, no watermark, no deformed fingers, no extra limbs")
    return " ".join(parts)


def load_scene_refs(scene_id: int | None, assets_dir: Path = Path("assets")) -> list[str]:
    """V3.3（2026-10-09）：从 assets/scenes/scene_NNNN_<variant>.url.txt 读场景参考 URL。

    优先 day → sunset → night 三档，按文件存在与否 fallback。
    返回 [] 表示无缓存（best-effort，不阻塞）。
    """
    if scene_id is None:
        return []
    scenes_dir = assets_dir / "scenes"
    if not scenes_dir.is_dir():
        return []
    for variant in ("day", "sunset", "night"):
        url_path = scenes_dir / f"scene_{int(scene_id):04d}_{variant}.url.txt"
        if url_path.is_file():
            try:
                url = url_path.read_text(encoding="utf-8").strip()
                if url:
                    return [url]
            except Exception:
                pass
    return []


def generate_frame_image(
    backend,
    prompt: str,
    output_path: Path,
    *,
    aspect_ratio: str = "9:16",
    subject_reference: list[str] | None = None,
) -> bool:
    """V3.3（2026-10-09）：调 MiniMax image-01 生成 1 张图。返回 True 成功。

    新增 subject_reference 参数：scene variant URL 列表（来自 load_scene_refs）。
    传入后 → image_engine.generate() 会作为 subject_references[] 传给 image-01（多 ref i2i 锚定）。
    """
    try:
        results = backend.generate(
            prompt,
            aspect_ratio_value=aspect_ratio,
            n_value=1,
            response_format_value="url",  # V3.2：用 url 模式（避免 base64 decode 复杂度）
            subject_reference=subject_reference,
        )
        if not results or not results[0].image_url:
            print(f"[FAIL] {output_path.name}: 无 image_url 响应", file=sys.stderr)
            return False
        url = results[0].image_url[0] if isinstance(results[0].image_url, list) else results[0].image_url
        if not url:
            print(f"[FAIL] {output_path.name}: 空 url", file=sys.stderr)
            return False
        # 下载 url 到 output_path
        import urllib.request
        urllib.request.urlretrieve(url, str(output_path))
        if not output_path.is_file() or output_path.stat().st_size < 1000:
            print(f"[FAIL] {output_path.name}: 下载失败或文件过小", file=sys.stderr)
            return False
        return True
    except Exception as exc:
        print(f"[FAIL] {output_path.name}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return False


def stitch_frames_to_clip(frame_paths: list[Path], output_path: Path, fps: int = 12) -> bool:
    """5 张图 + crossfade 拼成 6 秒定格动画（每帧 1.2s + 0.3s crossfade）。"""
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    # 输入：每个 -loop 1 -t 6 -i image.jpg（用 6s 防止 xfade 链被截短）
    inputs = []
    for fp in frame_paths:
        inputs.extend(["-loop", "1", "-t", "6", "-i", str(fp)])

    # filterchain：scale 归一 + tblend 帧间柔化 + xfade 拼
    n = len(frame_paths)
    w, h = 1280, 720
    fps = 12

    # 简化：用 scale 把每张图缩到统一大小（直接给 xfade 输入）
    scale_chain = ""
    for i in range(n):
        scale_chain += (
            f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1[v{i}];"
        )

    # xfade chain：相邻两帧 0.3s 叠化
    # 关键：第 i 个 xfade 的输出是 xf(i+1)，下一个 xfade 必须用它作输入（不能重复用 v_i）
    xfade_chain = ""
    for i in range(n - 1):
        offset = i * (1.2 - 0.3)
        prev_label = "v0" if i == 0 else f"xf{i}"      # 第 1 个用 v0，之后用 xf(i)
        next_label = f"xf{i+1}" if i < n - 2 else "vout"  # 最后产出 vout
        xfade_chain += f"[{prev_label}][v{i+1}]xfade=transition=fade:duration=0.3:offset={offset:.2f}[{next_label}];"

    # 完整 vf：scale + xfade（不加 noise/grain/rotate —— 简化避免 filtergraph 错误）
    vf = scale_chain + xfade_chain

    cmd = [
        ffmpeg, "-y",
        *inputs,
        "-filter_complex", vf,
        "-map", "[vout]",
        "-c:v", "libx264", "-preset", "slow", "-crf", "20",
        "-pix_fmt", "yuv420p", "-color_range", "tv",
        "-t", "6",  # 总时长
        "-an",  # 无音轨（最终 stitching 才加音）
        str(output_path),
    ]
    proc = subprocess_run(cmd, timeout=60)
    if proc.returncode != 0:
        print(f"[FAIL] stitch {output_path.name}: {proc.stderr[-200:]}", file=sys.stderr)
        return False
    return True


def subprocess_run(cmd: list[str], timeout: int = 60):
    import subprocess
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def process_one_shot(shot_id: str, arc: StoryArc, work_dir: Path, output_dir: Path) -> bool:
    """单镜 L2 全流程：读 arc 配置 → 5 张图 → 拼 6s mp4。"""
    if shot_id not in MOTION_FRAMES:
        print(f"[FAIL] no MOTION_FRAMES for {shot_id}", file=sys.stderr)
        return False

    # 找该 shot 在 arc 里的 shot 对象（用于 description + characters）
    shot = next((s for s in arc.shots if s.shot_id == shot_id), None)
    if not shot:
        print(f"[FAIL] {shot_id} not in arc", file=sys.stderr)
        return False

    frames = MOTION_FRAMES[shot_id]
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Step 1：5 张图（MiniMax image-01）
    print(f"[INFO] {shot_id}: generating {len(frames)} frames...", file=sys.stderr)
    from hailuo_video import image_engine as ie  # noqa: E402
    backend = ie.make_backend("minimax")

    frame_paths = []
    # V3.3（2026-10-09）：从缓存读 scene ref URL（day/sunset/night fallback）
    scene_refs = load_scene_refs(getattr(shot, "scene_id", None))
    if scene_refs:
        print(f"  [INFO] scene ref: {scene_refs[0][:80]}...", file=sys.stderr)
    for i, fr in enumerate(frames):
        out_jpg = work_dir / f"frame_{i+1}.jpg"
        prompt = assemble_frame_prompt(fr["delta"], shot.description, arc.style_anchor)
        ok = generate_frame_image(backend, prompt, out_jpg, subject_reference=scene_refs)
        if not ok:
            # 单帧失败：跳过这一镜（继续下一镜）
            print(f"[FAIL] {shot_id} frame {i+1} failed; skipping shot", file=sys.stderr)
            return False
        frame_paths.append(out_jpg)
        print(f"  [OK] frame {i+1}: {out_jpg.name} ({out_jpg.stat().st_size:,} B)", file=sys.stderr)

    # Step 2：拼成 6s mp4
    out_mp4 = output_dir / f"story_arc_orange_cat_4_seasons_{shot_id}_l2.mp4"
    if not stitch_frames_to_clip(frame_paths, out_mp4):
        return False
    print(f"  [OK] {out_mp4.name} ({out_mp4.stat().st_size:,} B)", file=sys.stderr)
    return True


def main() -> int:
    p = argparse.ArgumentParser(prog="multi_frame_shot")
    p.add_argument("shot_ids", nargs="+", help="要处理的 shot_id 列表（如 shot_001 shot_002）")
    p.add_argument("--arc", required=True, help="story_arc.json（Day 1 或 Day 2）")
    p.add_argument("--work-dir", default=None, help="中间帧图目录")
    p.add_argument("--out-dir", default=None, help="最终 mp4 输出目录")
    args = p.parse_args()

    arc_path = Path(args.arc)
    if not arc_path.is_file():
        print(f"[FAIL] {arc_path}", file=sys.stderr)
        return 2
    arc = StoryArc.from_json(arc_path)
    print(f"[INFO] loaded arc: {arc.title}", file=sys.stderr)

    day_tag = arc_path.stem  # day1 / day2
    work_root = Path(args.work_dir) if args.work_dir else Path(f"sample/_l2_work_{day_tag}")
    out_root = Path(args.out_dir) if args.out_dir else Path("sample")

    count = 0
    for sid in args.shot_ids:
        work_dir = work_root / sid
        ok = process_one_shot(sid, arc, work_dir, out_root)
        if ok:
            count += 1

    print(f"\n=== L2 done: {count}/{len(args.shot_ids)} clips ===")
    return 0 if count == len(args.shot_ids) else 1


if __name__ == "__main__":
    sys.exit(main())
