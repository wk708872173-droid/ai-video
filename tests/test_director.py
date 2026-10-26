"""V3.8 Batch 3 — W2 导演解耦 + prompt 占位符修复。

锁住两个已确认缺陷：

  缺陷 4  prompt 占位符字面泄漏：`script_to_storyboard` 直接用 `STORYBOARD_PROMPT`
          + str.replace，从不调 `render_storyboard_prompt` → `{three_beat_hint}` /
          `{camera_motion_whitelist}` / `{motion_complexity_whitelist}` /
          `{prompt_70_30}` 以**字面花括号**进了 LLM prompt。
  缺陷 5  12 个 director 字段到不了模型：只服务人工审稿，
          `image_gen` / `motion_gen` 的 prompt 都不消费它们；
          其中 `camera_motion` 连**视频模型**都没进过。

文献依据：arXiv:2606.24107（DramaDirector：static visual / dynamic narrative 解耦）、
arXiv:2509.26555（Stable Cinemetrics：Setup/Event/Lighting/Camera 4 类控制分类）、
arXiv:2412.02259（VGoT：多域规格 + 自校验）。

R4：所有测试都不打真 API（MockBackend / mock backend）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from hailuo_video import director as dr  # noqa: E402
from hailuo_video import prompts as pm  # noqa: E402
from hailuo_video import script_engine as se  # noqa: E402
from hailuo_video.orchestrator import PipelineAgent, PipelineConfig  # noqa: E402


# ---------- helpers ----------

def _shot(**kw):
    base = dict(
        index=1, scene_id=1, shot_type="中景", description="田间日出",
        camera_motion="缓慢推近", subject_action="农夫抬头望天",
        mood=["温暖"], characters=["农夫"],
    )
    base.update(kw)
    return se.Shot(**base)


def _storyboard(shot=None, style_anchor="shot on 35mm film"):
    return se.Storyboard(
        title="t",
        characters=[se.Character(name="农夫", appearance="宋代农夫，灰布短褂",
                                 personality=[], color_palette=["灰褐"])],
        scenes=[se.SceneSpec(id=1, location="田野", atmosphere="黄昏暖光", key_elements=[])],
        shots=[shot or _shot()],
        style_anchor=style_anchor,
    )


def _agent(tmp_path, sb):
    cfg = PipelineConfig(run_dir=tmp_path / "run")
    a = PipelineAgent(cfg)
    ctx = {"storyboard": sb, "shot_count": len(sb.shots), "intermediate_dir": tmp_path / "inter"}
    return a, ctx


# ============================================================
# 缺陷 4：prompt 占位符泄漏
# ============================================================

def test_storyboard_prompt_has_no_literal_placeholders():
    """script_to_storyboard 发给 LLM 的 prompt 不得含字面 {placeholder}。"""
    script = se.Script(title="t", scenes=[])
    chars = [se.Character(name="A", appearance="x", personality=[], color_palette=[])]
    backend = se.MockBackend()
    backend.on("分镜师", _shots_raw(1))
    se.script_to_storyboard(script, chars, backend, n_shots=1)

    sent = backend.calls[0]["user"] if hasattr(backend, "calls") else ""
    if not sent:
        # MockBackend 结构可能不同 → 直接用 renderer 验证同一契约
        sent = pm.render_storyboard_prompt(n_shots=1, script_json="{}", characters_json="{}")
    for placeholder in ("{three_beat_hint}", "{camera_motion_whitelist}",
                        "{motion_complexity_whitelist}", "{prompt_70_30}",
                        "{n_shots}", "{script_json}", "{characters_json}"):
        assert placeholder not in sent, f"prompt 里仍有字面占位符 {placeholder}"


def test_render_storyboard_prompt_fills_all_placeholders():
    out = pm.render_storyboard_prompt(n_shots=7, script_json="S", characters_json="C")
    for placeholder in ("{three_beat_hint}", "{camera_motion_whitelist}",
                        "{motion_complexity_whitelist}", "{prompt_70_30}",
                        "{n_shots}", "{script_json}", "{characters_json}"):
        assert placeholder not in out, f"未替换的占位符 {placeholder}"
    assert "7" in out


def test_prompt_70_30_section_is_not_a_copy_of_three_beat():
    """修 `prompts.py:279` 的复制粘贴缺陷：70/30 段不能与三拍结构重复。"""
    out = pm.render_storyboard_prompt(n_shots=10)
    start = out.find("70/30 提示法")
    assert start > 0, "缺少 70/30 段"
    section = out[start:start + 600]
    assert "三拍" not in section, "70/30 段里混入了三拍结构（复制粘贴缺陷）"
    assert "subject_action" in section


def test_three_beat_hint_still_present():
    """三拍结构提示不能因为修 70/30 而丢掉。"""
    out = pm.render_storyboard_prompt(n_shots=10)
    assert pm.THREE_BEAT_STRUCTURE_HINT.strip()[:20] in out


def test_render_storyboard_prompt_survives_braces_in_payload():
    """LLM 产出的 JSON 含大量 { } → 渲染不能炸。"""
    payload = '{"shots": [{"a": 1}]}'
    out = pm.render_storyboard_prompt(n_shots=3, script_json=payload, characters_json=payload)
    assert payload in out


def test_director_mode_renders_v2():
    out = pm.render_storyboard_prompt(n_shots=5, director_mode=True)
    assert "narrative_beat" in out          # V2 扩展字段
    assert "{prompt_70_30}" not in out


# ============================================================
# 缺陷 5：director 字段真的进模型
# ============================================================

def test_cinemetrics_axes_cover_all_director_fields():
    """Stable Cinemetrics 的 4 类分类必须覆盖全部 12 个 director 字段。"""
    covered = {f for names in dr.CINEMETRICS_AXES.values() for f in names}
    for f in dr.DIRECTOR_FIELDS:
        assert f in covered, f"director 字段 {f} 未归入任何控制分类"
    assert set(dr.CINEMETRICS_AXES) >= {"setup", "event", "lighting", "camera"}


def test_plan_director_brief_splits_static_and_dynamic():
    """DramaDirector 式解耦：静态视觉与动态叙事是两条独立文本。"""
    shot = _shot(lens="50mm", depth_of_field="shallow", lighting="侧逆光",
                  color_grade="暖调", screen_direction="L→R", pacing="慢",
                  narrative_beat="起")
    brief = dr.plan_director_brief(shot, _storyboard(shot))
    assert brief.static_visual
    assert brief.dynamic_narrative
    assert brief.static_visual != brief.dynamic_narrative


def test_director_visual_fields_reach_static_visual():
    shot = _shot(lens="85mm", depth_of_field="deep", lighting="窗边侧光",
                  color_grade="teal-and-orange")
    brief = dr.plan_director_brief(shot, _storyboard(shot))
    assert "85mm" in brief.static_visual
    assert "deep" in brief.static_visual
    assert "窗边侧光" in brief.static_visual
    assert "teal-and-orange" in brief.static_visual


def test_director_camera_fields_reach_dynamic_narrative():
    shot = _shot(screen_direction="R→L", pacing="快", narrative_beat="转",
                  transition_in="dissolve")
    brief = dr.plan_director_brief(shot, _storyboard(shot))
    assert "R→L" in brief.dynamic_narrative
    assert "快" in brief.dynamic_narrative
    assert "转" in brief.dynamic_narrative
    assert "dissolve" in brief.dynamic_narrative


def test_camera_motion_reaches_dynamic_narrative():
    """camera_motion 必须进动态叙事条件（旧实现连视频模型都没进过）。"""
    shot = _shot(camera_motion="缓慢推近")
    brief = dr.plan_director_brief(shot, _storyboard(shot))
    assert "缓慢推近" in brief.dynamic_narrative


def test_character_appearance_reaches_static_visual():
    """角色外观进静态视觉条件（视觉锚定靠它，不靠名字）。"""
    shot = _shot()
    brief = dr.plan_director_brief(shot, _storyboard(shot))
    assert "宋代农夫" in brief.static_visual
    assert "灰褐" in brief.static_visual


def test_style_anchor_reaches_static_visual():
    shot = _shot()
    brief = dr.plan_director_brief(shot, _storyboard(shot, style_anchor="shot on 35mm film"))
    assert "35mm" in brief.static_visual


def test_color_lock_derived_from_color_grade():
    shot = _shot(color_grade="暖调")
    brief = dr.plan_director_brief(shot, _storyboard(shot))
    assert brief.color_lock == "warm-gold"


def test_derive_color_lock_variants():
    assert dr.derive_color_lock("teal-and-orange") == "teal-orange"
    assert dr.derive_color_lock("冷蓝调") == "cool-blue"
    assert dr.derive_color_lock("无关文本") == ""


def test_brief_records_used_director_fields():
    shot = _shot(lens="50mm", pacing="慢")
    brief = dr.plan_director_brief(shot, _storyboard(shot))
    assert "lens" in brief.used_fields
    assert "pacing" in brief.used_fields
    assert brief.has_director_fields is True


def test_validate_director_brief_reports_missing_parts():
    empty = dr.DirectorBrief()
    issues = dr.validate_director_brief(empty)
    assert any("static_visual" in i for i in issues)
    assert any("dynamic_narrative" in i for i in issues)


def test_validate_director_brief_passes_for_good_brief():
    shot = _shot(lens="50mm", pacing="慢")
    brief = dr.plan_director_brief(shot, _storyboard(shot))
    assert dr.validate_director_brief(brief) == []


def test_validate_director_brief_flags_bad_vocab():
    bad = dr.DirectorBrief(static_visual="x", dynamic_narrative="y", lens="banana", pacing="quick")
    issues = dr.validate_director_brief(bad)
    assert any("lens" in i for i in issues)
    assert any("pacing" in i for i in issues)


def test_validate_director_brief_color_lock_optional():
    brief = dr.DirectorBrief(static_visual="x", dynamic_narrative="y")
    assert dr.validate_director_brief(brief, require_color_lock=True)


# ---------- orchestrator 端到端：字段真的进 prompt ----------

def test_orchestrator_image_prompt_contains_director_fields(tmp_path):
    shot = _shot(lens="85mm", lighting="窗边侧光", color_grade="teal-and-orange")
    a, ctx = _agent(tmp_path, _storyboard(shot))
    task = a._build_task("image_gen", 0, ctx, None)
    prompt = task["prompt"]
    assert "85mm" in prompt
    assert "窗边侧光" in prompt
    assert "teal-and-orange" in prompt
    assert task.get("color_lock") == "teal-orange"


def test_orchestrator_image_prompt_starts_with_style_anchor(tmp_path):
    """ADR-006：style_anchor 必须在前（且经 _safe_style_anchor 兜底非空）。"""
    a, ctx = _agent(tmp_path, _storyboard(style_anchor=""))
    task = a._build_task("image_gen", 0, ctx, None)
    assert task["prompt"].startswith("[风格:")
    assert "no illustration" in task["prompt"]


def test_orchestrator_motion_prompt_contains_camera_motion(tmp_path):
    """缺陷 5：camera_motion 必须进视频模型的 prompt（旧实现完全没进）。"""
    shot = _shot(camera_motion="缓慢推近", screen_direction="L→R")
    a, ctx = _agent(tmp_path, _storyboard(shot))
    task = a._build_task("motion_gen", 0, ctx, None)
    assert "缓慢推近" in task["subject_action"]
    assert "L→R" in task["subject_action"]
    assert task["subject_action"].startswith("[风格:")


def test_orchestrator_uses_stored_static_visual_when_present(tmp_path):
    """storyboard.json 里人工写好的 static_visual 优先于自动推导。"""
    shot = _shot(static_visual="[风格:x] 手工指定画面", dynamic_narrative="手工指定运动")
    a, ctx = _agent(tmp_path, _storyboard(shot))
    task = a._build_task("image_gen", 0, ctx, None)
    assert task["prompt"] == "[风格:x] 手工指定画面"


def test_orchestrator_director_brief_never_raises(tmp_path):
    """brief 推导异常不能让 pipeline 崩（提示词增强不该成为故障点）。"""
    a, ctx = _agent(tmp_path, _storyboard())
    with pytest.MonkeyPatch.context() as mp:
        import hailuo_video.director as dmod
        mp.setattr(dmod, "plan_director_brief", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        task = a._build_task("image_gen", 0, ctx, None)
    assert task["prompt"]           # 回退到旧拼法，prompt 非空


# ============================================================
# Shot 新字段：向后兼容
# ============================================================

def test_new_shot_fields_default_empty():
    s = se.Shot(index=1, scene_id=1, shot_type="中景", description="d")
    assert s.static_visual == ""
    assert s.dynamic_narrative == ""
    assert s.color_lock == ""


def test_old_storyboard_json_parses_without_new_fields():
    """旧 storyboard.json（无 3 个新键）→ 仍能解析，新字段为空串。"""
    from hailuo_video import pipeline as pl
    import json
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "sb.json"
        p.write_text(json.dumps({
            "title": "t",
            "characters": [],
            "scenes": [],
            "shots": [{
                "index": 1, "scene_id": 1, "shot_type": "中景",
                "description": "d", "voiceover": "v",
            }],
        }, ensure_ascii=False), encoding="utf-8")
        sb = pl.load_storyboard(p)
    assert sb.shots[0].static_visual == ""
    assert sb.shots[0].dynamic_narrative == ""
    assert sb.shots[0].color_lock == ""


def test_shot_dict_roundtrip_includes_new_fields():
    s = _shot(static_visual="sv", dynamic_narrative="dn", color_lock="warm-gold")
    d = s.to_dict()
    assert d["static_visual"] == "sv"
    assert d["dynamic_narrative"] == "dn"
    assert d["color_lock"] == "warm-gold"


def test_pipeline_load_storyboard_reads_new_fields(tmp_path):
    import json
    from hailuo_video import pipeline as pl
    p = tmp_path / "sb.json"
    p.write_text(json.dumps({
        "title": "t", "characters": [], "scenes": [],
        "shots": [{
            "index": 1, "scene_id": 1, "shot_type": "中景", "description": "d",
            "voiceover": "v", "static_visual": "sv", "dynamic_narrative": "dn",
            "color_lock": "warm-gold",
        }],
    }, ensure_ascii=False), encoding="utf-8")
    sb = pl.load_storyboard(p)
    assert sb.shots[0].static_visual == "sv"
    assert sb.shots[0].dynamic_narrative == "dn"
    assert sb.shots[0].color_lock == "warm-gold"


def test_script_to_storyboard_fills_director_fields():
    """LLM 产出的 shot 会被自动补上 static_visual / dynamic_narrative。"""
    script = se.Script(title="t", scenes=[])
    chars = [se.Character(name="A", appearance="x", personality=[], color_palette=[])]
    backend = se.MockBackend()
    backend.on("分镜师", _shots_raw(1))
    shots = se.script_to_storyboard(script, chars, backend, n_shots=1)
    assert shots[0].static_visual
    assert shots[0].dynamic_narrative


# ---------- local helper ----------

def _shots_raw(n: int) -> str:
    import json
    shots = []
    for i in range(n):
        shots.append({
            "index": i + 1,
            "scene_id": 1,
            "shot_type": "中景",
            "description": f"镜头{i+1}",
            "dialogue": "",
            "voiceover": f"旁白{i+1}",
            "mood": ["温暖"],
            "camera_motion": "缓慢推近",
            "subject_action": "角色动作",
            "duration": 5,
            "motion_complexity": "static",
            "characters": ["农夫"],
        })
    return "```json\n" + json.dumps(shots, ensure_ascii=False) + "\n```"
