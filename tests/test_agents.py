"""REQ-V3-002 Phase 2 — agents.py 单测。

验证 7 个 Agent 描述符的：
  - name 唯一性
  - validate_inputs 必填字段检查（缺关键 input → InputValidationError）
  - execute 委托现有 module（用 mock 验证调用）
  - idempotency_key 稳定性（同 inputs → 同 key）
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from hailuo_video import agents as ag  # noqa: E402
from hailuo_video.agents import (  # noqa: E402
    AgentResult,
    InputValidationError,
    ALL_AGENTS,
    get_agent,
)


# ---------- Registry ----------

def test_all_agents_have_unique_names():
    names = [a.name for a in ALL_AGENTS]
    assert len(names) == len(set(names)), f"duplicate names: {names}"


def test_all_agents_have_required_methods():
    for a in ALL_AGENTS:
        assert callable(a.execute)
        assert callable(a.validate_inputs)
        assert callable(a.idempotency_key)
        assert isinstance(a.name, str) and a.name


def test_get_agent_returns_correct():
    a = get_agent("image_gen")
    assert a.name == "image_gen"
    assert isinstance(a, ag.ImageGenAgent)


def test_get_agent_unknown_raises():
    with pytest.raises(KeyError):
        get_agent("not_a_real_agent")


# ---------- ScriptWriterAgent ----------

def test_script_writer_validate_empty_novel_raises():
    with pytest.raises(InputValidationError):
        get_agent("script_writer").validate_inputs({"novel": ""})


def test_script_writer_execute_empty_novel_returns_not_ok():
    result = get_agent("script_writer").execute({"novel": ""})
    assert not result.ok
    assert "empty" in result.error


def test_script_writer_idempotency_key_stable():
    a = get_agent("script_writer")
    k1 = a.idempotency_key({"novel": "test story"})
    k2 = a.idempotency_key({"novel": "test story"})
    assert k1 == k2
    assert len(k1) == 16


def test_script_writer_idempotency_key_different_for_different_novel():
    a = get_agent("script_writer")
    k1 = a.idempotency_key({"novel": "story 1"})
    k2 = a.idempotency_key({"novel": "story 2"})
    assert k1 != k2


# ---------- AssetPrepAgent ----------

def test_asset_prep_validate_missing_storyboard():
    with pytest.raises(InputValidationError):
        get_agent("asset_prep").validate_inputs({"image_backend": MagicMock()})


def test_asset_prep_validate_missing_backend():
    sb = MagicMock()
    with pytest.raises(InputValidationError):
        get_agent("asset_prep").validate_inputs({"storyboard": sb})


def test_asset_prep_execute_missing_inputs_returns_not_ok():
    result = get_agent("asset_prep").execute({})
    assert not result.ok
    assert "missing" in result.error


def test_asset_prep_idempotency_key_returns_none_when_no_storyboard():
    a = get_agent("asset_prep")
    assert a.idempotency_key({"storyboard": None}) is None


# ---------- ImageGenAgent ----------

def test_image_gen_validate_empty_prompt():
    with pytest.raises(InputValidationError):
        get_agent("image_gen").validate_inputs({"prompt": ""})


def test_image_gen_execute_missing_backend():
    result = get_agent("image_gen").execute({"prompt": "a cat"})
    assert not result.ok
    assert "image_backend" in result.error


def test_image_gen_execute_empty_prompt():
    result = get_agent("image_gen").execute({"prompt": "", "image_backend": MagicMock()})
    assert not result.ok
    assert "prompt empty" in result.error


def test_image_gen_idempotency_key_uses_prompt_and_refs():
    a = get_agent("image_gen")
    k1 = a.idempotency_key({"prompt": "a cat", "subject_references": ["u1"], "aspect_ratio": "16:9"})
    k2 = a.idempotency_key({"prompt": "a cat", "subject_references": ["u1"], "aspect_ratio": "16:9"})
    assert k1 == k2
    k3 = a.idempotency_key({"prompt": "a dog", "subject_references": ["u1"], "aspect_ratio": "16:9"})
    assert k1 != k3


# ---------- ImageGenAgent reference 解析（Gemini 本地 ref 支持） ----------

def test_image_gen_reference_urls_keeps_local_paths_for_capable_backend():
    """Gemini 缓存的 ref 是本地路径；声明 supports_local_refs 时不能被过滤掉。

    过滤掉 = 角色一致性静默消失（R4）。
    """
    local = "assets/characters/农夫/front.jpg"
    backend = MagicMock()
    backend.supports_local_refs = True
    urls = ag.ImageGenAgent._reference_urls({"subject_references": [local]}, backend)
    assert urls == [local]


def test_image_gen_reference_urls_filters_local_paths_for_minimax_like_backend():
    """回归：不支持本地 ref 的 backend 维持原过滤（http only）。"""
    backend = MagicMock()
    backend.supports_local_refs = False
    task = {"subject_references": ["assets/characters/x/front.jpg", "https://e.com/a.jpg"]}
    assert ag.ImageGenAgent._reference_urls(task, backend) == ["https://e.com/a.jpg"]


def test_image_gen_reference_urls_defaults_to_http_only_when_backend_absent():
    """旧调用方式（不传 backend）行为不变。"""
    task = {"subject_references": ["assets/x.jpg", "https://e.com/a.jpg"]}
    assert ag.ImageGenAgent._reference_urls(task) == ["https://e.com/a.jpg"]


def test_image_gen_reference_urls_caps_at_four_for_local_paths():
    backend = MagicMock()
    backend.supports_local_refs = True
    refs = [f"assets/{i}.jpg" for i in range(6)]
    assert len(ag.ImageGenAgent._reference_urls({"subject_references": refs}, backend)) == 4


# ---------- VideoSynthAgent ----------

def test_video_synth_validate_missing_image_path():
    with pytest.raises(InputValidationError):
        get_agent("video_synth").validate_inputs({"output_path": "/tmp/o.mp4"})


def test_video_synth_validate_missing_output_path():
    with pytest.raises(InputValidationError):
        get_agent("video_synth").validate_inputs({"image_path": "/tmp/i.jpg"})


def test_video_synth_execute_missing_inputs():
    result = get_agent("video_synth").execute({})
    assert not result.ok
    assert "missing" in result.error


# ---------- TTSAgent ----------

def test_tts_validate_empty_text():
    with pytest.raises(InputValidationError):
        get_agent("tts").validate_inputs({"output": "/tmp/o.mp3"})


def test_tts_validate_missing_output():
    with pytest.raises(InputValidationError):
        get_agent("tts").validate_inputs({"text": "hi"})


def test_tts_execute_missing_inputs():
    result = get_agent("tts").execute({})
    assert not result.ok


# ---------- ConcatAgent ----------

def test_concat_validate_empty_shots():
    with pytest.raises(InputValidationError):
        ag.ConcatAgent().validate_inputs({"output": "/tmp/o.mp4"})


def test_concat_validate_missing_output():
    with pytest.raises(InputValidationError):
        ag.ConcatAgent().validate_inputs({"shots": [{"video": "/tmp/a.mp4"}]})


def test_concat_execute_missing_inputs():
    result = ag.ConcatAgent().execute({})
    assert not result.ok


def test_concat_not_on_dag():
    with pytest.raises(KeyError):
        get_agent("concat")


# ---------- QAAgent ----------

def test_qa_validate_no_required_inputs():
    """QA 不需必填 input（空 list 跳过）。"""
    get_agent("qa").validate_inputs({})  # 不抛


def test_qa_execute_empty_image_paths_skipped():
    result = get_agent("qa").execute({"image_paths": []})
    assert result.ok
    assert result.metadata.get("skipped") is True


def test_qa_idempotency_key_returns_none():
    """QA 无副作用 → key=None 表示不缓存。"""
    assert get_agent("qa").idempotency_key({}) is None


# ---------- Round-trip — execute delegates to existing modules ----------

def test_image_gen_execute_delegates_to_backend():
    """execute() 应直接调用 backend.generate()。"""
    fake_result = MagicMock()
    fake_result.bytes = b"\xff\xd8 fake"
    fake_result.model = "image-01"
    fake_result.duration_seconds = 1.5
    backend = MagicMock()
    backend.generate.return_value = [fake_result]
    a = get_agent("image_gen")
    task = {"prompt": "a cat", "image_backend": backend, "aspect_ratio": "16:9"}
    a.execute(task)
    backend.generate.assert_called_once()
    args, kwargs = backend.generate.call_args
    assert args[0] == "a cat"
    assert kwargs.get("aspect_ratio_value") == "16:9"


def test_video_synth_execute_delegates_to_backend():
    fake_vr = MagicMock()
    fake_vr.ok = True
    fake_vr.output_path = Path("/tmp/o.mp4")
    fake_vr.error = ""
    fake_vr.effect = "zoom_in"
    fake_vr.seconds = 5
    fake_vr.fps = 8
    backend = MagicMock()
    backend.render.return_value = fake_vr
    a = get_agent("video_synth")
    task = {
        "video_backend": backend,
        "image_path": "/tmp/i.jpg",
        "output_path": "/tmp/o.mp4",
        "seconds": 3,
        "fps": 12,
        "effect": "pan_left",
    }
    a.execute(task)
    backend.render.assert_called_once()
    args, kwargs = backend.render.call_args
    assert args[0] == "/tmp/i.jpg"
    assert args[1] == "/tmp/o.mp4"
    assert kwargs.get("seconds") == 3
    assert kwargs.get("fps") == 12
    assert kwargs.get("effect") == "pan_left"
