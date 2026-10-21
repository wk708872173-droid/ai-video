"""FFmpeg 拼接引擎（V2.4）。

基于 imageio-ffmpeg 自带的 ffmpeg 二进制（pip 包，不依赖系统 ffmpeg 安装）。

核心操作：
  - merge_clip(video, audio, output)        # 视频 + 音频合并（短片级）
  - concat_videos([video_a, video_b, ...], output)   # 多段拼接
  - burn_subtitles(video, subtitle, output)  # 烧字幕
  - concat_pipeline(shots, output)           # 端到端：shots → 最终 mp4

shots 格式（来自 storyboard.json + 后续 image/video/tts 产物）：
  [
    {"video": "out/0000.mp4", "audio": "audio/0000.mp3", "subtitle": "audio/0000.vtt"},
    {"video": "out/0001.mp4", "audio": "audio/0001.mp3", "subtitle": "audio/0001.vtt"},
    ...
  ]

D-015 应用：ffmpeg 调用走 subprocess；不固化参数细节（codec/preset 可注入）。
单测 mock subprocess.run，不依赖 ffmpeg 二进制。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from hailuo_video.local_client import PACKAGE_ROOT


# ---------- 配置 ----------

class ConcatError(RuntimeError):
    """ffmpeg 调用 / 拼接失败。"""


# ffmpeg 二进制探测（imageio-ffmpeg 优先；回退系统 PATH）
def _resolve_ffmpeg_exe() -> str:
    """找 ffmpeg 二进制路径。优先 imageio-ffmpeg 自带；否则 PATH。"""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        pass
    exe = shutil.which("ffmpeg")
    if not exe:
        raise ConcatError(
            "未找到 ffmpeg 二进制；先 `pip install imageio-ffmpeg` "
            "或确保系统 ffmpeg 在 PATH"
        )
    return exe


def ffmpeg_version() -> str:
    """返回 ffmpeg 版本字符串。"""
    exe = _resolve_ffmpeg_exe()
    try:
        out = subprocess.run(
            [exe, "-version"], capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConcatError(f"ffmpeg 调用失败：{exc}") from exc
    if out.returncode != 0:
        raise ConcatError(f"ffmpeg -version 失败：{out.stderr}")
    return out.stdout.split("\n", 1)[0]


def is_available() -> bool:
    """ffmpeg 是否可用（用于 doctor 风格检查）。"""
    try:
        _resolve_ffmpeg_exe()
        return True
    except ConcatError:
        return False


# ---------- 数据类 ----------

@dataclass
class ShotItem:
    """单镜头的素材列表。video/audio/subtitle 路径均可选。"""
    index: int
    video: str | None = None        # mp4 / mov
    audio: str | None = None        # mp3 / wav / aac
    subtitle: str | None = None     # .vtt / .srt（可选）
    duration_seconds: float = 0.0  # 用于估算

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ConcatResult:
    output: str
    shot_count: int = 0
    duration_seconds: float = 0.0
    bytes: int = 0
    source: str = "ok"               # "ok" | "error"
    error: str = ""
    ffmpeg_log: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.source == "ok"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------- 内部：subprocess 运行器（可注入 mock） ----------

# 签名：run_ffmpeg(args_list, timeout) -> (returncode, stdout, stderr)
RunFFmpegFn = Callable[[list[str], int], tuple[int, str, str]]


def _default_runner(args: list[str], timeout: int) -> tuple[int, str, str]:
    """真网跑 ffmpeg。

    DingTalk / Windows CLI 注意：
      - 加 encoding="utf-8" + errors="replace"：Windows 默认 cp936 会把 ffmpeg 的
        UTF-8 stderr 错读成乱码（"Invalid argument" → "��Ч��"），加 encoding 强制 UTF-8 解码
      - args 全是 str() 路径，ffmpeg.exe 在 Windows 上接受 \\ 与 / 都行
    """
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConcatError(f"ffmpeg 子进程失败：{type(exc).__name__}: {exc}") from exc
    return proc.returncode, proc.stdout, proc.stderr


_runner: RunFFmpegFn = _default_runner


def set_runner(fn: RunFFmpegFn | None) -> None:
    """单测注入：替换 ffmpeg runner。None = 恢复默认。"""
    global _runner
    _runner = fn if fn is not None else _default_runner


# ---------- 单条：合并视频 + 音频 ----------

def merge_clip(
    video: str | Path,
    audio: str | Path,
    output: str | Path,
    *,
    timeout: int = 120,
    video_codec: str = "libx264",
    audio_codec: str = "aac",
    extra_args: list[str] | None = None,
) -> Path:
    """把音频合并到视频。短时长优先（以视频为准）。"""
    video = str(video)
    audio = str(audio)
    output = str(output)
    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    exe = _resolve_ffmpeg_exe()
    args = [
        exe, "-y",
        "-i", video,
        "-i", audio,
        "-c:v", video_codec,
        "-c:a", audio_codec,
        "-shortest",
        "-map", "0:v:0",
        "-map", "1:a:0",
    ]
    if extra_args:
        args.extend(extra_args)
    args.append(output)
    rc, _out, err = _runner(args, timeout)
    if rc != 0:
        raise ConcatError(f"merge_clip 失败 rc={rc}：{err[-300:]}")
    return out_path


# ---------- 多条：concat demuxer ----------

def _write_concat_list(items: Iterable[str], path: Path) -> Path:
    """写 ffmpeg concat demuxer 列表。路径含空格 / 特殊字符也安全。"""
    lines = []
    for item in items:
        # ffmpeg concat demuxer 协议：单行 `file '...'` 或 `file ...`
        escaped = item.replace("'", "'\\''")
        lines.append(f"file '{escaped}'\n")
    path.write_text("".join(lines), encoding="utf-8")
    return path


def concat_videos(
    video_paths: Iterable[str],
    output: str | Path,
    *,
    timeout: int = 600,
    codec: str = "copy",
    extra_args: list[str] | None = None,
) -> Path:
    """拼接多段视频（codec 同）。默认 `-c copy`（无损快速）；不重编码则要求所有段 codec/分辨率/采样率一致。"""
    items = list(video_paths)
    if not items:
        raise ConcatError("concat_videos：至少要一段视频")
    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        list_file = Path(tmp) / "concat.txt"
        _write_concat_list(items, list_file)
        exe = _resolve_ffmpeg_exe()
        args = [
            exe, "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_file),
            "-c", codec,
        ]
        if extra_args:
            args.extend(extra_args)
        args.append(str(out_path))
        rc, _out, err = _runner(args, timeout)
        if rc != 0:
            raise ConcatError(f"concat_videos 失败 rc={rc}：{err[-300:]}")
    return out_path


# ---------- 单条：烧字幕到视频 ----------

def burn_subtitles(
    video: str | Path,
    subtitle: str | Path,
    output: str | Path,
    *,
    timeout: int = 120,
    codec: str = "libx264",
    extra_args: list[str] | None = None,
) -> Path:
    """烧 .srt 字幕（vtt 不支持，需先转 srt）。"""
    video = str(video)
    subtitle = str(subtitle)
    output = str(output)
    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Windows ffmpeg vf=subtitles= 不接受反斜杠；用正斜杠。
    sub_filter = str(Path(subtitle)).replace("\\", "/")
    # : 也要转义
    sub_filter = sub_filter.replace(":", "\\:")

    exe = _resolve_ffmpeg_exe()
    args = [
        exe, "-y",
        "-i", video,
        "-vf", f"subtitles='{sub_filter}'",
        "-c:v", codec,
        "-c:a", "copy",
    ]
    if extra_args:
        args.extend(extra_args)
    args.append(output)
    rc, _out, err = _runner(args, timeout)
    if rc != 0:
        raise ConcatError(f"burn_subtitles 失败 rc={rc}：{err[-300:]}")
    return out_path


# ---------- 端到端：shots → 最终 mp4 ----------

def concat_pipeline(
    shots: list[ShotItem],
    output: str | Path,
    *,
    timeout_per_clip: int = 120,
    timeout_total: int = 1800,
    burn_subs: bool = True,
    keep_intermediate: bool = False,
    extra_args: list[str] | None = None,
) -> ConcatResult:
    """对每个 ShotItem：
         a. 若 audio≠ 没 voice 且 video≠ 没图：merge_clip → 中间 mp4
         b. 若 audio≠ 没 voice 且 video==没图：跳过（不接静片）；返回 source=error
         c. 若 subtitle 有 且 burn_subs=True：烧字幕
       收集所有中间 mp4 → concat_videos（-c copy）。
    失败抛 ConcatError；返回 ConcatResult 含 ffmpeg 日志。
    """
    if not shots:
        raise ConcatError("concat_pipeline：shots 列表为空")

    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    exe = _resolve_ffmpeg_exe()
    log_lines: list[str] = [f"[concat] ffmpeg: {exe}"]

    intermediate_dir = Path(tempfile.mkdtemp(prefix="concat_"))
    try:
        merged_paths: list[Path] = []
        for item in shots:
            if not item.video:
                raise ConcatError(f"shot[{item.index}] 缺 video 路径")
            if not Path(item.video).exists():
                raise ConcatError(f"shot[{item.index}] video 不存在：{item.video}")

            # 阶段 1：merge_clip（若有 audio）
            if item.audio and Path(item.audio).exists():
                merged = intermediate_dir / f"{item.index:04d}_merged.mp4"
                log_lines.append(f"[concat] merge {item.video} + {item.audio} → {merged}")
                merge_clip(
                    item.video, item.audio, merged,
                    timeout=timeout_per_clip,
                    extra_args=extra_args,
                )
                current = merged
            else:
                # 没 audio → 直接用视频
                log_lines.append(f"[concat] skip merge (no audio): {item.video}")
                current = Path(item.video)

            # 阶段 2：烧字幕（若有 subtitle 且 burn_subs=True）
            if burn_subs and item.subtitle and Path(item.subtitle).exists():
                burned = intermediate_dir / f"{item.index:04d}_sub.mp4"
                log_lines.append(f"[concat] burn subs {current} + {item.subtitle} → {burned}")
                burn_subtitles(
                    current, item.subtitle, burned,
                    timeout=timeout_per_clip,
                    extra_args=extra_args,
                )
                current = burned

            merged_paths.append(current)

        # 阶段 3：concat
        log_lines.append(f"[concat] concat {len(merged_paths)} clips → {out_path}")
        concat_videos(
            [str(p) for p in merged_paths],
            out_path,
            timeout=timeout_total,
            extra_args=extra_args,
        )

        elapsed = time.monotonic() - started
        out_bytes = out_path.stat().st_size if out_path.exists() else 0
        return ConcatResult(
            output=str(out_path),
            shot_count=len(shots),
            duration_seconds=elapsed,
            bytes=out_bytes,
            source="ok",
            ffmpeg_log="\n".join(log_lines),
            extra={"intermediate_dir": str(intermediate_dir) if keep_intermediate else "(deleted)"},
        )
    except ConcatError as exc:
        return ConcatResult(
            output=str(out_path),
            shot_count=len(shots),
            duration_seconds=time.monotonic() - started,
            source="error",
            error=str(exc),
            ffmpeg_log="\n".join(log_lines),
        )
    finally:
        if not keep_intermediate:
            try:
                shutil.rmtree(intermediate_dir, ignore_errors=True)
            except OSError:
                pass


# ---------- 工厂 / IO ----------

def make_concat_result_from_manifest(manifest_path: str | Path) -> list[ShotItem]:
    """从 manifest.json 读 shots 列表转 ShotItem[]。

    manifest 格式：
    {"shots": [{"video": ..., "audio": ..., "subtitle": ...}, ...]}
    """
    data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    raw_shots = data.get("shots", [])
    out: list[ShotItem] = []
    for i, s in enumerate(raw_shots):
        if not isinstance(s, dict):
            continue
        out.append(ShotItem(
            index=i,
            video=s.get("video"),
            audio=s.get("audio"),
            subtitle=s.get("subtitle"),
        ))
    return out


def write_concat_manifest(shots: list[ShotItem], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"shots": [s.to_dict() for s in shots]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target