"""V3.2 自动开发冲刺（轮 2-1）STORYBOARD_PROMPT 单测。"""
from __future__ import annotations

import sys
from pathlib import Path

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from hailuo_video.prompts import (  # noqa: E402
    CAMERA_MOTION_WHITELIST,
    MOTION_COMPLEXITY_WHITELIST,
    STORYBOARD_PROMPT,
    THREE_BEAT_STRUCTURE_HINT,
    render_storyboard_prompt,
)


# ---------- Whitelist 完整性 ----------

def test_camera_motion_whitelist_min_size():
    """白名单至少 16 种（与 PRD 文档一致）。"""
    assert len(CAMERA_MOTION_WHITELIST) >= 16


def test_camera_motion_whitelist_contains_key_motions():
    """关键运镜必须含：固定 / 推近 / 拉远 / 跟随 / 环绕。"""
    assert "固定" in CAMERA_MOTION_WHITELIST
    assert "缓慢推近" in CAMERA_MOTION_WHITELIST
    assert "缓慢拉远" in CAMERA_MOTION_WHITELIST
    assert "推进, 跟随" in CAMERA_MOTION_WHITELIST
    assert "环绕-顺时针" in CAMERA_MOTION_WHITELIST


def test_motion_complexity_whitelist_is_strict():
    """motion_complexity 白名单仅 3 种。"""
    assert MOTION_COMPLEXITY_WHITELIST == ("static", "simple", "complex")


def test_three_beat_structure_hint_has_4_beats():
    """三拍结构（起承转合）四个 beats 必须都有。"""
    for beat in ("起", "承", "转", "合"):
        assert beat in THREE_BEAT_STRUCTURE_HINT, f"missing beat: {beat}"


# ---------- render_storyboard_prompt ----------

def test_render_storyboard_prompt_includes_n_shots():
    out = render_storyboard_prompt(n_shots=42, script_json="{}", characters_json="{}")
    assert "42" in out
    assert "STORYBOARD" not in out or "分镜" in out


def test_render_storyboard_prompt_includes_three_beat_hint():
    out = render_storyboard_prompt(n_shots=10)
    assert "三拍结构" in out
    assert "Setup" in out  # 英文别名


def test_render_storyboard_prompt_includes_camera_whitelist():
    out = render_storyboard_prompt(n_shots=10)
    assert "白名单" in out
    assert "缓慢推近" in out


def test_render_storyboard_prompt_includes_motion_complexity_whitelist():
    out = render_storyboard_prompt(n_shots=10)
    assert "static" in out
    assert "simple" in out
    assert "complex" in out


def test_render_storyboard_prompt_includes_70_30_method():
    """70/30 提示法必须在 prompt 中显式说明。"""
    out = render_storyboard_prompt(n_shots=10)
    assert "70/30" in out
    assert "70%" in out
    assert "30%" in out


def test_render_storyboard_prompt_includes_subject_action_format():
    """subject_action 字段的强制格式（动作动词 + 对象/结果）必须在 prompt 中说明。"""
    out = render_storyboard_prompt(n_shots=10)
    assert "subject_action" in out
    assert "动作动词" in out or "动作" in out


def test_render_storyboard_prompt_includes_example_shot():
    """示例 shot 必须含，避免 LLM 字段错填。"""
    out = render_storyboard_prompt(n_shots=10)
    assert "示例 shot" in out or "示例" in out
    assert "shot_001" in out  # 示例 shot_id


def test_render_storyboard_prompt_include_hints_false_skips_explainers():
    """include_hints=False 跳过 explainer 段（三拍结构 + 白名单列表 + Setup/dev/twist 提示），
    但 base schema + 字段约束（提及 70/30 提示法 + 禁止纯抽象）仍保留。"""
    out = render_storyboard_prompt(n_shots=10, include_hints=False)
    # 跳过的 explainer 段：
    assert "三拍结构" not in out
    assert "Setup" not in out  # 英文别名 in explainer
    # 保留：base schema + 字段约束里仍会提到 70/30（作为字段规则）+ 禁止
    assert "70/30" in out
    assert "70%" in out
    assert "禁止" in out
    # 字段约束
    assert "shot_id" in out
    assert "subject_action" in out
    assert "duration" in out
    assert "motion_complexity" in out


def test_render_storyboard_prompt_include_hints_true_has_explainer_sections():
    """include_hints=True（默认）额外包含三拍结构 + 白名单列表。"""
    out = render_storyboard_prompt(n_shots=10)
    # hints 段独有
    assert "三拍结构" in out
    assert "Setup" in out
    # 白名单必须列出至少 16 种运镜
    assert "缓慢推近" in out
    assert "环绕-顺时针" in out


def test_render_storyboard_prompt_include_hints_true_keeps_everything():
    """include_hints=True（默认）保留 70/30 + 三拍 + 白名单。"""
    out = render_storyboard_prompt(n_shots=10)
    assert "70/30" in out
    assert "三拍结构" in out
    assert "白名单" in out
    assert "动作动词" in out


def test_render_storyboard_prompt_includes_script_json():
    """注入的 script_json 必须出现在 prompt 中。"""
    out = render_storyboard_prompt(n_shots=10, script_json="<MY_SCRIPT_JSON>")
    assert "<MY_SCRIPT_JSON>" in out


def test_render_storyboard_prompt_includes_characters_json():
    out = render_storyboard_prompt(n_shots=10, characters_json="<MY_CHARACTERS>")
    assert "<MY_CHARACTERS>" in out


# ---------- STORYBOARD_PROMPT 模板本身 ----------

def test_storyboard_prompt_template_format_placeholders():
    """模板本身必须含 4 个 format 占位符：n_shots / script_json / characters_json + 至少一个 hints。"""
    # 通过尝试 format() 不抛 KeyError 验证
    out = STORYBOARD_PROMPT.format(
        n_shots=10,
        three_beat_hint="",
        camera_motion_whitelist="",
        motion_complexity_whitelist="",
        prompt_70_30="",
        script_json="{}",
        characters_json="{}",
    )
    assert "shot_id" in out
    assert "camera_motion" in out
    assert "subject_action" in out
    assert "motion_complexity" in out
    assert "duration" in out


def test_storyboard_prompt_explicitly_rejects_abstract_descriptions():
    """模板必须禁止纯抽象 / 纯情绪词描述（"很悲伤"是 AI 生图死路）。"""
    out = STORYBOARD_PROMPT
    assert "禁止" in out
    assert "纯抽象" in out or "纯情绪" in out


def test_storyboard_prompt_json_schema_includes_all_required_fields():
    """JSON schema 含所有新字段：shot_id / subject_action / camera_motion / duration / motion_complexity。"""
    for field in ("shot_id", "subject_action", "camera_motion", "duration", "motion_complexity"):
        assert f'"{field}"' in STORYBOARD_PROMPT, f"missing field {field}"
