"""V3.5 W2：ProjectContext 接入 pipeline 测试。"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
for p in (SANDBOX,):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


@dataclass
class _FakeStoryboard:
    title: str = "test"
    characters: list = None
    scenes: list = None
    shots: list = None
    style_anchor: str = ""

    def __post_init__(self):
        if self.characters is None:
            self.characters = []
        if self.scenes is None:
            self.scenes = []
        if self.shots is None:
            self.shots = []


@pytest.fixture
def clean_minimax_env(monkeypatch):
    """清理 test_image_engine 残留的污染 env vars。

    test_image_engine.py 直接用 os.environ 设值（不是 monkeypatch），
    → test pollution 会让 pipeline.make_backend() 报非法值。
    本 fixture 在每个 W2 测试前清理 + 设合法 dummy key。
    """
    for key in (
        "MINIMAX_API_KEY",
        "MINIMAX_IMAGE_RESPONSE_FORMAT",
        "MINIMAX_IMAGE_MODEL",
        "MINIMAX_IMAGE_ASPECT_RATIO",
        "MINIMAX_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 32)
    return monkeypatch


def test_pipeline_run_pipeline_v3_accepts_project_context(clean_minimax_env, tmp_path):
    """V3.5 W2：run_pipeline_v3 接受 ProjectContext 并覆盖默认路径。"""
    from hailuo_video.project import ProjectContext
    from hailuo_video import pipeline as pl_mod

    clean_minimax_env.chdir(tmp_path)
    sb_path = tmp_path / "story.json"
    sb_path.write_text(json.dumps({"title": "test", "characters": [], "scenes": [], "shots": []}), encoding="utf-8")

    ctx = ProjectContext.from_storyboard_path(sb_path, aspect_ratio="9:16")
    sb = _FakeStoryboard()

    result = pl_mod.run_pipeline_v3(
        sb,
        output=tmp_path / "out.mp4",
        image_backend=None,
        video_backend=None,
        tts_backend=None,
        project_context=ctx,
        run_dir=tmp_path / "manual_run",  # 应该被 ctx.run_dir 覆盖
        assets_dir=tmp_path / "manual_assets",
    )
    assert result.intermediate_dir.startswith(str(ctx.run_dir)), (
        f"ctx.run_dir 应被采用。intermediate_dir={result.intermediate_dir}, ctx.run_dir={ctx.run_dir}"
    )


def test_pipeline_run_pipeline_v3_backward_compatible(clean_minimax_env, tmp_path):
    """V3.5 W2：无 project_context 时，run_pipeline_v3 仍按 V3.4 行为工作。"""
    from hailuo_video import pipeline as pl_mod

    clean_minimax_env.chdir(tmp_path)
    sb_path = tmp_path / "story.json"
    sb_path.write_text(json.dumps({"title": "test"}), encoding="utf-8")
    sb = _FakeStoryboard()

    result = pl_mod.run_pipeline_v3(
        sb,
        output=tmp_path / "out.mp4",
        run_dir=tmp_path / "my_run",
        assets_dir=tmp_path / "my_assets",
    )
    assert result.intermediate_dir.startswith(str(tmp_path / "my_run"))


def test_pipeline_rejects_invalid_project_context(clean_minimax_env, tmp_path):
    """V3.5 W2：project_context 必须是 ProjectContext 实例。"""
    from hailuo_video import pipeline as pl_mod

    clean_minimax_env.chdir(tmp_path)
    sb_path = tmp_path / "story.json"
    sb_path.write_text(json.dumps({"title": "test"}), encoding="utf-8")
    sb = _FakeStoryboard()

    with pytest.raises(ValueError, match="ProjectContext"):
        pl_mod.run_pipeline_v3(
            sb,
            output=tmp_path / "out.mp4",
            project_context="not a ProjectContext",  # type: ignore
        )