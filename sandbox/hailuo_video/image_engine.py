"""MiniMax 图像生成客户端（V2.2）。

V2.1 立了 LLM 辅助剧本；V2.2 加 MiniMax 图像生成（API Key 已具备，CN 域可用）。
D-013：用户核实端点 = `{MINIMAX_BASE_URL}/image_generation`（非 OpenAI DALL-E 风格）。
响应 schema：base64 模式下 `data.image_base64: list[str]`；url 模式 `data.image_url: list[str]`。
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from hailuo_video.local_client import PACKAGE_ROOT
from hailuo_video.script_engine import DEFAULT_BASE_URL, _load_dotenv, _env_str


# ---------- 配置 ----------

class ImageError(RuntimeError):
    """图像 API 调用 / 解析失败。"""


DEFAULT_IMAGE_MODEL = "image-01"
DEFAULT_RESPONSE_FORMAT = "base64"        # 推荐：无过期；"url" 24h 过期
DEFAULT_ASPECT_RATIO = "16:9"
DEFAULT_N = 1
REQUEST_TIMEOUT = 120

VALID_RESPONSE_FORMATS = ("base64", "url")
VALID_ASPECT_RATIOS = ("1:1", "16:9", "4:3", "3:4", "9:16", "2:3", "3:2")


# 复用 script_engine 的 .env 加载（共享 MINIMAX_* 变量）
_DOTENV_KEYS = (
    "MINIMAX_API_KEY",
    "MINIMAX_BASE_URL",
    "MINIMAX_IMAGE_MODEL",
    "MINIMAX_IMAGE_RESPONSE_FORMAT",
    "MINIMAX_IMAGE_ASPECT_RATIO",
    "MINIMAX_IMAGE_N",
)


def _env_int(key: str, default: int) -> int:
    v = os.environ.get(key)
    if not v:
        return default
    try:
        return int(v)
    except ValueError:
        return default


def api_key() -> str:
    return _env_str("MINIMAX_API_KEY", "")


def base_url() -> str:
    return _env_str("MINIMAX_BASE_URL", DEFAULT_BASE_URL)


def image_model() -> str:
    return _env_str("MINIMAX_IMAGE_MODEL", DEFAULT_IMAGE_MODEL)


def response_format() -> str:
    return _env_str("MINIMAX_IMAGE_RESPONSE_FORMAT", DEFAULT_RESPONSE_FORMAT)


def aspect_ratio() -> str:
    return _env_str("MINIMAX_IMAGE_ASPECT_RATIO", DEFAULT_ASPECT_RATIO)


def image_n() -> int:
    return _env_int("MINIMAX_IMAGE_N", DEFAULT_N)


def is_image_configured() -> bool:
    """MiniMax API Key 必填 + ≥16 字符 + 端点非空。"""
    k = api_key()
    return bool(k) and len(k) >= 16 and bool(base_url())


def configuration_warnings() -> list[str]:
    msgs: list[str] = []
    k = api_key()
    if not k:
        msgs.append("MINIMAX_API_KEY 未设置")
    elif len(k) < 16:
        msgs.append(f"MINIMAX_API_KEY 长度 {len(k)} < 16（可能不完整）")
    if base_url() != DEFAULT_BASE_URL:
        msgs.append(
            f"MINIMAX_BASE_URL={base_url()} ≠ 默认 {DEFAULT_BASE_URL}（端点非 CN 域；如海外可，国际域 api.minimax.io 也可）"
        )
    if response_format() not in VALID_RESPONSE_FORMATS:
        msgs.append(
            f"MINIMAX_IMAGE_RESPONSE_FORMAT={response_format()} 非法（{VALID_RESPONSE_FORMATS}）"
        )
    if aspect_ratio() not in VALID_ASPECT_RATIOS:
        msgs.append(
            f"MINIMAX_IMAGE_ASPECT_RATIO={aspect_ratio()} 非预定义（{VALID_ASPECT_RATIOS}）"
        )
    return msgs


# ---------- 结果数据类 ----------

@dataclass
class ImageResult:
    bytes: bytes = b""                       # 解码后的图片字节
    mime_type: str = "image/jpeg"            # 默认 jpeg（D-013 文档示例存 .jpeg）
    source: str = "ok"                       # "ok" | "error" | "disabled"
    error: str = ""
    prompt: str = ""
    model: str = ""
    aspect_ratio: str = ""
    n: int = 0
    duration_seconds: float = 0.0
    image_base64: list[str] = field(default_factory=list)  # 原始 base64
    image_url: list[str] = field(default_factory=list)    # 原始 URL
    request_id: str = ""
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

class ImageBackend(ABC):
    @abstractmethod
    def generate(self, prompt: str, **opts: Any) -> list[ImageResult]:
        """返回 N 张 ImageResult（n 张）。失败统一抛 ImageError。"""


def _make_minimal_jpeg() -> bytes:
    """懒生成 1×1 白色 JPEG。优先 PIL；不可用时回退到硬编码 JPEG 字节。"""
    try:
        from PIL import Image
        import io
        img = Image.new("RGB", (16, 16), "white")
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=70)
        return buf.getvalue()
    except ImportError:
        # 1×1 白色 JPEG (PIL Image.new('RGB', (1,1), 'white').save(...))
        # 用 ffmpeg 能解析的真 JPEG；尺寸小
        return bytes.fromhex(
            "ffd8ffe000104a46494600010100000100010000ffdb0043000806060706050807"
            "07070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e272022"
            "2c231c1c2837292c30313434341f27393d38323c2e333432ffc2000b0800010001"
            "01011100ffc4001f000001050101010101010000000000000000000102020306080"
            "a010000000000ffda00080101000000013fffe2ec0e28a2414b32212c5a2c912"
            "3456789abcdef00ffd9"
        )


class MockImageBackend(ImageBackend):
    """单测用：返回 1×1 白色 JPEG + 记录调用。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.response_format: str = "base64"
        # 真 JPEG（懒生成，PIL 不可用时回退到 raw bytes）
        self.next_bytes: bytes = _make_minimal_jpeg()

    def generate(self, prompt: str, **opts: Any) -> list[ImageResult]:
        self.calls.append({"prompt": prompt, **opts})
        # 兼容 n_value / n 两个 kwarg 名
        n = int(opts.get("n_value", opts.get("n", 1)))
        fmt = self.response_format
        results: list[ImageResult] = []
        for _ in range(n):
            r = ImageResult(
                bytes=self.next_bytes,
                prompt=prompt,
                model=opts.get("model", "image-01"),
                aspect_ratio=opts.get("aspect_ratio", "16:9"),
                n=n,
                duration_seconds=0.0,
            )
            if fmt == "url":
                r.image_url = [f"https://example.com/fake_{id(r)}.jpg"]
            else:
                b64 = base64.b64encode(self.next_bytes).decode("ascii")
                r.image_base64 = [b64]
            results.append(r)
        return results


