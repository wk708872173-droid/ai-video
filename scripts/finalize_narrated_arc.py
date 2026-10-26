"""带旁白的分镜合辑剪辑器（V3.8）。

解决什么：项目原有 `finalize_with_fallback.py` 负责单条镜位的 9:16 / 封面 / 归档，
但**没有**「多日合辑 + 逐镜旁白对齐 + 字幕烧录」的能力。本脚本补这一环。

用法：
    python scripts/finalize_narrated_arc.py \
        --manifest sample/story_arc_xiaohei_solo/finalize_3days.json \
        --out final/xiaohei_3days_9x16_full.mp4 --variant full
    ... --variant blur      # 16:9 完整画面 + 高斯模糊填充（零裁剪）
    ... --variant native    # 16:9 原生，不裁不填

设计要点：
  · **逐段出片再 concat**，而不是一个大 filter_complex —— 任何一段出错都能
    定位到段，且能单段重跑（合辑重剪最常见的诉求就是「改一段」）。
  · 旁白在每段内 **t=LEAD_IN 起播**，配合字幕，让句子落在画面中段而不是压头。
  · 16:9 → 9:16 只保留 31.6% 宽度，两只狗横向铺开的镜位**物理上放不下**；
    这不是 bug。故提供 full（逐镜选窗，填满）与 blur（零裁剪）两个版本并存。
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def ffmpeg_exe() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def run(args: list[str]) -> None:
    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-12:]
        raise RuntimeError("ffmpeg 失败：\n  " + "\n  ".join(tail))


def _fit_lines(text: str, font_path: str, max_w: int, base_size: int) -> tuple[list[str], int]:
    """按可用宽度把字幕拆成 ≤2 行，并缩小字号直到放得下。

    尾卡那句有 30+ 字，按固定字号会横向溢出被两边切掉 —— 实测过一次。
    """
    from PIL import ImageFont
    size = base_size
    while size >= 18:
        f = ImageFont.truetype(font_path, size)
        lines: list[str] = []
        cur = ""
        # 优先在标点处断行，读起来不割裂
        for ch in text:
            cur += ch
            if ch in "，。！？；、,." and len(cur) >= 6:
                lines.append(cur)
                cur = ""
        if cur:
            lines.append(cur)
        if len(lines) > 2:                      # 断不开就按字数硬切
            per = max(6, (len(text) + 1) // 2)
            lines = [text[:per], text[per:]]
        if all(f.getbbox(ln)[2] - f.getbbox(ln)[0] <= max_w for ln in lines):
            return lines, size
        size -= 2
    return [text], base_size


def burn_subtitle(clip: Path, text: str, out: Path, *, w: int, h: int, fontsize: int) -> None:
    """把旁白文本烧成字幕（底部，白字 + 半透明黑底条）。"""
    if not text:
        shutil.copy(clip, out)
        return
    font_path = r"C:\Windows\Fonts\msyh.ttc"
    lines, size = _fit_lines(text, font_path, int(w * 0.86), fontsize)
    esc = lambda s: s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’").replace("%", "\\%")
    box_h = int(h * (0.155 if len(lines) == 1 else 0.20))
    parts = [f"drawbox=x=0:y={h - box_h}:w={w}:h={box_h}:color=black@0.42:t=fill"]
    y0 = h - int(box_h * 0.76)
    for i, ln in enumerate(lines):
        parts.append(
            f"drawtext=fontfile='C\\:/Windows/Fonts/msyh.ttc':text='{esc(ln)}':"
            f"fontcolor=white:fontsize={size}:x=(w-text_w)/2:y={y0 + i * int(size * 1.28)}"
        )
    run([ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(clip),
         "-vf", ",".join(parts), "-c:v", "libx264", "-preset", "medium", "-crf", "20",
         "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)])


def build_card(seg: dict, out: Path, *, w: int, h: int, tmp: Path) -> None:
    """标题卡：PIL 画一张底图 → Ken Burns 轻推 → 字幕同片烧录。"""
    from PIL import Image, ImageDraw, ImageFont

    bg = Image.new("RGB", (w, h), (28, 26, 24))
    d = ImageDraw.Draw(bg)
    # 上方一点暖光渐变，避免纯黑卡死板
    for y in range(int(h * 0.55)):
        t = y / (h * 0.55)
        d.line([(0, y), (w, y)],
               fill=(int(28 + 26 * t), int(26 + 18 * t), int(24 + 8 * t)))
    f_title = ImageFont.truetype(r"C:\Windows\Fonts\msyhbd.ttc", int(w * 0.105))
    f_sub = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", int(w * 0.052))
    ty = int(h * 0.42)
    # 整串一次绘制。逐字 anchor="mm" 会让每个字都以同一中心点定位 → 全部重叠成一坨
    d.text((w / 2, ty), seg["title"], font=f_title, fill=(246, 240, 230), anchor="mm")
    if seg.get("subtitle"):
        d.text((w / 2, ty + int(h * 0.115)), seg["subtitle"],
               font=f_sub, fill=(196, 178, 156), anchor="mm")
    src_png = tmp / f"card_{seg['id']}.png"
    bg.save(src_png)

    dur = float(seg.get("duration", 2.6))
    silent = tmp / f"card_{seg['id']}_silent.mp4"
    run([ffmpeg_exe(), "-y", "-loglevel", "error", "-loop", "1", "-i", str(src_png),
         "-t", f"{dur}",
         "-vf", f"zoompan=z='min(zoom+0.0006,1.10)':d={int(dur * 25)}:s={w}x{h}:fps=25,"
                f"format=yuv420p",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", str(silent)])
    burn_subtitle(silent, seg.get("subtitle_text") or "", out, w=w, h=h,
                  fontsize=int(w * 0.045))


def build_shot(seg: dict, out: Path, *, variant: str, tmp: Path) -> None:
    """镜位：按 variant 做 9:16 适配 → 烧字幕 → 混旁白。"""
    src = Path(seg["src"])
    if not src.is_file():
        raise FileNotFoundError(f"源视频缺失：{src}")
    dur = float(seg.get("duration", 6.0))
    w, h = (1080, 1920) if variant != "native" else (1920, 1080)
    frac = float(seg.get("frac", 0.5))

    if variant == "native":
        vf = "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,format=yuv420p"
    elif variant == "blur":
        # 16:9 完整画面居中，上下用同帧高斯模糊填充 —— 零裁剪
        vf = ("split[a][b];[a]scale=1920:1080,boxblur=18:2,scale=1080:1920[bg];"
              "[b]scale=-2:1080[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,"
              "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
              "format=yuv420p")
    else:  # full：按 frac 选窗口填满
        vf = (f"scale=-1:1920,crop=1080:1920:iw*{frac}:0,format=yuv420p")

    body = tmp / f"shot_{seg['id']}.mp4"
    run([ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(src), "-t", f"{dur}",
         "-vf", vf, "-r", "24", "-c:v", "libx264", "-preset", "medium",
         "-crf", "20", "-c:a", "aac", "-ar", "44100", str(body)])
    burn_subtitle(body, seg.get("subtitle_text") or "", out, w=w, h=h,
                  fontsize=int(w * 0.042))


def mix_narration(clip: Path, seg: dict, out: Path, tmp: Path) -> None:
    """旁白 + 环境音垫底，混到片段里。"""
    import re
    probe = subprocess.run(
        [ffmpeg_exe(), "-i", str(clip)], capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", probe.stderr or "")
    dur = (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))) if m else 6.0

    lead = float(seg.get("lead_in", 0.15))
    nar = Path(seg["narration"]) if seg.get("narration") else None
    if nar and not nar.is_file():
        raise FileNotFoundError(f"旁白缺失：{nar}")

    # 输入：0=片段 1=旁白(可选) 末位=环境音垫
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(clip)]
    nar_idx = None
    if nar:
        cmd += ["-i", str(nar)]
        nar_idx = 1
    bed_idx = nar_idx + 1 if nar_idx is not None else 1
    cmd += ["-f", "lavfi", "-t", f"{dur}", "-i", "anoisesrc=c=pink:r=44100:a=0.012"]

    if nar:
        fc = (f"[{nar_idx}:a]adelay={int(lead * 1000)}|{int(lead * 1000)},"
              f"volume=1.8[n];[{bed_idx}:a]volume=1[bed];"
              f"[n][bed]amix=inputs=2:duration=first:dropout_transition=0[m]")
    else:
        fc = f"[{bed_idx}:a]volume=1[m]"

    cmd += ["-filter_complex", fc, "-map", "0:v", "-map", "[m]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", str(out)]
    run(cmd)


def main() -> int:
    ap = argparse.ArgumentParser(description="带旁白的分镜合辑剪辑")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--variant", choices=["full", "blur", "native"], default="full")
    ap.add_argument("--voice", default=None, help="覆盖 manifest 里的旁白目录")
    ap.add_argument("--only", default=None, help="只做某一段（调试用）")
    args = ap.parse_args()

    mpath = Path(args.manifest)
    man = json.loads(mpath.read_text(encoding="utf-8"))
    root = mpath.parent
    segs = man["segments"]
    if args.only:
        segs = [s for s in segs if s["id"] == args.only]
        if not segs:
            print(f"[FAIL] 找不到段：{args.only}", file=sys.stderr)
            return 2

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="narr_arc_"))
    parts: list[Path] = []
    try:
        # 清单里的相对路径一律相对「清单所在目录」解析，而不是调用者的 cwd
        segs = [
            dict(s, src=str(root / s["src"]) if s.get("src") and not Path(s["src"]).is_absolute() else s.get("src"),
                 narration=str(root / s["narration"]) if s.get("narration") and not Path(s["narration"]).is_absolute() else s.get("narration"))
            for s in segs
        ]
        for i, seg in enumerate(segs, 1):
            if args.voice and seg.get("narration"):
                seg = dict(seg, narration=str(Path(seg["narration"]).parent / args.voice)
                           / Path(seg["narration"]).name)
            p_raw = tmp / f"{i:02d}_{seg['id']}_sub.mp4"
            vw, vh = (1920, 1080) if args.variant == "native" else (1080, 1920)
            if seg["kind"] == "card":
                build_card(seg, p_raw, w=vw, h=vh, tmp=tmp)
            else:
                build_shot(seg, p_raw, variant=args.variant, tmp=tmp)
            p_fin = tmp / f"{i:02d}_{seg['id']}.mp4"
            mix_narration(p_raw, seg, p_fin, tmp)
            parts.append(p_fin)
            print(f"  [{i}/{len(segs)}] {seg['id']:<10} ok  {seg.get('note','')}")

        lst = tmp / "concat.txt"
        lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
        run([ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
             "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(out)])
        size = out.stat().st_size
        print(f"[OK] {out}  ({size / 1e6:.1f} MB, {len(parts)} 段)")
        return 0
    except Exception as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        print(f"       中间产物保留在 {tmp}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())