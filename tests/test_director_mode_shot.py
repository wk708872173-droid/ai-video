"""V3.5 Director 升级：Shot/Scene dataclass 扩展 单测。"""
from __future__ import annotations

import sys
from pathlib import Path

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))


# ---------- Shot 新字段默认值 ----------

def test_shot_construct_without_director_fields():
    """旧用法 Shot(index=1, scene_id=1, shot_type="中景", description="d") 仍能工作。"""
    from hailuo_video.script_engine import Shot
    s = Shot(index=1, scene_id=1, shot_type="中景", description="d")
    assert s.index == 1
    # 12 个 director 字段全部默认空
    assert s.hook == ""
    assert s.pacing == ""
    assert s.lens == ""
    assert s.depth_of_field == ""
    assert s.lighting == ""
    assert s.screen_direction == ""
    assert s.color_grade == ""
    assert s.transition_in == ""
    assert s.transition_out == ""
    assert s.bgm_cue == ""
    assert s.narrative_beat == ""
    assert s.emotion_arc_point == 0.0


def test_shot_to_dict_contains_director_fields():
    """to_dict() 含全部 12 个 director 字段（向后兼容 day1-3 JSON 序列化）。"""
    from hailuo_video.script_engine import Shot
    s = Shot(index=1, scene_id=1, shot_type="中景", description="d",
             lens="50mm", bgm_cue="soft piano", narrative_beat="起")
    d = s.to_dict()
    assert d["lens"] == "50mm"
    assert d["bgm_cue"] == "soft piano"
    assert d["narrative_beat"] == "起"
    # 默认空字段也应在 to_dict 里
    assert d["hook"] == ""
    assert d["emotion_arc_point"] == 0.0


# ---------- Scene 新字段 ----------

def test_scene_construct_with_narrative_beat():
    from hailuo_video.script_engine import Scene
    sc = Scene(id=1, time="白天", location="老宅", narrative_beat="起")
    assert sc.narrative_beat == "起"


def test_scene_narrative_beat_default_empty():
    from hailuo_video.script_engine import Scene
    sc = Scene(id=1, time="白天", location="老宅")
    assert sc.narrative_beat == ""


def test_script_from_dict_parses_scene_narrative_beat():
    """Script.from_dict 解析 scene.narrative_beat（白名单外 → 空串）。"""
    from hailuo_video.script_engine import Script
    payload = {
        "title": "测试",
        "scenes": [
            {"id": 1, "time": "白天", "location": "老宅", "narrative_beat": "起"},
            {"id": 2, "time": "黄昏", "location": "门口", "narrative_beat": "转"},
            {"id": 3, "time": "夜", "location": "窗台", "narrative_beat": "INVALID"},  # 白名单外
        ],
    }
    script = Script.from_dict(payload)
    assert script.scenes[0].narrative_beat == "起"
    assert script.scenes[1].narrative_beat == "转"
    assert script.scenes[2].narrative_beat == ""  # lenient → 空


def test_script_from_dict_backward_compat_no_narrative_beat():
    """day1-3 旧 JSON（scene 无 narrative_beat）解析不抛错。"""
    from hailuo_video.script_engine import Script
    payload = {
        "title": "老格式",
        "scenes": [
            {"id": 1, "time": "白天", "location": "老宅", "characters": ["幼犬"]},
        ],
    }
    script = Script.from_dict(payload)
    assert script.scenes[0].narrative_beat == ""


# ---------- _shot_from_dict 接受新字段 ----------

def test_shot_from_dict_with_director_fields():
    """_shot_from_dict 接受 director 字段（白名单外 → 空串）。"""
    from hailuo_video.script_engine import _shot_from_dict
    payload = {
        "index": 1, "scene_id": 1, "shot_type": "中景", "description": "d",
        "lens": "85mm",
        "depth_of_field": "shallow",
        "lighting": "暖黄金色侧光从 camera-right 切入",
        "screen_direction": "L→R",
        "color_grade": "暖金色（黄昏）",
        "transition_in": "fade",
        "transition_out": "cut",
        "pacing": "中",
        "bgm_cue": "soft piano",
        "narrative_beat": "承",
        "hook": "奶黄伸懒腰",
        "emotion_arc_point": 0.5,
    }
    shot = _shot_from_dict(payload, 0)
    assert shot.lens == "85mm"
    assert shot.depth_of_field == "shallow"
    assert "camera-right" in shot.lighting
    assert shot.screen_direction == "L→R"
    assert shot.color_grade == "暖金色（黄昏）"
    assert shot.transition_in == "fade"
    assert shot.transition_out == "cut"
    assert shot.pacing == "中"
    assert shot.bgm_cue == "soft piano"
    assert shot.narrative_beat == "承"
    assert shot.hook == "奶黄伸懒腰"
    assert shot.emotion_arc_point == 0.5


