"""V3.9 Batch C — W-C 角色语音一致性（REQ-403）。

锁住的不变量：**同一角色在任何镜头、任何一次运行里都是同一个音色**；
不同角色可区分；旁白与角色对白用不同音色。

背景：此前 `orchestrator._build_task` 的 tts 分支只有一个全局 voice
（`task["voice"] = self.config.voice`）→ 整条片子所有角色、所有旁白一个音色，
与本项目"角色一致性"的核心价值主张直接冲突。

文献依据：arXiv:2405.09768（TTS 评估五维之一 = speaker consistency）、
arXiv:2505.17320（角色驱动 TTS：按角色准备音色）、
arXiv:2210.05979（说话人域偏移 → 角色→音色映射必须稳定可预期）。

R4：所有测试都不调真 TTS（edge-tts 需要联网）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from hailuo_video.orchestrator import PipelineAgent, PipelineConfig  # noqa: E402
from hailuo_video.script_engine import Character, SceneSpec, Shot, Storyboard  # noqa: E402
from hailuo_video.voice_cast import (  # noqa: E402
    CHARACTER_VOICE_POOL,
    NARRATOR_VOICE,
    VoiceCast,
    normalize_voice,
    resolve_voice_for_shot,
    speaker_of,
)


# ---------- helpers ----------

def _shot(**kw):
    base = dict(
        index=1, scene_id=1, shot_type="中景", description="d",
        characters=["小黄"], voiceover="旁白",
    )
    base.update(kw)
    return Shot(**base)


def _storyboard(shots=None, characters=None):
    return Storyboard(
        title="t",
        characters=characters if characters is not None else [
            Character(name="小黄", appearance="奶黄拉布拉多", personality=[],
                      color_palette=["奶黄"]),
            Character(name="小黑", appearance="纯黑拉布拉多", personality=[],
                      color_palette=["纯黑"]),
        ],
        scenes=[SceneSpec(id=1, location="田野")],
        shots=shots or [_shot()],
    )


# ============================================================
# 分配表：确定性 + 稳定性
# ============================================================

def test_same_character_gets_same_voice_across_calls():
    """同一角色名 → 同一音色（跨调用、跨运行、跨项目恒定）。"""
    cast = VoiceCast()
    a = cast.voice_for("小黄")
    b = cast.voice_for("小黄")
    assert a == b


def test_assignment_is_deterministic_across_cast_instances():
    """新建的分配表也必须给出同一结果（不是随机 / 不是依赖实例状态）。"""
    a = VoiceCast().voice_for("小黄")
    b = VoiceCast().voice_for("小黄")
    assert a == b


def test_different_characters_get_different_voices():
    """不同角色应可区分（哈希分配到不同槽位）。"""
    cast = VoiceCast()
    voices = {cast.voice_for(n) for n in ("小黄", "小黑", "农夫", "兔子", "海龟", "梅花鹿")}
    assert len(voices) >= 3, f"角色之间几乎没有区分度：{voices}"


def test_assigned_voice_is_in_pool():
    cast = VoiceCast()
    for name in ("小黄", "小黑", "农夫"):
        assert cast.voice_for(name) in CHARACTER_VOICE_POOL


def test_explicit_voice_wins():
    cast = VoiceCast()
    v = cast.voice_for("小黄", explicit_voice="zh-CN-YunxiNeural")
    assert v == "zh-CN-YunxiNeural"
    assert cast.explicit["小黄"] == "zh-CN-YunxiNeural"


def test_explicit_voice_accepts_alias():
    cast = VoiceCast()
    assert cast.voice_for("小黄", explicit_voice="yunxi") == "zh-CN-YunxiNeural"


def test_normalize_voice():
    assert normalize_voice("xiaoxiao") == "zh-CN-XiaoxiaoNeural"
    assert normalize_voice("zh-CN-YunxiNeural") == "zh-CN-YunxiNeural"
    assert normalize_voice("") == ""


def test_empty_character_name_falls_back_to_default():
    cast = VoiceCast(default_voice="zh-CN-YunxiNeural")
    assert cast.voice_for("") == "zh-CN-YunxiNeural"


def test_cast_to_dict():
    cast = VoiceCast()
    cast.voice_for("小黄")
    d = cast.to_dict()
    assert "小黄" in d["assignments"]
    assert d["narrator_voice"] == NARRATOR_VOICE


# ============================================================
# 说话人判定
# ============================================================

def test_speaker_from_explicit_field():
    assert speaker_of(_shot(speaker="小黑")) == "小黑"


def test_speaker_inferred_from_dialogue_name():
    shot = _shot(dialogue="小黑说：走吧", characters=["小黄", "小黑"])
    assert speaker_of(shot) == "小黑"


def test_speaker_falls_back_to_first_character():
    shot = _shot(dialogue="走吧", characters=["小黄", "小黑"])
    assert speaker_of(shot) == "小黄"


def test_no_speaker_for_voiceover_only():
    """只有旁白 → 无说话角色（= 旁白音色）。"""
    assert speaker_of(_shot(dialogue="")) == ""


# ============================================================
# 逐镜解析
# ============================================================

def test_dialogue_uses_character_voice():
    sb = _storyboard(shots=[_shot(dialogue="小黑说：走吧", characters=["小黄", "小黑"])])
    voice, speaker = resolve_voice_for_shot(sb.shots[0], sb)
    assert speaker == "小黑"
    assert voice != NARRATOR_VOICE


def test_voiceover_uses_narrator_voice():
    sb = _storyboard(shots=[_shot(dialogue="", voiceover="旁白内容")])
    voice, speaker = resolve_voice_for_shot(sb.shots[0], sb)
    assert speaker == ""
    assert voice == NARRATOR_VOICE


def test_same_character_same_voice_across_shots():
    """核心不变量：同一角色在 shot 1 与 shot 9 音色相同。"""
    shots = [
        _shot(index=1, dialogue="小黄：汪", characters=["小黄"]),
        _shot(index=2, dialogue="小黄：汪汪", characters=["小黄"]),
        _shot(index=9, dialogue="小黄：呜", characters=["小黄"]),
    ]
    sb = _storyboard(shots=shots)
    cast = VoiceCast()
    voices = [resolve_voice_for_shot(s, sb, cast=cast)[0] for s in shots]
    assert len(set(voices)) == 1, f"同一角色在不同镜头音色不一致：{voices}"


def test_different_characters_different_voices_across_shots():
    shots = [
        _shot(index=1, dialogue="小黄：汪", characters=["小黄"]),
        _shot(index=2, dialogue="小黑：嗷", characters=["小黑"]),
    ]
    sb = _storyboard(shots=shots)
    cast = VoiceCast()
    v1, _ = resolve_voice_for_shot(shots[0], sb, cast=cast)
    v2, _ = resolve_voice_for_shot(shots[1], sb, cast=cast)
    assert v1 != v2


def test_shot_voice_override_wins():
    shot = _shot(dialogue="小黄：汪", voice="zh-CN-XiaoyiNeural")
    voice, _ = resolve_voice_for_shot(shot, _storyboard())
    assert voice == "zh-CN-XiaoyiNeural"


def test_character_explicit_voice_used():
    chars = [Character(name="小黄", appearance="x", personality=[], color_palette=[],
                       voice="zh-CN-XunjianNeural")]
    sb = _storyboard(shots=[_shot(dialogue="小黄：汪", characters=["小黄"])], characters=chars)
    voice, speaker = resolve_voice_for_shot(sb.shots[0], sb)
    assert speaker == "小黄"
    assert voice == "zh-CN-XunjianNeural"


# ============================================================
# orchestrator 接线
# ============================================================

def test_orchestrator_tts_task_carries_voice(tmp_path):
    """tts task 必须带上按角色解出的音色（不是全局默认）。"""
    shots = [
        _shot(index=1, dialogue="小黄：汪", characters=["小黄"]),
        _shot(index=2, dialogue="小黑：嗷", characters=["小黑"]),
    ]
    sb = _storyboard(shots=shots)
    agent = PipelineAgent(PipelineConfig(run_dir=tmp_path / "run"))
    ctx = {"storyboard": sb, "shot_count": 2, "intermediate_dir": tmp_path}

    t1 = agent._build_task("tts", 0, ctx, None)
    t2 = agent._build_task("tts", 1, ctx, None)
    assert t1["voice"]
    assert t2["voice"]
    assert t1["voice"] != t2["voice"], "两个角色拿到了同一个音色"
    assert t1["speaker"] == "小黄"
    assert t2["speaker"] == "小黑"


def test_orchestrator_same_character_same_voice_across_shots(tmp_path):
    """同一角色跨镜头音色一致（跨 run 也一致 —— 分配是名字哈希决定的）。"""
    shots = [
        _shot(index=1, dialogue="小黄：汪", characters=["小黄"]),
        _shot(index=2, dialogue="小黄：呜", characters=["小黄"]),
    ]
    sb = _storyboard(shots=shots)
    agent = PipelineAgent(PipelineConfig(run_dir=tmp_path / "run"))
    ctx = {"storyboard": sb, "shot_count": 2, "intermediate_dir": tmp_path}
    v1 = agent._build_task("tts", 0, ctx, None)["voice"]
    v2 = agent._build_task("tts", 1, ctx, None)["voice"]
    assert v1 == v2

    # 换一个 agent（= 另一次运行）→ 仍然同音色
    agent2 = PipelineAgent(PipelineConfig(run_dir=tmp_path / "run2"))
    ctx2 = {"storyboard": sb, "shot_count": 2, "intermediate_dir": tmp_path}
    assert agent2._build_task("tts", 0, ctx2, None)["voice"] == v1


def test_orchestrator_voiceover_only_uses_narrator(tmp_path):
    sb = _storyboard(shots=[_shot(dialogue="", voiceover="只有旁白")])
    agent = PipelineAgent(PipelineConfig(run_dir=tmp_path / "run"))
    ctx = {"storyboard": sb, "shot_count": 1, "intermediate_dir": tmp_path}
    task = agent._build_task("tts", 0, ctx, None)
    assert task["voice"] == NARRATOR_VOICE
    assert task["speaker"] == ""


def test_tts_agent_passes_voice_to_backend(tmp_path):
    """音色必须真的传到 TTS 后端。"""
    from hailuo_video import tts_engine as te
    from hailuo_video.agents import TTSAgent

    backend = te.MockTTSBackend()
    agent = TTSAgent()
    result = agent.execute({
        "text": "汪",
        "output": str(tmp_path / "a.mp3"),
        "tts_backend": backend,
        "voice": "zh-CN-YunxiNeural",
    })
    assert result.ok, result.error
    assert backend.calls[0]["voice"] == "zh-CN-YunxiNeural"


# ---------- 向后兼容 ----------

def test_old_storyboard_without_voice_parses():
    """旧 storyboard.json（无 voice/speaker）→ 仍能解析，字段为空串。"""
    import json
    import tempfile
    from hailuo_video import pipeline as pl

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "sb.json"
        p.write_text(json.dumps({
            "title": "t",
            "characters": [{"name": "小黄", "appearance": "x"}],
            "scenes": [],
            "shots": [{"index": 1, "scene_id": 1, "shot_type": "中景",
                       "description": "d", "voiceover": "v"}],
        }, ensure_ascii=False), encoding="utf-8")
        sb = pl.load_storyboard(p)
    assert sb.characters[0].voice == ""
    assert sb.shots[0].voice == ""
    assert sb.shots[0].speaker == ""


def test_shot_new_fields_default_empty():
    s = Shot(index=1, scene_id=1, shot_type="中景", description="d")
    assert s.voice == ""
    assert s.speaker == ""


def test_narrator_voice_is_not_in_character_pool():
    """旁白音色不得在角色池里 —— 否则某角色可能与旁白同音色。

    （V3.9 自测踩到：最初旁白用 Yunxi，而 Yunxi 也在角色池里。）
    """
    assert NARRATOR_VOICE not in CHARACTER_VOICE_POOL


def test_collisions_are_detectable():
    """两个角色撞音色 → collisions() 必须报出来（不静默）。"""
    cast = VoiceCast()
    # 人为制造撞车：把两个角色显式指到同一音色
    cast.assign("小黄", explicit_voice="zh-CN-YunxiNeural")
    cast.assign("小黑", explicit_voice="zh-CN-YunxiNeural")
    collisions = cast.collisions()
    assert collisions, "撞音色必须被检测出来"
    voice, names = collisions[0]
    assert voice == "zh-CN-YunxiNeural"
    assert set(names) == {"小黄", "小黑"}


def test_no_collisions_in_normal_case():
    cast = VoiceCast()
    for n in ("小黄", "小黑", "农夫", "兔子", "海龟", "梅花鹿"):
        cast.voice_for(n)
    assert cast.collisions() == [], "探测式分配下不应撞车"


def test_many_characters_still_get_distinct_voices():
    """角色数接近池子大小时仍不应撞车（探测式分配的核心价值）。"""
    cast = VoiceCast()
    names = [f"角色{i}" for i in range(12)]
    voices = [cast.voice_for(n) for n in names]
    assert len(set(voices)) == len(names), "探测式分配未能避免撞车"


def test_assignment_order_is_deterministic():
    """同一组角色、同一顺序 → 每次分配结果完全相同。"""
    names = ["小黄", "小黑", "农夫", "兔子"]
    a = [VoiceCast().voice_for(n) for n in names]
    b = [VoiceCast().voice_for(n) for n in names]
    assert a == b
