"""V3.2 REQ-401 Publish Agent — YouTube 上传后端（ABC + Mock + Real stub）。

设计原则：
  - ABC: PublishBackend（generate_upload / upload_video / get_video_url）
  - Mock: 单测 / 离线演示用，不发任何网络请求
  - Real (YoutubePublishBackend): 需要 YOUTUBE_CLIENT_ID + YOUTUBE_CLIENT_SECRET +
    YOUTUBE_REFRESH_TOKEN。**任一缺失 → PublishAuthError 硬失败**（CLAUDE.md R4：
    Secrets/PII/Auth 域必须硬失败，禁止 placeholder / mock 静默 fallback）
  - 真 API 实装待用户后续提供凭据 + 选 google-api-python-client 库接入

接口契约（最小可测）：
    backend = make_publish_backend("mock")
    result = backend.upload_video(
        video_path=Path("final.mp4"),
        title="漫剧标题",
        description="...",
        tags=["漫剧", "AI"],
        privacy="unlisted",      # "public" | "unlisted" | "private"
    )
    # → PublishResult(video_id, url, status, uploaded_at)
"""
from __future__ import annotations

import abc
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, ClassVar


# ---------- Errors / Data ----------

class PublishError(RuntimeError):
    """publish 通用错。"""


class PublishAuthError(PublishError):
    """认证失败 / 凭据缺失。CLAUDE.md R4：硬失败，不静默降级。"""


@dataclass
class PublishResult:
    """单次上传结果。"""
    video_id: str = ""             # YouTube videoId（如 "dQw4w9WgXcQ"）
    url: str = ""                  # 公开 URL（mock 时拼 fake URL）
    status: str = ""               # "uploaded" | "processing" | "failed" | "pending"
    uploaded_at: str = ""          # ISO timestamp
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------- ABC ----------

class PublishBackend(abc.ABC):
    """发布后端抽象基类。"""

    name: ClassVar[str] = ""

    @abc.abstractmethod
    def upload_video(
        self,
        video_path: str | Path,
        *,
        title: str,
        description: str = "",
        tags: list[str] | None = None,
        privacy: str = "unlisted",
        timeout: int = 1800,
    ) -> PublishResult:
        """上传单条视频。失败抛 PublishError / PublishAuthError。"""

    @abc.abstractmethod
    def is_configured(self) -> bool:
        """是否配好（凭据齐全）。Mock 永远 True；Real 需 3 个 env 齐全。"""

    def validate_inputs(
        self,
        video_path: str | Path,
        *,
        title: str,
        privacy: str = "unlisted",
    ) -> None:
        """输入校验（公开方法；execute 前调用）。"""
        if not Path(video_path).is_file():
            raise PublishError(f"video 不存在：{video_path}")
        if not title or not title.strip():
            raise PublishError("title 不能为空")
        if privacy not in ("public", "unlisted", "private"):
            raise PublishError(f"privacy 非法：{privacy!r}（允许 public/unlisted/private）")
        if len(title) > 100:
            raise PublishError(f"title 过长（{len(title)} > 100）")


# ---------- Mock 后端（单测 / 离线演示） ----------

class MockPublishBackend(PublishBackend):
    """Mock 后端：不发任何网络请求，返回 fake video_id + 本地 URL。

    用于：
      - 单测
      - 端到端冒烟（无 YouTube 凭据时）
      - 演示 daily_cron 链路完整性

    不算"DONE"（CLAUDE.md R2：Mocks 不算真实端到端）。
    """

    name: ClassVar[str] = "mock"

    def __init__(self, *, base_url: str = "https://mock.youtube.local") -> None:
        self.base_url = base_url.rstrip("/")
        self.calls: list[dict[str, Any]] = []  # 单测可审计

    def upload_video(
        self,
        video_path: str | Path,
        *,
        title: str,
        description: str = "",
        tags: list[str] | None = None,
        privacy: str = "unlisted",
        timeout: int = 1800,
    ) -> PublishResult:
        self.validate_inputs(video_path, title=title, privacy=privacy)
        self.calls.append({
            "video_path": str(video_path),
            "title": title,
            "description": description,
            "tags": list(tags or []),
            "privacy": privacy,
        })
        video_id = f"mock_{uuid.uuid4().hex[:11]}"  # 11 字符 → 模仿 YouTube ID
        return PublishResult(
            video_id=video_id,
            url=f"{self.base_url}/watch?v={video_id}",
            status="uploaded",
            uploaded_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            metadata={"backend": "mock"},
        )

    def is_configured(self) -> bool:
        return True


