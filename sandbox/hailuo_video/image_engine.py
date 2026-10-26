"""MiniMax 图像生成客户端（V2.2）。

V2.1 立了 LLM 辅助剧本；V2.2 加 MiniMax 图像生成（API Key 已具备，CN 域可用）。
D-013：用户核实端点 = `{MINIMAX_BASE_URL}/image_generation`（非 OpenAI DALL-E 风格）。
响应 schema：base64 模式下 `data.image_base64: list[str]`；url 模式 `data.image_url: list[str]`。
"""
from __future__ import annotations

import base64
import json
import os
import sys
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


# ---------- Google Gemini "Nano Banana" 图像生成 ----------

DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_GEMINI_IMAGE_MODEL = "gemini-2.5-flash-image"
# i2i 请求体可达数 MB（参考图 base64），经代理需长于 REQUEST_TIMEOUT
GEMINI_IMAGE_TIMEOUT = 300

# gemini-2.5-flash-image 支持集。与 MiniMax 的 VALID_ASPECT_RATIOS 不同：
# 多 4:5 / 5:4 / 21:9。两者不互相覆盖，各自校验。
GEMINI_VALID_ASPECT_RATIOS = (
    "1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9",
)

# generateContent 的非 OK 终止原因（R4：必须显式报错，绝不静默返回 0 张）
GEMINI_ERROR_FINISH_REASONS = ("IMAGE_SAFETY", "PROHIBITED_CONTENT", "RECITATION")
GEMINI_OK_FINISH_REASONS = ("STOP", "MAX_TOKENS")

# 下载参考图（http URL）的超时；本地文件读取不受此限
REFERENCE_FETCH_TIMEOUT = 30


