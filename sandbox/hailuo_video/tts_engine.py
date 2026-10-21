"""Edge-TTS 客户端（V2.3）。

Edge-TTS 用 Microsoft Edge 浏览器内置的 read-aloud API；免 Key、免账号、零成本。
CPU 友好，不需要 GPU。

后端抽象：TTSBackend (ABC) → EdgeTTSBackend (生产) / MockTTSBackend (单测)。

⚠️ 注意：Edge-TTS 是闭源微软 web 服务，端点不公开文档。API 偶尔会改（历史上有过几次）。
D-010 应用：单测不依赖网络；mock backend 验证 I/O 契约；首次烟测必跑。
"""
from __future__ import annotations

import asyncio
import json
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from hailuo_video.local_client import PACKAGE_ROOT


# ---------- 配置 ----------

class TTSError(RuntimeError):
    """TTS 调用 / 解析失败。"""


DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
DEFAULT_RATE = "+0%"
DEFAULT_VOLUME = "+0%"
DEFAULT_PITCH = "+0Hz"

# 常用 voice 速查（不全；list_voices() 拿全量）
KNOWN_VOICES = {
    "xiaoxiao": "zh-CN-XiaoxiaoNeural",      # 中文女（默认）
    "yunxi": "zh-CN-YunxiNeural",            # 中文男
    "yunjian": "zh-CN-YunjianNeural",        # 中文男（体育）
    "xiaoyi": "zh-CN-XiaoyiNeural",          # 中文女（少年）
    "yating": "zh-CN-YatingNeural",          # 中文女（体育）
    "ava_en": "en-US-AvaNeural",             # en-US 女
    "andrew_en": "en-US-AndrewNeural",        # en-US 男
}


def _env_str(key: str, default: str) -> str:
    import os
    v = os.environ.get(key)
    return v if v else default


def default_voice() -> str:
    return _env_str("EDGE_TTS_VOICE", DEFAULT_VOICE)


def default_rate() -> str:
    return _env_str("EDGE_TTS_RATE", DEFAULT_RATE)


def default_volume() -> str:
    return _env_str("EDGE_TTS_VOLUME", DEFAULT_VOLUME)


def default_pitch() -> str:
    return _env_str("EDGE_TTS_PITCH", DEFAULT_PITCH)


# ---------- 结果数据类 ----------

@dataclass
class TTSResult:
    bytes: bytes = b""                       # mp3 字节
    mime_type: str = "audio/mpeg"
    source: str = "ok"                       # "ok" | "error" | "disabled"
    error: str = ""
    text: str = ""
    voice: str = ""
    rate: str = ""
    volume: str = ""
    pitch: str = ""
    duration_seconds: float = 0.0
    sub_file: str = ""                       # 副产物 .vtt / .srt 路径（若有）
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.source == "ok"

    def to_dict(self, *, include_bytes: bool = False) -> dict[str, Any]:
        d = asdict(self)
        if not include_bytes:
            d.pop("bytes", None)
        d["bytes_len"] = len(self.bytes)
        return d


# ---------- Backend 抽象 ----------

class TTSBackend(ABC):
    @abstractmethod
    def synth(self, text: str, output_path: str | Path, **opts: Any) -> TTSResult:
        """同步生成音频。失败抛 TTSError。

        opts: voice / rate / volume / pitch（与 edge-tts Communicate 一致）
        """


