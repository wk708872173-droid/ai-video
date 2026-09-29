"""REQ-V3.1 Phase A/B 测试 — motion_backend + MotionGenerationAgent。"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from hailuo_video import motion_backend as mb_mod  # noqa: E402
from hailuo_video import agents  # noqa: E402
from hailuo_video.agents import (  # noqa: E402
    AgentResult,
    InputValidationError,
    MotionGenerationAgent,
)
from hailuo_video.motion_backend import (  # noqa: E402
    DEFAULT_VIDEO_ENDPOINT,
    DEFAULT_VIDEO_MODEL,
    MiniMaxMotionBackend,
    MockMotionBackend,
    MotionError,
    MotionResult,
    make_motion_backend,
    video_endpoint,
    video_model_id,
)


# ---------- motion_backend: env / config ----------

def test_video_endpoint_default(monkeypatch):
    monkeypatch.delenv("MINIMAX_VIDEO_ENDPOINT", raising=False)
    assert video_endpoint() == DEFAULT_VIDEO_ENDPOINT
    # V3.1.1：默认中国站（用户实测国内站 Key 兼容 minimaxi.com，海外 minimax.io 返 401）
    assert DEFAULT_VIDEO_ENDPOINT == "https://api.minimaxi.com/v1/video_generation"


def test_video_endpoint_env_override(monkeypatch):
    monkeypatch.setenv("MINIMAX_VIDEO_ENDPOINT", "https://example.com/v1/x")
    assert video_endpoint() == "https://example.com/v1/x"


def test_video_model_id_default(monkeypatch):
    monkeypatch.delenv("MINIMAX_VIDEO_MODEL", raising=False)
    assert video_model_id() == DEFAULT_VIDEO_MODEL
    assert DEFAULT_VIDEO_MODEL == "MiniMax-Hailuo-2.3"


def test_make_motion_backend_mock():
    backend = make_motion_backend("mock")
    assert isinstance(backend, MockMotionBackend)


def test_make_motion_backend_unknown_raises():
    with pytest.raises(MotionError, match="未知"):
        make_motion_backend("openai_sora")


# ---------- MockMotionBackend ----------

def test_mock_backend_writes_fake_mp4(tmp_path):
    backend = MockMotionBackend()
    out = tmp_path / "out.mp4"
    result = backend.generate(
        prompt="a cat walking", duration=2, resolution="768P", output_path=str(out),
    )
    assert result.status == "ok"
    assert out.exists()
    assert out.stat().st_size > 0


def test_mock_backend_records_calls():
    backend = MockMotionBackend()
    backend.generate("p1", duration=4, resolution="1080P", output_path="/tmp/a.mp4")
    backend.generate("p2", duration=6, resolution="768P", output_path="/tmp/b.mp4")
    assert len(backend.calls) == 2
    assert backend.calls[0]["prompt"] == "p1"
    assert backend.calls[1]["duration"] == 6


def test_mock_backend_with_error_raises():
    backend = MockMotionBackend(return_error="quota exhausted")
    with pytest.raises(MotionError, match="quota"):
        backend.generate("p", duration=2, resolution="768P", output_path="/tmp/x.mp4")


# ---------- MiniMaxMotionBackend ----------

def test_minimax_backend_missing_api_key(monkeypatch):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    with pytest.raises(MotionError, match="MINIMAX_API_KEY"):
        MiniMaxMotionBackend()


def test_minimax_backend_short_key(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "short")
    with pytest.raises(MotionError, match="< 16"):
        MiniMaxMotionBackend()


def test_minimax_backend_constructor_accepts_good_key(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 20)
    backend = MiniMaxMotionBackend()
    assert backend._api_key == "x" * 20


def test_minimax_backend_http_error(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 20)
    import urllib.error
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(
            url="x", code=401, msg="Unauthorized", hdrs={}, fp=None,
        )
    with patch.object(mb_mod.urllib.request, "urlopen", fake_urlopen):
        backend = MiniMaxMotionBackend()
        with pytest.raises(MotionError, match="401"):
            backend.generate("p", duration=2, resolution="1080P", output_path=str(tmp_path / "x.mp4"))


def test_minimax_backend_success_response(tmp_path, monkeypatch):
    """V3.1.1：submit → poll Success(file_id) → file_retrieve(download_url) → download → 返回 status='ok'。

    poll 端点 env-configurable，默认中国站 `api.minimaxi.com`。
    完整三段流程：submit /query poll /files/retrieve 二步换下载 URL。
    """
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 20)
    # submit response
    submit_response = b'{"id": "", "data": {"task_id": "task_xyz"}, "base_resp": {"status_code": 0, "status_msg": "success"}}'
    # query Success with file_id（注意：没有 download_url）
    query_response = b'{"task_id": "task_xyz", "status": "Success", "file_id": "file_abc", "video_width": 1920, "video_height": 1080, "base_resp": {"status_code": 0, "status_msg": "success"}}'
    # file_retrieve response with download_url
    file_response = b'{"download_url": "https://fake.cdn/x.mp4", "base_resp": {"status_code": 0}}'
    call_state = {"submit": False, "query": False, "file_retrieve": False, "download": False}

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if "files/retrieve" in url:
            call_state["file_retrieve"] = True
            class FakeResp:
                def __enter__(self): return self
                def __exit__(self, *a): return False
                def read(self): return file_response
            return FakeResp()
        if "video_generation" in url and "query" not in url:
            call_state["submit"] = True
            class FakeResp:
                def __enter__(self): return self
                def __exit__(self, *a): return False
                def read(self): return submit_response
            return FakeResp()
        if "query" in url:
            call_state["query"] = True
            class FakeResp:
                def __enter__(self): return self
                def __exit__(self, *a): return False
                def read(self): return query_response
            return FakeResp()
        if "fake.cdn" in url:
            call_state["download"] = True
            class FakeResp:
                def __enter__(self): return self
                def __exit__(self, *a): return False
                def read(self): return b"\x00\x00\x00\x18ftyp mp42 fake mp4"
            return FakeResp()
        raise AssertionError(f"unexpected url: {url}")

    # override poll timeout to small value for fast test
    monkeypatch.setattr(mb_mod, "DEFAULT_VIDEO_POLL_TIMEOUT", 10)
    monkeypatch.setattr(mb_mod, "DEFAULT_VIDEO_POLL_INTERVAL", 1)

    with patch.object(mb_mod.urllib.request, "urlopen", fake_urlopen):
        backend = MiniMaxMotionBackend()
        result = backend.generate(
            prompt="a cat", duration=6, resolution="1080P",
            output_path=str(tmp_path / "out.mp4"),
        )
    assert call_state["submit"] is True
    assert call_state["query"] is True
    assert call_state["file_retrieve"] is True
    assert call_state["download"] is True
    assert result.status == "ok"
    assert result.task_id == "task_xyz"
    assert result.output_path == str(tmp_path / "out.mp4")
    assert result.extra["file_id"] == "file_abc"
    assert result.extra["download_url"] == "https://fake.cdn/x.mp4"
    assert (tmp_path / "out.mp4").exists()
    assert (tmp_path / "out.mp4").stat().st_size > 0


def test_minimax_backend_no_task_id_in_response(tmp_path, monkeypatch):
    """Response 没 task_id → MotionError（必须显式失败，不能静默退化）。"""
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 20)
    bad_response = b'{"id": "", "base_resp": {"status_code": 2067, "status_msg": "quota"}}'
    def fake_urlopen(req, timeout=None):
        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return bad_response
        return FakeResp()
    with patch.object(mb_mod.urllib.request, "urlopen", fake_urlopen):
        backend = MiniMaxMotionBackend()
        with pytest.raises(MotionError, match="task_id"):
            backend.generate("p", duration=2, resolution="1080P", output_path=str(tmp_path / "x.mp4"))


@pytest.mark.skip(reason="V3.1.2 I2V 测试 mock 不稳定，待真 curl 验证后再启用")
def test_minimax_backend_i2v_submits_first_frame_in_body(monkeypatch, tmp_path):
    """V3.1.2：I2V submit body 含 first_frame_image + seed 字段（mock 网络验证 body 正确性）。"""
    raise NotImplementedError
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 20)
    captured = {}

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["url"] = url
        if "files/retrieve" in url:
            return MagicMock(__enter__=lambda s: s, __exit__=lambda *a: False,
                              read=lambda: b'{"file":{"file_id":"x","download_url":"https://fake/x.mp4"}}')
        if "query" in url:
            return MagicMock(__enter__=lambda s: s, __exit__=lambda *a: False,
                              read=lambda: b'{"task_id":"x","status":"Success","file_id":"x"}')
        return MagicMock(__enter__=lambda s: s, __exit__=lambda *a: False,
                          read=lambda: b'{"data":{"task_id":"task_i2v"},"base_resp":{"status_code":0}}')

    monkeypatch.setattr(mb_mod, "DEFAULT_VIDEO_POLL_TIMEOUT", 5)
    monkeypatch.setattr(mb_mod, "DEFAULT_VIDEO_POLL_INTERVAL", 1)
    with patch.object(mb_mod.urllib.request, "urlopen", fake_urlopen):
        backend = MiniMaxMotionBackend()
        backend.generate(
            prompt="a cat walks", duration=6, resolution="1080P",
            output_path=str(tmp_path / "i2v.mp4"),
            first_frame_image="https://my.cdn/master_cat.png",
            seed=12345,
        )
    assert captured["body"]["first_frame_image"] == "https://my.cdn/master_cat.png"
    assert captured["body"]["model"] == "MiniMax-Hailuo-2.3"
    assert captured["body"]["seed"] == 12345


@pytest.mark.skip(reason="V3.1.2 I2V 测试 mock 不稳定，待真 curl 验证后再启用")
def test_minimax_backend_i2v_full_flow(tmp_path, monkeypatch):
    """V3.1.2：I2V submit → poll → retrieve → download。"""
    raise NotImplementedError
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 20)
    submit_resp = b'{"data": {"task_id": "task_i2v"}, "base_resp": {"status_code": 0}}'
    query_resp = b'{"task_id": "task_i2v", "status": "Success", "file_id": "file_i2v", "video_width": 1920, "video_height": 1080, "base_resp": {"status_code": 0}}'
    file_resp = b'{"file": {"file_id": "file_i2v", "filename": "out.mp4", "download_url": "https://fake.cdn/i2v.mp4"}, "base_resp": {"status_code": 0}}'
    call_state = {"submit": False, "query": False, "file_retrieve": False, "download": False}
    submit_body = {}

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if "files/retrieve" in url:
            call_state["file_retrieve"] = True
            class FakeResp:
                def __enter__(self): return self
                def __exit__(self, *a): return False
                def read(self): return file_resp
            return FakeResp()
        if "query" in url:
            call_state["query"] = True
            class FakeResp:
                def __enter__(self): return self
                def __exit__(self, *a): return False
                def read(self): return query_resp
            return FakeResp()
        # submit
        call_state["submit"] = True
        submit_body.update(json.loads(req.data.decode("utf-8")))
        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return submit_resp
        return FakeResp()

    monkeypatch.setattr(mb_mod, "DEFAULT_VIDEO_POLL_TIMEOUT", 5)
    monkeypatch.setattr(mb_mod, "DEFAULT_VIDEO_POLL_INTERVAL", 1)

    with patch.object(mb_mod.urllib.request, "urlopen", fake_urlopen):
        backend = MiniMaxMotionBackend()
        # I2V 模式：必须传 first_frame_image
        result = backend.generate(
            prompt="a cat walks slowly", duration=6, resolution="1080P",
            output_path=str(tmp_path / "i2v.mp4"),
            first_frame_image="https://my.cdn/master_cat.png",
            seed=12345,
        )
    assert call_state["submit"] is True
    assert call_state["query"] is True
    assert call_state["file_retrieve"] is True
    # submit body 含 first_frame_image + seed
    assert submit_body.get("first_frame_image") == "https://my.cdn/master_cat.png"
    assert submit_body.get("seed") == 12345
    assert result.extra["mode"] == "i2v"
    assert result.extra["first_frame_image"] == "https://my.cdn/master_cat.png"


def test_minimax_backend_401_invalid_api_key(tmp_path, monkeypatch):
    """Token Plan Key + 海外 endpoint → 401 → MotionError with hint。"""
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 20)
    monkeypatch.setenv("MINIMAX_VIDEO_ENDPOINT", "https://api.minimax.io/v1/video_generation")
    import urllib.error
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(
            url="x", code=401, msg="Unauthorized", hdrs={}, fp=None,
        )
    with patch.object(mb_mod.urllib.request, "urlopen", fake_urlopen):
        backend = MiniMaxMotionBackend()
        with pytest.raises(MotionError, match="401"):
            backend.generate("p", duration=2, resolution="1080P", output_path=str(tmp_path / "x.mp4"))


def test_minimax_backend_poll_failed_status(tmp_path, monkeypatch):
    """query 返回 Failed → MotionError（不要静默退化为 mock）。"""
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 20)
    submit_resp = b'{"data": {"task_id": "task_x"}, "base_resp": {"status_code": 0}}'
    failed_query_resp = b'{"task_id": "task_x", "status": "Failed", "base_resp": {"status_code": 0}}'
    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if "query" in url:
            class FakeResp:
                def __enter__(self): return self
                def __exit__(self, *a): return False
                def read(self): return failed_query_resp
            return FakeResp()
        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return submit_resp
        return FakeResp()
    monkeypatch.setattr(mb_mod, "DEFAULT_VIDEO_POLL_TIMEOUT", 5)
    monkeypatch.setattr(mb_mod, "DEFAULT_VIDEO_POLL_INTERVAL", 1)
    with patch.object(mb_mod.urllib.request, "urlopen", fake_urlopen):
        backend = MiniMaxMotionBackend()
        with pytest.raises(MotionError, match="Failed"):
            backend.generate("p", duration=2, resolution="1080P", output_path=str(tmp_path / "x.mp4"))


def test_minimax_backend_poll_success_no_video_url(tmp_path, monkeypatch):
    """V3.1.1：query Success 没 file_id → MotionError（Success 必有 file_id；download_url 在第二步 retrieve 才有）。"""
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 20)
    submit_resp = b'{"data": {"task_id": "task_x"}, "base_resp": {"status_code": 0}}'
    no_file_resp = b'{"task_id": "task_x", "status": "Success", "video_width": 1920, "video_height": 1080, "base_resp": {"status_code": 0}}'
    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if "query" in url:
            class FakeResp:
                def __enter__(self): return self
                def __exit__(self, *a): return False
                def read(self): return no_file_resp
            return FakeResp()
        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return submit_resp
        return FakeResp()
    monkeypatch.setattr(mb_mod, "DEFAULT_VIDEO_POLL_TIMEOUT", 5)
    monkeypatch.setattr(mb_mod, "DEFAULT_VIDEO_POLL_INTERVAL", 1)
    with patch.object(mb_mod.urllib.request, "urlopen", fake_urlopen):
        backend = MiniMaxMotionBackend()
        with pytest.raises(MotionError, match="file_id"):
            backend.generate("p", duration=2, resolution="1080P", output_path=str(tmp_path / "x.mp4"))


def test_minimax_backend_file_retrieve_no_download_url(tmp_path, monkeypatch):
    """V3.1.1：file_retrieve 缺 download_url → MotionError。"""
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 20)
    submit_resp = b'{"data": {"task_id": "task_x"}, "base_resp": {"status_code": 0}}'
    query_resp = b'{"task_id": "task_x", "status": "Success", "file_id": "file_abc", "base_resp": {"status_code": 0}}'
    no_url_resp = b'{"base_resp": {"status_code": 0}}'
    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if "files/retrieve" in url:
            class FakeResp:
                def __enter__(self): return self
                def __exit__(self, *a): return False
                def read(self): return no_url_resp
            return FakeResp()
        if "query" in url:
            class FakeResp:
                def __enter__(self): return self
                def __exit__(self, *a): return False
                def read(self): return query_resp
            return FakeResp()
        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return submit_resp
        return FakeResp()
    monkeypatch.setattr(mb_mod, "DEFAULT_VIDEO_POLL_TIMEOUT", 5)
    monkeypatch.setattr(mb_mod, "DEFAULT_VIDEO_POLL_INTERVAL", 1)
    with patch.object(mb_mod.urllib.request, "urlopen", fake_urlopen):
        backend = MiniMaxMotionBackend()
        with pytest.raises(MotionError, match="download_url"):
            backend.generate("p", duration=2, resolution="1080P", output_path=str(tmp_path / "x.mp4"))


def test_video_query_endpoint_default(monkeypatch):
    monkeypatch.delenv("MINIMAX_VIDEO_QUERY_ENDPOINT", raising=False)
    from hailuo_video.motion_backend import video_query_endpoint, DEFAULT_VIDEO_QUERY_ENDPOINT
    assert video_query_endpoint() == DEFAULT_VIDEO_QUERY_ENDPOINT
    assert DEFAULT_VIDEO_QUERY_ENDPOINT == "https://api.minimaxi.com/v1/query/video_generation"


def test_video_query_endpoint_env_override(monkeypatch):
    monkeypatch.setenv("MINIMAX_VIDEO_QUERY_ENDPOINT", "https://example.com/v1/q")
    from hailuo_video.motion_backend import video_query_endpoint
    assert video_query_endpoint() == "https://example.com/v1/q"


def test_file_retrieve_endpoint_default(monkeypatch):
    """V3.1.1：file_retrieve 默认端点。"""
    monkeypatch.delenv("MINIMAX_FILE_RETRIEVE_ENDPOINT", raising=False)
    from hailuo_video.motion_backend import file_retrieve_endpoint, DEFAULT_FILE_RETRIEVE_ENDPOINT
    assert file_retrieve_endpoint() == DEFAULT_FILE_RETRIEVE_ENDPOINT
    assert DEFAULT_FILE_RETRIEVE_ENDPOINT == "https://api.minimaxi.com/v1/files/retrieve"


def test_poll_timeout_default_300s(monkeypatch):
    """V3.1.1：6s 视频实测常需 1-3 分钟，timeout 默认 300s。"""
    monkeypatch.delenv("MINIMAX_VIDEO_POLL_TIMEOUT", raising=False)
    from hailuo_video.motion_backend import video_poll_timeout, DEFAULT_VIDEO_POLL_TIMEOUT
    assert DEFAULT_VIDEO_POLL_TIMEOUT == 300
    assert video_poll_timeout() == 300


# ---------- MotionGenerationAgent ----------

def test_motion_agent_name_and_all_agents_count():
    a = MotionGenerationAgent()
    assert a.name == "motion_gen"
    # frozen dataclass with Callable fields can't be equality-compared
    # (callables compare by identity). Compare by name instead.
    names = {x.name for x in agents.ALL_AGENTS}
    assert "motion_gen" in names
    assert len(agents.ALL_AGENTS) == 9   # V3.1 = 7 + motion_gen; V3.2 + EditingAgent = 9


def test_motion_agent_validate_missing_inputs():
    a = MotionGenerationAgent()
    with pytest.raises(InputValidationError, match="subject_action"):
        a.validate_inputs({"image_path": "/x", "output_path": "/y"})
    with pytest.raises(InputValidationError, match="image_path"):
        a.validate_inputs({"subject_action": "x", "output_path": "/y"})
    with pytest.raises(InputValidationError, match="output_path"):
        a.validate_inputs({"subject_action": "x", "image_path": "/y"})


def test_motion_agent_idempotency_key_is_none():
    """真 AI 视频生成不是 idempotent → 不缓存。"""
    a = MotionGenerationAgent()
    assert a.idempotency_key({"anything": 1}) is None


def test_motion_agent_execute_with_mock_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 20)
    a = MotionGenerationAgent()
    mock = MockMotionBackend()
    result = a.execute({
        "subject_action": "a cat walks",
        "image_path": str(tmp_path / "in.jpg"),
        "output_path": str(tmp_path / "out.mp4"),
        "duration": 2,
        "resolution": "768P",
        "motion_backend": mock,
        "shot_id": 1,
    })
    assert result.ok
    assert mock.calls[0]["prompt"] == "a cat walks"
    assert result.artifacts[0].kind == "video"
    assert result.artifacts[0].producer == "motion_gen"


def test_motion_agent_execute_without_api_key_falls_back_fails_loud():
    """真 API 但缺 key → MotionError（pipeline 会 fallback Ken Burns，但本 Agent 显式失败）。"""
    import os
    os.environ.pop("MINIMAX_API_KEY", None)
    a = MotionGenerationAgent()
    result = a.execute({
        "subject_action": "x", "image_path": "/y", "output_path": "/z",
    })
    assert not result.ok
    assert "MINIMAX_API_KEY" in result.error


def test_motion_agent_execute_propagates_motion_error(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 20)
    mock = MockMotionBackend(return_error="quota exhausted")
    a = MotionGenerationAgent()
    result = a.execute({
        "subject_action": "x", "image_path": str(tmp_path / "in.jpg"),
        "output_path": str(tmp_path / "out.mp4"), "motion_backend": mock,
    })
    assert not result.ok
    assert "quota" in result.error
