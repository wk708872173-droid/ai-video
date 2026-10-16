"""V3.5 Director 升级：_parse_shots_payload 接受 director 字段 单测。"""
from __future__ import annotations

import sys
from pathlib import Path

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))


# ---------- LLM 输出含 director 字段 ----------

def test_parse_shots_payload_with_director_fields():
    """_parse_shots_payload 接受 director_mode=True 的 LLM 输出。"""
    from hailuo_video.script_engine import (
        MockBackend, novel_to_storyboard,
    )

    backend = MockBackend()
    backend.on("漫剧编剧", '{"title": "测试", "scenes": [{"id": 1, "time": "白天", "location": "老宅", "characters": [], "summary": "s", "dialogue": ""}]}')
    backend.on("角色设计师", '{"characters": []}')
    backend.on("分镜师", '{"shots": [{"shot_id": "shot_001", "scene_id": 1, "shot_type": "中景", "description": "d", "characters": [], "dialogue": "", "voiceover": "", "mood": ["治愈"], "camera_motion": "缓慢推近", "subject_action": "伸懒腰", "duration": 5, "motion_complexity": "simple", "narrative_beat": "起", "pacing": "中", "lens": "50mm", "depth_of_field": "shallow", "lighting": "暖黄金色侧光", "screen_direction": "L→R", "color_grade": "暖金色", "transition_in": "fade", "transition_out": "cut", "bgm_cue": "soft piano", "hook": "奶黄伸懒腰", "emotion_arc_point": 0.3}]}')

    sb = novel_to_storyboard("x", backend, n_shots=1)
    shot = sb.shots[0]
    assert shot.lens == "50mm"
    assert shot.depth_of_field == "shallow"
    assert "侧光" in shot.lighting
    assert shot.screen_direction == "L→R"
    assert shot.color_grade == "暖金色"
    assert shot.transition_in == "fade"
    assert shot.transition_out == "cut"
    assert shot.pacing == "中"
    assert shot.bgm_cue == "soft piano"
    assert shot.narrative_beat == "起"
    assert shot.hook == "奶黄伸懒腰"
    assert shot.emotion_arc_point == 0.3


def test_parse_shots_payload_invalid_director_fields_fallback():
    """白名单外的 director 字段 → lenient 空串（不抛 LLMError）。"""
    from hailuo_video.script_engine import (
        MockBackend, novel_to_storyboard,
    )

    backend = MockBackend()
    backend.on("漫剧编剧", '{"title": "测试", "scenes": [{"id": 1, "time": "白天", "location": "老宅", "characters": [], "summary": "s", "dialogue": ""}]}')
    backend.on("角色设计师", '{"characters": []}')
    backend.on("分镜师", '{"shots": [{"shot_id": "shot_001", "scene_id": 1, "shot_type": "中景", "description": "d", "characters": [], "dialogue": "", "voiceover": "", "mood": [], "camera_motion": "固定", "subject_action": "x", "duration": 5, "motion_complexity": "static", "lens": "99mm", "pacing": "very fast", "transition_in": "wipe", "narrative_beat": "中段", "emotion_arc_point": "abc"}]}')

    sb = novel_to_storyboard("x", backend, n_shots=1)
    shot = sb.shots[0]
    # 全部白名单外 → 全部空
    assert shot.lens == ""
    assert shot.pacing == ""
    assert shot.transition_in == ""
    assert shot.narrative_beat == ""
    assert shot.emotion_arc_point == 0.0  # "abc" → 0.0


def test_parse_shots_payload_old_format_still_works():
    """旧 LLM 输出（无 director 字段）→ 仍解析成功，director 字段默认空。"""
    from hailuo_video.script_engine import (
        MockBackend, novel_to_storyboard,
    )

    backend = MockBackend()
    backend.on("漫剧编剧", '{"title": "测试", "scenes": [{"id": 1, "time": "白天", "location": "老宅", "characters": [], "summary": "s", "dialogue": ""}]}')
    backend.on("角色设计师", '{"characters": []}')
    backend.on("分镜师", '{"shots": [{"shot_id": "shot_001", "scene_id": 1, "shot_type": "中景", "description": "d", "characters": [], "dialogue": "", "voiceover": "", "mood": [], "camera_motion": "固定", "subject_action": "x", "duration": 5, "motion_complexity": "static"}]}')

    sb = novel_to_storyboard("x", backend, n_shots=1)
    shot = sb.shots[0]
    # 旧格式：director 字段全部默认空
    assert shot.lens == ""
    assert shot.bgm_cue == ""
    assert shot.narrative_beat == ""
    assert shot.emotion_arc_point == 0.0


def test_parse_shots_payload_motion_complexity_still_strict():
    """motion_complexity 仍是 strict 校验（不在白名单 → LLMError）。"""
    from hailuo_video.script_engine import (
        MockBackend, novel_to_storyboard, LLMError,
    )

    backend = MockBackend()
    backend.on("漫剧编剧", '{"title": "测试", "scenes": [{"id": 1, "time": "白天", "location": "老宅", "characters": [], "summary": "s", "dialogue": ""}]}')
    backend.on("角色设计师", '{"characters": []}')
    backend.on("分镜师", '{"shots": [{"shot_id": "shot_001", "scene_id": 1, "shot_type": "中景", "description": "d", "characters": [], "dialogue": "", "voiceover": "", "mood": [], "camera_motion": "固定", "subject_action": "x", "duration": 5, "motion_complexity": "INVALID"}]}')

    import pytest
    with pytest.raises(LLMError, match="motion_complexity 不合法"):
        novel_to_storyboard("x", backend, n_shots=1)