# ---------- Real YouTube 后端（stub，待用户凭据） ----------

class YoutubePublishBackend(PublishBackend):
    """YouTube 真后端（stub）。

    凭据要求（3 个 env 全部必须）：
      - YOUTUBE_CLIENT_ID       (Google Cloud Console → OAuth 2.0 Client ID)
      - YOUTUBE_CLIENT_SECRET   (同上)
      - YOUTUBE_REFRESH_TOKEN   (首次 OAuth 授权后保存的 refresh_token)

    实装策略：建议 google-api-python-client（pip install google-api-python-client）。
    本类目前只占位（upload_video → raise NotImplementedError）；凭据齐全后接入
    googleapiclient.discovery.build("youtube", "v3", credentials=...)。

    CLAUDE.md R4：Auth 域必须硬失败，禁止 placeholder / mock 静默 fallback——
    本类不接 Mock fallback；用户没凭据就报 PublishAuthError 明确提示。
    """

    name: ClassVar[str] = "youtube"
    REQUIRED_ENV: ClassVar[tuple[str, ...]] = (
        "YOUTUBE_CLIENT_ID",
        "YOUTUBE_CLIENT_SECRET",
        "YOUTUBE_REFRESH_TOKEN",
    )

    def __init__(self) -> None:
        # 不调 _require_credentials()——构造时不一定需要凭据（is_configured 不抛）
        pass

    def is_configured(self) -> bool:
        return all(os.environ.get(k) for k in self.REQUIRED_ENV)

    def _require_credentials(self) -> tuple[str, str, str]:
        missing = [k for k in self.REQUIRED_ENV if not os.environ.get(k)]
        if missing:
            raise PublishAuthError(
                "YouTube OAuth 凭据缺失："
                f"{', '.join(missing)}\n"
                "申请路径：Google Cloud Console → API & Services → Credentials → "
                "Create OAuth 2.0 Client ID → 启用 YouTube Data API v3 → "
                "首次 OAuth 授权后保存 refresh_token。"
                "\n本错误不可静默吞掉（CLAUDE.md R4：Auth 域硬失败）。"
            )
        return (
            os.environ["YOUTUBE_CLIENT_ID"],
            os.environ["YOUTUBE_CLIENT_SECRET"],
            os.environ["YOUTUBE_REFRESH_TOKEN"],
        )

    def upload_video(
        self,
        video_path: str | Path,
        *,
        title: str,
        description: str = "",
        tags: list[str] | None = None,
        privacy: str = "unlisted",
        timeout: int = 1800,
    ) -> PublishResult:
        self.validate_inputs(video_path, title=title, privacy=privacy)
        client_id, client_secret, refresh_token = self._require_credentials()

        # TODO: google-api-python-client 实装待用户后续接入
        # 占位：未实现时硬失败（不静默 fallback 到 mock）
        raise NotImplementedError(
            "YoutubePublishBackend.upload_video 实装待 YOUTUBE_* 凭据接入；"
            "目前仅 Stub。\n"
            f"client_id 长度={len(client_id)}, "
            f"refresh_token 长度={len(refresh_token)}\n"
            "实装思路：\n"
            "  1. pip install google-api-python-client\n"
            "  2. 用 refresh_token + client_id/secret → Credentials 对象\n"
            "  3. youtube.videos().insert(part='snippet,status', body={...}, "
            "media_body=MediaFileUpload(video_path, mimetype='video/mp4', resumable=True))\n"
            "  4. execute() → response['id'] → 拼 URL"
        )


# ---------- Factory ----------

_KNOWN_BACKENDS: dict[str, type[PublishBackend]] = {
    "mock": MockPublishBackend,
    "youtube": YoutubePublishBackend,
}


def make_publish_backend(name: str, **kwargs: Any) -> PublishBackend:
    """按 name 选 backend。未知 name → PublishError。

    Args:
        name: "mock" | "youtube"
        kwargs: 传给 backend 构造函数
    """
    cls = _KNOWN_BACKENDS.get(name)
    if cls is None:
        raise PublishError(
            f"未知 publish backend：{name!r}；可选：{list(_KNOWN_BACKENDS)}"
        )
    return cls(**kwargs)


def list_backends() -> list[str]:
    """已注册的 backend 名。"""
    return list(_KNOWN_BACKENDS)
