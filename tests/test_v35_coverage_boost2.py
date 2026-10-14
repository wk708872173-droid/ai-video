"""V3.5 P4-b：更多 tts + cli 覆盖率补测。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
for p in (SANDBOX, SCRIPTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


# ---------- tts_engine coverage (53% → 80%+) ----------

def test_tts_result_ok_property():
    """TTSResult.ok 在 source='ok' 时 True，否则 False。"""
    from hailuo_video.tts_engine import TTSResult

    ok = TTSResult(source="ok", bytes=b"x", mime_type="audio/mp3")
    assert ok.ok is True

    bad = TTSResult(source="error", bytes=b"", mime_type="audio/mp3")
    assert bad.ok is False


def test_tts_result_to_dict_strips_bytes_by_default():
    """to_dict() 默认不包含 bytes 字段（节省日志空间）。"""
    from hailuo_video.tts_engine import TTSResult

    r = TTSResult(source="ok", bytes=b"x" * 100, mime_type="audio/mp3")
    d = r.to_dict()
    assert "bytes" not in d
    assert d["bytes_len"] == 100


def test_tts_result_to_dict_includes_bytes_when_requested():
    """to_dict(include_bytes=True) 包含 bytes。"""
    from hailuo_video.tts_engine import TTSResult

    r = TTSResult(source="ok", bytes=b"audio_data", mime_type="audio/mp3")
    d = r.to_dict(include_bytes=True)
    assert d["bytes"] == b"audio_data"


def test_mock_tts_backend_synth_writes_audio(tmp_path):
    """MockTTSBackend.synth 写一个 mp3 文件（懒生成 0.5s 静音）。"""
    from hailuo_video.tts_engine import MockTTSBackend

    backend = MockTTSBackend()
    out = tmp_path / "test.mp3"
    result = backend.synth("hello world", out)
    assert out.is_file()
    assert out.stat().st_size > 0
    # mp3 文件通常以 "ID3" 或 0xFF 0xFB 开头
    with out.open("rb") as f:
        head = f.read(3)
        assert head == b"ID3" or head[:2] == b"\xff\xfb"
    assert result.source == "ok"
    assert result.mime_type == "audio/mpeg"


def test_mock_tts_backend_synth_raises_on_empty_text():
    """MockTTSBackend.synth 对空 text 抛 TTSError。"""
    from hailuo_video.tts_engine import MockTTSBackend, TTSError

    backend = MockTTSBackend()
    with pytest.raises(TTSError, match="text 为空"):
        backend.synth("", "/tmp/out.mp3")
    with pytest.raises(TTSError):
        backend.synth("   ", "/tmp/out.mp3")


def test_make_backend_returns_mock_for_explicit_mock():
    """make_backend('mock') 返 MockTTSBackend。"""
    from hailuo_video import tts_engine

    backend = tts_engine.make_backend("mock")
    assert isinstance(backend, tts_engine.MockTTSBackend)


def test_make_backend_returns_edge_for_auto():
    """make_backend() 默认 'auto' 返 EdgeTTSBackend（需 edge-tts）。"""
    from hailuo_video import tts_engine

    backend = tts_engine.make_backend("auto")
    assert isinstance(backend, tts_engine.EdgeTTSBackend)


def test_make_backend_returns_edge_for_explicit_edge():
    from hailuo_video import tts_engine

    backend = tts_engine.make_backend("edge")
    assert isinstance(backend, tts_engine.EdgeTTSBackend)


def test_make_backend_raises_for_unknown():
    from hailuo_video import tts_engine

    with pytest.raises(tts_engine.TTSError, match="未知 backend"):
        tts_engine.make_backend("nonexistent_backend")


# ---------- cli.py main() entry + doctor path ----------

def test_cli_doctor_runs_without_crash(monkeypatch):
    """cli doctor() 不 crash（即使 configured=False 因 torch 缺），返回 None/0/1 都 OK。"""
    import sys as _sys
    from hailuo_video import cli

    saved_argv = _sys.argv[:]
    _sys.argv = ["hailuo_video", "doctor"]
    try:
        rc = cli.main()
        # doctor 不返回 0（configured=False 时 sys.exit(2)）— 主测：能跑完
        assert rc in (None, 0, 1, 2)
    finally:
        _sys.argv = saved_argv


def test_cli_status_runs():
    """cli status() 路径覆盖（不验证返回值）。"""
    import sys as _sys
    from hailuo_video import cli

    saved_argv = _sys.argv[:]
    _sys.argv = ["hailuo_video", "status"]
    try:
        cli.main()  # 期望不 crash；返回码可能是 0/1 都 OK
    finally:
        _sys.argv = saved_argv


# ---------- ProjectContext additional ----------

def test_project_context_env_overrides_default_construction():
    """ProjectContext.__init__ 时 env_overrides 默认 {}。"""
    from hailuo_video.project import ProjectContext

    ctx = ProjectContext(
        project_id="test1234",
        project_name="t",
        storyboard_path=Path("/tmp/x.json"),
        storyboard_hash="x" * 64,
        assets_dir=Path("/tmp/a"),
        run_dir=Path("/tmp/r"),
        output_dir=Path("/tmp/o"),
        log_path=Path("/tmp/l"),
    )
    assert ctx.env_overrides == {}


def test_project_context_rejects_short_project_id():
    """project_id 太短 (<4 字符) → ProjectContextError。"""
    from hailuo_video.project import ProjectContext, ProjectContextError

    with pytest.raises(ProjectContextError, match="project_id"):
        ProjectContext(
            project_id="ab",
            project_name="t",
            storyboard_path=Path("/tmp/x.json"),
            storyboard_hash="x" * 64,
            assets_dir=Path("/tmp/a"),
            run_dir=Path("/tmp/r"),
            output_dir=Path("/tmp/o"),
            log_path=Path("/tmp/l"),
        )


# ---------- asset_prep _safe_style_anchor variants ----------

def test_safe_style_anchor_already_has_guard_does_not_duplicate():
    """已含 'no illustration' 守卫的 style_anchor 不重复追加。"""
    from hailuo_video.asset_prep import _safe_style_anchor

    anchor = "photorealistic, no illustration"
    result = _safe_style_anchor(anchor)
    # 仅出现 1 次
    assert result.count("no illustration") == 1


def test_safe_style_anchor_handles_whitespace_only():
    """仅空格的 style_anchor → 兜底。"""
    from hailuo_video.asset_prep import _safe_style_anchor, PHOTOREALISTIC_FALLBACK

    assert _safe_style_anchor("   ") == PHOTOREALISTIC_FALLBACK


# ---------- image_engine additional ----------

def test_image_engine_aspect_ratio_helper_default():
    """aspect_ratio() 默认值是 16:9（VALID_ASPECT_RATIOS 内）。"""
    import os
    os.environ.pop("MINIMAX_IMAGE_ASPECT_RATIO", None)
    from hailuo_video.image_engine import aspect_ratio, VALID_ASPECT_RATIOS

    ar = aspect_ratio()
    assert ar in VALID_ASPECT_RATIOS
    assert ar == "16:9"  # default


def test_image_engine_image_extension_mappings():
    """image_extension() mime → 扩展名。"""
    from hailuo_video.image_engine import image_extension

    assert image_extension("image/jpeg") == ".jpg"
    assert image_extension("image/png") == ".png"
    assert image_extension("image/webp") == ".webp"
    assert image_extension("unknown") == ".img"


# ---------- lib_ffmpeg additional ----------

def test_lib_ffmpeg_lut_recipes_have_three_tuples():
    """LUT_RECIPES 每条是 (b, sat, gamma) 3-tuple。"""
    import lib_ffmpeg

    for sid, lut in lib_ffmpeg.LUT_RECIPES.items():
        assert len(lut) == 3
        assert all(isinstance(v, (int, float)) for v in lut)