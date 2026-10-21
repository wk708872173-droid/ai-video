"""V3.2 自动开发冲刺（轮 2-2）cli.py:cmd_editing 单测。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from hailuo_video.cli import build_parser, cmd_editing  # noqa: E402
from hailuo_video import editing as ed_mod  # noqa: E402  # V3.2 重构：editing_engine → editing 包


# ---------- 子命令注册 ----------

def test_editing_subparser_registered():
    """build_parser 必须注册 editing 子命令。"""
    p = build_parser()
    # argparse subparsers are accessed via _subparsers._actions[2] (the choices action)
    choices = None
    for action in p._actions:
        if hasattr(action, "choices") and isinstance(action.choices, dict):
            choices = action.choices
            break
    assert choices is not None
    assert "editing" in choices


def test_editing_subparser_required_mode():
    """--mode 是必填，且 choices 限定 5 种。"""
    p = build_parser()
    editing_p = None
    for action in p._actions:
        if hasattr(action, "choices") and isinstance(action.choices, dict):
            if "editing" in action.choices:
                editing_p = action.choices["editing"]
                break
    assert editing_p is not None, "editing subparser not registered"
    # 找 --mode action 并验证 choices
    mode_choices = None
    for a in editing_p._actions:
        if "--mode" in (a.option_strings or []):
            mode_choices = a.choices
            break
    assert mode_choices is not None
    assert set(mode_choices) == {"color_normalize", "crossfade", "motion_match", "apply_lut", "full"}


def test_editing_subparser_has_input_action_append():
    """--input 是 append action（支持多次传）。"""
    p = build_parser()
    for action in p._actions:
        if hasattr(action, "choices") and isinstance(action.choices, dict):
            if "editing" in action.choices:
                editing_p = action.choices["editing"]
                break
    found_input_append = False
    for a in editing_p._actions:
        if "--input" in (a.option_strings or []) and a.__class__.__name__ == "_AppendAction":
            found_input_append = True
            break
    assert found_input_append


# ---------- cmd_editing 行为（mocked editing_engine） ----------

def _make_args(**kw):
    """构造 argparse.Namespace-like 对象。"""
    from argparse import Namespace
    defaults = dict(
        mode="color_normalize",
        inputs=[],
        video=None,
        output=None,
        output_dir=None,
        lut=None,
        work_dir=None,
        transition="fade",
        overlap=0.25,
        sample_count=8,
        timeout=180,
    )
    defaults.update(kw)
    return Namespace(**defaults)


def test_cmd_editing_color_normalize_calls_pipeline(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        ed_mod, "color_normalize_pipeline",
        lambda video_paths, output_dir, **_kw: [
            ed_mod.EditingResult(output_path=str(Path(output_dir) / f"n_{i}.mp4"))
            for i in range(len(video_paths))
        ],
    )
    v1 = tmp_path / "a.mp4"
    v2 = tmp_path / "b.mp4"
    v1.write_bytes(b"x")
    v2.write_bytes(b"x")
    rc = cmd_editing(_make_args(
        mode="color_normalize",
        inputs=[str(v1), str(v2)],
        output_dir=str(tmp_path / "out"),
    ))
    assert rc == 0
    out = capsys.readouterr().out
    assert "[OK] color_normalize" in out
    assert "2 clips" in out


def test_cmd_editing_crossfade_calls_clips(tmp_path, monkeypatch, capsys):
    called = {"called": 0}

    def fake_crossfade(video_paths, output_path, **kw):
        called["called"] += 1
        Path(output_path).write_bytes(b"x")
        return ed_mod.CrossfadeResult(
            output_path=str(output_path),
            clip_count=len(video_paths),
            transition=kw.get("transition", "fade"),
            overlap_seconds=kw.get("overlap_seconds", 0.25),
        )

    monkeypatch.setattr(ed_mod, "crossfade_clips", fake_crossfade)
    v1 = tmp_path / "a.mp4"
    v2 = tmp_path / "b.mp4"
    v1.write_bytes(b"x")
    v2.write_bytes(b"x")
    rc = cmd_editing(_make_args(
        mode="crossfade",
        inputs=[str(v1), str(v2)],
        output=str(tmp_path / "out.mp4"),
    ))
    assert rc == 0
    assert called["called"] == 1
    out = capsys.readouterr().out
    assert "[OK] crossfade" in out


def test_cmd_editing_crossfade_requires_two_clips(tmp_path, capsys):
    v = tmp_path / "a.mp4"
    v.write_bytes(b"x")
    rc = cmd_editing(_make_args(
        mode="crossfade",
        inputs=[str(v)],
        output=str(tmp_path / "out.mp4"),
    ))
    assert rc == 2  # 参数错
    err = capsys.readouterr().err
    assert "至少需要 2 段" in err


def test_cmd_editing_motion_match_prints_peaks(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        ed_mod, "motion_match_cut_points",
        lambda video_paths, **kw: [
            ed_mod.MotionPeak(
                video_path=str(p),
                peak_time_seconds=2.5 + i,
                diff_score=0.3 + i * 0.1,
            )
            for i, p in enumerate(video_paths)
        ],
    )
    v = tmp_path / "a.mp4"
    v.write_bytes(b"x")
    rc = cmd_editing(_make_args(mode="motion_match", inputs=[str(v)]))
    assert rc == 0
    out = capsys.readouterr().out
    assert "[OK] motion_match" in out
    assert "1 peaks" in out
    assert "t=2.50s" in out


def test_cmd_editing_apply_lut_calls_apply(tmp_path, monkeypatch, capsys):
    called = {"called": 0}

    def fake_apply(video_path, output_path, lut_path, **kw):
        called["called"] += 1
        Path(output_path).write_bytes(b"x")
        return ed_mod.LUTResult(output_path=str(output_path), source_path=str(video_path), lut_path=str(lut_path))

    monkeypatch.setattr(ed_mod, "apply_lut", fake_apply)

    cube = tmp_path / "n.cube"
    ed_mod.write_neutral_cube_lut(cube)
    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")

    rc = cmd_editing(_make_args(
        mode="apply_lut",
        video=str(v),
        output=str(tmp_path / "out.mp4"),
        lut=str(cube),
    ))
    assert rc == 0
    assert called["called"] == 1
    out = capsys.readouterr().out
    assert "[OK] apply_lut" in out


def test_cmd_editing_apply_lut_missing_video_returns_2(capsys):
    rc = cmd_editing(_make_args(mode="apply_lut", video=None, output="o.mp4", lut="n.cube"))
    assert rc == 2
    err = capsys.readouterr().err
    assert "apply_lut 需要 --video" in err


def test_cmd_editing_apply_lut_missing_lut_file_returns_2(tmp_path, capsys):
    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    rc = cmd_editing(_make_args(
        mode="apply_lut", video=str(v), output="o.mp4",
        lut=str(tmp_path / "nonexistent.cube"),
    ))
    assert rc == 2
    err = capsys.readouterr().err
    assert "LUT 文件不存在" in err


def test_cmd_editing_full_runs_full_pipeline(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        ed_mod, "full_editing_pipeline",
        lambda video_paths, output_path, **kw: ed_mod.FullEditingResult(
            final_path=str(output_path),
            color_results=[ed_mod.EditingResult(output_path=str(p)) for p in video_paths],
            crossfade=ed_mod.CrossfadeResult(output_path="x", clip_count=2, transition="fade"),
            lut=None,
            motion_peaks=[],
        ),
    )
    v1 = tmp_path / "a.mp4"
    v2 = tmp_path / "b.mp4"
    v1.write_bytes(b"x")
    v2.write_bytes(b"x")
    rc = cmd_editing(_make_args(
        mode="full",
        inputs=[str(v1), str(v2)],
        output=str(tmp_path / "final.mp4"),
    ))
    assert rc == 0
    out = capsys.readouterr().out
    assert "[OK] full_editing_pipeline" in out
    assert "color_normalized: 2" in out


def test_cmd_editing_full_requires_output(tmp_path, capsys):
    v = tmp_path / "a.mp4"
    v.write_bytes(b"x")
    rc = cmd_editing(_make_args(mode="full", inputs=[str(v)], output=None))
    assert rc == 2
    err = capsys.readouterr().err
    assert "--output 必填" in err


def test_cmd_editing_unknown_mode_returns_2(capsys):
    rc = cmd_editing(_make_args(mode="bogus"))
    assert rc == 2


def test_cmd_editing_missing_input_file_returns_2(tmp_path, capsys):
    rc = cmd_editing(_make_args(
        mode="color_normalize",
        inputs=[str(tmp_path / "nonexistent.mp4")],
        output_dir=str(tmp_path / "out"),
    ))
    assert rc == 2
    err = capsys.readouterr().err
    assert "输入文件不存在" in err


def test_cmd_editing_color_normalize_requires_output_dir(tmp_path, capsys):
    """color_normalize 缺 --output-dir → rc=2。"""
    v = tmp_path / "a.mp4"
    v.write_bytes(b"x")  # 文件存在 → 跳过 input 检查
    rc = cmd_editing(_make_args(mode="color_normalize", inputs=[str(v)], output_dir=None))
    assert rc == 2
    err = capsys.readouterr().err
    assert "--output-dir 必填" in err


def test_cmd_editing_no_input_returns_2_for_non_apply_lut(capsys):
    rc = cmd_editing(_make_args(mode="motion_match", inputs=[]))
    assert rc == 2
    err = capsys.readouterr().err
    assert "至少需要 1 个 --input" in err


def test_cmd_editing_editing_error_returns_1(tmp_path, monkeypatch, capsys):
    def raise_error(*a, **kw):
        raise ed_mod.EditingError("ffmpeg failed")
    monkeypatch.setattr(ed_mod, "color_normalize_pipeline", raise_error)
    v = tmp_path / "a.mp4"
    v.write_bytes(b"x")
    rc = cmd_editing(_make_args(
        mode="color_normalize",
        inputs=[str(v)],
        output_dir=str(tmp_path / "out"),
    ))
    assert rc == 1
    err = capsys.readouterr().err
    assert "color_normalize" in err
    assert "ffmpeg failed" in err
