"""V3.2 自动开发冲刺（轮 5）script → pipeline 链式集成测试。

覆盖：
  - ScriptWriterAgent 输出 Storyboard 结构（必含运动指令字段）
  - Orchestrator._build_task 把 shot 字段映射到各 Agent 输入（image_gen / motion_gen / tts / video_synth）
  - 70/30 提示法拼接到 image_gen.prompt
  - motion_gen.subject_action 来自 shot.subject_action
  - tts.text 来自 shot.dialogue / voiceover 优先级
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from hailuo_video.orchestrator import (  # noqa: E402
    PipelineAgent,
    PipelineConfig,
)
from hailuo_video.agents import (  # noqa: E402
    ScriptWriterAgent,
    AssetPrepAgent,
    ImageGenAgent,
    MotionGenerationAgent,
    VideoSynthAgent,
    TTSAgent,
    EditingAgent,
    QAAgent,
    InputValidationError,
)
from hailuo_video.script_engine import (  # noqa: E402
    Character,
    SceneSpec,
    Shot,
    Storyboard,
)


# ---------- Fixtures ----------

def _make_storyboard(n: int = 2) -> Storyboard:
    """建一个 n shots 的最小 Storyboard（含 V3.2 运动字段）。"""
    chars = [
        Character(name="农夫", appearance="宋代农夫", personality=[], color_palette=["灰褐"]),
        Character(name="兔子", appearance="白兔", personality=[], color_palette=["白"]),
    ]
    scenes = [SceneSpec(id=1, location="田野", key_elements=[])]
    shots = [
        Shot(
            index=i + 1,
            scene_id=1,
            shot_type="中景",
            description=f"镜头{i+1}：田间景色",
            voiceover=f"旁白{i+1}",
            camera_motion="缓慢推近",
            subject_action=f"角色动作{i+1}",
            expected_duration=5.0,
            motion_complexity="simple",
            characters=["农夫", "兔子"][i % 2:i % 2 + 1],
            mood=["紧张", "期待"][i % 2:i % 2 + 1],
        )
        for i in range(n)
    ]
    return Storyboard(title="t", characters=chars, scenes=scenes, shots=shots)


def _make_cfg(run_dir: Path) -> PipelineConfig:
    return PipelineConfig(
        image_backend=None, video_backend=None, tts_backend=None,
        voice="zh-CN-XiaoxiaoNeural",
        video_seconds=5, video_fps=8, video_effect="cycle",
        assets_dir=Path("./assets"),
        skip_asset_prep=True, keep_intermediate=True, parallel=1,
        motion_budget=0,
        run_dir=run_dir,
        log=None,
    )


# ---------- ScriptWriterAgent 输出验证 ----------

def test_script_writer_agent_output_has_required_motion_fields():
    """ScriptWriterAgent 输出的 Storyboard 必须含 V3.2 运动指令字段。"""
    from hailuo_video.script_engine import Storyboard, Shot, SceneSpec, Character

    # 直接构造 Storyboard，验证 agent 正确传递到 metadata['storyboard']
    expected_sb = Storyboard(
        title="测试",
        characters=[Character(name="农夫", appearance="x", personality=[], color_palette=["灰褐"])],
        scenes=[SceneSpec(id=1, location="田野", key_elements=[])],
        shots=[
            Shot(
                index=1, scene_id=1, shot_type="远景",
                description="田间日出",
                camera_motion="拉远",
                subject_action="太阳升起",
                expected_duration=5.0,
                motion_complexity="simple",
            )
        ],
    )

    from hailuo_video import agents as ag_mod
    # Stub novel_to_storyboard
    ag_mod.se_mod.novel_to_storyboard = lambda novel, backend, **kw: expected_sb
    try:
        agent = ScriptWriterAgent()
        result = agent.execute({"novel": "测试小说"})
    finally:
        # 重置（避免污染其它测试）
        from importlib import reload
        reload(ag_mod.se_mod)

    assert result.ok
    assert "storyboard" in result.metadata
    sb = result.metadata["storyboard"]
    assert sb is expected_sb
    assert hasattr(sb, "shots") and len(sb.shots) == 1
    # Shot 含 V3.2 运动字段
    shot = sb.shots[0]
    assert shot.camera_motion == "拉远"
    assert shot.subject_action == "太阳升起"
    assert shot.motion_complexity == "simple"


def test_script_writer_agent_validate_empty_novel():
    """空 novel → InputValidationError。"""
    agent = ScriptWriterAgent()
    with pytest.raises(InputValidationError, match="novel"):
        agent.validate_inputs({"novel": ""})


def test_script_writer_agent_execute_empty_novel_returns_error():
    """execute(novel="") → AgentResult ok=False。"""
    agent = ScriptWriterAgent()
    result = agent.execute({"novel": ""})
    assert result.ok is False
    assert "novel" in result.error.lower() or "empty" in result.error.lower()


# ---------- Orchestrator _build_task shot → agent 映射 ----------

def test_build_task_image_gen_gets_prompt_from_shot(tmp_path):
    """_build_task(image_gen) → task['prompt'] = description + camera_motion + subject_action + mood。"""
    cfg = _make_cfg(tmp_path / "run")
    a = PipelineAgent(cfg)
    sb = _make_storyboard(n=1)
    ctx = {"storyboard": sb, "shot_count": 1, "intermediate_dir": tmp_path / "inter"}
    task = a._build_task("image_gen", 0, ctx, None)
    # prompt 必须非空且含 description + camera_motion + subject_action
    assert "prompt" in task
    assert "田间景色" in task["prompt"]
    assert "缓慢推近" in task["prompt"]
    assert "角色动作0" in task["prompt"] or "角色动作1" in task["prompt"]  # shot 0 / 1
    # 只传 http(s) URL，不传角色名。缓存缺失时列表为空。
    assert all(u.startswith(("http://", "https://")) for u in task["subject_references"])
    assert "农夫" not in task["subject_references"]


def test_build_task_image_gen_prompt_uses_70_30_format(tmp_path):
    """70/30 提示法：description (70%) + 氛围修饰 (30%) + V3.4 ADR-006 style_anchor 前缀。"""
    cfg = _make_cfg(tmp_path / "run")
    a = PipelineAgent(cfg)
    sb = _make_storyboard(n=1)
    ctx = {"storyboard": sb, "shot_count": 1, "intermediate_dir": tmp_path / "inter"}
    task = a._build_task("image_gen", 0, ctx, None)
    # V3.4 ADR-006：style_anchor 经 _safe_style_anchor 兜底，必含 [风格:...] 前缀在最前
    assert task["prompt"].startswith("[风格:")
    # description 是主体（70%）
    assert "镜头1" in task["prompt"]
    # 包含 [运镜:...] [动作:...] [氛围:...] 等 70/30 修饰
    assert "[中景]" in task["prompt"] or "[运镜:" in task["prompt"]


def test_build_task_motion_gen_gets_subject_action(tmp_path):
    """_build_task(motion_gen) → task['subject_action'] 来自 shot.subject_action + V3.4 ADR-006 前缀。"""
    cfg = _make_cfg(tmp_path / "run")
    a = PipelineAgent(cfg)
    sb = _make_storyboard(n=1)
    ctx = {"storyboard": sb, "shot_count": 1, "intermediate_dir": tmp_path / "inter"}
    task = a._build_task("motion_gen", 0, ctx, None)
    # V3.4 ADR-006：subject_action 现在必含 [风格:...] 前缀（不再是 verbatim）
    assert "角色动作1" in task["subject_action"]
    assert "[风格:" in task["subject_action"]
    assert task["duration"] == 5
    assert task["resolution"] == "1080P"


def test_build_task_tts_gets_dialogue_or_voiceover(tmp_path):
    """_build_task(tts) → task['text'] = shot.dialogue || shot.voiceover。"""
    cfg = _make_cfg(tmp_path / "run")
    a = PipelineAgent(cfg)
    sb = _make_storyboard(n=1)
    ctx = {"storyboard": sb, "shot_count": 1, "intermediate_dir": tmp_path / "inter"}
    task = a._build_task("tts", 0, ctx, None)
    # shot 没有 dialogue → text 应来自 voiceover
    assert task["text"] == "旁白1"
    assert task["write_subtitle"] is True


def test_build_task_tts_prefers_dialogue_over_voiceover(tmp_path):
    """_build_task(tts)：dialogue 优先于 voiceover。"""
    cfg = _make_cfg(tmp_path / "run")
    a = PipelineAgent(cfg)
    sb = _make_storyboard(n=1)
    # 手动设 dialogue
    sb.shots[0].dialogue = "对白内容"
    sb.shots[0].voiceover = "旁白内容"
    ctx = {"storyboard": sb, "shot_count": 1, "intermediate_dir": tmp_path / "inter"}
    task = a._build_task("tts", 0, ctx, None)
    assert task["text"] == "对白内容"


def test_build_task_video_synth_gets_duration(tmp_path):
    """_build_task(video_synth) → task['seconds'] = shot.duration。"""
    cfg = _make_cfg(tmp_path / "run")
    a = PipelineAgent(cfg)
    sb = _make_storyboard(n=1)
    ctx = {"storyboard": sb, "shot_count": 1, "intermediate_dir": tmp_path / "inter"}
    task = a._build_task("video_synth", 0, ctx, None)
    assert task["seconds"] == 5


def test_build_task_returns_base_fields_for_non_shot_agents(tmp_path):
    """_build_task(asset_prep / script_writer / qa) → 走基础字段（不含 shot 映射）。"""
    cfg = _make_cfg(tmp_path / "run")
    a = PipelineAgent(cfg)
    sb = _make_storyboard(n=1)
    ctx = {"storyboard": sb, "shot_count": 1, "intermediate_dir": tmp_path / "inter"}
    for agent_name in ("asset_prep", "script_writer", "qa", "editing"):
        task = a._build_task(agent_name, 0, ctx, None)
        assert "image_backend" in task
        assert "storyboard" in task
        # 不应包含 per-shot 字段
        assert "prompt" not in task


def test_build_task_handles_missing_shot_gracefully(tmp_path):
    """shot_idx 越界 → shot=None → 不抛错，返回基础 task。"""
    cfg = _make_cfg(tmp_path / "run")
    a = PipelineAgent(cfg)
    sb = _make_storyboard(n=1)
    ctx = {"storyboard": sb, "shot_count": 1, "intermediate_dir": tmp_path / "inter"}
    task = a._build_task("image_gen", 99, ctx, None)  # shot_idx 越界
    assert "prompt" not in task  # 没 shot → 没 prompt
    assert "image_backend" in task  # 基础字段仍在


def test_build_task_pipeline_config_fields_propagated(tmp_path):
    """PipelineConfig 的字段（voice / video_seconds / aspect_ratio 等）都进 task。"""
    cfg = PipelineConfig(
        image_backend=None, video_backend=None, tts_backend=None,
        voice="zh-CN-YunxiNeural",
        video_seconds=10, video_fps=12, video_effect="zoom_in",
        aspect_ratio="9:16",       # 竖屏
        assets_dir=Path("./assets"),
        skip_asset_prep=True, keep_intermediate=False, parallel=2,
        motion_budget=0,
        run_dir=tmp_path / "run",
        log=None,
    )
    a = PipelineAgent(cfg)
    sb = _make_storyboard(n=1)
    ctx = {"storyboard": sb, "shot_count": 1, "intermediate_dir": tmp_path / "inter"}
    task = a._build_task("image_gen", 0, ctx, None)
    assert task["voice"] == "zh-CN-YunxiNeural"
    assert task["aspect_ratio"] == "9:16"
    assert task["parallel"] == 2


# ---------- 链式集成：ScriptWriterAgent 输出 → Orchestrator task 构造 ----------

def test_chain_script_writer_output_feeds_build_task(tmp_path):
    """完整链路：ScriptWriterAgent(novel) → Storyboard → Orchestrator._build_task(image_gen)。"""
    from hailuo_video.script_engine import Storyboard, Shot, SceneSpec, Character
    from hailuo_video import agents as ag_mod

    expected_sb = Storyboard(
        title="t",
        characters=[Character(name="农夫", appearance="x", personality=[], color_palette=["灰褐"])],
        scenes=[SceneSpec(id=1, location="田野", key_elements=[])],
        shots=[
            Shot(
                index=1, scene_id=1, shot_type="远景",
                description="田间日出",
                camera_motion="拉远",
                subject_action="太阳升起",
                expected_duration=5.0,
                motion_complexity="simple",
                characters=["农夫"],
            )
        ],
    )
    ag_mod.se_mod.novel_to_storyboard = lambda novel, backend, **kw: expected_sb
    try:
        # Step 1: ScriptWriterAgent 跑 novel → Storyboard
        swa = ScriptWriterAgent()
        result = swa.execute({"novel": "测试"})
    finally:
        from importlib import reload
        reload(ag_mod.se_mod)
    assert result.ok
    sb = result.metadata["storyboard"]

    # Step 2: Orchestrator 用 sb 构造 image_gen task
    cfg = _make_cfg(tmp_path / "run")
    a = PipelineAgent(cfg)
    ctx = {"storyboard": sb, "shot_count": len(sb.shots), "intermediate_dir": tmp_path / "inter"}
    task = a._build_task("image_gen", 0, ctx, None)
    # 链式断言：script 输出的字段完整传到 image_gen.prompt
    assert "田间日出" in task["prompt"]
    assert "拉远" in task["prompt"]
    assert "太阳升起" in task["prompt"]
    assert all(u.startswith(("http://", "https://")) for u in task["subject_references"])
    assert "农夫" not in task["subject_references"]


# ---------- V3.2 轮 7：style_anchor 注入 image_gen / motion_gen ----------


def test_build_task_image_gen_prompt_includes_style_anchor(tmp_path):
    """style_anchor 必须出现在 image_gen.prompt 最前（基线优先）。"""
    cfg = _make_cfg(tmp_path / "run")
    a = PipelineAgent(cfg)
    sb = _make_storyboard(n=1)
    sb.style_anchor = "documentary wildlife photography, Kodak Portra 800"
    ctx = {"storyboard": sb, "shot_count": 1, "intermediate_dir": tmp_path / "inter"}
    task = a._build_task("image_gen", 0, ctx, None)
    assert "documentary wildlife photography" in task["prompt"]
    # style_anchor 必须在 description 之前（[风格:...] tag 紧贴开头）
    assert task["prompt"].startswith("[风格:") or "documentary wildlife photography" in task["prompt"].split(" [")[0]


def test_build_task_image_gen_no_style_anchor_uses_fallback(tmp_path):
    """V3.4 ADR-006：storyboard 无 style_anchor → prompt 仍含 [风格:PHOTOREALISTIC_FALLBACK] 前缀。

    V3.3 行为：sb.style_anchor="" → prompt 不含 [风格:...]
    V3.4 行为：_safe_style_anchor("") = PHOTOREALISTIC_FALLBACK → 必含 [风格:...]
    目的：空 style_anchor 不再放任 prompt 缺 anti-cartoon 守卫。
    """
    from hailuo_video.asset_prep import PHOTOREALISTIC_FALLBACK
    cfg = _make_cfg(tmp_path / "run")
    a = PipelineAgent(cfg)
    sb = _make_storyboard(n=1)
    sb.style_anchor = ""
    ctx = {"storyboard": sb, "shot_count": 1, "intermediate_dir": tmp_path / "inter"}
    task = a._build_task("image_gen", 0, ctx, None)
    # 新行为：必含 [风格:PHOTOREALISTIC_FALLBACK] 前缀（不再是 verbatim）
    assert PHOTOREALISTIC_FALLBACK in task["prompt"]
    assert "[风格:" in task["prompt"]
    assert "镜头1" in task["prompt"]


def test_build_task_motion_gen_includes_style_anchor(tmp_path):
    """motion_gen task['subject_action'] 必须包含 style_anchor（让 Hailuo 知道视频风格）。

    V3.4 ADR-006：style_anchor 自动追加 'no illustration no cartoon no anime no CGI' 守卫词，
    → task["style_anchor"] 不再 verbatim，而是带守卫词的版本。
    """
    cfg = _make_cfg(tmp_path / "run")
    a = PipelineAgent(cfg)
    sb = _make_storyboard(n=1)
    sb.style_anchor = "BBC nature documentary, RED V-Raptor"
    ctx = {"storyboard": sb, "shot_count": 1, "intermediate_dir": tmp_path / "inter"}
    task = a._build_task("motion_gen", 0, ctx, None)
    assert "BBC nature documentary" in task["subject_action"]
    # V3.4 ADR-006：style_anchor 字段也带守卫词
    assert "no illustration no cartoon no anime no CGI" in task["style_anchor"]
    assert "BBC nature documentary, RED V-Raptor" in task["style_anchor"]


def test_build_task_motion_gen_no_style_anchor_uses_fallback(tmp_path):
    """V3.4 ADR-006：style_anchor 空 → motion_gen 用 PHOTOREALISTIC_FALLBACK 兜底（不再是 verbatim）。

    V3.3 行为：sb.style_anchor="" → subject_action 原值（无 [风格:...] 前缀）
    V3.4 行为：_safe_style_anchor("") = PHOTOREALISTIC_FALLBACK → 前缀必含
    目的：空 style_anchor 不再放任 prompt 缺 anti-cartoon 守卫。
    """
    from hailuo_video.asset_prep import PHOTOREALISTIC_FALLBACK
    cfg = _make_cfg(tmp_path / "run")
    a = PipelineAgent(cfg)
    sb = _make_storyboard(n=1)
    sb.style_anchor = ""
    ctx = {"storyboard": sb, "shot_count": 1, "intermediate_dir": tmp_path / "inter"}
    task = a._build_task("motion_gen", 0, ctx, None)
    # 新行为：必含 [风格:PHOTOREALISTIC_FALLBACK] 前缀（不再是 verbatim）
    assert PHOTOREALISTIC_FALLBACK in task["subject_action"]
    assert sb.shots[0].subject_action in task["subject_action"]


def test_build_task_style_anchor_does_not_leak_to_unrelated_agents(tmp_path):
    """style_anchor 只注入 image_gen / motion_gen，不应传给 asset_prep / qa / editing。"""
    cfg = _make_cfg(tmp_path / "run")
    a = PipelineAgent(cfg)
    sb = _make_storyboard(n=1)
    sb.style_anchor = "should-not-leak"
    ctx = {"storyboard": sb, "shot_count": 1, "intermediate_dir": tmp_path / "inter"}
    for agent_name in ("asset_prep", "qa", "editing", "script_writer"):
        task = a._build_task(agent_name, 0, ctx, None)
        # style_anchor 不应在 prompt / subject_action 中泄漏
        prompt = task.get("prompt", "") + task.get("subject_action", "") + task.get("text", "")
        assert "should-not-leak" not in prompt, f"style leaked to {agent_name}"


# ---------- Agent 计数验证（8 个 + ScriptWriterAgent 包含） ----------

def test_all_8_agents_registered():
    """DAG 上的 8 个 Agent 在 ALL_AGENTS 里。ConcatAgent 不再注册。"""
    from hailuo_video.agents import ALL_AGENTS
    names = {a.name for a in ALL_AGENTS}
    assert names == {
        "script_writer",
        "asset_prep",
        "image_gen",
        "motion_gen",
        "video_synth",
        "tts",
        "editing",
        "qa",
    }
