"""V3.5 P3：audit_llm 单元测试（CLAUDE.md §5 反向自动化）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
for p in (SANDBOX,):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


# ---------- redact() ----------

def test_redact_strips_minimax_api_key():
    """MiniMax API key（sk-cp-...）必须 redact。"""
    from hailuo_video.audit_llm import redact

    s = "Authorization Bearer sk-cp-mCnaSWPMs_388HWJulqOogu1dAB3MJnadZ7"
    out = redact(s)
    assert "sk-cp-" not in out
    assert "[REDACTED_API_KEY]" in out


def test_redact_strips_openai_api_key():
    from hailuo_video.audit_llm import redact

    s = "Key: sk-projabc123def456ghi789jkl012mno345pqr678"
    out = redact(s)
    assert "sk-projabc" not in out
    assert "[REDACTED_API_KEY]" in out


def test_redact_strips_bearer_token():
    from hailuo_video.audit_llm import redact

    s = "Authorization: Bearer abc123def456ghi789jkl012mno345"
    out = redact(s)
    assert "abc123def" not in out
    assert "[REDACTED_BEARER]" in out


def test_redact_dict_recursively():
    """dict 递归 redact values，keys 保留。"""
    from hailuo_video.audit_llm import redact

    d = {
        "prompt": "a cute puppy playing",
        "api_key": "sk-cp-mCnaSWPMs_388HWJulqOogu1dAB3MJnadZ7",
        "nested": {
            "secret": "AKIAIOSFODNN7EXAMPLE",
            "name": "normal",
        },
    }
    out = redact(d)
    assert out["prompt"] == "a cute puppy playing"  # 不变
    assert "sk-cp-" not in out["api_key"]
    assert "[REDACTED_AWS_KEY]" in out["nested"]["secret"]
    assert out["nested"]["name"] == "normal"


def test_redact_list_recursively():
    from hailuo_video.audit_llm import redact

    lst = ["sk-cp-fake1fake2fake3fake4fake5fake6fake7", "safe"]
    out = redact(lst)
    assert "sk-cp-" not in out[0]
    assert out[1] == "safe"


def test_hash_short_is_stable_for_same_payload():
    from hailuo_video.audit_llm import hash_short

    p = {"a": 1, "b": 2}
    assert hash_short(p) == hash_short(p)
    assert hash_short(p) == hash_short({"b": 2, "a": 1})  # sort_keys=True


def test_hash_short_differs_for_different_payload():
    from hailuo_video.audit_llm import hash_short

    assert hash_short({"a": 1}) != hash_short({"a": 2})


def test_hash_short_redacts_before_hashing():
    """hash_short 应先 redact 再 hash（防泄漏）。"""
    from hailuo_video.audit_llm import hash_short

    # 2 个 key 都 ≥20 chars → 都 redact → 同 hash
    p1 = {"key": "sk-cp-fakekeyfakekeyfakekey12345"}  # 30 chars
    p2 = {"key": "sk-cp-differentkeyvaluealsofake12345"}  # 35 chars
    assert hash_short(p1) == hash_short(p2)


def test_hash_short_does_NOT_keep_secret_after_hashing():
    """hash_short 输出（debug 不会泄漏 secret 的 substring）。"""
    from hailuo_video.audit_llm import hash_short

    h = hash_short({"key": "sk-cp-fakekeyfakefakefakefakekey123"})
    # h 只是 16 chars hex 摘要，不含原 secret
    assert "fakekeyfake" not in h
    assert "sk-cp-" not in h


def test_hash_short_differs_for_different_keys():
    """不同 key（不被 redact）→ 不同 hash。"""
    from hailuo_video.audit_llm import hash_short

    assert hash_short({"a": 1}) != hash_short({"b": 1})


# ---------- AuditLogger ----------

def test_audit_logger_writes_jsonl(tmp_path):
    from hailuo_video.audit_llm import AuditLogger, AuditEvent

    logger = AuditLogger("proj1234", log_dir=tmp_path)
    event = AuditEvent(
        timestamp="2026-10-14T12:00:00",
        kind="request",
        project_id="proj1234",
        endpoint="image_generation",
        payload_keys=["prompt"],
        payload_hash="abc123",
    )
    logger.write(event)
    # 读回
    events = logger.read_all()
    assert len(events) == 1
    assert events[0]["endpoint"] == "image_generation"


def test_audit_logger_rejects_short_project_id(tmp_path):
    from hailuo_video.audit_llm import AuditLogger

    with pytest.raises(ValueError, match="project_id"):
        AuditLogger("ab", log_dir=tmp_path)


def test_audit_logger_isolates_per_project(tmp_path):
    """不同 project_id 写到不同 jsonl 文件。"""
    from hailuo_video.audit_llm import AuditLogger, AuditEvent

    a = AuditLogger("proj_aaa", log_dir=tmp_path)
    b = AuditLogger("proj_bbb", log_dir=tmp_path)
    a.write(AuditEvent(timestamp="2026-10-14T12:00:00", kind="request", project_id="proj_aaa", endpoint="x"))
    b.write(AuditEvent(timestamp="2026-10-14T12:00:01", kind="request", project_id="proj_bbb", endpoint="y"))

    a_events = a.read_all()
    b_events = b.read_all()
    assert len(a_events) == 1
    assert len(b_events) == 1
    assert a_events[0]["project_id"] == "proj_aaa"
    assert b_events[0]["project_id"] == "proj_bbb"
    assert a.log_path != b.log_path


# ---------- audit_request / audit_response ----------

def test_audit_request_returns_payload_hash(tmp_path):
    from hailuo_video.audit_llm import audit_request, AuditLogger

    payload = {"prompt": "a puppy", "size": "1024x1024"}
    h = audit_request("proj1234", "image_generation", payload, log_dir=tmp_path)
    assert len(h) == 16  # sha256[:16]


def test_audit_request_writes_event_with_keys(tmp_path):
    from hailuo_video.audit_llm import audit_request, AuditLogger

    audit_request(
        "proj1234", "image_generation",
        {"prompt": "hello", "negative": "bad"},
        log_dir=tmp_path,
    )
    events = AuditLogger("proj1234", log_dir=tmp_path).read_all()
    assert len(events) == 1
    assert events[0]["kind"] == "request"
    assert events[0]["payload_keys"] == ["negative", "prompt"]  # sorted


def test_audit_response_records_duration_seconds(tmp_path):
    from hailuo_video.audit_llm import audit_response, AuditLogger

    audit_response(
        "proj1234", "image_generation",
        payload={"prompt": "x"},
        response_summary={"task_id": "abc", "status_code": 200},
        duration_seconds=1.42,
        ok=True,
        log_dir=tmp_path,
    )
    events = AuditLogger("proj1234", log_dir=tmp_path).read_all()
    assert len(events) == 1
    assert events[0]["kind"] == "response"
    assert events[0]["duration_seconds"] == 1.42
    assert events[0]["ok"] is True
    assert events[0]["response_summary"]["task_id"] == "abc"


def test_audit_response_error_records_kind_error(tmp_path):
    from hailuo_video.audit_llm import audit_response, AuditLogger

    audit_response(
        "proj1234", "image_generation",
        payload={"prompt": "x"},
        response_summary={},
        duration_seconds=0.5,
        ok=False,
        error_code="API_TIMEOUT",
        log_dir=tmp_path,
    )
    events = AuditLogger("proj1234", log_dir=tmp_path).read_all()
    assert events[0]["kind"] == "error"
    assert events[0]["ok"] is False
    assert events[0]["error_code"] == "API_TIMEOUT"


# ---------- truncate() ----------

def test_truncate_short_string_unchanged():
    from hailuo_video.audit_llm import truncate

    assert truncate("hello") == "hello"


def test_truncate_long_string_truncated():
    from hailuo_video.audit_llm import truncate

    long = "x" * 500
    out = truncate(long)
    assert len(out) < 500
    assert "..." in out


def test_truncate_dict_truncates_values():
    from hailuo_video.audit_llm import truncate

    d = {"prompt": "x" * 500}
    out = truncate(d)
    assert len(out["prompt"]) < 500


# ---------- AuditEvent.to_jsonl() ----------

def test_audit_event_to_jsonl_round_trip():
    """to_jsonl 输出在测一次读回 dict 一致。"""
    from hailuo_video.audit_llm import AuditEvent

    event = AuditEvent(
        timestamp="2026-10-14T12:00:00",
        kind="response",
        project_id="p1234",
        endpoint="image_generation",
        duration_seconds=1.5,
        ok=True,
    )
    line = event.to_jsonl()
    parsed = json.loads(line)
    assert parsed["kind"] == "response"
    assert parsed["project_id"] == "p1234"
    assert parsed["duration_seconds"] == 1.5


def test_audit_event_to_jsonl_truncates_preview():
    from hailuo_video.audit_llm import AuditEvent

    long = "x" * 1000
    event = AuditEvent(
        timestamp="2026-10-14T12:00:00",
        kind="request",
        payload_preview={"big": long},
    )
    line = event.to_jsonl()
    parsed = json.loads(line)
    # payload_preview 被 str() 然后 [:256]
    # 因此是 string 类型，长度 ≤ 256
    assert isinstance(parsed["payload_preview"], str)
    assert len(parsed["payload_preview"]) <= 256