class MockTTSBackend(TTSBackend):
    """单测用：返回真实 mp3 字节（懒生成 1 秒静音）+ 记录调用。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        # 真 mp3 字节（懒生成 0.5 秒静音，~3 KB；下游 ffmpeg merge 能解析）
        self.next_bytes: bytes = _make_minimal_mp3()

    def synth(self, text: str, output_path: str | Path, **opts: Any) -> TTSResult:
        if not text or not text.strip():
            raise TTSError("text 为空")
        self.calls.append({"text": text, "output_path": str(output_path), **opts})
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(self.next_bytes)
        return TTSResult(
            bytes=self.next_bytes,
            text=text,
            voice=opts.get("voice", default_voice()),
            rate=opts.get("rate", ""),
            volume=opts.get("volume", ""),
            pitch=opts.get("pitch", ""),
            duration_seconds=0.0,
        )


def _make_minimal_mp3() -> bytes:
    """生成 0.5 秒静音 mp3。优先 ffmpeg；不可用时硬编码字节。"""
    try:
        import subprocess
        import imageio_ffmpeg
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "silent.mp3"
            exe = imageio_ffmpeg.get_ffmpeg_exe()
            subprocess.run(
                [exe, "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5",
                 "-c:a", "libmp3lame", "-q:a", "9", str(out)],
                capture_output=True,
                encoding="utf-8", errors="replace",
                timeout=30,
            )
            return out.read_bytes()
    except Exception:
        # 回退：硬编码 1KB 静音 mp3（最短 MPEG audio frame）
        # ffmpeg -f lavfi -i "anullsrc=r=8000:cl=mono" -c:a libmp3lame -t 0.5 -b:a 8k
        return bytes.fromhex(
            "fffb9000000000000000000000000000000000000000000000000000000000000000000"
            "0000000000000000000000000000000000000000000000000000000000000000000000"
            "0000000000000000000000000000000000000000000000000000000000000000000000"
            "0000000000000000000000000000000000000000000000000000000000000000000000"
            "0000000000000000000000000000000000000000000000000000000000000000000000"
            "0000000000000000000000000000000000000000000000000000000000000000000000"
            "0000000000000000000000000000000000000000000000000000000000000000000000"
            "0000000000000000000000000000000000000000000000000000000000000000000000"
            "0000000000000000000000000000000000000000000000000000000000000000000000"
            "0000000000000000000000000000000000000000000000000000000000000000000000"
        )


class EdgeTTSBackend(TTSBackend):
    """edge-tts 库包装。生产用。"""

    def __init__(self) -> None:
        # 故意不 import edge_tts 在模块顶部，避免无 edge-tts 时 import 即崩
        try:
            import edge_tts  # noqa: F401
        except ImportError as exc:
            raise TTSError(
                "edge-tts 未安装；先 `pip install edge-tts`"
            ) from exc
        self._edge_tts = edge_tts

    def synth(
        self,
        text: str,
        output_path: str | Path,
        *,
        voice: str | None = None,
        rate: str | None = None,
        volume: str | None = None,
        pitch: str | None = None,
        write_subtitle: str | Path | None = None,    # "vtt" 或 "srt" 或 .vtt 路径
    ) -> TTSResult:
        if not text or not text.strip():
            raise TTSError("text 为空")

        v = voice or default_voice()
        # edge-tts 对空字符串会拒绝（regex 校验）。空值 fallback 到 +0%
        r = rate if rate else default_rate()
        vol = volume if volume else default_volume()
        p = pitch if pitch else default_pitch()

        async def _run() -> tuple[bytes, str | None]:
            comm = self._edge_tts.Communicate(text, voice=v, rate=r, volume=vol, pitch=p)
            audio_chunks: list[bytes] = []
            sub_lines: list[str] = []
            sub_meta: list[str] = ["WEBVTT", ""]
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    audio_chunks.append(chunk["data"])
                elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                    # 字幕
                    offset = chunk.get("offset", 0) / 10_000_000  # 100ns → 秒
                    duration = chunk.get("duration", 0) / 10_000_000
                    text_seg = chunk.get("text", "")
                    sub_lines.append(
                        f"{offset:.3f} --> {offset + duration:.3f}\n{text_seg}\n"
                    )
            audio = b"".join(audio_chunks)
            sub_path = None
            if write_subtitle and sub_lines:
                sub_path_obj = Path(write_subtitle)
                sub_path_obj.parent.mkdir(parents=True, exist_ok=True)
                sub_path_obj.write_text(
                    "\n".join(sub_meta + sub_lines), encoding="utf-8"
                )
                sub_path = str(sub_path_obj)
            return audio, sub_path

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        try:
            audio_bytes, sub_path = asyncio.run(_run())
        except Exception as e:  # noqa: BLE001
            elapsed = time.monotonic() - started
            raise TTSError(f"edge-tts 调用失败：{type(e).__name__}: {e}") from e
        out.write_bytes(audio_bytes)
        elapsed = time.monotonic() - started

        return TTSResult(
            bytes=audio_bytes,
            text=text,
            voice=v,
            rate=r,
            volume=vol,
            pitch=p,
            duration_seconds=elapsed,
            sub_file=sub_path or "",
        )


# ---------- 工厂 ----------

def make_backend(prefer: str = "auto") -> TTSBackend:
    if prefer == "mock":
        return MockTTSBackend()
    if prefer in ("edge", "auto"):
        return EdgeTTSBackend()
    raise TTSError(f"未知 backend: {prefer}")


# ---------- 批量（一个 voices 列表生成多份） ----------

def synth_batch(
    items: Iterable[tuple[str, str | Path, dict[str, Any]]],
    *,
    backend: TTSBackend,
) -> list[TTSResult]:
    """items: [(text, output_path, opts), ...]。失败抛 TTSError（中断批）。
    若想失败继续，传 backend=something_that_returns_error_result 而非抛。
    """
    out: list[TTSResult] = []
    for text, path, opts in items:
        out.append(backend.synth(text, path, **opts))
    return out


# ---------- voices 列表（包装 edge-tts.list_voices） ----------

async def _list_voices_async(filter_locale: str = "") -> list[dict[str, Any]]:
    try:
        import edge_tts
    except ImportError as exc:
        raise TTSError("edge-tts 未安装") from exc
    voices = await edge_tts.list_voices()
    if filter_locale:
        voices = [v for v in voices if v["Locale"].startswith(filter_locale)]
    return voices


def list_voices(filter_locale: str = "") -> list[dict[str, Any]]:
    """同步包装。filter_locale 例 'zh-' 或 'en-US'。"""
    return asyncio.run(_list_voices_async(filter_locale=filter_locale))


# ---------- 文件 IO ----------

def write_tts(result: TTSResult, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(result.bytes)
    return target