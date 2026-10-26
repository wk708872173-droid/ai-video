"""V4.0 W-D — 单镜媒体合并：烧字幕 + 混旁白。

## 为什么需要这个模块

V3 的 editing 链路（`editing/full.py:full_editing_pipeline`）只处理**画面**：

```
color_normalize → crossfade(xfade) → LUT
```

**TTS 阶段的配音与字幕从未进入成片**。实测仓库里已交付的成片：

```
★无音轨★  sample/story_arc_qingdao_autumn/final/qingdao_autumn_day1_9x16_xiaohongshu.mp4
★无音轨★  sample/story_arc_xiaohei_solo/final/xiaohei_2days_9x16_xiaohongshu.mp4
有音轨      sample/story_arc_xiaohei_solo/final/xiaohei_3days_16x9.mp4   ← 另一条脚本产的
```

即 **9 个成片里 8 个没有音轨**（包括客户主推的小红书 9:16 版本）。
TTS phase 跑完了（调了 edge-tts、花了时间），产物被丢在 registry 里没人用。

## 为什么在"每镜"层面合并（而不是整片层面）

- **字幕**：每镜的 .vtt 时间戳是**相对该镜**的。整片烧字幕需要把时间戳平移到
  拼接后的时间轴上（还要算 xfade 的 0.25s 重叠）—— 极易算错。
  在每镜层面烧，时间戳天然正确。
- **音频**：每镜合并后带上自己的音轨，随后 `crossfade_clips` 的 `acrossfade`
  就能把各镜音轨叠起来（该能力本来就有，只是此前没输入带音轨的视频）。

## 顺序

```
每镜：烧字幕（vtt→srt）→ 混旁白（shortest=False，画面不被截断）
```

`shortest=False` 很关键：`merge_clip` 默认 `-shortest`（以较短流为准），
一镜 6s、旁白 3s 时会把画面**截成 3s**。
"""
from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

from hailuo_video.concat_engine import burn_subtitles, merge_clip

# vtt 时间戳 `00:00:01.000` → srt `00:00:01,000`
_TS_DOT = re.compile(r"(\d{2}:\d{2}:\d{2})\.(\d{1,3})")
# vtt 里要跳过的块头
_VTT_SKIP_PREFIXES = ("WEBVTT", "NOTE", "STYLE", "REGION")


def vtt_to_srt(text: str) -> str:
    """WebVTT → SRT。

    - 去掉 `WEBVTT` 头与 NOTE/STYLE/REGION 块
    - 时间戳的 `.` → `,`
    - 重新连续编号（srt 要求序号）
    """
    lines = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[list[str]] = []
    current: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            if current:
                blocks.append(current)
                current = []
            continue
        if any(line.upper().startswith(p) for p in _VTT_SKIP_PREFIXES):
            continue
        current.append(line)
    if current:
        blocks.append(current)

    out: list[str] = []
    n = 0
    for block in blocks:
        if len(block) < 2 or "-->" not in block[0]:
            continue
        n += 1
        out.append(str(n))
        out.append(_TS_DOT.sub(r"\1,\2", block[0]))
        out.extend(block[1:])
        out.append("")
    return "\n".join(out)


def ensure_srt(subtitle_path: Path, work_dir: Path) -> Path:
    """保证拿到 .srt（.vtt 就转一份；.srt 原样返回）。"""
    src = Path(subtitle_path)
    if src.suffix.lower() == ".srt":
        return src
    try:
        text = src.read_text(encoding="utf-8")
    except OSError:
        return src
    dst = Path(work_dir) / (src.stem + ".srt")
    try:
        dst.write_text(vtt_to_srt(text), encoding="utf-8")
    except OSError:
        return src
    return dst


def merge_shot_media(
    video: Path | str,
    output: Path | str,
    *,
    audio: Path | str | None = None,
    subtitle: Path | str | None = None,
    work_dir: Path | str | None = None,
    timeout: int = 120,
) -> Path:
    """把一镜的字幕烧进画面、配音混进视频，输出到 `output`。

    Args:
        video: 该镜画面（color_normalize 之后）
        output: 输出路径
        audio: 该镜旁白 mp3（可空）
        subtitle: 该镜字幕 .vtt / .srt（可空）
        work_dir: 中间文件目录（默认系统临时目录）
        timeout: 单次 ffmpeg 超时

    Returns:
        output 的 Path
    """
    video_path = Path(video)
    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    work = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="merge_shot_"))
    work.mkdir(parents=True, exist_ok=True)

    current = video_path

    # 1) 烧字幕（.vtt → .srt 先转）
    if subtitle and Path(subtitle).is_file():
        srt = ensure_srt(Path(subtitle), work)
        burned = work / f"{video_path.stem}_sub.mp4"
        burn_subtitles(current, srt, burned, timeout=timeout)
        current = burned

    # 2) 混旁白（shortest=False：画面不被旁白长度截断）
    if audio and Path(audio).is_file():
        merged = work / f"{video_path.stem}_merged.mp4"
        merge_clip(current, audio, merged, timeout=timeout, shortest=False)
        current = merged

    # 3) 落到目标路径
    if current.resolve() != out_path.resolve():
        shutil.copyfile(current, out_path)
    return out_path


__all__ = [
    "ensure_srt",
    "merge_shot_media",
    "vtt_to_srt",
]
