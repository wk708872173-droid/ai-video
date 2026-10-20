"""concat_engine 专项测试。

补 V3.5 P5 覆盖率缺口：merge_clip / concat_videos / burn_subtitles /
concat_pipeline / manifest IO / ffmpeg 探测，全部 mock `_runner`，
不依赖真 ffmpeg 二进制、不产出真视频。

覆盖目标（删 v31_compat.py 后 concat_engine 仅 63%）：
  - _resolve_ffmpeg_exe / ffmpeg_version / is_available 三条探测分支
  - merge_clip 成功 + rc!=0 抛 ConcatError
  - concat_videos 空列表守卫 + 正常路径 + 失败路径
  - burn_subtitles Windows 反斜杠转义 + 失败抛错
  - concat_pipeline 5 条分支（空 shots / 缺 video / 有 audio / 烧字幕 / 异常兜底）
  - make_concat_result_from_manifest 非 dict 项跳过
  - write_concat_manifest 往返
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SANDBOX = REPO / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from hailuo_video import concat_engine as ce  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_runner():
    """每个用例后恢复真 runner，避免污染其他测试文件。"""
    yield
    ce.set_runner(None)


class _Recorder:
    """记录 ffmpeg 调用并可指定返回码。"""

    def __init__(self, rc: int = 0, stderr: str = ""):
        self.calls: list[tuple[list[str], int]] = []
        self.rc = rc
        self.stderr = stderr

    def __call__(self, args, timeout):
        self.calls.append((list(args), timeout))
        # 模拟 ffmpeg 落盘输出文件（concat_pipeline 靠 exists()/stat() 读大小）
        out = args[-1]
        if isinstance(out, str) and not out.startswith("-"):
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_bytes(b"\x00" * 2048)
        return self.rc, "", self.stderr

    @property
    def last_args(self) -> list[str]:
        return self.calls[-1][0]


# ---------- ffmpeg 探测 ----------

def test_resolve_ffmpeg_exe_prefers_imageio(monkeypatch):
    """优先 imageio-ffmpeg 自带二进制。"""
    import types

    fake = types.ModuleType("imageio_ffmpeg")
    fake.get_ffmpeg_exe = lambda: "C:/fake/ffmpeg.exe"
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", fake)
    assert ce._resolve_ffmpeg_exe() == "C:/fake/ffmpeg.exe"


def test_resolve_ffmpeg_exe_falls_back_to_path(monkeypatch):
    """imageio-ffmpeg 缺失时回落 shutil.which。"""
    import builtins

    real_import = builtins.__import__

    def _no_imageio(name, *a, **k):
        if name == "imageio_ffmpeg":
            raise ImportError("no imageio_ffmpeg")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_imageio)
    monkeypatch.setattr(ce.shutil, "which", lambda _n: "C:/system/ffmpeg.exe")
    assert ce._resolve_ffmpeg_exe() == "C:/system/ffmpeg.exe"


def test_resolve_ffmpeg_exe_raises_when_absent(monkeypatch):
    """两边都没有时抛 ConcatError（带 pip 安装提示）。"""
    import builtins

    real_import = builtins.__import__

    def _no_imageio(name, *a, **k):
        if name == "imageio_ffmpeg":
            raise ImportError("no imageio_ffmpeg")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_imageio)
    monkeypatch.setattr(ce.shutil, "which", lambda _n: None)
    with pytest.raises(ce.ConcatError) as ei:
        ce._resolve_ffmpeg_exe()
    assert "pip install imageio-ffmpeg" in str(ei.value)


def test_ffmpeg_version_returns_first_line(monkeypatch):
    class _P:
        returncode = 0
        stdout = "ffmpeg version 7.1-essentials\nbuilt with gcc\n"
        stderr = ""

    monkeypatch.setattr(ce.subprocess, "run", lambda *a, **k: _P())
    assert ce.ffmpeg_version() == "ffmpeg version 7.1-essentials"


def test_ffmpeg_version_raises_on_nonzero_rc(monkeypatch):
    class _P:
        returncode = 1
        stdout = ""
        stderr = "no such binary"

    monkeypatch.setattr(ce.subprocess, "run", lambda *a, **k: _P())
    with pytest.raises(ce.ConcatError) as ei:
        ce.ffmpeg_version()
    assert "ffmpeg -version 失败" in str(ei.value)


def test_ffmpeg_version_wraps_oserror(monkeypatch):
    def boom(*a, **k):
        raise OSError("exec format error")

    monkeypatch.setattr(ce.subprocess, "run", boom)
    with pytest.raises(ce.ConcatError) as ei:
        ce.ffmpeg_version()
    assert "ffmpeg 调用失败" in str(ei.value)


def test_is_available_true_when_resolvable(monkeypatch):
    monkeypatch.setattr(ce, "_resolve_ffmpeg_exe", lambda: "C:/fake/ffmpeg.exe")
    assert ce.is_available() is True


def test_is_available_false_when_unresolvable(monkeypatch):
    def _raise():
        raise ce.ConcatError("no ffmpeg")

    monkeypatch.setattr(ce, "_resolve_ffmpeg_exe", _raise)
    assert ce.is_available() is False


# ---------- merge_clip ----------

def test_merge_clip_builds_expected_args(tmp_path):
    rec = _Recorder()
    ce.set_runner(rec)
    out = tmp_path / "merged.mp4"
    ce.merge_clip("v.mp4", "a.mp3", out)
    args = rec.last_args
    assert "-shortest" in args
    assert "libx264" in args and "aac" in args
    assert args[-1] == str(out)
    assert out.exists()


def test_merge_clip_extra_args_appended(tmp_path):
    rec = _Recorder()
    ce.set_runner(rec)
    ce.merge_clip("v.mp4", "a.mp3", tmp_path / "m.mp4", extra_args=["-vf", "scale=2"])
    assert rec.last_args[-3:] == ["-vf", "scale=2", str(tmp_path / "m.mp4")]


def test_merge_clip_raises_on_nonzero_rc(tmp_path):
    ce.set_runner(_Recorder(rc=1, stderr="invalid data"))
    with pytest.raises(ce.ConcatError) as ei:
        ce.merge_clip("v.mp4", "a.mp3", tmp_path / "m.mp4")
    assert "merge_clip 失败 rc=1" in str(ei.value)


# ---------- concat_videos ----------

def test_concat_videos_empty_list_raises(tmp_path):
    with pytest.raises(ce.ConcatError):
        ce.concat_videos([], tmp_path / "out.mp4")


def test_concat_videos_writes_concat_list(tmp_path):
    rec = _Recorder()
    ce.set_runner(rec)
    clips = [tmp_path / f"{i}.mp4" for i in range(3)]
    for c in clips:
        c.write_bytes(b"x")
    ce.concat_videos([str(c) for c in clips], tmp_path / "out.mp4")
    args = rec.last_args
    assert "-f" in args and "concat" in args
    assert "-c" in args and "copy" in args


def test_concat_videos_raises_on_nonzero_rc(tmp_path):
    clips = [tmp_path / "a.mp4"]
    clips[0].write_bytes(b"x")
    ce.set_runner(_Recorder(rc=2, stderr="moov atom not found"))
    with pytest.raises(ce.ConcatError) as ei:
        ce.concat_videos([str(c) for c in clips], tmp_path / "out.mp4")
    assert "concat_videos 失败 rc=2" in str(ei.value)


# ---------- burn_subtitles ----------

def test_burn_subtitles_escapes_windows_path(tmp_path):
    rec = _Recorder()
    ce.set_runner(rec)
    # 用带盘符的路径触发 : 与 \ 转义分支
    sub = tmp_path / "s.srt"
    sub.write_text("1\n", encoding="utf-8")
    ce.burn_subtitles(tmp_path / "v.mp4", sub, tmp_path / "b.mp4")
    vf = rec.last_args[rec.last_args.index("-vf") + 1]
    assert "\\:" in vf          # 冒号被转义
    assert "\\\\" not in vf     # 反斜杠已换成正斜杠


def test_burn_subtitles_extra_args(tmp_path):
    rec = _Recorder()
    ce.set_runner(rec)
    sub = tmp_path / "s.srt"
    sub.write_text("1\n", encoding="utf-8")
    ce.burn_subtitles(tmp_path / "v.mp4", sub, tmp_path / "b.mp4", extra_args=["-crf", "20"])
    assert "20" in rec.last_args


def test_burn_subtitles_raises_on_nonzero_rc(tmp_path):
    ce.set_runner(_Recorder(rc=1, stderr="no such filter"))
    with pytest.raises(ce.ConcatError) as ei:
        ce.burn_subtitles(tmp_path / "v.mp4", tmp_path / "s.srt", tmp_path / "b.mp4")
    assert "burn_subtitles 失败 rc=1" in str(ei.value)


# ---------- concat_pipeline ----------

def test_concat_pipeline_empty_shots_raises(tmp_path):
    with pytest.raises(ce.ConcatError):
        ce.concat_pipeline([], tmp_path / "final.mp4")


def test_concat_pipeline_missing_video_field(tmp_path):
    rec = _Recorder()
    ce.set_runner(rec)
    items = [ce.ShotItem(index=0, video=None)]
    res = ce.concat_pipeline(items, tmp_path / "final.mp4")
    assert res.source == "error"
    assert "缺 video 路径" in (res.error or "")


def test_concat_pipeline_nonexistent_video(tmp_path):
    rec = _Recorder()
    ce.set_runner(rec)
    items = [ce.ShotItem(index=0, video=str(tmp_path / "nope.mp4"))]
    res = ce.concat_pipeline(items, tmp_path / "final.mp4")
    assert res.source == "error"
    assert "video 不存在" in (res.error or "")


def test_concat_pipeline_video_only_ok(tmp_path):
    rec = _Recorder()
    ce.set_runner(rec)
    v = tmp_path / "v0.mp4"
    v.write_bytes(b"x")
    items = [ce.ShotItem(index=0, video=str(v))]
    res = ce.concat_pipeline(items, tmp_path / "final.mp4")
    assert res.source == "ok"
    assert res.shot_count == 1
    assert res.bytes > 0
    assert "[concat] concat 1 clips" in res.ffmpeg_log


def test_concat_pipeline_with_audio_triggers_merge(tmp_path):
    rec = _Recorder()
    ce.set_runner(rec)
    v = tmp_path / "v0.mp4"
    a = tmp_path / "a0.mp3"
    v.write_bytes(b"x")
    a.write_bytes(b"x")
    items = [ce.ShotItem(index=0, video=str(v), audio=str(a))]
    res = ce.concat_pipeline(items, tmp_path / "final.mp4")
    assert res.source == "ok"
    assert "[concat] merge" in res.ffmpeg_log


def test_concat_pipeline_burns_subtitles(tmp_path):
    rec = _Recorder()
    ce.set_runner(rec)
    v = tmp_path / "v0.mp4"
    s = tmp_path / "s0.srt"
    v.write_bytes(b"x")
    s.write_text("1\n", encoding="utf-8")
    items = [ce.ShotItem(index=0, video=str(v), subtitle=str(s))]
    res = ce.concat_pipeline(items, tmp_path / "final.mp4")
    assert res.source == "ok"
    assert "[concat] burn subs" in res.ffmpeg_log


def test_concat_pipeline_skip_burn_when_disabled(tmp_path):
    rec = _Recorder()
    ce.set_runner(rec)
    v = tmp_path / "v0.mp4"
    s = tmp_path / "s0.srt"
    v.write_bytes(b"x")
    s.write_text("1\n", encoding="utf-8")
    items = [ce.ShotItem(index=0, video=str(v), subtitle=str(s))]
    res = ce.concat_pipeline(items, tmp_path / "final.mp4", burn_subs=False)
    assert res.source == "ok"
    assert "[concat] burn subs" not in res.ffmpeg_log


def test_concat_pipeline_audio_without_video_returns_error(tmp_path):
    rec = _Recorder()
    ce.set_runner(rec)
    a = tmp_path / "a0.mp3"
    a.write_bytes(b"x")
    items = [ce.ShotItem(index=0, video=None, audio=str(a))]
    res = ce.concat_pipeline(items, tmp_path / "final.mp4")
    assert res.source == "error"


def test_concat_pipeline_keep_intermediate_true(tmp_path):
    rec = _Recorder()
    ce.set_runner(rec)
    v = tmp_path / "v0.mp4"
    v.write_bytes(b"x")
    items = [ce.ShotItem(index=0, video=str(v))]
    res = ce.concat_pipeline(items, tmp_path / "final.mp4", keep_intermediate=True)
    assert res.extra["intermediate_dir"] != "(deleted)"
    assert Path(res.extra["intermediate_dir"]).exists()


def test_concat_pipeline_cleans_intermediate_by_default(tmp_path):
    rec = _Recorder()
    ce.set_runner(rec)
    v = tmp_path / "v0.mp4"
    v.write_bytes(b"x")
    items = [ce.ShotItem(index=0, video=str(v))]
    res = ce.concat_pipeline(items, tmp_path / "final.mp4")
    assert res.extra["intermediate_dir"] == "(deleted)"


def test_concat_pipeline_extra_args_propagate(tmp_path):
    rec = _Recorder()
    ce.set_runner(rec)
    v = tmp_path / "v0.mp4"
    v.write_bytes(b"x")
    items = [ce.ShotItem(index=0, video=str(v))]
    ce.concat_pipeline(items, tmp_path / "final.mp4", extra_args=["-preset", "fast"])
    assert "fast" in " ".join(rec.last_args)


# ---------- manifest IO ----------

def test_make_concat_result_from_manifest_skips_non_dict(tmp_path):
    mf = tmp_path / "manifest.json"
    mf.write_text(json.dumps({
        "shots": [
            {"video": "a.mp4", "audio": "a.mp3", "subtitle": "a.srt"},
            "not-a-dict",           # 应被跳过
            {"video": "b.mp4"},
        ]
    }), encoding="utf-8")
    items = ce.make_concat_result_from_manifest(mf)
    assert len(items) == 2
    assert items[0].video == "a.mp4"
    assert items[1].audio is None


def test_make_concat_result_from_manifest_missing_shots_key(tmp_path):
    mf = tmp_path / "manifest.json"
    mf.write_text(json.dumps({"other": 1}), encoding="utf-8")
    assert ce.make_concat_result_from_manifest(mf) == []


def test_write_concat_manifest_roundtrip(tmp_path):
    items = [
        ce.ShotItem(index=0, video="a.mp4", audio="a.mp3"),
        ce.ShotItem(index=1, video="b.mp4"),
    ]
    mf = tmp_path / "out" / "manifest.json"
    ce.write_concat_manifest(items, mf)
    assert mf.exists()
    back = ce.make_concat_result_from_manifest(mf)
    assert [i.video for i in back] == ["a.mp4", "b.mp4"]


def test_write_concat_manifest_creates_parent_dir(tmp_path):
    target = tmp_path / "deep" / "nested" / "m.json"
    ce.write_concat_manifest([], target)
    assert target.exists()


# ---------- ShotItem / ConcatResult dataclass ----------

def test_shotitem_to_dict_keys():
    d = ce.ShotItem(index=3, video="v.mp4").to_dict()
    assert d["index"] == 3
    assert d["video"] == "v.mp4"
    assert set(d) >= {"index", "video", "audio", "subtitle"}


def test_concat_result_defaults():
    r = ce.ConcatResult(output="x.mp4", shot_count=2)
    assert r.source == "ok"
    assert r.bytes == 0
    assert r.duration_seconds == 0.0


def test_default_runner_wraps_oserror(monkeypatch):
    """真 runner 遇 OSError 应转 ConcatError（不直接抛裸异常）。"""
    import subprocess as sp

    def boom(*a, **k):
        raise OSError("ffmpeg missing")

    monkeypatch.setattr(ce.subprocess, "run", boom)
    with pytest.raises(ce.ConcatError) as ei:
        ce._default_runner(["ffmpeg"], 10)
    assert "OSError" in str(ei.value)
    assert isinstance(ce.ConcatError("x"), RuntimeError)
    assert sp  # 保持 import 引用