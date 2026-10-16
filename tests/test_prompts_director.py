"""V3.5 Director 升级：prompts_director.py 单测。"""
from __future__ import annotations

import sys
from pathlib import Path

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))


# ---------- 白名单完整性 ----------

def test_lens_whitelist_min_size():
    """焦段白名单至少 5 种。"""
    from hailuo_video.prompts_director import LENS_WHITELIST
    assert len(LENS_WHITELIST) >= 5


def test_lens_whitelist_contains_key_lenses():
    """关键焦段必须含：wide / 35mm / 50mm / 85mm / telephoto。"""
    from hailuo_video.prompts_director import LENS_WHITELIST
    for lens in ("wide", "35mm", "50mm", "85mm", "telephoto"):
        assert lens in LENS_WHITELIST, f"missing lens: {lens}"


def test_transition_whitelist_contains_4_choices():
    from hailuo_video.prompts_director import TRANSITION_WHITELIST
    for t in ("cut", "dissolve", "match_cut", "fade"):
        assert t in TRANSITION_WHITELIST, f"missing transition: {t}"


def test_pacing_whitelist_chinese():
    """节奏白名单用中文（与 shot.mood 一致）。"""
    from hailuo_video.prompts_director import PACING_WHITELIST
    assert PACING_WHITELIST == ("快", "中", "慢")


def test_narrative_beat_whitelist_4():
    """起承转合 4 拍。"""
    from hailuo_video.prompts_director import NARRATIVE_BEAT_WHITELIST
    assert NARRATIVE_BEAT_WHITELIST == ("起", "承", "转", "合")


def test_depth_of_field_whitelist_2():
    from hailuo_video.prompts_director import DEPTH_OF_FIELD_WHITELIST
    assert DEPTH_OF_FIELD_WHITELIST == ("shallow", "deep")


def test_director_role_whitelists_aggregated():
    """DIRECTOR_ROLE_WHITELISTS 字典聚合所有白名单。"""
    from hailuo_video.prompts_director import DIRECTOR_ROLE_WHITELISTS
    assert "lens" in DIRECTOR_ROLE_WHITELISTS
    assert "transition" in DIRECTOR_ROLE_WHITELISTS
    assert "pacing" in DIRECTOR_ROLE_WHITELISTS
    assert "narrative_beat" in DIRECTOR_ROLE_WHITELISTS
    assert "depth_of_field" in DIRECTOR_ROLE_WHITELISTS


# ---------- 系统 Prompt 完整性 ----------

def test_dop_system_prompt_mentions_arri():
    from hailuo_video.prompts_director import DOP_SYSTEM_PROMPT
    assert "ARRI" in DOP_SYSTEM_PROMPT
    assert "摄影指导" in DOP_SYSTEM_PROMPT or "DOP" in DOP_SYSTEM_PROMPT


def test_editor_system_prompt_mentions_9_16():
    from hailuo_video.prompts_director import EDITOR_SYSTEM_PROMPT
    assert "9:16" in EDITOR_SYSTEM_PROMPT or "竖屏" in EDITOR_SYSTEM_PROMPT
    assert "剪辑" in EDITOR_SYSTEM_PROMPT


def test_operator_system_prompt_mentions_xiaohongshu():
    from hailuo_video.prompts_director import OPERATOR_SYSTEM_PROMPT
    assert "小红书" in OPERATOR_SYSTEM_PROMPT
    assert "hook" in OPERATOR_SYSTEM_PROMPT or "钩子" in OPERATOR_SYSTEM_PROMPT


# ---------- User Prompt 模板 ----------

def test_dop_prompt_v1_mentions_lens_whitelist():
    from hailuo_video.prompts_director import DOP_PROMPT_V1
    assert "{lens_whitelist}" in DOP_PROMPT_V1
    assert "{n_shots}" in DOP_PROMPT_V1
    assert "{shots_json}" in DOP_PROMPT_V1


def test_editor_prompt_v1_mentions_whitelist_and_constraints():
    from hailuo_video.prompts_director import EDITOR_PROMPT_V1
    assert "{transition_whitelist}" in EDITOR_PROMPT_V1
    assert "fade" in EDITOR_PROMPT_V1  # 首尾 fade 提示


def test_operator_prompt_v1_mentions_platform_recos():
    from hailuo_video.prompts_director import OPERATOR_PROMPT_V1
    assert "platform_recos" in OPERATOR_PROMPT_V1
    assert "hashtags" in OPERATOR_PROMPT_V1
    assert "best_post_time" in OPERATOR_PROMPT_V1


# ---------- 共用段落 ----------

def test_director_guidance_block_has_6_dimensions():
    """导演视角段落必须含 6 个维度：构图 / 镜头语言 / 光线 / 视线 / 屏幕方向 / 节奏。"""
    from hailuo_video.prompts_director import DIRECTOR_GUIDANCE_BLOCK
    for dim in ("构图", "镜头语言", "光线", "视线", "屏幕方向", "节奏"):
        assert dim in DIRECTOR_GUIDANCE_BLOCK, f"missing dimension: {dim}"


def test_director_guidance_block_rejects_abstract():
    from hailuo_video.prompts_director import DIRECTOR_GUIDANCE_BLOCK
    assert "禁止" in DIRECTOR_GUIDANCE_BLOCK
    assert "很温馨" in DIRECTOR_GUIDANCE_BLOCK or "纯抽象" in DIRECTOR_GUIDANCE_BLOCK


# ---------- 解析器：lenient 行为 ----------

