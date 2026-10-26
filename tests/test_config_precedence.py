"""V4.0 Batch 3 — BUG-D / BUG-E：配置遮蔽与参考图配额。

## BUG-D：`--voice` 硬编码默认值遮蔽 `EDGE_TTS_VOICE`

`cli.py` 的 `pipeline` 子命令把 `--voice` 默认值写成硬编码的
`"zh-CN-XiaoxiaoNeural"` → 即使用户在 `.env` 里配了 `EDGE_TTS_VOICE`，
pipeline 路径也**永远不会**用它。

（`tts` 子命令的 `--voice` 用的是 `default=None`，所以只有 pipeline 有这个坑 ——
与 V3.8 修的 `aspect_ratio` 是同一类问题：**硬编码默认值逐层遮蔽上游配置**。）

## BUG-E：场景参考图被参考图配额挤掉

`MAX_SUBJECT_REFERENCES = 4`（image-01 的硬上限）。旧 picker 顺序是
"每角色：角度 + 表情，然后场景" → **双角色镜头**就是 2×(角度+表情) = 4 个槽位占满
→ 场景参考图（背景 / 光线锚定）被挤掉。

而场景锚定正是 V3.8 W3 的跨镜连续性门禁要衡量的一致性来源 ——
**我们建了门禁，却把它的锚点扔了**。

R4：不打真 API。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from hailuo_video import asset_prep as ap  # noqa: E402
from hailuo_video.orchestrator import PipelineAgent, PipelineConfig  # noqa: E402
from hailuo_video.voice_cast import NARRATOR_VOICE  # noqa: E402


# ---------- BUG-D：voice 默认值链 ----------

def test_cli_pipeline_voice_defaults_to_none():
    """pipeline 子命令的 --voice 默认必须是 None（"未指定"）。"""
    from hailuo_video import cli

    parser = cli.build_parser()
    args = parser.parse_args(["pipeline", "sb.json", "-o", "out.mp4"])
    assert args.voice is None, "硬编码默认值会遮蔽 EDGE_TTS_VOICE"

    args2 = parser.parse_args(["pipeline", "sb.json", "-o", "out.mp4",
                               "--voice", "zh-CN-YunxiNeural"])
    assert args2.voice == "zh-CN-YunxiNeural"


def test_pipeline_config_voice_defaults_to_empty():
    """PipelineConfig.voice 默认 "" = 未指定（不是硬编码音色）。"""
    assert PipelineConfig().voice == ""


def test_run_pipeline_v3_voice_defaults_to_empty():
    import inspect
    from hailuo_video import pipeline as pl

    sig = inspect.signature(pl.run_pipeline_v3)
    assert sig.parameters["voice"].default == ""


def test_edge_tts_voice_env_is_not_shadowed(tmp_path, monkeypatch):
    """EDGE_TTS_VOICE 真的会影响旁白音色（此前被硬编码默认值遮蔽）。"""
    from hailuo_video.script_engine import SceneSpec, Shot, Storyboard

    monkeypatch.setenv("EDGE_TTS_VOICE", "zh-CN-YunxiNeural")
    sb = Storyboard(
        title="t", characters=[],
        scenes=[SceneSpec(id=1, location="x")],
        shots=[Shot(index=1, scene_id=1, shot_type="中景", description="d",
                    voiceover="只有旁白", dialogue="")],
    )
    agent = PipelineAgent(PipelineConfig(run_dir=tmp_path / "run"))
    ctx = {"storyboard": sb, "shot_count": 1, "intermediate_dir": tmp_path}
    task = agent._build_task("tts", 0, ctx, None)
    assert task["voice"] == "zh-CN-YunxiNeural"


def test_narrator_uses_builtin_voice_when_env_unset(tmp_path, monkeypatch):
    """env 未设 → 旁白用内置叙述者音色（不被 default_voice() 的默认值顶掉）。"""
    from hailuo_video.script_engine import SceneSpec, Shot, Storyboard

    monkeypatch.delenv("EDGE_TTS_VOICE", raising=False)
    sb = Storyboard(
        title="t", characters=[],
        scenes=[SceneSpec(id=1, location="x")],
        shots=[Shot(index=1, scene_id=1, shot_type="中景", description="d",
                    voiceover="只有旁白", dialogue="")],
    )
    agent = PipelineAgent(PipelineConfig(run_dir=tmp_path / "run"))
    ctx = {"storyboard": sb, "shot_count": 1, "intermediate_dir": tmp_path}
    task = agent._build_task("tts", 0, ctx, None)
    assert task["voice"] == NARRATOR_VOICE


def test_explicit_global_voice_overrides_role_assignment(tmp_path):
    """显式 --voice → 全片用这个音色（保持旧语义）。"""
    from hailuo_video.script_engine import SceneSpec, Shot, Storyboard

    sb = Storyboard(
        title="t", characters=[],
        scenes=[SceneSpec(id=1, location="x")],
        shots=[Shot(index=1, scene_id=1, shot_type="中景", description="d",
                    dialogue="小黄：汪", characters=["小黄"])],
    )
    agent = PipelineAgent(PipelineConfig(run_dir=tmp_path / "run",
                                         voice="zh-CN-YunjianNeural"))
    ctx = {"storyboard": sb, "shot_count": 1, "intermediate_dir": tmp_path}
    task = agent._build_task("tts", 0, ctx, None)
    assert task["voice"] == "zh-CN-YunjianNeural"


# ---------- BUG-E：参考图优先级 ----------

def _write_ref(assets: Path, rel: str, url: str) -> None:
    p = assets / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(url, encoding="utf-8")


def _shot(characters):
    class _S:
        pass
    s = _S()
    s.characters = characters
    s.shot_type = "中景"
    s.camera_motion = ""
    s.mood = []
    s.scene_id = 1
    s.time = ""
    return s


def test_scene_reference_survives_two_character_shot(tmp_path):
    """双角色镜头：场景参考图必须在（不能被 2×(角度+表情) 挤掉）。"""
    assets = tmp_path / "assets"
    _write_ref(assets, "characters/小黄/side_left.url.txt", "https://x/huang-front.jpg")
    _write_ref(assets, "characters/小黄/expression_neutral.url.txt", "https://x/huang-expr.jpg")
    _write_ref(assets, "characters/小黑/side_left.url.txt", "https://x/hei-front.jpg")
    _write_ref(assets, "characters/小黑/expression_neutral.url.txt", "https://x/hei-expr.jpg")
    _write_ref(assets, "scenes/scene_0001_day.url.txt", "https://x/scene-day.jpg")

    refs = ap.resolve_shot_reference_urls(_shot(["小黄", "小黑"]), assets, shot_index=0)
    assert len(refs.urls) <= ap.MAX_SUBJECT_REFERENCES
    assert "https://x/scene-day.jpg" in refs.urls, (
        f"场景参考图被挤掉了：{refs.urls}")


def test_reference_priority_is_angle_scene_expression(tmp_path):
    """优先级：角度 → 场景 → 表情。"""
    assets = tmp_path / "assets"
    _write_ref(assets, "characters/小黄/side_left.url.txt", "https://x/angle.jpg")
    _write_ref(assets, "characters/小黄/expression_neutral.url.txt", "https://x/expr.jpg")
    _write_ref(assets, "scenes/scene_0001_day.url.txt", "https://x/scene.jpg")

    refs = ap.resolve_shot_reference_urls(_shot(["小黄"]), assets, shot_index=0)
    assert refs.urls.index("https://x/angle.jpg") < refs.urls.index("https://x/scene.jpg")
    assert refs.urls.index("https://x/scene.jpg") < refs.urls.index("https://x/expr.jpg")


def test_single_character_still_gets_all_three(tmp_path):
    """单角色镜头：三个轴都在（行为不变）。"""
    assets = tmp_path / "assets"
    _write_ref(assets, "characters/小黄/side_left.url.txt", "https://x/angle.jpg")
    _write_ref(assets, "characters/小黄/expression_neutral.url.txt", "https://x/expr.jpg")
    _write_ref(assets, "scenes/scene_0001_day.url.txt", "https://x/scene.jpg")

    refs = ap.resolve_shot_reference_urls(_shot(["小黄"]), assets, shot_index=0)
    assert set(refs.urls) == {
        "https://x/angle.jpg", "https://x/expr.jpg", "https://x/scene.jpg"}


def test_scene_dropped_with_many_characters_warns(tmp_path, capsys):
    """角色多到挤掉场景 → 必须告警（不静默丢锚定）。"""
    assets = tmp_path / "assets"
    names = ["小黄", "小黑", "小灰", "小花"]
    for n in names:
        _write_ref(assets, f"characters/{n}/side_left.url.txt", f"https://x/{n}-angle.jpg")
        _write_ref(assets, f"characters/{n}/expression_neutral.url.txt", f"https://x/{n}-expr.jpg")
    _write_ref(assets, "scenes/scene_0001_day.url.txt", "https://x/scene-day.jpg")

    refs = ap.resolve_shot_reference_urls(_shot(names), assets, shot_index=0)
    err = capsys.readouterr().err
    assert "https://x/scene-day.jpg" not in refs.urls
    assert "场景参考图" in err, "场景锚定被丢掉必须告警"