def test_parse_shots_payload_duration_still_strict():
    """duration 仍是 strict 校验（≤0 或 >30 → LLMError）。"""
    from hailuo_video.script_engine import (
        MockBackend, novel_to_storyboard, LLMError,
    )

    backend = MockBackend()
    backend.on("漫剧编剧", '{"title": "测试", "scenes": [{"id": 1, "time": "白天", "location": "老宅", "characters": [], "summary": "s", "dialogue": ""}]}')
    backend.on("角色设计师", '{"characters": []}')
    backend.on("分镜师", '{"shots": [{"shot_id": "shot_001", "scene_id": 1, "shot_type": "中景", "description": "d", "characters": [], "dialogue": "", "voiceover": "", "mood": [], "camera_motion": "固定", "subject_action": "x", "duration": -1, "motion_complexity": "static"}]}')

    import pytest
    with pytest.raises(LLMError, match="duration 越界"):
        novel_to_storyboard("x", backend, n_shots=1)


def test_parse_shots_payload_mixed_old_and_new_fields():
    """混合：部分 director 字段有，部分无 → 各自处理。"""
    from hailuo_video.script_engine import (
        MockBackend, novel_to_storyboard,
    )

    backend = MockBackend()
    backend.on("漫剧编剧", '{"title": "测试", "scenes": [{"id": 1, "time": "白天", "location": "老宅", "characters": [], "summary": "s", "dialogue": ""}]}')
    backend.on("角色设计师", '{"characters": []}')
    backend.on("分镜师", '{"shots": [{"shot_id": "shot_001", "scene_id": 1, "shot_type": "中景", "description": "d", "characters": [], "dialogue": "", "voiceover": "", "mood": [], "camera_motion": "固定", "subject_action": "x", "duration": 5, "motion_complexity": "static", "lens": "50mm"}, {"shot_id": "shot_002", "scene_id": 1, "shot_type": "近景", "description": "d2", "characters": [], "dialogue": "", "voiceover": "", "mood": [], "camera_motion": "固定", "subject_action": "y", "duration": 5, "motion_complexity": "static", "bgm_cue": "silence"}]}')

    sb = novel_to_storyboard("x", backend, n_shots=2)
    # shot_001：lens 有，bgm_cue 无
    assert sb.shots[0].lens == "50mm"
    assert sb.shots[0].bgm_cue == ""
    # shot_002：lens 无，bgm_cue 有
    assert sb.shots[1].lens == ""
    assert sb.shots[1].bgm_cue == "silence"


# ---------- 3-shots 完整 novel_to_storyboard E2E（director_mode） ----------

def test_novel_to_storyboard_3_shots_with_director_mode():
    """3 镜完整流：MockBackend 设 3 个响应，验证全部 director 字段都进 Shot。"""
    from hailuo_video.script_engine import (
        MockBackend, novel_to_storyboard,
    )

    backend = MockBackend()
    backend.on("漫剧编剧", '{"title": "三镜小故事", "scenes": [{"id": 1, "time": "白天", "location": "老宅", "characters": ["幼犬"], "summary": "晨光", "dialogue": ""}, {"id": 2, "time": "黄昏", "location": "门口", "characters": ["幼犬"], "summary": "夕阳", "dialogue": ""}]}')
    backend.on("角色设计师", '{"characters": [{"name": "幼犬", "appearance": "奶黄幼犬", "personality": ["治愈"], "color_palette": ["奶黄"]}]}')
    backend.on("分镜师", '{"shots": [{"shot_id": "shot_001", "scene_id": 1, "shot_type": "中景", "description": "d1", "characters": ["幼犬"], "dialogue": "", "voiceover": "", "mood": ["治愈"], "camera_motion": "缓慢推近", "subject_action": "伸懒腰", "duration": 5, "motion_complexity": "simple", "lens": "50mm", "bgm_cue": "soft piano", "narrative_beat": "起"}, {"shot_id": "shot_002", "scene_id": 1, "shot_type": "近景", "description": "d2", "characters": ["幼犬"], "dialogue": "", "voiceover": "", "mood": ["好奇"], "camera_motion": "固定", "subject_action": "嗅落叶", "duration": 5, "motion_complexity": "simple", "lens": "85mm", "bgm_cue": "nature ambient", "narrative_beat": "承"}, {"shot_id": "shot_003", "scene_id": 2, "shot_type": "中景", "description": "d3", "characters": ["幼犬"], "dialogue": "", "voiceover": "", "mood": ["温暖"], "camera_motion": "缓慢拉远", "subject_action": "看夕阳", "duration": 5, "motion_complexity": "static", "lens": "35mm", "bgm_cue": "soft piano", "narrative_beat": "合"}]}')

    sb = novel_to_storyboard("x", backend, n_shots=3)
    assert len(sb.shots) == 3
    assert sb.shots[0].lens == "50mm"
    assert sb.shots[1].lens == "85mm"
    assert sb.shots[2].lens == "35mm"
    # narrative_beat 三拍（起承合；中间"承"）
    assert sb.shots[0].narrative_beat == "起"
    assert sb.shots[1].narrative_beat == "承"
    assert sb.shots[2].narrative_beat == "合"
    # bgm_cue 三镜各异
    assert sb.shots[0].bgm_cue == "soft piano"
    assert sb.shots[1].bgm_cue == "nature ambient"
    assert sb.shots[2].bgm_cue == "soft piano"