def test_parse_dop_response_valid_fenced_json():
    """正常 DOP 输出（fenced JSON）能正确解析。"""
    from hailuo_video.prompts_director import parse_dop_response
    raw = """```json
{
  "dop": [
    {
      "shot_id": "shot_001",
      "lens": "50mm",
      "depth_of_field": "shallow",
      "lighting": "暖黄金色侧光从 camera-right 切入",
      "screen_direction": "L→R",
      "color_grade": "暖金色（黄昏）"
    }
  ]
}
```"""
    out = parse_dop_response(raw)
    assert "shot_001" in out
    assert out["shot_001"]["lens"] == "50mm"
    assert out["shot_001"]["depth_of_field"] == "shallow"
    assert "camera-right" in out["shot_001"]["lighting"]
    assert out["shot_001"]["color_grade"] == "暖金色（黄昏）"


def test_parse_dop_response_rejects_invalid_lens():
    """白名单外的 lens 值 → lenient 回退空串。"""
    from hailuo_video.prompts_director import parse_dop_response
    raw = '{"dop": [{"shot_id": "shot_001", "lens": "99mm", "depth_of_field": "shallow"}]}'
    out = parse_dop_response(raw)
    assert out["shot_001"]["lens"] == ""  # 99mm 不在白名单
    assert out["shot_001"]["depth_of_field"] == "shallow"  # valid 保留


def test_parse_dop_response_invalid_json_returns_empty():
    from hailuo_video.prompts_director import parse_dop_response
    out = parse_dop_response("not json at all")
    assert out == {}


def test_parse_dop_response_missing_dop_key_returns_empty():
    from hailuo_video.prompts_director import parse_dop_response
    out = parse_dop_response('{"wrong_key": []}')
    assert out == {}


def test_parse_editor_response_valid():
    from hailuo_video.prompts_director import parse_editor_response
    raw = """{"editor": [{"shot_id": "shot_001", "transition_in": "fade", "transition_out": "cut", "pacing": "中", "bgm_cue": "soft piano"}]}"""
    out = parse_editor_response(raw)
    assert out["shot_001"]["pacing"] == "中"
    assert out["shot_001"]["transition_in"] == "fade"


def test_parse_editor_response_rejects_invalid_pacing():
    """节奏白名单外值 → 空串。"""
    from hailuo_video.prompts_director import parse_editor_response
    raw = '{"editor": [{"shot_id": "shot_001", "pacing": "very fast"}]}'
    out = parse_editor_response(raw)
    assert out["shot_001"]["pacing"] == ""


def test_parse_operator_response_valid():
    from hailuo_video.prompts_director import parse_operator_response
    raw = """{"operator": {
        "hook": "奶黄伸懒腰——整个下午都甜了",
        "captions": ["今日份治愈", "萌宠治愈", "幼犬"],
        "hashtags": ["#治愈", "#萌宠"],
        "best_post_time": "19:30-21:00",
        "platform_recos": {"xiaohongshu": "封面奶黄特写"}
    }}"""
    out = parse_operator_response(raw)
    assert "奶黄" in out["hook"]
    assert len(out["captions"]) == 3
    assert "#治愈" in out["hashtags"]
    assert "xiaohongshu" in out["platform_recos"]


def test_parse_operator_response_invalid_returns_empty():
    from hailuo_video.prompts_director import parse_operator_response
    out = parse_operator_response("garbage")
    assert out == {}


# ---------- 回填助手 ----------

def test_apply_dop_to_shot_fills_empty_only():
    """DOP 回填只填空字段（幂等）。"""
    from hailuo_video.prompts_director import apply_dop_to_shot
    shot = {"lens": "85mm", "lighting": ""}
    dop = {"lens": "50mm", "lighting": "侧光", "screen_direction": "L→R"}
    apply_dop_to_shot(shot, dop)
    # 已有 "85mm" 不被覆盖
    assert shot["lens"] == "85mm"
    # 空字段被回填
    assert shot["lighting"] == "侧光"
    # 新字段被加
    assert shot["screen_direction"] == "L→R"


def test_apply_editor_to_shot_fills_empty_only():
    from hailuo_video.prompts_director import apply_editor_to_shot
    shot = {"pacing": "中"}
    ed = {"pacing": "快", "bgm_cue": "soft piano"}
    apply_editor_to_shot(shot, ed)
    # 已有 "中" 不被覆盖
    assert shot["pacing"] == "中"
    # 新字段被加
    assert shot["bgm_cue"] == "soft piano"


def test_apply_dop_to_shot_empty_dop_noop():
    from hailuo_video.prompts_director import apply_dop_to_shot
    shot = {"lens": ""}
    apply_dop_to_shot(shot, {})
    # 不报错，shot 不变
    assert shot == {"lens": ""}


# ---------- __all__ 导出完整性 ----------

def test_director_module_all_exports():
    from hailuo_video import prompts_director
    expected = {
        "DOP_SYSTEM_PROMPT", "EDITOR_SYSTEM_PROMPT", "OPERATOR_SYSTEM_PROMPT",
        "DOP_PROMPT_V1", "EDITOR_PROMPT_V1", "OPERATOR_PROMPT_V1",
        "LENS_WHITELIST", "TRANSITION_WHITELIST", "PACING_WHITELIST",
        "NARRATIVE_BEAT_WHITELIST", "DEPTH_OF_FIELD_WHITELIST",
        "DIRECTOR_ROLE_WHITELISTS",
        "DIRECTOR_GUIDANCE_BLOCK",
        "parse_dop_response", "parse_editor_response", "parse_operator_response",
        "apply_dop_to_shot", "apply_editor_to_shot",
    }
    assert set(prompts_director.__all__) == expected
