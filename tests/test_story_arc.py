"""V3.2 自动开发冲刺（轮 8）—— 故事弧线 StoryArc 单测。

覆盖：
  - StoryArc.from_json 解析（含 shot_id / continuity_from_prev）
  - to_storyboards() 拆 N 个 Storyboard（每个 1 shot）
  - 跨 shot 角色一致性（character 锁定 + style_anchor 锁定）
  - continuity 字段保留
  - 字段缺失/非法时容错
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
SAMPLE = Path(__file__).resolve().parents[1] / "sample"
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
TESTS_DIR = Path(__file__).resolve().parent

for p in (SANDBOX, SCRIPTS, TESTS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from hailuo_video.script_engine import Character, SceneSpec, Shot, StoryArc, Storyboard  # noqa: E402


# ---------- Fixtures ----------

def _make_day1_arc_dict() -> dict:
    """新鲜 day1 arc dict（避免测试间 fixture 共享污染）。"""
    return {
        "title": "测试故事 Day 1",
        "style_anchor": "test style anchor",
        "characters": [
            {"name": "橘猫", "appearance": "测试猫外观", "personality": ["温柔"], "color_palette": ["橙黄"]},
        ],
        "scenes": [
            {"id": 1, "location": "窗台", "atmosphere": "晨光", "key_elements": ["窗框"]},
            {"id": 2, "location": "庭院", "atmosphere": "正午", "key_elements": ["花丛"]},
        ],
        "shots": [
            {
                "shot_id": "shot_001", "scene_id": 1, "shot_type": "中景",
                "description": "窗台上的猫", "characters": ["橘猫"],
                "camera_motion": "缓慢推近", "subject_action": "猫伸懒腰",
                "duration": 6, "motion_complexity": "simple", "mood": ["温柔"],
                "continuity_from_prev": "STORY OPENING",
            },
            {
                "shot_id": "shot_002", "scene_id": 2, "shot_type": "中景",
                "description": "庭院里走", "characters": ["橘猫"],
                "camera_motion": "跟随-主体运动", "subject_action": "猫走",
                "duration": 6, "motion_complexity": "simple", "mood": ["活力"],
                "continuity_from_prev": "从 shot_001 窗台走出",
            },
            {
                "shot_id": "shot_003", "scene_id": 2, "shot_type": "远景",
                "description": "屋檐剪影", "characters": ["橘猫"],
                "camera_motion": "拉远", "subject_action": "猫蹲",
                "duration": 6, "motion_complexity": "static", "mood": ["悠远"],
                "continuity_from_prev": "从庭院散步到屋檐",
            },
        ],
    }


@pytest.fixture
def day1_arc_json():
    """每次调用 fixture 都返回新 dict（避免 test 间 mutation）。"""
    return _make_day1_arc_dict()


# ---------- StoryArc.from_json ----------

def test_story_arc_from_dict_parses_all_fields(day1_arc_json):
    arc = StoryArc.from_dict(day1_arc_json)
    assert arc.title == "测试故事 Day 1"
    assert arc.style_anchor == "test style anchor"
    assert len(arc.characters) == 1
    assert arc.characters[0].name == "橘猫"
    assert len(arc.scenes) == 2
    assert len(arc.shots) == 3


def test_story_arc_shot_preserves_shot_id_and_continuity(day1_arc_json):
    arc = StoryArc.from_dict(day1_arc_json)
    s0 = arc.shots[0]
    assert s0.shot_id == "shot_001"
    assert s0.continuity_from_prev == "STORY OPENING"
    s2 = arc.shots[2]
    assert s2.shot_id == "shot_003"
    assert "屋檐" in s2.continuity_from_prev


def test_story_arc_shot_motion_complexity_validated(day1_arc_json):
    """非法 motion_complexity → fallback "static"（不抛错）。"""
    day1_arc_json["shots"][1]["motion_complexity"] = "invalid_value"
    arc = StoryArc.from_dict(day1_arc_json)
    assert arc.shots[1].motion_complexity == "static"


def test_story_arc_shot_duration_fallback(day1_arc_json):
    """duration 缺失或非数字 → fallback 5.0。"""
    day1_arc_json["shots"][0]["duration"] = "not_a_number"
    arc = StoryArc.from_dict(day1_arc_json)
    assert arc.shots[0].expected_duration == 5.0


# ---------- StoryArc.to_storyboards ----------

def test_to_storyboards_returns_one_per_shot(day1_arc_json):
    arc = StoryArc.from_dict(day1_arc_json)
    sbs = arc.to_storyboards()
    assert len(sbs) == 3
    for sb in sbs:
        # duck typing（避免 reload 后类身份失效）
        assert hasattr(sb, "shots")
        assert hasattr(sb, "style_anchor")
        assert len(sb.shots) == 1


def test_to_storyboards_preserves_character_continuity(day1_arc_json):
    """跨 shot 主角一致（同一只橘猫）—— 这是故事产品能力的核心。"""
    arc = StoryArc.from_dict(day1_arc_json)
    sbs = arc.to_storyboards()
    for sb in sbs:
        # 每个子 Storyboard 都带同一只橘猫
        assert sb.characters[0].name == "橘猫"
        assert sb.characters[0].appearance == "测试猫外观"


def test_to_storyboards_preserves_style_anchor(day1_arc_json):
    """style_anchor 必须每个子 Storyboard 都继承（写实风格基线一致）。"""
    arc = StoryArc.from_dict(day1_arc_json)
    sbs = arc.to_storyboards()
    for sb in sbs:
        assert sb.style_anchor == "test style anchor"


def test_to_storyboards_title_includes_shot_id(day1_arc_json):
    """子 Storyboard title = "{arc.title} · {shot.shot_id}"（便于日志）。"""
    arc = StoryArc.from_dict(day1_arc_json)
    sbs = arc.to_storyboards()
    assert "shot_001" in sbs[0].title
    assert "shot_003" in sbs[2].title


# ---------- build_storyboards.py 脚本 ----------

def test_build_storyboards_writes_one_json_per_shot(day1_arc_json, tmp_path):
    arc_json = tmp_path / "arc.json"
    arc_json.write_text(json.dumps(day1_arc_json, ensure_ascii=False), encoding="utf-8")
    output_dir = tmp_path / "storyboards"

    sys.path.insert(0, str(SCRIPTS))
    from build_storyboards import build_storyboards
    written = build_storyboards(arc_json, output_dir)

    assert len(written) == 3
    assert (output_dir / "shot_001.json").is_file()
    assert (output_dir / "shot_002.json").is_file()
    assert (output_dir / "shot_003.json").is_file()
    # 每个子 storyboard 含 1 shot 且 shots 长度 = 1
    sb1 = json.loads((output_dir / "shot_001.json").read_text(encoding="utf-8"))
    assert len(sb1["shots"]) == 1
    assert sb1["shots"][0]["shot_id"] == "shot_001"


def test_build_storyboards_substory_has_continuity(day1_arc_json, tmp_path):
    """continuity_from_prev 字段在拆分后保留（pipeline 后续 agent 可读取）。"""
    arc_json = tmp_path / "arc.json"
    arc_json.write_text(json.dumps(day1_arc_json, ensure_ascii=False), encoding="utf-8")
    output_dir = tmp_path / "storyboards"

    sys.path.insert(0, str(SCRIPTS))
    from build_storyboards import build_storyboards
    build_storyboards(arc_json, output_dir)

    sb2 = json.loads((output_dir / "shot_002.json").read_text(encoding="utf-8"))
    assert "continuity_from_prev" in sb2["shots"][0]
    assert sb2["shots"][0]["continuity_from_prev"] == "从 shot_001 窗台走出"


# ---------- 端到端：读 sample/ 真实 story_arc ----------

def test_real_day1_json_parses():
    arc_path = SAMPLE / "story_arc_orange_cat_4_seasons" / "day1.json"
    if not arc_path.is_file():
        pytest.skip("sample/story_arc_orange_cat_4_seasons/day1.json 不存在")
    arc = StoryArc.from_json(arc_path)
    assert arc.title.startswith("橘猫·四季")
    assert len(arc.shots) == 3
    sbs = arc.to_storyboards()
    assert len(sbs) == 3
    # 同主角 / 同 style_anchor
    for sb in sbs:
        assert sb.characters[0].name == "橘猫"
        assert "ARRI Alexa" in sb.style_anchor or "Cooke" in sb.style_anchor


def test_real_day2_continuity_links_back_to_day1():
    """Day 2 shot_004 的 continuity_from_prev 必须引用 Day 1 shot_003。"""
    arc = StoryArc.from_json(SAMPLE / "story_arc_orange_cat_4_seasons" / "day2.json")
    s0 = arc.shots[0]
    assert "Day 1 shot_003" in s0.continuity_from_prev


def test_real_day2_shot_006_circular_continuity():
    """Day 2 shot_006 必须引用 Day 1 shot_001（首尾呼应——故事成环）。"""
    arc = StoryArc.from_json(SAMPLE / "story_arc_orange_cat_4_seasons" / "day2.json")
    last = arc.shots[-1]
    assert "Day 1 shot_001" in last.continuity_from_prev
