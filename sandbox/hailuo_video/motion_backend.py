"""V3.1.1 Motion Backend — MiniMax Hailuo-2.3 真 AI 视频生成（中国站 Token Plan）。

2026-09-28 更新（DeepSeek 反馈）：
  - 用户在 platform.minimaxi.com 买 Token Plan（国内站）
  - 海外端点 api.minimax.io 已被实测 401 invalid_api_key
  - 切换到国内站 api.minimaxi.com + 加 query 端点做轮询

完整流程：
  submit → 返回 task_id → poll (循环 5s) → Success → download video_url → 保存到 output_path

端点（env-configurable，符合 D-010）：
  MINIMAX_VIDEO_ENDPOINT          POST   api.minimaxi.com/v1/video_generation
  MINIMAX_VIDEO_QUERY_ENDPOINT     GET    api.minimaxi.com/v1/query/video_generation

⚠️ UNVERIFIED response schemas（chatGPT/DeepSeek 提供，未实测 curl）：
  - submit response: {id, data: {task_id}, base_resp: {status_code, status_msg}}
  - query response: {task_id, status, video_url, base_resp: {status_code, status_msg}}
  - status 取值：Processing / Success / Failed（推测）
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class MotionError(RuntimeError):
    """motion backend 调用 / 解析失败。"""


# ---------- Config（env-driven，避免 D-010 硬编码） ----------

# V3.1.1 默认值改国内站（用户实测确认）
DEFAULT_VIDEO_ENDPOINT = "https://api.minimaxi.com/v1/video_generation"
DEFAULT_VIDEO_QUERY_ENDPOINT = "https://api.minimaxi.com/v1/query/video_generation"
DEFAULT_FILE_RETRIEVE_ENDPOINT = "https://api.minimaxi.com/v1/files/retrieve"  # V3.1.1 实测：Success 后取下载 URL
DEFAULT_VIDEO_MODEL = "MiniMax-Hailuo-2.3"
DEFAULT_VIDEO_POLL_TIMEOUT = 300     # V3.1.1 实测：6s 视频常需 1-3 分钟（用户 2026-09-29 截图证实）
DEFAULT_VIDEO_POLL_INTERVAL = 10       # V3.1.1 实测：5s 太频繁，10s 合理
DEFAULT_VIDEO_RESOLUTION = "1080P"
DEFAULT_VIDEO_DURATION = 6              # 秒


def video_endpoint() -> str:
    """submit 端点。"""
    return os.environ.get("MINIMAX_VIDEO_ENDPOINT", DEFAULT_VIDEO_ENDPOINT)


def video_query_endpoint() -> str:
    """poll 状态端点。"""
    return os.environ.get("MINIMAX_VIDEO_QUERY_ENDPOINT", DEFAULT_VIDEO_QUERY_ENDPOINT)


def file_retrieve_endpoint() -> str:
    """V3.1.1 实测：Success 后用 file_id 换 download_url 的端点。"""
    return os.environ.get("MINIMAX_FILE_RETRIEVE_ENDPOINT", DEFAULT_FILE_RETRIEVE_ENDPOINT)


def video_model_id() -> str:
    return os.environ.get("MINIMAX_VIDEO_MODEL", DEFAULT_VIDEO_MODEL)


def video_poll_timeout() -> int:
    return int(os.environ.get("MINIMAX_VIDEO_POLL_TIMEOUT", str(DEFAULT_VIDEO_POLL_TIMEOUT)))


def video_poll_interval() -> int:
    return int(os.environ.get("MINIMAX_VIDEO_POLL_INTERVAL", str(DEFAULT_VIDEO_POLL_INTERVAL)))


def video_resolution() -> str:
    return os.environ.get("MINIMAX_VIDEO_RESOLUTION", DEFAULT_VIDEO_RESOLUTION)


def video_duration() -> int:
    return int(os.environ.get("MINIMAX_VIDEO_DURATION", str(DEFAULT_VIDEO_DURATION)))


def _api_key() -> str:
    k = os.environ.get("MINIMAX_API_KEY", "")
    if not k or len(k) < 16:
        raise MotionError(
            "MINIMAX_API_KEY 未设置或长度 < 16。Hailuo-2.3 真 AI 视频生成需 MiniMax Max 套餐。\n"
            "若未升级到 Max，请保持 motion_complexity='static'（Ken Burns 即可）"
        )
    return k


# ---------- Result dataclass ----------

@dataclass
class MotionResult:
    output_path: str = ""                # local file path（已下载）
    task_id: str = ""                    # MiniMax 返回的 task_id
    status: str = "ok"                   # "ok" | "submitted" | "polling" | "error" | "fallback"
    duration_seconds: float = 0.0        # API call + 下载耗时
    model: str = ""
    prompt: str = ""
    fps: int = 0
    width: int = 0
    height: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


# ---------- ABC ----------

class MotionBackend(ABC):
    @abstractmethod
    def generate(self, prompt: str, *, duration: int = DEFAULT_VIDEO_DURATION,
                 resolution: str = DEFAULT_VIDEO_RESOLUTION,
                 output_path: str = "", **kwargs: Any) -> MotionResult:
        """完整流程：submit → poll → download → 返回 MotionResult。失败抛 MotionError。"""


# ---------- Mock (单测 / 无 quota) ----------

class MockMotionBackend(MotionBackend):
    """单测 / 无 quota 时用。不调 API；用 ffmpeg lavfi 写一个 1 色填充 mp4 占位。"""

    def __init__(self, return_error: str = "") -> None:
        self.return_error = return_error
        self.calls: list[dict] = []

    def generate(self, prompt, *, duration=DEFAULT_VIDEO_DURATION, resolution=DEFAULT_VIDEO_RESOLUTION,
                 output_path="", **kwargs):
        self.calls.append({"prompt": prompt, "duration": duration, "resolution": resolution, "output_path": output_path, **kwargs})
        if self.return_error:
            raise MotionError(self.return_error)
        try:
            import imageio_ffmpeg
            exe = imageio_ffmpeg.get_ffmpeg_exe()
            subprocess.run(
                [exe, "-y", "-f", "lavfi", "-i", "color=c=black:s=1280x720:d=" + str(duration),
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "24", output_path],
                capture_output=True, check=True, timeout=30,
            )
        except Exception as exc:
            raise MotionError(f"mock backend 写 fake mp4 失败：{exc}") from exc
        return MotionResult(
            output_path=output_path, task_id="mock_task", status="ok",
            duration_seconds=0.0, model="mock", prompt=prompt,
            width=1280, height=720, fps=24,
        )


# ---------- MiniMax Hailuo-2.3（国内站 Token Plan） ----------

# 已知 status 取值（V3.1.1 用户实测 2026-09-29 截图证实：Preparing / Queueing 是 transition 状态）
STATUS_PROCESSING = ("Preparing", "Queueing", "Queued", "Pending", "Running", "Processing")
STATUS_SUCCESS = ("Success", "Succeed", "Completed", "Done")
STATUS_FAILED = ("Failed", "Fail", "Error")


class MiniMaxMotionBackend(MotionBackend):
    """MiniMax Hailuo-2.3 中国站完整流程：submit → poll → download。"""

    def __init__(self, timeout: int = DEFAULT_VIDEO_POLL_TIMEOUT,
                 poll_interval: int = DEFAULT_VIDEO_POLL_INTERVAL) -> None:
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._api_key = _api_key()  # 构造时校验

    def generate(self, prompt, *, duration=DEFAULT_VIDEO_DURATION,
                 resolution=DEFAULT_VIDEO_RESOLUTION,
                 output_path="", **kwargs):
        # V3.1.2：I2V 支持 — task dict 里有 first_frame_image / last_frame_image 时启用
        first_frame = kwargs.get("first_frame_image") or ""
        last_frame = kwargs.get("last_frame_image") or ""
        if first_frame or last_frame:
            return self._generate_i2v(
                prompt=prompt, duration=duration, resolution=resolution,
                output_path=output_path,
                first_frame_image=first_frame,
                last_frame_image=last_frame,
                seed=kwargs.get("seed"),
                motion_lock=kwargs.get("motion_lock"),
            )
        return self._generate_t2v(
            prompt=prompt, duration=duration, resolution=resolution,
            output_path=output_path,
            seed=kwargs.get("seed"),
            motion_lock=kwargs.get("motion_lock"),
        )

    def _generate_t2v(self, *, prompt, duration, resolution, output_path, seed=None, motion_lock=None):
        """V3.1.1 实现的纯 T2V 路径。"""
        if not output_path:
            raise MotionError("MiniMaxMotionBackend.generate requires 'output_path'")
        task_id = self._submit(prompt, duration, resolution, seed=seed)
        if not task_id:
            raise MotionError("MiniMax video 未返回 task_id（schema UNVERIFIED）")
        file_id = self._poll_until_ready(task_id)
        if not file_id:
            raise MotionError("MiniMax video Success 但缺 file_id")
        download_url = self._fetch_download_url(file_id)
        self._download(download_url, output_path)
        return MotionResult(
            output_path=output_path, task_id=task_id, status="ok",
            duration_seconds=0.0, model=video_model_id(), prompt=prompt,
            extra={"file_id": file_id, "download_url": download_url, "mode": "t2v",
                   "seed": seed, "motion_lock": motion_lock or {}},
        )

    def _generate_i2v(self, *, prompt, duration, resolution, output_path,
                       first_frame_image="", last_frame_image="",
                       seed=None, motion_lock=None):
        """V3.1.2 I2V 路径 — first_frame_image + last_frame_image 锚点。

        走与 T2V 相同的 submit → poll → retrieve → download 流程，
        body 加 first_frame_image / last_frame_image / seed 等字段。
        """
        if not output_path:
            raise MotionError("MiniMaxMotionBackend._generate_i2v requires 'output_path'")
        if not first_frame_image and not last_frame_image:
            raise MotionError("I2V 必须至少指定 first_frame_image 或 last_frame_image 之一")

        task_id = self._submit(prompt, duration, resolution,
                               first_frame_image=first_frame_image,
                               last_frame_image=last_frame_image,
                               seed=seed)
        if not task_id:
            raise MotionError("MiniMax I2V 未返回 task_id")
        file_id = self._poll_until_ready(task_id)
        if not file_id:
            raise MotionError("MiniMax I2V Success 但缺 file_id")
        download_url = self._fetch_download_url(file_id)
        self._download(download_url, output_path)
        return MotionResult(
            output_path=output_path, task_id=task_id, status="ok",
            duration_seconds=0.0, model=video_model_id(), prompt=prompt,
            extra={
                "file_id": file_id, "download_url": download_url, "mode": "i2v",
                "first_frame_image": first_frame_image,
                "last_frame_image": last_frame_image,
                "seed": seed, "motion_lock": motion_lock or {},
            },
        )

    def _submit(self, prompt, duration, resolution, *, first_frame_image="",
                last_frame_image="", seed=None):
        body = {"model": video_model_id(), "prompt": prompt, "duration": duration, "resolution": resolution}
        # V3.1.2 I2V：first_frame_image / last_frame_image 用 url（OSS / 公网 url）
        if first_frame_image:
            body["first_frame_image"] = first_frame_image
        if last_frame_image:
            body["last_frame_image"] = last_frame_image
        if seed is not None:
            body["seed"] = seed
        url = video_endpoint()
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self._api_key}"}
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                status = getattr(resp, "status", None) or getattr(resp, "code", None)
                if status is None:
                    try:
                        status = resp.getcode()
                    except Exception:
                        status = 200  # 单元测试 FakeResp 没 status 时默认成功
                # logger 没引入到此文件，避免依赖；打印到 stderr 即可
                import sys
                print(f"  status  = {status}", file=sys.stderr)
                print(f"  raw     = {raw}", file=sys.stderr)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                pass
            if exc.code in (401, 403):
                raise MotionError(
                    f"MiniMax video HTTP {exc.code}：{detail}\n"
                    f"提示：Token Plan Key 可能不兼容该 endpoint。"
                    f"请确认 (1) 用的是 platform.minimaxi.com 买的国内站 Key，(2) endpoint = {url}。"
                ) from exc
            raise MotionError(f"MiniMax video HTTP {exc.code}：{detail}") from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise MotionError(f"MiniMax video 网络错：{type(exc).__name__}: {exc}") from exc

        payload = self._safe_json(raw)
        task_id = (
            payload.get("task_id")
            or payload.get("id")
            or payload.get("data", {}).get("task_id")
            or payload.get("data", {}).get("id")
            or ""
        )
        if not task_id:
            raise MotionError(f"MiniMax video 未返回 task_id（response: {raw[:200]}）")
        return task_id

    # ----- submit (V3.1.2：旧 _submit 已统一到上面带 I2V 参数的版本) -----

    # ----- poll -----
    def _poll_until_ready(self, task_id: str) -> str:
        """循环 GET query endpoint 状态。

        V3.1.1 用户实测 2026-09-29 截图证实：
        - 状态流转：Preparing → Queueing → ... → Success
        - Success 响应**不包含** video_url（这是之前 V3.1.1 的 bug！）
        - Success 响应**包含** file_id（需另调 /v1/files/retrieve 获取 download_url）

        Returns:
            file_id (str)：Success 时返回；否则 raise MotionError
        """
        deadline = time.monotonic() + self.timeout
        url = f"{video_query_endpoint()}?task_id={task_id}"
        headers = {"Authorization": f"Bearer {self._api_key}"}

        while time.monotonic() < deadline:
            try:
                req = urllib.request.Request(url, headers=headers, method="GET")
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = resp.read().decode("utf-8")
                payload = self._safe_json(raw)
                status = (
                    payload.get("status")
                    or payload.get("data", {}).get("status")
                    or ""
                )
                file_id = (
                    payload.get("file_id")
                    or payload.get("data", {}).get("file_id")
                    or ""
                )
                if status in STATUS_SUCCESS:
                    if not file_id:
                        raise MotionError(f"MiniMax video Success 但缺 file_id（response: {raw[:200]}）")
                    return file_id
                if status in STATUS_FAILED:
                    raise MotionError(f"MiniMax video Failed（response: {raw[:200]}）")
                # STATUS_PROCESSING → 继续等
            except MotionError:
                raise
            except Exception:
                pass  # 轮询中瞬态错忽略
            time.sleep(self.poll_interval)

        raise MotionError(f"MiniMax video 轮询超时（{self.timeout}s 内未 Success）")

    # ----- fetch download_url -----
    def _fetch_download_url(self, file_id: str) -> str:
        """V3.1.1 实测：Success 后 Success 不直接返 video_url。

        需调 `GET /v1/files/retrieve?file_id=xxx` 取 `download_url`。
        V3.1.1 用户实测 2026-09-29 截图证实这个二步流程。
        """
        url = f"{file_retrieve_endpoint()}?file_id={file_id}"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
        except Exception as exc:
            raise MotionError(f"MiniMax file_retrieve 网络错：{type(exc).__name__}: {exc}") from exc
        payload = self._safe_json(raw)
        download_url = (
            payload.get("download_url")
            or payload.get("file", {}).get("download_url")
            or payload.get("data", {}).get("download_url")
            or ""
        )
        if not download_url:
            raise MotionError(f"MiniMax file_retrieve 缺 download_url（response: {raw[:200]}）")
        return download_url

    # ----- download -----
    def _download(self, url: str, output_path: str) -> None:
        """下载 video_url 到 output_path。"""
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
        except Exception as exc:
            raise MotionError(f"MiniMax video 下载失败：{exc}") from exc
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(data)

    # ----- helpers -----
    @staticmethod
    def _safe_json(raw: str) -> dict:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}


# ---------- 工厂 ----------

def make_motion_backend(prefer: str = "auto") -> MotionBackend:
    if prefer == "mock":
        return MockMotionBackend()
    if prefer == "minimax":
        return MiniMaxMotionBackend()
    if prefer in ("auto", ""):
        try:
            return MiniMaxMotionBackend()
        except MotionError:
            return MockMotionBackend()  # graceful fallback
    raise MotionError(f"未知 motion backend: {prefer}")


# 顶层 import（避免循环依赖）
from pathlib import Path
