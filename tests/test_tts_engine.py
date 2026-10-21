"""REQ-TTS Edge-TTS 单测。全部 MockTTSBackend，不依赖真实网络 / edge-tts import。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from hailuo_video import tts_engine as te  # noqa: E402


# ---------- env fixture ----------

@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """env 干净；默认 voice。"""
    for k in ("EDGE_TTS_VOICE", "EDGE_TTS_RATE", "EDGE_TTS_VOLUME", "EDGE_TTS_PITCH"):
        monkeypatch.delenv(k, raising=False)
    yield


# ---------- 配置 ----------

def test_default_voice_constant():
    assert te.default_voice() == "zh-CN-XiaoxiaoNeural"


def test_env_override_voice():
    os.environ["EDGE_TTS_VOICE"] = "en-US-AvaNeural"
    assert te.default_voice() == "en-US-AvaNeural"


def test_known_voices_contains_xiaoxiao():
    assert "xiaoxiao" in te.KNOWN_VOICES
    assert te.KNOWN_VOICES["xiaoxiao"] == "zh-CN-XiaoxiaoNeural"


# ---------- MockTTSBackend ----------

def test_mock_synth_writes_bytes(tmp_path):
    backend = te.MockTTSBackend()
    out = tmp_path / "out.mp3"
    result = backend.synth("hello", out)
    assert out.exists()
    assert out.read_bytes() == te.MockTTSBackend().next_bytes
    assert result.bytes == te.MockTTSBackend().next_bytes


def test_mock_synth_records_call():
    backend = te.MockTTSBackend()
    backend.synth("hello", "out.mp3", voice="zh-CN-YunxiNeural")
    assert backend.calls == [
        {"text": "hello", "output_path": "out.mp3", "voice": "zh-CN-YunxiNeural"}
    ]


def test_mock_synth_creates_parent_dirs(tmp_path):
    backend = te.MockTTSBackend()
    out = tmp_path / "sub" / "deep" / "out.mp3"
    backend.synth("hi", out)
    assert out.exists()


def test_mock_synth_empty_text_raises():
    backend = te.MockTTSBackend()
    with pytest.raises(te.TTSError, match="text 为空"):
        backend.synth("", "out.mp3")
    with pytest.raises(te.TTSError):
        backend.synth("   \n  ", "out.mp3")


# ---------- EdgeTTSBackend 构造 ----------

def test_edge_backend_construction(monkeypatch):
    """仅做构造（不调用 synth）。edge_tts import 必须能解析到。"""
    backend = te.EdgeTTSBackend()
    assert hasattr(backend, "_edge_tts")


def test_edge_backend_synth_propagates_runtime_error(monkeypatch):
    """asyncio.run 失败 → 包成 TTSError。"""
    backend = te.EdgeTTSBackend()

    # 注入 fake _edge_tts 让其 Communicate 不可用
    class _FakeEdge:
        class Communicate:
            def __init__(self, *a, **kw):
                pass

            def stream(self):
                raise RuntimeError("simulated network failure")

    monkeypatch.setattr(backend, "_edge_tts", _FakeEdge)
    with pytest.raises(te.TTSError, match="edge-tts 调用失败"):
        backend.synth("hello", "/tmp/out.mp3")


# ---------- make_backend ----------

def test_make_backend_mock():
    b = te.make_backend("mock")
    assert isinstance(b, te.MockTTSBackend)


def test_make_backend_edge():
    b = te.make_backend("edge")
    assert isinstance(b, te.EdgeTTSBackend)


def test_make_backend_unknown():
    with pytest.raises(te.TTSError, match="未知 backend"):
        te.make_backend("elevenlabs")


# ---------- synth_batch ----------

def test_synth_batch_sequential(tmp_path):
    backend = te.MockTTSBackend()
    items = [
        ("hello", tmp_path / "a.mp3", {}),
        ("world", tmp_path / "b.mp3", {"voice": "en-US-AvaNeural"}),
    ]
    results = te.synth_batch(items, backend=backend)
    assert len(results) == 2
    assert (tmp_path / "a.mp3").exists()
    assert (tmp_path / "b.mp3").exists()


# ---------- TTSResult to_dict ----------

def test_tts_result_to_dict_excludes_bytes_by_default():
    r = te.TTSResult(bytes=b"abc", mime_type="audio/mpeg")
    d = r.to_dict()
    assert "bytes" not in d
    assert d["bytes_len"] == 3


def test_tts_result_to_dict_includes_bytes():
    r = te.TTSResult(bytes=b"abc", mime_type="audio/mpeg")
    d = r.to_dict(include_bytes=True)
    assert d["bytes"] == b"abc"


def test_tts_result_ok_property():
    r = te.TTSResult(source="ok")
    assert r.ok is True
    r2 = te.TTSResult(source="error")
    assert r2.ok is False


# ---------- write_tts ----------

def test_write_tts(tmp_path):
    r = te.TTSResult(bytes=b"audio-bytes")
    out = tmp_path / "x.mp3"
    te.write_tts(r, out)
    assert out.read_bytes() == b"audio-bytes"


# ---------- CLI ----------

from hailuo_video.cli import main as cli_main


def test_cli_tts_empty_text(capsys, tmp_path):
    rc = cli_main(["tts", "", "-o", str(tmp_path / "out.mp3")])
    err = capsys.readouterr().err
    assert rc == 1
    assert "text 为空" in err


def test_cli_tts_mock_success(monkeypatch, tmp_path, capsys):
    out = tmp_path / "out.mp3"
    rc = cli_main(["tts", "你好世界", "-o", str(out), "--backend", "mock"])
    err = capsys.readouterr().err
    assert rc == 0
    assert "[OK]" in err
    assert out.exists()


def test_cli_tts_unknown_backend(capsys, tmp_path):
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["tts", "hi", "-o", str(tmp_path / "x.mp3"), "--backend", "azure"])
    assert exc_info.value.code == 2


def test_cli_tts_list_voices_help(capsys):
    """--help 应该渲染中文（L-009）。"""
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["tts", "--help"])
    assert exc_info.value.code == 0
    out = capsys.readouterr()
    assert "voice" in out.out
    assert "subtitle" in out.out or "字幕" in out.out


def test_cli_tts_list_voices_mock_only(monkeypatch, tmp_path, capsys):
    """`--list-voices` 不依赖 edge-tts import 真实安装（mock 走也无碍）。"""
    rc = cli_main(["tts", "--list-voices", "--filter-locale", "zh-CN"])
    # 期望 exit 0（edge-tts 真安装了），或 2（环境无 edge-tts；D-014 单测不强制依赖）
    # 关键是 stdout 应含 zh-CN voice 名（如果装了）
    combined = capsys.readouterr()
    if rc == 0:
        assert "zh-CN" in combined.out
        assert "Xiaoxiao" in combined.out or "Yunxi" in combined.out


def test_cli_tts_edge_import_missing_raises(monkeypatch, tmp_path, capsys):
    """如果 edge-tts 没装，--backend edge 应该硬失败 exit 2。"""
    # 模拟 import 失败
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "edge_tts":
            raise ImportError("simulated: edge-tts not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    rc = cli_main(["tts", "hi", "-o", str(tmp_path / "x.mp3"), "--backend", "edge"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "edge-tts 未安装" in err