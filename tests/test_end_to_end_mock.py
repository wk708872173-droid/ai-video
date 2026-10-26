"""V3.8 Batch 7 — 端到端 mock 回归（全链路）。

V3.8 的 6 条工作流 + P0 止血批改了**提示词拼装、画幅解析、reference 选取、
artifact 命名、拼接帧率、连续性门禁**等横切面 —— 单模块测试全绿并不等于
全链路仍然自洽。本文件跑一次**全 mock**链路，锁住端到端契约。

证据阶梯声明：这是 **mock 端到端**，属于 Operating Rule 2 的 (b) 级
（真代码 + mock 的真测试），**不是 (a) 级真实端到端** —— 真 API 的行为
（Hailuo / image-01 / Gemini）仍需用户手工烟测。

R4：不打真 API，不写仓库内的知识库/资产目录（全部用 tmp_path）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from hailuo_video import image_engine as ie  # noqa: E402
from hailuo_video import pipeline as pl  # noqa: E402
from hailuo_video import script_engine as se  # noqa: E402
from hailuo_video.orchestrator import PipelineAgent, PipelineConfig  # noqa: E402


# ---------- helpers ----------

def _storyboard(n: int = 2) -> se.Storyboard:
    chars = [se.Character(name="农夫", appearance="宋代农夫，灰布短褂",
                          personality=[], color_palette=["灰褐"])]
    scenes = [se.SceneSpec(id=1, location="田野", atmosphere="黄昏暖光", key_elements=[])]
    shots = [
        se.Shot(
            index=i + 1, scene_id=1, shot_type="中景",
            description=f"镜头{i+1}：田间日出",
            camera_motion="缓慢推近", subject_action="农夫抬头望天",
            mood=["温暖"], characters=["农夫"], voiceover=f"旁白{i+1}",
            expected_duration=5.0, motion_complexity="static",
            lens="50mm", lighting="侧逆光", color_grade="暖调",
        )
        for i in range(n)
    ]
    return se.Storyboard(title="t", characters=chars, scenes=scenes, shots=shots,
                         style_anchor="shot on 35mm film, warm golden hour")


def _mock_agents(agent: PipelineAgent) -> None:
    """把 agent lookup 全换成 mock（不调真 agent / 真 API）。"""
    from unittest.mock import MagicMock

    from hailuo_video.agents import AgentResult

    empty = AgentResult(ok=True, artifacts=[], metadata={})
    for name in ("script_writer", "asset_prep", "image_gen", "motion_gen",
                 "video_synth", "tts", "editing", "qa"):
        m = MagicMock()
        m.name = name
        m.validate_inputs = MagicMock()
        m.execute = MagicMock(return_value=empty)
        m.idempotency_key = MagicMock(return_value=f"{name}-key")
        agent._agent_lookup[name] = m


# ============================================================
# 全链路
# ============================================================

def test_full_mock_pipeline_produces_result(tmp_path):
    """全 mock 跑完 8 phase → 拿到 PipelineResult，不抛异常。"""
    sb = _storyboard(2)
    cfg = PipelineConfig(
        image_backend=ie.MockImageBackend(),
        run_dir=tmp_path / "run",
        assets_dir=tmp_path / "assets",
    )
    agent = PipelineAgent(cfg, load_registry=False)
    _mock_agents(agent)

    result = agent.run(sb, tmp_path / "final.mp4")
    assert result.shot_count == 2
    assert isinstance(result.consistency, dict)
    assert result.to_dict()["consistency"] == result.consistency


def test_full_mock_pipeline_records_static_skips(tmp_path):
    """全部 shot 是 static → consistency 里必须有 static_skipped（不再静默）。"""
    sb = _storyboard(2)
    cfg = PipelineConfig(run_dir=tmp_path / "run", assets_dir=tmp_path / "assets")
    agent = PipelineAgent(cfg, load_registry=False)
    _mock_agents(agent)

    result = agent.run(sb, tmp_path / "final.mp4")
    assert result.consistency.get("static_skipped_count") == 2
    assert result.consistency["static_skipped_shots"] == [0, 1]


def test_full_mock_pipeline_continuity_is_evaluated(tmp_path):
    """跨镜连续性门禁在全链路里被触发（且结果进 consistency）。"""
    from PIL import Image

    sb = _storyboard(2)
    cfg = PipelineConfig(run_dir=tmp_path / "run", assets_dir=tmp_path / "assets")
    agent = PipelineAgent(cfg, load_registry=False)
    _mock_agents(agent)

    # 造两张差异大的图塞进 registry → 连续性应判 fail
    from hailuo_video.artifact import Artifact, Registry
    reg = Registry()
    for idx, color in enumerate([(240, 240, 240), (10, 10, 10)]):
        p = tmp_path / f"img_{idx}.jpg"
        Image.new("RGB", (64, 64), color).save(p)
        reg.add(Artifact(artifact_id=f"image_{idx}", kind="image", path=p,
                         producer="t", metadata={"shot_idx": idx}), compute_hash=False)
    agent.registry = reg

    result = agent.run(sb, tmp_path / "final.mp4")
    cont = result.consistency.get("continuity")
    assert cont is not None, "全链路没跑跨镜连续性门禁"
    assert cont["evaluated"] is True


def test_full_mock_pipeline_does_not_write_repo_knowledge(tmp_path):
    """默认不写仓库知识库（写库是副作用，默认关闭）。"""
    repo_kb = Path(__file__).resolve().parents[1] / "knowledge" / "motion_params.json"
    before = repo_kb.read_text(encoding="utf-8") if repo_kb.is_file() else ""

    sb = _storyboard(1)
    cfg = PipelineConfig(run_dir=tmp_path / "run", assets_dir=tmp_path / "assets")
    agent = PipelineAgent(cfg, load_registry=False)
    _mock_agents(agent)
    agent.run(sb, tmp_path / "final.mp4")

    after = repo_kb.read_text(encoding="utf-8") if repo_kb.is_file() else ""
    assert before == after, "默认配置不应改写仓库内的知识库"


# ============================================================
# 关键横切面在链路上的表现
# ============================================================

def test_image_gen_task_carries_all_v38_signals(tmp_path):
    """image_gen task 必须同时带上：导演字段、负提示词、画幅。"""
    sb = _storyboard(1)
    cfg = PipelineConfig(
        image_backend=ie.MockImageBackend(),
        run_dir=tmp_path / "run",
        assets_dir=tmp_path / "assets",
        aspect_ratio="9:16",
    )
    agent = PipelineAgent(cfg, load_registry=False)
    ctx = {"storyboard": sb, "shot_count": 1, "intermediate_dir": tmp_path}
    task = agent._build_task("image_gen", 0, ctx, None)

    # 导演字段进 prompt
    assert "50mm" in task["prompt"]
    assert "侧逆光" in task["prompt"]
    assert "暖调" in task["prompt"]
    # 负提示词
    assert task["negative_prompt"]
    # 画幅
    assert task["aspect_ratio"] == "9:16"
    # 色基调（连续性门禁用）
    assert task["color_lock"] == "warm-gold"


def test_motion_gen_task_carries_camera_motion(tmp_path):
    """运镜必须进视频模型 prompt（旧实现完全没进）。"""
    sb = _storyboard(1)
    cfg = PipelineConfig(run_dir=tmp_path / "run", assets_dir=tmp_path / "assets")
    agent = PipelineAgent(cfg, load_registry=False)
    ctx = {"storyboard": sb, "shot_count": 1, "intermediate_dir": tmp_path}
    task = agent._build_task("motion_gen", 0, ctx, None)
    assert "缓慢推近" in task["subject_action"]


def test_image_artifacts_are_per_shot_isolated(tmp_path):
    """多镜时每镜的图必须是不同 artifact_id（缺陷 15 的端到端确认）。"""
    from hailuo_video.agents import ImageGenAgent

    agent = ImageGenAgent()
    ids = []
    for shot_idx in (0, 1, 2):
        backend = ie.MockImageBackend()
        result = agent.execute({
            "prompt": f"p{shot_idx}",
            "image_backend": backend,
            "shot_idx": shot_idx,
            "intermediate_dir": str(tmp_path),
        })
        assert result.ok, result.error
        ids.append(result.artifacts[0].artifact_id)
    assert ids == ["image_0", "image_1", "image_2"]
    files = sorted(p.name for p in tmp_path.iterdir() if p.suffix == ".jpg")
    assert files == ["image_000.jpg", "image_001.jpg", "image_002.jpg"]


def test_storyboard_roundtrip_preserves_v38_fields(tmp_path):
    """storyboard.json → Storyboard → storyboard.json 保留 V3.8 新字段。"""
    sb = _storyboard(1)
    sb.shots[0].static_visual = "静态视觉条件"
    sb.shots[0].dynamic_narrative = "动态叙事条件"
    sb.shots[0].color_lock = "warm-gold"

    path = tmp_path / "sb.json"
    path.write_text(json.dumps({
        "title": sb.title,
        "style_anchor": sb.style_anchor,
        "characters": [c.to_dict() for c in sb.characters],
        "scenes": [s.to_dict() for s in sb.scenes],
        "shots": [s.to_dict() for s in sb.shots],
    }, ensure_ascii=False), encoding="utf-8")

    loaded = pl.load_storyboard(path)
    assert loaded.shots[0].static_visual == "静态视觉条件"
    assert loaded.shots[0].dynamic_narrative == "动态叙事条件"
    assert loaded.shots[0].color_lock == "warm-gold"


def test_cli_mock_script_to_pipeline_chain(tmp_path):
    """CLI mock 后端产出的 storyboard 能直接进 pipeline（脚本→分镜→链路）。"""
    from hailuo_video import cli

    sb = se.novel_to_storyboard("从前有座山", se.CliMockBackend(), n_shots=2)
    assert len(sb.shots) == 2

    cfg = PipelineConfig(
        image_backend=ie.MockImageBackend(),
        run_dir=tmp_path / "run",
        assets_dir=tmp_path / "assets",
    )
    agent = PipelineAgent(cfg, load_registry=False)
    _mock_agents(agent)
    result = agent.run(sb, tmp_path / "final.mp4")
    assert result.shot_count == 2