class MiniMaxImageBackend(ImageBackend):
    """MiniMax 图像生成客户端（V2.2）。

    端点 = `{MINIMAX_BASE_URL}/image_generation`（D-013 用户核实）。
    R1 [UNVERIFIED]：响应 schema `data.image_base64` (base64 模式) / `data.image_url` (url 模式)。
    首次使用前必须烟测一次；schema 变动时调整本类的 `_parse_response`。
    """

    def __init__(
        self,
        *,
        api_key_value: str | None = None,
        base_url_value: str | None = None,
        model_value: str | None = None,
        response_format_value: str | None = None,
        timeout: int = REQUEST_TIMEOUT,
        # 单测注入
        http_post: Callable[[str, dict, dict], dict] | None = None,
    ) -> None:
        k = api_key_value if api_key_value is not None else api_key()
        if not k:
            raise ImageError("MINIMAX_API_KEY 未设置；请填 .env 或显式传入 api_key_value")
        if len(k) < 16:
            raise ImageError(f"MINIMAX_API_KEY 长度 {len(k)} < 16（可能不完整）")
        self._api_key = k
        self._base_url = (base_url_value or base_url()).rstrip("/")
        self._model = model_value or image_model()
        fmt = response_format_value or response_format()
        if fmt not in VALID_RESPONSE_FORMATS:
            raise ImageError(f"response_format={fmt} 非法（{VALID_RESPONSE_FORMATS}）")
        self._response_format = fmt
        self._timeout = timeout
        self._http_post = http_post

    # ---------- 公开 API ----------

    def generate(
        self,
        prompt: str,
        *,
        model_value: str | None = None,
        aspect_ratio_value: str | None = None,
        n_value: int | None = None,
        response_format_value: str | None = None,
        subject_reference: str | list[str] | None = None,    # V2.5：单 URL 或 URL 列表（multi-ref i2i）
    ) -> list[ImageResult]:
        if not prompt or not prompt.strip():
            raise ImageError("prompt 为空")
        mdl = model_value or self._model
        ar = aspect_ratio_value or aspect_ratio()
        n = n_value if n_value is not None else image_n()
        fmt = response_format_value or self._response_format

        url = f"{self._base_url}/image_generation"
        body = {
            "model": mdl,
            "prompt": prompt,
            "aspect_ratio": ar,
            "response_format": fmt,
            "n": int(n),
        }
        # V2.5：subject_references 角色一致性（**复数**，实测 2026-09）
        # 24h URL 有效；prompt 需明示"保持参考图中的角色外观"
        # 支持单 URL 或 URL 列表（multi-ref：角色 + 场景同时参考）
        if subject_reference:
            ref_list = [subject_reference] if isinstance(subject_reference, str) else list(subject_reference)
            if ref_list:
                body["subject_references"] = ref_list
                body["prompt_optimizer"] = False  # 关闭自动改写，避免破坏 reference 锚定
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }

        started = time.monotonic()
        try:
            if self._http_post is not None:
                data = self._http_post(url, body, headers)
            else:
                data = self._real_post(url, body, headers)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                if hasattr(exc, "read") and exc.read is not None:
                    detail = exc.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            raise ImageError(f"MiniMax image HTTP {exc.code}：{detail}") from exc
        except urllib.error.URLError as exc:
            raise ImageError(f"MiniMax image URLError：{exc.reason}") from exc
        except (OSError, TimeoutError) as exc:
            raise ImageError(f"MiniMax image 网络错：{type(exc).__name__}: {exc}") from exc

        if not isinstance(data, dict):
            raise ImageError(f"MiniMax image 返回非 dict：{data!r}")
        elapsed = time.monotonic() - started
        return self._parse_response(data, prompt, mdl, ar, n, fmt, elapsed)

    # ---------- 内部 ----------

    def _real_post(self, url: str, body: dict, headers: dict) -> dict:
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            raw = resp.read().decode("utf-8")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ImageError(f"MiniMax image 返回非 JSON：{raw[:200]!r}") from exc

    def _parse_response(
        self,
        data: dict,
        prompt: str,
        model: str,
        aspect_ratio: str,
        n: int,
        fmt: str,
        elapsed: float,
    ) -> list[ImageResult]:
        """D-013 schema：`data.image_base64: list[str]` 或 `data.image_url: list[str]`。

        R1 [UNVERIFIED]：实际 schema 可能嵌套更深（如 `data.data.image_base64`），
        或包含 `request_id` / `usage`。本方法按用户提供的 Python 示例路径解析，
        失败时打印 raw 让用户调整。
        """
        # 兼容可能的 `data.data` 嵌套（递归剥开所有 data 包裹）
        payload = data
        while isinstance(payload, dict) and isinstance(payload.get("data"), dict):
            payload = payload["data"]

        b64_list: list[str] = []
        url_list: list[str] = []
        if fmt == "base64":
            # R1 [V2.5 实测修正]：schema 是 `image_base64: list[str]`
            raw_b64 = payload.get("image_base64")
            if isinstance(raw_b64, list):
                b64_list = [str(s) for s in raw_b64 if isinstance(s, (str, bytes))]
            elif isinstance(raw_b64, str):
                b64_list = [raw_b64]
            elif isinstance(raw_b64, (bytes, bytearray)):
                b64_list = [raw_b64.decode("ascii")]
            else:
                raise ImageError(f"MiniMax image base64 响应结构不符：{data!r}")
        else:  # fmt == "url"
            # R1 [V2.5 实测修正]：url 模式 schema 是 `image_urls: list[str]`（复数）
            raw_url = payload.get("image_urls") or payload.get("image_url")
            if isinstance(raw_url, list):
                url_list = [str(s) for s in raw_url if isinstance(s, str)]
            elif isinstance(raw_url, str):
                url_list = [raw_url]
            else:
                raise ImageError(f"MiniMax image url 响应结构不符：{data!r}")

        results: list[ImageResult] = []
        for idx in range(len(b64_list) or len(url_list)):
            r = ImageResult(
                prompt=prompt,
                model=model,
                aspect_ratio=aspect_ratio,
                n=n,
                duration_seconds=elapsed,
                mime_type="image/jpeg" if fmt == "url" else self._guess_mime_from_b64(b64_list[idx] if idx < len(b64_list) else ""),
                image_base64=[b64_list[idx]] if idx < len(b64_list) else [],
                image_url=[url_list[idx]] if idx < len(url_list) else [],
                request_id=str(data.get("id") or data.get("request_id") or ""),
            )
            # 解析字节
            if b64_list:
                try:
                    r.bytes = base64.b64decode(b64_list[idx], validate=False)
                except Exception as exc:
                    raise ImageError(f"base64 解码失败（idx={idx}）：{exc}") from exc
            results.append(r)

        if not results:
            raise ImageError(f"MiniMax image 响应 0 张：{data!r}")
        return results

    @staticmethod
    def _guess_mime_from_b64(b64: str) -> str:
        """简单嗅探 jpeg / png。"""
        try:
            head = base64.b64decode(b64[:32], validate=False)
        except Exception:
            return "image/jpeg"
        if head.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if head.startswith(b"\x89PNG"):
            return "image/png"
        if head.startswith(b"GIF"):
            return "image/gif"
        if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            return "image/webp"
        return "image/jpeg"


# ---------- 工厂 ----------

def make_backend(prefer: str = "auto") -> ImageBackend:
    if prefer == "mock":
        return MockImageBackend()
    if prefer in ("minimax", "auto"):
        return MiniMaxImageBackend()
    raise ImageError(f"未知 backend: {prefer}")


# ---------- 文件 IO ----------

def write_image(result: ImageResult, path: str | Path, *, fmt: str | None = None) -> Path:
    """落盘。fmt=None 时按 result.mime_type 推断扩展名。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(result.bytes)
    return target


def image_extension(mime: str) -> str:
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
    }.get(mime, ".img")