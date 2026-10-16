"""V3.5 Director 升级：MiniMaxChatBackend.chat() 接入 audit_llm 单测。"""
from __future__ import annotations

import sys
from pathlib import Path

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))


# ---------- 注入 http_post fixture：env 干净 + 假响应 ----------

def _make_fake_response(content: str = "fake llm content") -> dict:
    """构造一个 valid MiniMax /chat/completions 响应（OpenAI 格式）。"""
    return {
        "id": "test-task-001",
        "model": "MiniMax-M3",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def _make_backend(monkeypatch, tmp_path, http_post):
    """构造 MiniMaxChatBackend（env 干净 + 注入 http_post）。"""
    from hailuo_video import script_engine as se
    # 清 env（防 .env 污染）
    for k in se._DOTENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-cp-testkeyfakekeyfakekeyfake12345")
    return se.MiniMaxChatBackend(http_post=http_post)


# ---------- 默认行为：不打 audit（向后兼容）----------

def test_chat_no_audit_by_default(monkeypatch, tmp_path):
    """不传 project_id → chat() 正常工作，且不打 audit。"""
    from hailuo_video.audit_llm import AuditLogger
    backend = _make_backend(monkeypatch, tmp_path, lambda u, b, h: _make_fake_response("hi"))
    out = backend.chat("sys", "user", temperature=0.5)
    assert out == "hi"
    # 不应有 audit log 写入
    audit_dir = tmp_path / "no_audit_should_not_exist"
    audit_dir.mkdir()
    # 因为 backend 没传 project_id, 不会调 audit_request/audit_response


def test_chat_no_audit_when_no_project_id(monkeypatch, tmp_path):
    """显式传 log_dir 但不传 project_id → 不打 audit（保持向后兼容）。"""
    from hailuo_video import script_engine as se
    backend = _make_backend(monkeypatch, tmp_path, lambda u, b, h: _make_fake_response("ok"))
    # 不传 project_id 也不传 log_dir → 不打 audit
    backend.chat("sys", "user")


# ---------- 默认：打 audit（project_id 非空）----------

def test_chat_audit_writes_request_and_response(monkeypatch, tmp_path):
    """传 project_id → 写 1 request + 1 response event。"""
    from hailuo_video.audit_llm import AuditLogger
    backend = _make_backend(monkeypatch, tmp_path, lambda u, b, h: _make_fake_response("奶黄伸懒腰"))
    out = backend.chat(
        "你是漫剧编剧", "请写剧本",
        project_id="test_proj_001", endpoint="novel_to_script",
        log_dir=tmp_path,
    )
    assert out == "奶黄伸懒腰"
    # 读 audit log
    events = AuditLogger("test_proj_001", log_dir=tmp_path).read_all()
    assert len(events) == 2
    kinds = [e["kind"] for e in events]
    assert "request" in kinds
    assert "response" in kinds
    # 验证 endpoint
    assert all(e["endpoint"] == "novel_to_script" for e in events)
    assert all(e["project_id"] == "test_proj_001" for e in events)
    # 验证 ok=True
    response = [e for e in events if e["kind"] == "response"][0]
    assert response["ok"] is True
    assert response["duration_seconds"] > 0


def test_chat_audit_payload_redacts_api_key(monkeypatch, tmp_path):
    """audit event 的 payload_preview 中 API key 必须 redact（防泄漏）。"""
    from hailuo_video.audit_llm import AuditLogger
    # 让 _make_backend 设的 key 流入 audit payload
    captured_payload = {}

    def capture_post(u, b, h):
        captured_payload.update(b)
        return _make_fake_response("ok")

    backend = _make_backend(monkeypatch, tmp_path, capture_post)
    out = backend.chat(
        "sys", "user",
        project_id="test_proj_redact", log_dir=tmp_path,
    )
    assert out == "ok"
    events = AuditLogger("test_proj_redact", log_dir=tmp_path).read_all()
    # 验证 audit event 中 API key 被 redact（不在 payload_preview 里）
    import json
    for e in events:
        preview = e.get("payload_preview", "")
        if isinstance(preview, dict):
            preview = json.dumps(preview, ensure_ascii=False)
        assert "sk-cp-testkeyfakekey" not in str(preview)


def test_chat_audit_default_endpoint(monkeypatch, tmp_path):
    """不传 endpoint → 默认 "minimax_chat"。"""
    from hailuo_video.audit_llm import AuditLogger
    backend = _make_backend(monkeypatch, tmp_path, lambda u, b, h: _make_fake_response("x"))
    backend.chat("sys", "user", project_id="test_default_ep", log_dir=tmp_path)
    events = AuditLogger("test_default_ep", log_dir=tmp_path).read_all()
    assert all(e["endpoint"] == "minimax_chat" for e in events)


# ---------- 错误路径：打 audit（kind=error）----------

def test_chat_audit_records_http_error(monkeypatch, tmp_path):
    """http_post 抛 HTTPError → audit_response 写 kind="error", ok=False, error_code="HTTP_xxx"。"""
    import urllib.error
    from hailuo_video.audit_llm import AuditLogger

    def boom(u, b, h):
        raise urllib.error.HTTPError(
            url=u, code=401, msg="Unauthorized", hdrs={}, fp=None,
        )

    backend = _make_backend(monkeypatch, tmp_path, boom)
    from hailuo_video import script_engine as se
    import pytest
    with pytest.raises(se.LLMError, match="HTTP 401"):
        backend.chat("sys", "user", project_id="test_err", log_dir=tmp_path)
    events = AuditLogger("test_err", log_dir=tmp_path).read_all()
    # 应该有 request + error event
    assert len(events) == 2
    error_event = [e for e in events if e["kind"] == "error"][0]
    assert error_event["ok"] is False
    assert error_event["error_code"] == "HTTP_401"


def test_chat_audit_records_url_error(monkeypatch, tmp_path):
    """url 错误 → audit kind=error, error_code='URL_ERROR'。"""
    import urllib.error
    from hailuo_video.audit_llm import AuditLogger

    def boom(u, b, h):
        raise urllib.error.URLError("DNS failure")

    backend = _make_backend(monkeypatch, tmp_path, boom)
    from hailuo_video import script_engine as se
    import pytest
    with pytest.raises(se.LLMError, match="URLError"):
        backend.chat("sys", "user", project_id="test_url_err", log_dir=tmp_path)
    events = AuditLogger("test_url_err", log_dir=tmp_path).read_all()
    error_event = [e for e in events if e["kind"] == "error"][0]
    assert error_event["ok"] is False
    assert error_event["error_code"] == "URL_ERROR"


def test_chat_audit_records_structure_error(monkeypatch, tmp_path):
    """响应结构不符 → audit kind=error, error_code='STRUCTURE_ERROR'。"""
    from hailuo_video.audit_llm import AuditLogger

    def bad_response(u, b, h):
        return {"choices": "not a list"}  # 错误结构

    backend = _make_backend(monkeypatch, tmp_path, bad_response)
    from hailuo_video import script_engine as se
    import pytest
    with pytest.raises(se.LLMError, match="响应结构不符"):
        backend.chat("sys", "user", project_id="test_struct_err", log_dir=tmp_path)
    events = AuditLogger("test_struct_err", log_dir=tmp_path).read_all()
    error_event = [e for e in events if e["kind"] == "error"][0]
    assert error_event["error_code"] == "STRUCTURE_ERROR"


# ---------- audit_chat_call helper ----------

def test_audit_chat_call_writes_request():
    from hailuo_video.audit_llm import audit_chat_call, AuditLogger
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        log_dir = Path(tmp)
        h = audit_chat_call(
            "test_helper", "minimax_chat",
            {"system": "sys", "user": "user"}, log_dir=log_dir,
        )
        assert len(h) == 16  # sha256[:16]
        events = AuditLogger("test_helper", log_dir=log_dir).read_all()
        assert len(events) == 1
        assert events[0]["kind"] == "request"


def test_audit_chat_call_noop_when_no_project_id():
    """空 project_id → 不打 audit，返回空串。"""
    from hailuo_video.audit_llm import audit_chat_call
    h = audit_chat_call("", "minimax_chat", {"x": 1})
    assert h == ""


# ---------- audit helper 内部一致性 ----------

def test_audit_request_and_response_have_same_payload_hash(monkeypatch, tmp_path):
    """同一个 chat() 调用，request event 和 response event 的 payload_hash 必须一致。"""
    from hailuo_video.audit_llm import AuditLogger
    backend = _make_backend(monkeypatch, tmp_path, lambda u, b, h: _make_fake_response("hi"))
    backend.chat("sys", "user", project_id="test_hash", log_dir=tmp_path)
    events = AuditLogger("test_hash", log_dir=tmp_path).read_all()
    request = [e for e in events if e["kind"] == "request"][0]
    response = [e for e in events if e["kind"] == "response"][0]
    assert request["payload_hash"] == response["payload_hash"]


# ---------- 现有 40 个 test_script_engine.py 必须不依赖 audit hook ----------

def test_existing_chat_calls_still_work_without_audit_kwargs(monkeypatch, tmp_path):
    """旧调用方式 backend.chat('sys', 'user')（无 project_id）必须仍正常工作。"""
    backend = _make_backend(monkeypatch, tmp_path, lambda u, b, h: _make_fake_response("legacy"))
    out = backend.chat("sys", "user")
    assert out == "legacy"
