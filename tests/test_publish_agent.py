"""V3.2 REQ-401 publish_agent 单测。

覆盖：
  - Mock 后端：upload / is_configured / 审计 calls
  - input 校验：video 不存在 / title 空 / privacy 非法
  - Youtube stub：凭据缺失硬失败（CLAUDE.md R4 Auth 域）；凭据齐全仍 raise
    NotImplementedError（明确实装未到位，不静默 fallback 到 mock）
  - Factory：make_publish_backend / list_backends
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from hailuo_video.publish_agent import (  # noqa: E402
    MockPublishBackend,
    PublishAuthError,
    PublishError,
    PublishResult,
    YoutubePublishBackend,
    list_backends,
    make_publish_backend,
)


# ---------- Mock 后端 ----------

def test_mock_upload_success(tmp_path):
    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    b = MockPublishBackend()
    r = b.upload_video(v, title="T", description="D", tags=["a", "b"], privacy="unlisted")
    assert isinstance(r, PublishResult)
    assert r.status == "uploaded"
    assert r.video_id.startswith("mock_")
    assert r.url.endswith(r.video_id)
    assert r.uploaded_at  # ISO 戳非空


def test_mock_audit_calls(tmp_path):
    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    b = MockPublishBackend()
    b.upload_video(v, title="T1", tags=["x"], privacy="private")
    assert len(b.calls) == 1
    assert b.calls[0]["title"] == "T1"
    assert b.calls[0]["privacy"] == "private"


def test_mock_is_configured_always_true():
    assert MockPublishBackend().is_configured() is True


def test_mock_missing_video_raises(tmp_path):
    b = MockPublishBackend()
    with pytest.raises(PublishError, match="video 不存在"):
        b.upload_video(tmp_path / "nope.mp4", title="T")


def test_mock_empty_title_raises(tmp_path):
    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    b = MockPublishBackend()
    with pytest.raises(PublishError, match="title 不能为空"):
        b.upload_video(v, title="")


def test_mock_invalid_privacy_raises(tmp_path):
    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    b = MockPublishBackend()
    with pytest.raises(PublishError, match="privacy 非法"):
        b.upload_video(v, title="T", privacy="bogus")


def test_mock_title_too_long_raises(tmp_path):
    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    b = MockPublishBackend()
    long_title = "x" * 101
    with pytest.raises(PublishError, match="title 过长"):
        b.upload_video(v, title=long_title)


def test_mock_default_tags_none_is_ok(tmp_path):
    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    b = MockPublishBackend()
    r = b.upload_video(v, title="T")  # tags=None
    assert r.status == "uploaded"


def test_mock_base_url_configurable(tmp_path):
    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    b = MockPublishBackend(base_url="https://custom.host")
    r = b.upload_video(v, title="T")
    assert r.url.startswith("https://custom.host/")


def test_mock_video_id_unique_per_call(tmp_path):
    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    b = MockPublishBackend()
    r1 = b.upload_video(v, title="T1")
    r2 = b.upload_video(v, title="T2")
    assert r1.video_id != r2.video_id


# ---------- Youtube stub（Auth 域硬失败） ----------

def test_youtube_is_configured_false_when_env_missing(monkeypatch):
    monkeypatch.delenv("YOUTUBE_CLIENT_ID", raising=False)
    monkeypatch.delenv("YOUTUBE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("YOUTUBE_REFRESH_TOKEN", raising=False)
    assert YoutubePublishBackend().is_configured() is False


def test_youtube_is_configured_true_when_env_complete(monkeypatch):
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "fake-id")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "fake-secret")
    monkeypatch.setenv("YOUTUBE_REFRESH_TOKEN", "fake-token")
    assert YoutubePublishBackend().is_configured() is True


def test_youtube_partial_env_still_returns_false(monkeypatch):
    """仅配 client_id → 仍 False（必须 3 个齐全）。"""
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "fake-id")
    monkeypatch.delenv("YOUTUBE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("YOUTUBE_REFRESH_TOKEN", raising=False)
    assert YoutubePublishBackend().is_configured() is False


def test_youtube_upload_missing_credentials_raises_publish_auth_error(tmp_path, monkeypatch):
    """Auth 域硬失败：缺凭据 → PublishAuthError（不静默）。"""
    monkeypatch.delenv("YOUTUBE_CLIENT_ID", raising=False)
    monkeypatch.delenv("YOUTUBE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("YOUTUBE_REFRESH_TOKEN", raising=False)

    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    b = YoutubePublishBackend()
    with pytest.raises(PublishAuthError) as exc_info:
        b.upload_video(v, title="T")
    msg = str(exc_info.value)
    assert "YOUTUBE_CLIENT_ID" in msg
    assert "YOUTUBE_CLIENT_SECRET" in msg
    assert "YOUTUBE_REFRESH_TOKEN" in msg
    assert "硬失败" in msg or "不可静默" in msg


def test_youtube_upload_with_credentials_still_raises_not_implemented(tmp_path, monkeypatch):
    """凭据齐全时仍 raise NotImplementedError（明确实装未到位）。"""
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "fake-id-1234")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "fake-secret-5678")
    monkeypatch.setenv("YOUTUBE_REFRESH_TOKEN", "fake-token-9012")

    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    b = YoutubePublishBackend()
    with pytest.raises(NotImplementedError, match="YoutubePublishBackend"):
        b.upload_video(v, title="T")


def test_youtube_input_validation_runs_before_credential_check(tmp_path, monkeypatch):
    """input 校验应在凭据检查之前（避免无意义的 OSError）。"""
    monkeypatch.delenv("YOUTUBE_CLIENT_ID", raising=False)

    b = YoutubePublishBackend()
    # video 不存在 → PublishError（不是 PublishAuthError）
    with pytest.raises(PublishError, match="video 不存在"):
        b.upload_video(tmp_path / "nope.mp4", title="T")


def test_youtube_missing_title_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "id")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "sec")
    monkeypatch.setenv("YOUTUBE_REFRESH_TOKEN", "tok")
    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    b = YoutubePublishBackend()
    with pytest.raises(PublishError, match="title 不能为空"):
        b.upload_video(v, title="")


# ---------- Factory ----------

def test_make_publish_backend_mock():
    b = make_publish_backend("mock")
    assert isinstance(b, MockPublishBackend)


def test_make_publish_backend_youtube():
    b = make_publish_backend("youtube")
    assert isinstance(b, YoutubePublishBackend)


def test_make_publish_backend_unknown_raises():
    with pytest.raises(PublishError, match="未知 publish backend"):
        make_publish_backend("bilibili")


def test_make_publish_backend_passes_kwargs():
    b = make_publish_backend("mock", base_url="https://x.test")
    assert b.base_url == "https://x.test"


def test_list_backends_contains_known():
    names = list_backends()
    assert "mock" in names
    assert "youtube" in names
    assert len(names) == 2


# ---------- PublishResult.to_dict ----------

def test_publish_result_to_dict_round_trip():
    r = PublishResult(
        video_id="abc", url="https://x/y", status="uploaded",
        uploaded_at="2026-09-29T09:00:00", metadata={"k": "v"},
    )
    d = r.to_dict()
    assert d["video_id"] == "abc"
    assert d["metadata"]["k"] == "v"
    # 反序列化（手动）保持字段一致
    r2 = PublishResult(**d)
    assert r2.video_id == r.video_id
    assert r2.metadata == r.metadata
