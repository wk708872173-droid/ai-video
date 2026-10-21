"""REQ-V3-003 Phase 3 — orchestrator.py 单测。

覆盖：
  - DEFAULT_DAG 结构（6 phases，依赖关系正确）
  - PipelineAgent.run() 端到端（用 mock agents）
  - Phase 失败 → halt（halt_on_error=True 的 phase）
  - Phase 失败 → 继续（halt_on_error=False）
  - 并发路径（is_parallel=True + parallel>1）
  - QA post-flight 触发
  - Registry 写入
  - 单 shot 失败 → skip-shot（其他 shot 继续）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from hailuo_video.orchestrator import (  # noqa: E402
    DEFAULT_DAG,
    Phase,
    PipelineAgent,
    PipelineConfig,
    PipelineResult,
    PipelineStep,
)
from hailuo_video.agents import (  # noqa: E402
    AgentResult,
)
from hailuo_video.script_engine import (  # noqa: E402
    Character,
    SceneSpec,
    Shot,
    Storyboard,
)


# ---------- Fixtures ----------

def _storyboard(n: int = 2) -> Storyboard:
    """建一个 n shots 的最小 Storyboard。"""
    chars = [Character(name="农夫", appearance="宋代农夫", personality=[], color_palette=["灰褐"])]
    scenes = [SceneSpec(id=1, location="田野", key_elements=[])]
    shots = [
        Shot(
            index=i + 1,
            scene_id=1,
            shot_type="中景",
            description=f"镜头{i+1}",
            voiceover=f"旁白{i+1}",
        )
        for i in range(n)
    ]
    return Storyboard(title="t", characters=chars, scenes=scenes, shots=shots)


def _agent_result_ok(path_suffix: str = "ok") -> AgentResult:
    """Artifact 必须有可序列化的 path/artifact_id，否则 registry.save_json 报 TypeError。"""
    from hailuo_video.artifact import Artifact
    return AgentResult(
        ok=True,
        artifacts=[Artifact(
            artifact_id=f"test_{path_suffix}",
            kind="image",
            path=Path(f"/tmp/{path_suffix}.jpg"),
            producer="test",
        )],
        metadata={"model": "image-01"},
    )


def _agent_result_error(msg: str = "boom") -> AgentResult:
    return AgentResult(ok=False, error=msg, artifacts=[])


def _make_mock_agent(name: str, result: AgentResult) -> MagicMock:
    """建一个 mock agent，含 name/validate_inputs/execute/idempotency_key 四件套。"""
    m = MagicMock()
    m.name = name
    m.validate_inputs = MagicMock()
    m.execute = MagicMock(return_value=result)
    m.idempotency_key = MagicMock(return_value=f"{name}-key")
    return m


def _mock_all_agents(agent: PipelineAgent, results: dict[str, AgentResult]) -> None:
    """把 PipelineAgent._agent_lookup 全部替换成 mock。

    未指定的 name 默认返回 ok 但 artifacts=[]（不污染 registry）。
    """
    empty = AgentResult(ok=True, artifacts=[], metadata={})
    for name in ("script_writer", "asset_prep", "image_gen",
                 "video_synth", "tts", "editing", "qa"):
        agent._agent_lookup[name] = _make_mock_agent(name, results.get(name, empty))


# ---------- DEFAULT_DAG 结构 ----------

def test_dag_has_eight_phases():
    """V3.2 加了 script phase + concat 改名 editing → 8 phases total。"""
    assert len(DEFAULT_DAG) == 8
    names = [p.name for p in DEFAULT_DAG]
    assert names == [
        "prep", "script", "image_gen", "motion_gen",
        "video_synth", "tts", "editing", "qa",
    ]


def test_dag_phase_dependencies():
    script_deps = [p.inputs_from for p in DEFAULT_DAG if p.name == "script"][0]
    assert "prep" in script_deps
    image_deps = [p.inputs_from for p in DEFAULT_DAG if p.name == "image_gen"][0]
    assert "script" in image_deps
    motion_deps = [p.inputs_from for p in DEFAULT_DAG if p.name == "motion_gen"][0]
    assert "image_gen" in motion_deps
    editing_deps = [p.inputs_from for p in DEFAULT_DAG if p.name == "editing"][0]
    assert "image_gen" in editing_deps
    assert "video_synth" in editing_deps
    assert "tts" in editing_deps
    assert "motion_gen" in editing_deps     # V3.1 editing 也等 motion_gen


def test_dag_halt_phases():
    """prep / script / editing 失败要 halt；qa 不 halt（warning 即可）。"""
    halt_names = {p.name for p in DEFAULT_DAG if p.halt_on_error}
    assert halt_names == {"prep", "script", "editing"}


def test_dag_parallel_phases():
    parallel_names = {p.name for p in DEFAULT_DAG if p.is_parallel}
    assert parallel_names == {"image_gen", "motion_gen", "video_synth", "tts"}


def test_dag_motion_gen_conditional():
    """V3.1：motion_gen 是 conditional phase（按 shot.motion_complexity 路由）。"""
    motion_phase = [p for p in DEFAULT_DAG if p.name == "motion_gen"][0]
    assert motion_phase.conditional is True


# ---------- V3.2：script phase + editing phase ----------

def test_dag_script_phase_is_serial_halt():
    """script phase 是 serial + halt_on_error（V3.2：Storyboard 是后续 phase 输入，失败 HALT）。"""
    script_phase = [p for p in DEFAULT_DAG if p.name == "script"][0]
    assert script_phase.is_parallel is False
    assert script_phase.halt_on_error is True
    assert script_phase.agent_names == ("script_writer",)
    assert "prep" in script_phase.inputs_from


def test_dag_editing_phase_replaces_concat():
    """V3.2：editing phase 取代原 concat（接管后期处理 + halt_on_error）。"""
    editing_phase = [p for p in DEFAULT_DAG if p.name == "editing"][0]
    assert editing_phase.agent_names == ("editing",)
    assert editing_phase.halt_on_error is True
    assert editing_phase.is_parallel is False
    # editing 依赖上游 4 个 parallel phase
    for upstream in ("image_gen", "motion_gen", "video_synth", "tts"):
        assert upstream in editing_phase.inputs_from
    # qa 依赖 editing（取代旧的 "concat"）
    qa_phase = [p for p in DEFAULT_DAG if p.name == "qa"][0]
    assert "editing" in qa_phase.inputs_from
    assert "concat" not in qa_phase.inputs_from


def test_dag_script_phase_runs_before_image_gen():
    """script phase 在 image_gen 之前执行（顺序约束）。"""
    names = [p.name for p in DEFAULT_DAG]
    assert names.index("script") < names.index("image_gen")
    assert names.index("prep") < names.index("script")


# ---------- PipelineAgent.run() 端到端（mocked） ----------

def test_pipeline_agent_run_end_to_end_with_mocks(tmp_path):
    """全部 agent 替换成 mock，跑一遍完整 DAG。"""
    cfg = PipelineConfig(
        image_backend=MagicMock(),
        video_backend=MagicMock(),
        tts_backend=MagicMock(),
        assets_dir=tmp_path / "assets",
        run_dir=tmp_path / "run",
        keep_intermediate=False,
        parallel=1,
    )
    agent = PipelineAgent(cfg)
    _mock_all_agents(agent, {
        "asset_prep": _agent_result_ok("prep"),
        "image_gen": _agent_result_ok("img"),
        "video_synth": _agent_result_ok("vid"),
        "tts": _agent_result_ok("tts"),
        "editing": _agent_result_ok("final"),
        "qa": AgentResult(ok=True, artifacts=[], metadata={"mean_similarity": 0.92, "min_similarity": 0.81}),
    })

    sb = _storyboard(n=2)
    result = agent.run(sb, tmp_path / "out.mp4")
    assert isinstance(result, PipelineResult)
    assert result.source == "ok"
    # V3.2 轮 6 起：script phase 在 storyboard 预加载时 skip（round 7 增 _script_already_loaded）。
    # 实际 steps：prep(1) + image_gen(2) + video_synth(2) + tts(2) + editing(1) + qa(1) + qa_post(1) = 10 steps
    assert len(result.steps) == 10


def test_pipeline_agent_halt_on_prep_failure(tmp_path):
    """prep 失败 → halt，整个 pipeline 返回 source=error。"""
    cfg = PipelineConfig(
        assets_dir=tmp_path / "assets",
        run_dir=tmp_path / "run",
        keep_intermediate=False,
        parallel=1,
    )
    agent = PipelineAgent(cfg)
    # prep fail；其余 OK + mock asset_prep 防止真实 prep 调用
    _mock_all_agents(agent, {})
    agent._agent_lookup["asset_prep"] = _make_mock_agent(
        "asset_prep", _agent_result_error("MiniMax down"))

    sb = _storyboard(n=2)
    result = agent.run(sb, tmp_path / "out.mp4")
    assert result.source == "error"
    assert "halted" in result.error


def test_pipeline_agent_continues_on_tts_failure(tmp_path):
    """tts 失败（非 halt phase）→ 继续跑 concat 和 qa。"""
    cfg = PipelineConfig(
        image_backend=MagicMock(), video_backend=MagicMock(), tts_backend=MagicMock(),
        assets_dir=tmp_path / "assets", run_dir=tmp_path / "run", keep_intermediate=False, parallel=1,
    )
    agent = PipelineAgent(cfg)
    # happy mocks for image_gen/video_synth/editing/qa
    _mock_all_agents(agent, {
        "image_gen": _agent_result_ok("img"),
        "video_synth": _agent_result_ok("vid"),
        "editing": _agent_result_ok("final"),
        "qa": AgentResult(ok=True, artifacts=[], metadata={"mean_similarity": 0.9, "min_similarity": 0.71}),
    })
    # tts fail
    agent._agent_lookup["tts"] = _make_mock_agent(
        "tts", _agent_result_error("tts timeout"))

    result = agent.run(_storyboard(n=2), tmp_path / "out.mp4")
    assert result.source == "ok"   # 不 halt
    tts_steps = [s for s in result.steps if "tts" in s.name]
    assert any(not s.ok for s in tts_steps)
    assert any("qa" in s.name for s in result.steps)


def test_pipeline_agent_qa_post_flight_triggers(tmp_path):
    """有 image artifacts → qa step 出现。

    qa_mock.execute 总共被调 2 次：
      - 1 次：DEFAULT_DAG 中 qa phase（per-shot=0 一次）
      - 1 次：post-flight
    """
    cfg = PipelineConfig(
        image_backend=MagicMock(), video_backend=MagicMock(), tts_backend=MagicMock(),
        assets_dir=tmp_path / "assets", run_dir=tmp_path / "run", keep_intermediate=False, parallel=1,
    )
    agent = PipelineAgent(cfg)
    _mock_all_agents(agent, {
        "image_gen": _agent_result_ok("img"),
        "video_synth": _agent_result_ok("vid"),
        "tts": _agent_result_ok("tts"),
        "editing": _agent_result_ok("final"),
    })
    qa_mock = _make_mock_agent("qa", AgentResult(
        ok=True, artifacts=[], metadata={"mean_similarity": 0.85, "min_similarity": 0.71}
    ))
    agent._agent_lookup["qa"] = qa_mock

    agent.run(_storyboard(n=2), tmp_path / "out.mp4")
    assert qa_mock.execute.call_count == 2  # DAG qa + post-flight


def test_pipeline_agent_qa_skipped_when_no_images(tmp_path):
    """image artifacts 为空 → qa step 不出现。"""
    cfg = PipelineConfig(
        image_backend=MagicMock(), video_backend=MagicMock(), tts_backend=MagicMock(),
        assets_dir=tmp_path / "assets", run_dir=tmp_path / "run", keep_intermediate=False, parallel=1,
    )
    agent = PipelineAgent(cfg)
    # 所有 agent 都不产 image artifacts（返回空 artifacts）
    empty = AgentResult(ok=True, artifacts=[], metadata={})
    _mock_all_agents(agent, {
        "image_gen": empty, "video_synth": empty, "tts": empty, "editing": empty,
    })
    agent._agent_lookup["qa"] = _make_mock_agent("qa", AgentResult(ok=True, artifacts=[], metadata={"skipped": True}))

    agent.run(_storyboard(n=2), tmp_path / "out.mp4")
    img_kinds = agent.registry.by_kind.get("image", [])
    assert len(img_kinds) == 0


# ---------- 并发路径 ----------

def test_pipeline_agent_parallel_path_runs(tmp_path):
    """parallel=2 时 image_gen 是 per-shot 并发跑——ThreadPoolExecutor fan-out。"""
    cfg = PipelineConfig(
        image_backend=MagicMock(), video_backend=MagicMock(), tts_backend=MagicMock(),
        assets_dir=tmp_path / "assets", run_dir=tmp_path / "run",
        keep_intermediate=False, parallel=2,
    )
    agent = PipelineAgent(cfg)
    _mock_all_agents(agent, {
        "image_gen": _agent_result_ok("img"),
        "video_synth": _agent_result_ok("vid"),
        "tts": _agent_result_ok("tts"),
        "editing": _agent_result_ok("final"),
    })
    agent._agent_lookup["qa"] = _make_mock_agent(
        "qa", AgentResult(ok=True, artifacts=[], metadata={}))

    result = agent.run(_storyboard(n=3), tmp_path / "out.mp4")
    assert result.source == "ok"
    img_steps = [s for s in result.steps if "image_gen" in s.name]
    assert len(img_steps) == 3
    assert all(s.ok for s in img_steps)


def test_pipeline_agent_registry_written_for_resume(tmp_path):
    """run 后 registry.json 落地——--resume-from 续跑基础。"""
    from hailuo_video.orchestrator import Registry
    from hailuo_video.artifact import Artifact
    cfg = PipelineConfig(
        image_backend=MagicMock(), video_backend=MagicMock(), tts_backend=MagicMock(),
        assets_dir=tmp_path / "assets", run_dir=tmp_path / "run", keep_intermediate=False, parallel=1,
    )
    agent = PipelineAgent(cfg)
    # 各 mock 返回不同 kind 的 artifact 以模拟真实 phase 产出
    def _ok_agent(name: str, kind: str, suffix: str) -> MagicMock:
        m = MagicMock()
        m.name = name; m.validate_inputs = MagicMock()
        m.execute = MagicMock(return_value=AgentResult(
            ok=True,
            artifacts=[Artifact(artifact_id=f"{name}_{suffix}", kind=kind, path=Path(f"/tmp/{suffix}"), producer=name)],
            metadata={},
        ))
        m.idempotency_key = MagicMock(return_value=f"{name}-key")
        return m
    agent._agent_lookup["image_gen"] = _ok_agent("image_gen", "image", "img")
    agent._agent_lookup["video_synth"] = _ok_agent("video_synth", "video", "vid")
    agent._agent_lookup["tts"] = _ok_agent("tts", "audio", "aud")
    agent._agent_lookup["editing"] = _ok_agent("editing", "video", "final")
    agent._agent_lookup["qa"] = _make_mock_agent("qa", AgentResult(ok=True, artifacts=[]))
    # script_writer + asset_prep 默认 empty
    agent._agent_lookup["script_writer"] = _make_mock_agent("script_writer", AgentResult(ok=True, artifacts=[]))
    agent._agent_lookup["asset_prep"] = _make_mock_agent("asset_prep", AgentResult(ok=True, artifacts=[]))

    agent.run(_storyboard(n=2), tmp_path / "out.mp4")
    reg_path = tmp_path / "run" / "registry.json"
    assert reg_path.exists()
    data = json.loads(reg_path.read_text(encoding="utf-8"))
    # by_kind 应有 image / video / audio
    assert "image" in data["by_kind"]
    assert "video" in data["by_kind"]
    assert "audio" in data["by_kind"]


def test_pipeline_agent_load_registry_or_empty(tmp_path):
    """Registry.load_json_or_empty 在不存在的文件上返回空 Registry。"""
    from hailuo_video.artifact import Registry
    r = Registry.load_json_or_empty(tmp_path / "nope.json")
    assert len(r.artifacts) == 0


# ---------- Registry persistence ----------

def test_pipeline_agent_persists_registry_json(tmp_path):
    cfg = PipelineConfig(
        image_backend=MagicMock(), video_backend=MagicMock(), tts_backend=MagicMock(),
        assets_dir=tmp_path / "assets", run_dir=tmp_path / "run", keep_intermediate=False, parallel=1,
    )
    agent = PipelineAgent(cfg)
    _mock_all_agents(agent, {
        "image_gen": _agent_result_ok("img"),
        "video_synth": _agent_result_ok("vid"),
        "tts": _agent_result_ok("tts"),
        "editing": _agent_result_ok("final"),
    })
    agent._agent_lookup["qa"] = _make_mock_agent("qa", AgentResult(ok=True, artifacts=[]))
    agent.run(_storyboard(n=2), tmp_path / "out.mp4")

    reg_path = tmp_path / "run" / "registry.json"
    assert reg_path.exists()
    data = json.loads(reg_path.read_text(encoding="utf-8"))
    assert "artifacts" in data
    assert "by_kind" in data
