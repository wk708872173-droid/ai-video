"""V3.2 自动开发冲刺（轮 3-1）orchestrator --resume-from 单测。

设计：
  - PipelineAgent.__init__ 新增 resume_from + load_registry 参数
  - 跑小流程：先跑一次完整 pipeline → 再 resume_from 跳过 prep/image_gen
  - 不依赖真 image / video / tts engine（用 mock 后端或最小 storyboard）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from hailuo_video.orchestrator import (  # noqa: E402
    DEFAULT_DAG,
    Phase,
    PipelineAgent,
    PipelineConfig,
)
from hailuo_video.artifact import Artifact, Registry  # noqa: E402
from hailuo_video.agents import AgentResult  # noqa: E402


# ---------- Fixtures ----------

@pytest.fixture
def tmp_run_dir(tmp_path):
    return tmp_path / "run"


def _make_cfg(run_dir: Path) -> PipelineConfig:
    """构造最小 PipelineConfig（避免真 backend）。"""
    return PipelineConfig(
        image_backend=None,
        video_backend=None,
        tts_backend=None,
        voice="zh-CN-XiaoxiaoNeural",
        video_seconds=5,
        video_fps=8,
        video_effect="cycle",
        assets_dir=Path("./assets"),
        skip_asset_prep=True,
        keep_intermediate=True,
        parallel=1,
        motion_budget=0,
        run_dir=run_dir,
        log=None,
    )


# ---------- _resolve_resume_index ----------

def test_resume_index_none_returns_zero(tmp_run_dir):
    a = PipelineAgent(_make_cfg(tmp_run_dir), resume_from=None)
    assert a._resolve_resume_index() == 0


def test_resume_index_valid_name(tmp_run_dir):
    a = PipelineAgent(_make_cfg(tmp_run_dir), resume_from="editing")
    # editing 在 DEFAULT_DAG 里的索引
    expected = next(i for i, p in enumerate(DEFAULT_DAG) if p.name == "editing")
    assert a._resolve_resume_index() == expected


def test_resume_index_invalid_name_returns_zero(tmp_run_dir):
    a = PipelineAgent(_make_cfg(tmp_run_dir), resume_from="nonexistent_phase")
    assert a._resolve_resume_index() == 0


def test_resume_index_first_phase(tmp_run_dir):
    a = PipelineAgent(_make_cfg(tmp_run_dir), resume_from="prep")
    assert a._resolve_resume_index() == 0


def test_resume_index_last_phase(tmp_run_dir):
    a = PipelineAgent(_make_cfg(tmp_run_dir), resume_from="qa")
    assert a._resolve_resume_index() == len(DEFAULT_DAG) - 1


# ---------- Registry 加载 ----------

def test_load_registry_true_loads_existing_artifacts(tmp_run_dir):
    tmp_run_dir.mkdir(parents=True)
    reg_path = tmp_run_dir / "registry.json"
    reg = Registry()
    reg.add(Artifact(
        artifact_id="image_001",
        kind="image",
        path=Path("intermediate/shot_001.jpg"),
        producer="image_gen",
    ))
    reg.add(Artifact(
        artifact_id="video_001",
        kind="video",
        path=Path("intermediate/shot_001.mp4"),
        producer="motion_gen",
    ))
    reg.save_json(reg_path)

    a = PipelineAgent(_make_cfg(tmp_run_dir), load_registry=True)
    assert "image_001" in a.registry.artifacts
    assert "video_001" in a.registry.artifacts
    assert len(a.registry.artifacts) == 2


def test_load_registry_false_skips_existing(tmp_run_dir):
    tmp_run_dir.mkdir(parents=True)
    reg_path = tmp_run_dir / "registry.json"
    reg = Registry()
    reg.add(Artifact(
        artifact_id="image_001",
        kind="image",
        path=Path("intermediate/shot_001.jpg"),
        producer="image_gen",
    ))
    reg.save_json(reg_path)

    a = PipelineAgent(_make_cfg(tmp_run_dir), load_registry=False)
    assert len(a.registry.artifacts) == 0


def test_load_registry_missing_file_starts_empty(tmp_run_dir):
    """run_dir 不存在 → 不报错，registry 空。"""
    a = PipelineAgent(_make_cfg(tmp_run_dir), load_registry=True)
    assert len(a.registry.artifacts) == 0


def test_load_registry_corrupted_file_raises(tmp_run_dir):
    """registry.json 解析失败 → 应抛错（CLAUDE.md R4：不静默）。"""
    import pytest as _pt
    tmp_run_dir.mkdir(parents=True)
    reg_path = tmp_run_dir / "registry.json"
    reg_path.write_text("not json{", encoding="utf-8")
    with _pt.raises(Exception):
        PipelineAgent(_make_cfg(tmp_run_dir), load_registry=True)


# ---------- run() resume_from 行为 ----------

def test_run_skips_phases_before_resume_from(tmp_run_dir, monkeypatch):
    """resume_from='concat' → prep/image_gen/motion_gen/video_synth/tts 都跳过。

    用 object.__setattr__ 替换所有 agent 的 execute + validate_inputs 为 mock。
    """
    call_counts = {}

    def make_mock(name):
        def _execute(task):
            call_counts[name] = call_counts.get(name, 0) + 1
            return AgentResult(ok=True)

        def _validate(_task):
            pass

        return _execute, _validate

    a = PipelineAgent(_make_cfg(tmp_run_dir), resume_from="editing")
    # frozen dataclass 需用 object.__setattr__ 替换 execute + validate_inputs
    for name, agent in a._agent_lookup.items():
        ex, val = make_mock(name)
        object.__setattr__(agent, "execute", ex)
        object.__setattr__(agent, "validate_inputs", val)

    monkeypatch.setattr(
        a, "_run_motion_gen_conditional",
        lambda ctx: [],
    )

    from types import SimpleNamespace
    storyboard = SimpleNamespace(shots=[])
    a.run(storyboard, Path(tmp_run_dir / "out.mp4"))

    # prep / image_gen / motion_gen / video_synth / tts 都不应被调用
    assert "asset_prep" not in call_counts
    assert "image_gen" not in call_counts
    assert "video_synth" not in call_counts
    assert "tts" not in call_counts
    # editing 应该被调用一次（非 parallel phase，单次）
    assert call_counts.get("editing") == 1


def test_run_no_resume_from_runs_all_phases(tmp_run_dir, monkeypatch):
    """resume_from=None → 所有 phase 都跑。"""
    call_counts = {}

    def make_mock(name):
        def _execute(task):
            call_counts[name] = call_counts.get(name, 0) + 1
            return AgentResult(ok=True)

        def _validate(_task):
            pass

        return _execute, _validate

    a = PipelineAgent(_make_cfg(tmp_run_dir))
    for name, agent in a._agent_lookup.items():
        ex, val = make_mock(name)
        object.__setattr__(agent, "execute", ex)
        object.__setattr__(agent, "validate_inputs", val)
    monkeypatch.setattr(a, "_run_motion_gen_conditional", lambda ctx: [])

    from types import SimpleNamespace
    storyboard = SimpleNamespace(shots=[])
    a.run(storyboard, Path(tmp_run_dir / "out.mp4"))

    # editing 应被调用
    assert call_counts.get("editing") == 1
    # prep 应被调用（不跳过）
    assert call_counts.get("asset_prep") == 1


def test_run_saves_registry_after_skip(tmp_run_dir, monkeypatch):
    """resume 跑完后应保存 registry.json（即使跳过了 phase）。"""
    monkeypatch.setattr(
        PipelineAgent, "_run_motion_gen_conditional",
        lambda self, ctx: [],
    )

    a = PipelineAgent(_make_cfg(tmp_run_dir), resume_from="editing")
    # mock 所有 agent 让 concat 跑过（不抛错触发 halt_on_error）
    for name, agent in a._agent_lookup.items():
        object.__setattr__(agent, "execute", lambda task: AgentResult(ok=True))
        object.__setattr__(agent, "validate_inputs", lambda task: None)

    from types import SimpleNamespace
    storyboard = SimpleNamespace(shots=[])
    a.run(storyboard, Path(tmp_run_dir / "out.mp4"))

    reg_path = tmp_run_dir / "registry.json"
    assert reg_path.is_file()
    loaded = Registry.load_json_or_empty(reg_path)
    assert isinstance(loaded, Registry)


def test_run_logs_skipped_phases(tmp_run_dir, monkeypatch):
    """resume 时应写 log：列出被跳过的 phase。"""
    monkeypatch.setattr(
        PipelineAgent, "_run_motion_gen_conditional",
        lambda self, ctx: [],
    )

    a = PipelineAgent(_make_cfg(tmp_run_dir), resume_from="editing")
    for name, agent in a._agent_lookup.items():
        object.__setattr__(agent, "execute", lambda task: AgentResult(ok=True))
        object.__setattr__(agent, "validate_inputs", lambda task: None)

    events: list[tuple] = []

    class FakeLogger:
        def event(self, kind, **kw):
            events.append((kind, kw))

    a.logger = FakeLogger()
    from types import SimpleNamespace
    storyboard = SimpleNamespace(shots=[])
    a.run(storyboard, Path(tmp_run_dir / "out.mp4"))

    kinds = [k for k, _ in events]
    assert "resume_skipped" in kinds
    skip_event = next(kw for k, kw in events if k == "resume_skipped")
    skipped = skip_event.get("phases", [])
    assert "prep" in skipped
    assert "image_gen" in skipped


def test_run_invalid_resume_from_falls_back_to_zero(tmp_run_dir, monkeypatch):
    """resume_from='bogus' → 视作 None，所有 phase 都跑。"""
    monkeypatch.setattr(
        PipelineAgent, "_run_motion_gen_conditional",
        lambda self, ctx: [],
    )

    call_counts = {}

    def make_mock(name):
        def _execute(task):
            call_counts[name] = call_counts.get(name, 0) + 1
            return AgentResult(ok=True)

        def _validate(_task):
            pass

        return _execute, _validate

    a = PipelineAgent(_make_cfg(tmp_run_dir), resume_from="bogus_phase")
    for name, agent in a._agent_lookup.items():
        ex, val = make_mock(name)
        object.__setattr__(agent, "execute", ex)
        object.__setattr__(agent, "validate_inputs", val)

    from types import SimpleNamespace
    storyboard = SimpleNamespace(shots=[])
    a.run(storyboard, Path(tmp_run_dir / "out.mp4"))

    assert call_counts.get("asset_prep") == 1


def test_run_resume_preserves_loaded_artifacts(tmp_run_dir, monkeypatch):
    """resume 时 registry 中已有 artifacts 在 run 完应保留。"""
    tmp_run_dir.mkdir(parents=True)
    reg_path = tmp_run_dir / "registry.json"
    reg = Registry()
    reg.add(Artifact(
        artifact_id="image_001",
        kind="image",
        path=Path("intermediate/shot_001.jpg"),
        producer="image_gen",
    ))
    reg.save_json(reg_path)

    monkeypatch.setattr(
        PipelineAgent, "_run_motion_gen_conditional",
        lambda self, ctx: [],
    )

    a = PipelineAgent(_make_cfg(tmp_run_dir), resume_from="editing", load_registry=True)
    for name, agent in a._agent_lookup.items():
        object.__setattr__(agent, "execute", lambda task: AgentResult(ok=True))
        object.__setattr__(agent, "validate_inputs", lambda task: None)

    from types import SimpleNamespace
    storyboard = SimpleNamespace(shots=[])
    a.run(storyboard, Path(tmp_run_dir / "out.mp4"))

    loaded = Registry.load_json_or_empty(reg_path)
    assert "image_001" in loaded.artifacts