def test_shot_from_dict_backward_compat_no_director_fields():
    """day1-3 旧 dict（无 director 字段）→ defaults apply。"""
    from hailuo_video.script_engine import _shot_from_dict
    payload = {
        "index": 1, "scene_id": 1, "shot_type": "中景",
        "description": "d", "shot_id": "shot_001",
        "camera_motion": "固定", "subject_action": "x",
        "duration": 5, "motion_complexity": "static",
    }
    shot = _shot_from_dict(payload, 0)
    assert shot.lens == ""
    assert shot.pacing == ""
    assert shot.bgm_cue == ""
    assert shot.narrative_beat == ""
    assert shot.emotion_arc_point == 0.0


def test_shot_from_dict_invalid_whitelist_falls_back_to_empty():
    """白名单外值 → lenient 空串（不抛错）。"""
    from hailuo_video.script_engine import _shot_from_dict
    payload = {
        "index": 1, "scene_id": 1, "shot_type": "中景", "description": "d",
        "lens": "99mm",  # 不在白名单
        "pacing": "very fast",  # 不在白名单
        "transition_in": "wipe",  # 不在白名单
        "narrative_beat": "中段",  # 不在白名单
    }
    shot = _shot_from_dict(payload, 0)
    assert shot.lens == ""
    assert shot.pacing == ""
    assert shot.transition_in == ""
    assert shot.narrative_beat == ""


def test_shot_from_dict_invalid_emotion_arc_falls_back_to_0():
    """emotion_arc_point 不是 float → 0.0。"""
    from hailuo_video.script_engine import _shot_from_dict
    payload = {
        "index": 1, "scene_id": 1, "shot_type": "中景", "description": "d",
        "emotion_arc_point": "not a number",
    }
    shot = _shot_from_dict(payload, 0)
    assert shot.emotion_arc_point == 0.0


# ---------- StoryArc 兼容旧 day1_rerun.json（V3.2 era） ----------

def test_story_arc_from_old_format():
    """StoryArc.from_dict 接受 day1-3 era JSON（无 director 字段）。"""
    from hailuo_video.script_engine import StoryArc
    payload = {
        "title": "中华田园犬·奶黄幼犬·治愈一日 (Day 1)",
        "style_anchor": "photorealistic cinematic",
        "characters": [
            {"name": "幼犬", "appearance": "奶黄", "personality": ["治愈"], "color_palette": ["奶黄"]},
        ],
        "scenes": [
            {"id": 1, "location": "老宅窗台", "atmosphere": "晨光", "key_elements": ["木格窗"]},
        ],
        "shots": [
            {
                "index": 1, "scene_id": 1, "shot_type": "中景",
                "description": "d", "shot_id": "shot_001",
                "characters": ["幼犬"], "dialogue": "", "voiceover": "",
                "mood": ["治愈"], "camera_motion": "固定",
                "subject_action": "伸懒腰", "duration": 5,
                "motion_complexity": "static", "continuity_from_prev": "",
            },
        ],
        "arc_metadata": {},
    }
    arc = StoryArc.from_dict(payload)
    assert len(arc.shots) == 1
    assert arc.shots[0].lens == ""  # 旧数据 → 默认空
    assert arc.shots[0].narrative_beat == ""
    assert arc.shots[0].emotion_arc_point == 0.0


# ---------- _whitelist_or_empty helper ----------

def test_whitelist_or_empty_valid_value():
    from hailuo_video.script_engine import _whitelist_or_empty
    assert _whitelist_or_empty("50mm", ("35mm", "50mm", "85mm")) == "50mm"


def test_whitelist_or_empty_invalid_value():
    from hailuo_video.script_engine import _whitelist_or_empty
    assert _whitelist_or_empty("99mm", ("35mm", "50mm")) == ""


def test_whitelist_or_empty_non_string_value():
    from hailuo_video.script_engine import _whitelist_or_empty
    assert _whitelist_or_empty(123, ("1", "2", "3")) == ""
    assert _whitelist_or_empty(None, ("1", "2")) == ""


# ---------- to_storyboards 仍工作（V3.2 已有）----------

def test_story_arc_to_storyboards_preserves_director_fields():
    """StoryArc.to_storyboards() 拆分时 director 字段不丢失。"""
    from hailuo_video.script_engine import StoryArc
    payload = {
        "title": "test", "style_anchor": "x",
        "shots": [
            {"index": 1, "scene_id": 1, "shot_type": "中景", "description": "d",
             "lens": "50mm", "bgm_cue": "soft piano", "narrative_beat": "起"},
            {"index": 2, "scene_id": 1, "shot_type": "近景", "description": "d",
             "lens": "85mm", "bgm_cue": "silence", "narrative_beat": "承"},
        ],
    }
    arc = StoryArc.from_dict(payload)
    boards = arc.to_storyboards()
    assert len(boards) == 2
    assert boards[0].shots[0].lens == "50mm"
    assert boards[0].shots[0].bgm_cue == "soft piano"
    assert boards[1].shots[0].lens == "85mm"