# 复用 script_engine 的 .env 加载（共享 MINIMAX_* 变量）
_DOTENV_KEYS = (
    "MINIMAX_API_KEY",
    "MINIMAX_BASE_URL",
    "MINIMAX_IMAGE_MODEL",
    "MINIMAX_IMAGE_RESPONSE_FORMAT",
    "MINIMAX_IMAGE_ASPECT_RATIO",
    "MINIMAX_IMAGE_N",
    "GEMINI_API_KEY",
    "GEMINI_BASE_URL",
    "GEMINI_IMAGE_MODEL",
    "GEMINI_IMAGE_ASPECT_RATIO",
    "GEMINI_IMAGE_N",
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


def gemini_api_key() -> str:
    return _env_str("GEMINI_API_KEY", "")


def gemini_base_url() -> str:
    return _env_str("GEMINI_BASE_URL", DEFAULT_GEMINI_BASE_URL)


def gemini_image_model() -> str:
    return _env_str("GEMINI_IMAGE_MODEL", DEFAULT_GEMINI_IMAGE_MODEL)


def gemini_aspect_ratio() -> str:
    return _env_str("GEMINI_IMAGE_ASPECT_RATIO", DEFAULT_ASPECT_RATIO)


def gemini_image_n() -> int:
    return _env_int("GEMINI_IMAGE_N", DEFAULT_N)


def is_gemini_configured() -> bool:
    """Gemini API Key 必填 + 端点非空。

    R1：不照搬 MiniMax 的 `len(k) >= 16`（`:87`）——Google key 形如 `AIza...`，
    长度启发式属于发明规则。只校验非空。
    R4：Auth 域硬失败，不 fallback。
    """
    return bool(gemini_api_key()) and bool(gemini_base_url())


def gemini_configuration_warnings() -> list[str]:
    msgs: list[str] = []
    if not gemini_api_key():
        msgs.append("GEMINI_API_KEY 未设置")
    if gemini_aspect_ratio() not in GEMINI_VALID_ASPECT_RATIOS:
        msgs.append(
            f"GEMINI_IMAGE_ASPECT_RATIO={gemini_aspect_ratio()} 非 Gemini 支持集"
            f"（{GEMINI_VALID_ASPECT_RATIOS}）"
        )
    if gemini_image_n() < 1:
        msgs.append(f"GEMINI_IMAGE_N={gemini_image_n()} < 1")
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
    # 是否接受本地文件路径作为 subject_reference。
    # MiniMax image-01 的 subject_references 只吃 http(s) URL → False；
    # Gemini inlineData 吃裸字节，本地/URL 都行 → True。
    # R4：False 时遇到本地 ref 必须显式报错，不静默丢弃（见 MiniMaxImageBackend.generate）。
    supports_local_refs: bool = False

    # V3.8 W4：后端是否有**独立的** negative_prompt 字段。
    # [UNVERIFIED] MiniMax image-01 / Gemini generateContent 的官方文档均未列出该参数
    # → 默认 False（走内联），避免发送未确认字段导致 4xx（而请求已经计费）。
    # 内联不是降级凑合：项目已在用同一手法（asset_prep._safe_style_anchor 追加
    # "no illustration no cartoon no anime no CGI" 守卫词，V3.4 ADR-003）。
    supports_native_negative_prompt: bool = False

    @abstractmethod
    def generate(self, prompt: str, **opts: Any) -> list[ImageResult]:
        """返回 N 张 ImageResult（n 张）。失败统一抛 ImageError。"""

    def default_aspect_ratio(self) -> str:
        """本 backend 在调用方未显式指定时用的默认画幅。

        F4 修复（2026-10-22）：pipeline 此前无条件读 `ie.aspect_ratio()`，
        那是 **MiniMax** 的 env key —— 用户若设了 `MINIMAX_IMAGE_ASPECT_RATIO=21:9`
        再选 `--image-backend gemini`，会撞上 Gemini 的支持集校验而硬报错。
        各家支持集不同（Gemini 多 4:5/5:4/21:9），故让 backend 自己回答。
        """
        return aspect_ratio()


def _guess_mime_from_bytes(raw: bytes) -> str:
    """按 magic bytes 嗅探 jpeg / png / gif / webp。识别不出按 jpeg（与历史行为一致）。"""
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw.startswith(b"\x89PNG"):
        return "image/png"
    if raw.startswith(b"GIF"):
        return "image/gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


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
        # V3.8 W4：与真后端一致 —— 无独立 negative_prompt 字段时内联到 prompt 末尾
        # （内联结果体现在返回的 ImageResult.prompt 里，calls 形状保持不变）
        negative = str(opts.get("negative_prompt", "") or "").strip()
        effective_prompt = f"{prompt}, {negative}" if negative else prompt
        results: list[ImageResult] = []
        for _ in range(n):
            r = ImageResult(
                bytes=self.next_bytes,
                prompt=effective_prompt,
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
        negative_prompt: str = "",                           # V3.8 W4：负提示词（无独立字段时内联）
    ) -> list[ImageResult]:
        if not prompt or not prompt.strip():
            raise ImageError("prompt 为空")
        mdl = model_value or self._model
        ar = aspect_ratio_value or aspect_ratio()
        n = n_value if n_value is not None else image_n()
        fmt = response_format_value or self._response_format

        # V2.5：subject_references 角色一致性（**复数**，实测 2026-09）
        # 24h URL 有效；prompt 需明示"保持参考图中的角色外观"
        # 支持单 URL 或 URL 列表（multi-ref：角色 + 场景同时参考）
        ref_list: list[str] = []
        if subject_reference:
            ref_list = (
                [subject_reference] if isinstance(subject_reference, str) else list(subject_reference)
            )
            # R4：image-01 只接受 http(s) URL。收到本地路径必须显式报错，
            # 不能静默丢弃（那会让角色一致性无声消失）。
            # 存量缓存里都是 http URL，故对现有用户零影响。
            bad = [r for r in ref_list if not str(r).startswith(("http://", "https://"))]
            if bad:
                raise ImageError(
                    "MiniMax image-01 的 subject_references 只支持 http(s) URL，"
                    f"收到本地路径 {bad[:2]}；请改用 --image-backend gemini（支持本地参考图）。"
                )

        url = f"{self._base_url}/image_generation"
        # V3.8 W4：负提示词 —— 后端无独立字段时**内联**到 prompt 末尾
        # （与 ADR-006 守卫词同款写法，已被验证有效）。
        negative = str(negative_prompt or "").strip()
        effective_prompt = prompt
        if negative:
            if self.supports_native_negative_prompt:
                effective_prompt = prompt
            else:
                effective_prompt = f"{prompt}, {negative}"
        body = {
            "model": mdl,
            "prompt": effective_prompt,
            "aspect_ratio": ar,
            "response_format": fmt,
            "n": int(n),
        }
        if negative and self.supports_native_negative_prompt:
            body["negative_prompt"] = negative
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
        """简单嗅探 jpeg / png。委托给模块级 `_guess_mime_from_bytes`（两个 backend 共用）。"""
        try:
            head = base64.b64decode(b64[:32], validate=False)
        except Exception:
            return "image/jpeg"
        return _guess_mime_from_bytes(head)


# ---------- Reference 解析（http URL 或本地文件） ----------

def load_reference_bytes(ref: str) -> tuple[str, bytes]:
    """把一个 reference（http(s) URL 或本地文件路径）解析为 (mime_type, bytes)。

    Gemini i2i 的 `inlineData` 需要裸字节，不能传 URL；MiniMax 侧也用它做前置校验。
    asset_prep 缓存的参考图既可能是 URL（MiniMax 的 24h 过期链接），
    也可能是本地路径（Gemini 生成的资产），这里统一处理。
    失败统一抛 ImageError（R4：不静默降级）。
    """
    if not ref or not str(ref).strip():
        raise ImageError("reference 为空")
    ref = str(ref).strip()

    if ref.startswith(("http://", "https://")):
        ctype = ""
        try:
            with urllib.request.urlopen(ref, timeout=REFERENCE_FETCH_TIMEOUT) as resp:
                data = resp.read()
                ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
        except urllib.error.HTTPError as exc:
            raise ImageError(f"reference 下载 HTTP {exc.code}：{ref[:120]}") from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise ImageError(f"reference 下载失败 {ref[:120]}：{exc}") from exc
        if not data:
            raise ImageError(f"reference 下载为空：{ref[:120]}")
        return (ctype if ctype.startswith("image/") else _guess_mime_from_bytes(data)), data

    p = Path(ref)
    if not p.is_file():
        raise ImageError(f"reference 本地文件不存在：{ref}")
    try:
        data = p.read_bytes()
    except OSError as exc:
        raise ImageError(f"reference 本地读取失败 {ref}：{exc}") from exc
    if not data:
        raise ImageError(f"reference 本地文件为空：{ref}")
    return _guess_mime_from_bytes(data), data


class GeminiImageBackend(ImageBackend):
    """Google Gemini "Nano Banana"（gemini-2.5-flash-image）图像生成。

    端点：POST `{GEMINI_BASE_URL}/models/{model}:generateContent`
    认证：header `x-goog-api-key`
    t2i：`generationConfig.responseModalities=["IMAGE"]` + `imageConfig.aspectRatio`
    i2i：`contents.parts` = `[{"inlineData":{mimeType,data}}, {"text":...}]`（图在前、文在后）
    响应：`candidates[0].content.parts[].inlineData.{data,mimeType}`
          —— REST 是 **camelCase**，`inline_data`/`mime_type` 是 Python SDK 的写法，不要混用

    两条硬约束（与 MiniMax 不同，调用方需知）：
      - **无 n 参数**：一次调用只出一张，n>1 走 `_generate_one` 顺序循环。
      - **无 url 响应模式**：永远 inline base64，`ImageResult.image_url` 恒为空。

    R1 [UNVERIFIED]：以下字段名来自官方文档，首次真机烟测后确认。
    """

    supports_local_refs = True

    def default_aspect_ratio(self) -> str:
        """F4：读 GEMINI_IMAGE_ASPECT_RATIO，不共用 MiniMax 的 env key。"""
        return gemini_aspect_ratio()

    def __init__(
        self,
        *,
        api_key_value: str | None = None,
        base_url_value: str | None = None,
        model_value: str | None = None,
        timeout: int = GEMINI_IMAGE_TIMEOUT,
        # 单测注入
        http_post: Callable[[str, dict, dict], dict] | None = None,
    ) -> None:
        k = api_key_value if api_key_value is not None else gemini_api_key()
        if not k:
            raise ImageError("GEMINI_API_KEY 未设置；请填 .env 或显式传入 api_key_value")
        self._api_key = k
        self._base_url = (base_url_value or gemini_base_url()).rstrip("/")
        self._model = model_value or gemini_image_model()
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
        subject_reference: str | list[str] | None = None,
        negative_prompt: str = "",                           # V3.8 W4：负提示词（无独立字段时内联）
    ) -> list[ImageResult]:
        if not prompt or not prompt.strip():
            raise ImageError("prompt 为空")
        mdl = model_value or self._model
        ar = aspect_ratio_value or gemini_aspect_ratio()
        n = int(n_value if n_value is not None else gemini_image_n())
        if n < 1:
            raise ImageError(f"n={n} < 1")

        # R4：Gemini 没有 url 响应模式。asset_prep 4 处都传 response_format_value="url"，
        # 硬报错要改 4 处调用点 + CLI flag，diff 过大；显式告警后按 base64 继续即可
        # —— 告警的存在保证了这不是静默降级。
        if response_format_value and response_format_value != "base64":
            print(
                f"[WARN] Gemini 后端无 url 响应模式，response_format="
                f"{response_format_value} 被忽略，一律返回 inline base64",
                file=sys.stderr,
            )

        # 注意：校验的是 Gemini 自己的支持集，不是 MiniMax 的 VALID_ASPECT_RATIOS
        if ar not in GEMINI_VALID_ASPECT_RATIOS:
            raise ImageError(f"aspect_ratio={ar} 不在 Gemini 支持集（{GEMINI_VALID_ASPECT_RATIOS}）")

        # i2i 参考图：统一解析成裸字节（本地文件或 http URL 都行）
        ref_parts: list[dict] = []
        if subject_reference:
            refs = (
                [subject_reference] if isinstance(subject_reference, str) else list(subject_reference)
            )
            for ref in refs:
                mime, raw = load_reference_bytes(ref)  # 失败即抛 ImageError（R4）
                ref_parts.append({
                    "inlineData": {
                        "mimeType": mime,
                        "data": base64.b64encode(raw).decode("ascii"),
                    }
                })

        url = f"{self._base_url}/models/{mdl}:generateContent"
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self._api_key,
        }

        # V3.8 W4：负提示词 —— Gemini generateContent 也无独立 negative_prompt 字段
        # （[UNVERIFIED] 官方文档未列出）→ 内联到 prompt 末尾。
        negative = str(negative_prompt or "").strip()
        effective_prompt = prompt
        if negative:
            if self.supports_native_negative_prompt:
                effective_prompt = prompt
            else:
                effective_prompt = f"{prompt}, {negative}"

        # Gemini 无 n 参数：n>1 = 顺序多次调用，每次 1 张（并发只会放大限流与内存压力）
        results: list[ImageResult] = []
        for _ in range(n):
            results.append(self._generate_one(url, effective_prompt, mdl, ar, ref_parts, headers))
        return results

    # ---------- 内部 ----------

    def _generate_one(
        self,
        url: str,
        prompt: str,
        model: str,
        aspect_ratio: str,
        ref_parts: list[dict],
        headers: dict,
    ) -> ImageResult:
        # 图在前、指令文本在后。复制一份避免多次调用间互相污染。
        parts = list(ref_parts) + [{"text": prompt}]
        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                # 只放 aspectRatio：本模型会拒收 imageConfig.imageSize
                "imageConfig": {"aspectRatio": aspect_ratio},
            },
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
            raise ImageError(f"Gemini image HTTP {exc.code}：{detail}") from exc
        except urllib.error.URLError as exc:
            raise ImageError(f"Gemini image URLError：{exc.reason}") from exc
        except (OSError, TimeoutError) as exc:
            raise ImageError(f"Gemini image 网络错：{type(exc).__name__}: {exc}") from exc

        if not isinstance(data, dict):
            raise ImageError(f"Gemini image 返回非 dict：{data!r}")
        return self._parse_response(data, prompt, model, aspect_ratio, time.monotonic() - started)

    def _real_post(self, url: str, body: dict, headers: dict) -> dict:
        # urllib 的默认 opener 本就会自动读 HTTPS_PROXY/HTTP_PROXY；这里显式构造
        # ProxyHandler，是为了让运行时改 env 也生效（i2i 请求体数 MB，代理超时常见）。
        opener = urllib.request.build_opener(urllib.request.ProxyHandler())
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with opener.open(req, timeout=self._timeout) as resp:
            raw = resp.read().decode("utf-8")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ImageError(f"Gemini image 返回非 JSON：{raw[:200]!r}") from exc

    def _parse_response(
        self,
        data: dict,
        prompt: str,
        model: str,
        aspect_ratio: str,
        elapsed: float,
    ) -> ImageResult:
        """解析 generateContent 响应。**单张**（generate 每次调用 append 一个）。

        处理顺序：error 信封 → candidates/finishReason → parts（跳过 thought 草稿）
        → base64 解码 → mime（API 给的优先，否则嗅探，不硬编码 png/jpeg）。
        """
        err = data.get("error")
        if isinstance(err, dict):
            raise ImageError(
                f"Gemini image error {err.get('code', '?')} "
                f"{err.get('status', '')}：{err.get('message', '')}"
            )

        candidates = data.get("candidates") or []
        if not candidates:
            # prompt 阶段就被拦下时，blockReason 在 promptFeedback 而不在 candidates
            pf = data.get("promptFeedback") or {}
            block = pf.get("blockReason") if isinstance(pf, dict) else None
            if block:
                raise ImageError(f"Gemini image prompt 被拦截：blockReason={block}")
            raise ImageError(f"Gemini image 响应无 candidates：{str(data)[:200]}")

        cand = candidates[0] or {}
        finish = str(cand.get("finishReason") or "")
        if finish and finish not in GEMINI_OK_FINISH_REASONS:
            hint = (
                "（内容被安全策略/版权过滤拦截；R4：不静默返回 0 张）"
                if finish in GEMINI_ERROR_FINISH_REASONS else ""
            )
            raise ImageError(f"Gemini image finishReason={finish}{hint}")

        parts = ((cand.get("content") or {}).get("parts")) or []
        b64_list: list[str] = []
        mime_list: list[str] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            if part.get("thought"):
                continue  # 草稿/思考图，不是成图
            inline = part.get("inlineData")
            if not isinstance(inline, dict):
                continue
            b64 = inline.get("data")
            if isinstance(b64, str) and b64:
                b64_list.append(b64)
                mime_list.append(str(inline.get("mimeType") or ""))

        if not b64_list:
            raise ImageError(f"Gemini image 响应 0 张：{str(data)[:200]}")

        b64 = b64_list[0]
        try:
            raw = base64.b64decode(b64, validate=False)
        except Exception as exc:
            raise ImageError(f"Gemini image base64 解码失败：{exc}") from exc

        mime = mime_list[0] if mime_list[0].startswith("image/") else ""
        return ImageResult(
            bytes=raw,
            mime_type=mime or _guess_mime_from_bytes(raw),
            prompt=prompt,
            model=model,
            aspect_ratio=aspect_ratio,
            n=1,
            duration_seconds=elapsed,
            image_base64=[b64],
            image_url=[],  # Gemini 无 URL 模式
            request_id=str(data.get("responseId") or ""),
            extra={
                "finishReason": finish,
                "parts_total": len(parts),
                "parts_used": len(b64_list),
            },
        )


# ---------- 工厂 ----------

def make_backend(prefer: str = "auto") -> ImageBackend:
    if prefer == "mock":
        return MockImageBackend()
    if prefer == "gemini":
        return GeminiImageBackend()
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