"""REQ-V3-001 Phase 1 — artifact.py / retry.py / log.py 单测。

不依赖网络、不依赖 MiniMax、不依赖 torch——纯本地逻辑测试。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from hailuo_video.artifact import (  # noqa: E402
    Artifact,
    Registry,
    compute_content_hash,
)
from hailuo_video.retry import RetryPolicy, with_retry  # noqa: E402
from hailuo_video.log import LogEvent, StructuredLogger  # noqa: E402


# ---------- Artifact ----------

def test_artifact_to_from_dict_roundtrip():
    a = Artifact(
        artifact_id="a1", kind="image", path=Path("/tmp/x.jpg"),
        content_hash="abc123", producer="image_gen",
        metadata={"prompt_chars": 12, "model": "image-01"},
    )
    d = a.to_dict()
    a2 = Artifact.from_dict(d)
    assert a2.artifact_id == a.artifact_id
    assert a2.kind == a.kind
    assert a2.path == a.path
    assert a2.content_hash == a.content_hash
    assert a2.producer == a.producer
    assert a2.metadata == a.metadata


def test_compute_content_hash_from_real_file(tmp_path):
    p = tmp_path / "f.txt"
    p.write_bytes(b"hello world")
    h = compute_content_hash(p)
    # sha256("hello world")
    assert h == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"


def test_compute_content_hash_missing_file(tmp_path):
    assert compute_content_hash(tmp_path / "nope.txt") == ""


def test_compute_content_hash_accepts_str_path(tmp_path):
    p = tmp_path / "f.txt"
    p.write_bytes(b"abc")
    h_str = compute_content_hash(str(p))
    h_path = compute_content_hash(p)
    assert h_str == h_path


# ---------- Registry ----------

def _artifact(artifact_id, kind, path):
    return Artifact(artifact_id=artifact_id, kind=kind, path=path)


def test_registry_add_and_get():
    r = Registry()
    a = _artifact("a1", "image", Path("/tmp/x.jpg"))
    r.add(a)
    assert r.get("a1") == a
    assert r.exists("a1")


def test_registry_by_kind_iter():
    r = Registry()
    r.add(_artifact("a1", "image", Path("/tmp/a.jpg")))
    r.add(_artifact("a2", "video", Path("/tmp/b.mp4")))
    r.add(_artifact("a3", "image", Path("/tmp/c.jpg")))
    images = r.by_kind_iter("image")
    assert len(images) == 2
    assert {a.artifact_id for a in images} == {"a1", "a3"}


def test_registry_to_from_dict_roundtrip():
    r = Registry()
    r.add(_artifact("a1", "image", Path("/tmp/a.jpg")))
    r.add(_artifact("a2", "video", Path("/tmp/b.mp4")))
    d = r.to_dict()
    r2 = Registry.from_dict(d)
    assert {a.artifact_id for a in r2.artifacts.values()} == {"a1", "a2"}
    assert r2.by_kind == r.by_kind


def test_registry_save_load_json_roundtrip(tmp_path):
    r = Registry()
    r.add(_artifact("a1", "image", Path("/tmp/a.jpg")))
    p = tmp_path / "registry.json"
    r.save_json(p)
    assert p.exists()
    r2 = Registry.load_json(p)
    assert {a.artifact_id for a in r2.artifacts.values()} == {"a1"}


def test_registry_load_json_or_empty_missing(tmp_path):
    r = Registry.load_json_or_empty(tmp_path / "nope.json")
    assert len(r.artifacts) == 0


def test_registry_save_json_creates_parent_dirs(tmp_path):
    """registry.json 写到不存在的子目录应自动 mkdir。"""
    r = Registry()
    p = tmp_path / "subdir1" / "subdir2" / "registry.json"
    r.save_json(p)
    assert p.exists()


# ---------- RetryPolicy + with_retry ----------

def test_retry_policy_success_first_attempt():
    p = RetryPolicy(max_attempts=3, backoff_seconds=0.01)
    attempts = 0
    def ok():
        nonlocal attempts
        attempts += 1
        return "result"
    with with_retry(ok, p) as result:
        pass
    assert attempts == 1


def test_retry_policy_retry_then_succeed():
    p = RetryPolicy(max_attempts=3, backoff_seconds=0.01)
    attempts = 0
    def flaky():
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            raise TimeoutError("blip")
        return "ok"
    with with_retry(flaky, p) as result:
        pass
    assert attempts == 2


def test_retry_policy_give_up_after_max():
    p = RetryPolicy(max_attempts=2, backoff_seconds=0.01)
    def always_fail():
        raise TimeoutError("nope")
    with pytest.raises(TimeoutError):
        with with_retry(always_fail, p):
            pass


def test_retry_policy_does_not_retry_non_retry_on():
    p = RetryPolicy(max_attempts=5, backoff_seconds=0.01)
    def value_error():
        raise ValueError("not transient")
    with pytest.raises(ValueError):
        with with_retry(value_error, p):
            pass


def test_retry_policy_backoff_grows():
    p = RetryPolicy(max_attempts=4, backoff_seconds=1.0)
    assert p.compute_delay(1) == 1.0
    assert p.compute_delay(2) == 2.0
    assert p.compute_delay(3) == 4.0


# ---------- StructuredLogger ----------

def test_log_event_roundtrip():
    ev = LogEvent(event_type="phase_started", timestamp="2026-09-28T00:00:00",
                   payload={"name": "image_gen"})
    d = ev.to_dict()
    ev2 = LogEvent.from_dict(d)
    assert ev2.event_type == ev.event_type
    assert ev2.timestamp == ev.timestamp
    assert ev2.payload == ev.payload


def test_logger_event_appends_to_jsonl(tmp_path):
    log = StructuredLogger(tmp_path / "log.jsonl")
    log.event("phase_started", name="prep")
    log.event("agent_completed", agent="image_gen")
    lines = (tmp_path / "log.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    events = [LogEvent.from_dict(json.loads(l)) for l in lines]
    assert events[0].event_type == "phase_started"
    assert events[0].payload["name"] == "prep"
    assert events[1].event_type == "agent_completed"


def test_logger_event_returns_event_object():
    log = StructuredLogger()
    ev = log.event("test", x=1)
    assert isinstance(ev, LogEvent)
    assert ev.event_type == "test"
    assert ev.payload["x"] == 1
    assert ev.timestamp != ""  # auto-filled


def test_logger_event_creates_parent_dir(tmp_path):
    log = StructuredLogger(tmp_path / "sub" / "log.jsonl")
    log.event("test")
    assert (tmp_path / "sub" / "log.jsonl").exists()


def test_logger_tail_returns_recent_events(tmp_path):
    log = StructuredLogger(tmp_path / "log.jsonl")
    for i in range(5):
        log.event("test", i=i)
    events = log.tail(n=3)
    assert len(events) == 3
    # tail 返回最后 3 个（i=2, 3, 4）
    assert [e.payload["i"] for e in events] == [2, 3, 4]


def test_logger_tail_missing_file():
    log = StructuredLogger()
    assert log.tail() == []


def test_logger_tail_corrupt_line_skipped(tmp_path):
    log = StructuredLogger(tmp_path / "log.jsonl")
    (tmp_path / "log.jsonl").write_text(
        '{"event_type":"ok","timestamp":"","payload":{}}\n'
        'THIS IS NOT JSON\n'
        '{"event_type":"ok","timestamp":"","payload":{}}\n',
        encoding="utf-8",
    )
    events = log.tail(n=10)
    assert len(events) == 2


def test_logger_format_for_stderr_compact(tmp_path):
    log = StructuredLogger(tmp_path / "log.jsonl")
    log.event("phase_started", name="prep")
    log.event("agent_completed", agent="image_gen", shots=5)
    out = log.format_for_stderr()
    assert "phase_started" in out
    assert "name=prep" in out
    assert "agent_completed" in out
    assert "shots=5" in out
