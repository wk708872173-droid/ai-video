"""V3.8 Batch 0 — P0 止血批的回归护栏。

锁住本批修掉的 6 个已确认缺陷（每个测试对应一条 file:line 级缺陷）：

  缺陷 1  9:16 在 V3 生产路径不可达（画幅被硬编码 "16:9"）
  缺陷 2  motion_complexity="static" 静默降级（走 Ken Burns 却报 [OK]）
  缺陷 12 concat_files 的 duration_seconds 报的是墙钟耗时而非视频时长
  缺陷 13 registry 落盘失败被 `except OSError: pass` 吞掉
  缺陷 15 image artifact_id 不按 shot 隔离 → 多镜 storyboard 全部镜头共用最后一张图

不覆盖：缺陷 14（`_collect_video_paths_for_editing` 的死分支）—— 那是代码整洁问题，
不改变行为，无可观测契约；已在 orchestrator 里删掉分支，不在本文件断言。

R4：所有测试都不打真 API（mock backend / mock ffmpeg）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO = Path(__file__).resolve().parents[1]
SANDBOX = REPO / "sandbox"
SCRIPTS = REPO / "scripts"
for _p in (SANDBOX, SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from hailuo_video import agents as ag  # noqa: E402
from hailuo_video.agents import ImageGenAgent  # noqa: E402
from hailuo_video.orchestrator import (  # noqa: E402
    PipelineAgent,
    PipelineConfig,
)
from hailuo_video.project import ProjectContext  # noqa: E402
from hailuo_video.script_engine import (  # noqa: E402
    Character,
    SceneSpec,
    Shot,
    Storyboard,
)


# ---------- helpers ----------

def _fake_image_result(tag: str):
    """造一个 ImageResult-like 对象（带 bytes / mime_type / model）。"""
    from hailuo_video.image_engine import ImageResult
    return ImageResult(
        prompt=tag,
        bytes=b"\xff\xd8\xff\xe0" + b"\x00" * 200,
        mime_type="jpeg",
        model="fake",
        duration_seconds=0.0,
    )


def _storyboard(n: int = 2, motion: str = "static") -> Storyboard:
    chars = [Character(name="农夫", appearance="宋代农夫", personality=[], color_palette=["灰褐"])]
    scenes = [SceneSpec(id=1, location="田野", key_elements=[])]
    shots = [
        Shot(
            index=i + 1,
            scene_id=1,
            shot_type="中景",
            description=f"镜头{i+1}",
            voiceover=f"旁白{i+1}",
            motion_complexity=motion,
        )
        for i in range(n)
    ]
    return Storyboard(title="t", characters=chars, scenes=scenes, shots=shots)


# ============================================================
# 缺陷 15：image artifact_id / 落盘文件名必须按 shot 隔离
# ============================================================

def test_image_artifact_ids_are_isolated_per_shot(tmp_path):
    """两个 shot 的图必须是不同 artifact_id。

    旧实现恒为 f"image_{idx}"，n=1 时两个 shot 都是 "image_0"，
    Registry.add 用 artifact_id 做 key → 后者覆盖前者。
    """
    agent = ImageGenAgent()
    ids = []
    for shot_idx in (0, 1):
        backend = MagicMock()
        backend.generate.return_value = [_fake_image_result(f"s{shot_idx}")]
        task = {
            "prompt": "p",
            "image_backend": backend,
            "shot_idx": shot_idx,
            "intermediate_dir": str(tmp_path),
        }
        result = agent.execute(task)
        assert result.ok, result.error
        assert len(result.artifacts) == 1
        ids.append(result.artifacts[0].artifact_id)
    assert ids == ["image_0", "image_1"], f"artifact_id 未按 shot 隔离：{ids}"


def test_image_artifact_id_extra_images_do_not_collide(tmp_path):
    """同一 shot 出多张图时，第 2 张不能与第 1 张撞 id。"""
    agent = ImageGenAgent()
    backend = MagicMock()
    backend.generate.return_value = [_fake_image_result("a"), _fake_image_result("b")]
    task = {
        "prompt": "p",
        "image_backend": backend,
        "shot_idx": 3,
        "intermediate_dir": str(tmp_path),
    }
    result = agent.execute(task)
    assert result.ok, result.error
    ids = [a.artifact_id for a in result.artifacts]
    assert ids == ["image_3", "image_3_v1"], f"多图 id 撞车：{ids}"


def test_image_files_are_isolated_per_shot(tmp_path):
    """落盘文件名也必须按 shot 隔离（否则后一个 shot 覆盖前一个的 jpg）。"""
    agent = ImageGenAgent()
    for shot_idx in (0, 1):
        backend = MagicMock()
        backend.generate.return_value = [_fake_image_result(f"s{shot_idx}")]
        agent.execute({
            "prompt": "p",
            "image_backend": backend,
            "shot_idx": shot_idx,
            "intermediate_dir": str(tmp_path),
        })
    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == ["image_000.jpg", "image_001.jpg"], f"落盘文件名撞车：{files}"


def test_image_artifact_id_without_shot_idx_keeps_legacy_scheme(tmp_path):
    """无 shot_idx（直接调 agent 的旧调用方）→ 退回 f"image_{idx}"，保持向后兼容。"""
    agent = ImageGenAgent()
    backend = MagicMock()
    backend.generate.return_value = [_fake_image_result("x")]
    result = agent.execute({
        "prompt": "p",
        "image_backend": backend,
        "intermediate_dir": str(tmp_path),
    })
    assert result.ok, result.error
    assert result.artifacts[0].artifact_id == "image_0"


def test_get_image_path_for_shot_matches_by_metadata(tmp_path):
    """缺陷 15：_get_image_path_for_shot 必须按 metadata.shot_idx 精确命中。

    旧实现只按 artifact_id 命名规则找 "image_{shot_idx}"，
    而 ImageGenAgent 造的是 "image_{idx}" → 首镜拿到最后一张图，其余落到兜底。
    """
    from hailuo_video.artifact import Artifact, Registry

    agent = PipelineAgent(PipelineConfig(run_dir=tmp_path / "run"))
    reg = Registry()
    for shot_idx in (0, 1):
        p = tmp_path / f"img_{shot_idx}.jpg"
        p.write_bytes(b"\xff\xd8\xff\xe0")
        reg.add(Artifact(
            artifact_id=f"image_{shot_idx}",
            kind="image",
            path=p,
            producer="test",
            metadata={"shot_idx": shot_idx},
        ), compute_hash=False)
    agent.registry = reg
    assert agent._get_image_path_for_shot({}, 0).endswith("img_0.jpg")
    assert agent._get_image_path_for_shot({}, 1).endswith("img_1.jpg")


# ============================================================
# 缺陷 1：画幅（aspect_ratio）贯通
# ============================================================

def test_resolve_aspect_ratio_prefers_explicit_config():
    agent = PipelineAgent(PipelineConfig(aspect_ratio="9:16"))
    assert agent._resolve_aspect_ratio() == "9:16"


def test_resolve_aspect_ratio_falls_back_to_backend_env(monkeypatch):
    """config 没指定 → 问 backend（backend 读自己的 env）。"""
    monkeypatch.setenv("MINIMAX_IMAGE_ASPECT_RATIO", "9:16")
    backend = MagicMock()
    backend.default_aspect_ratio.return_value = "9:16"
    agent = PipelineAgent(PipelineConfig(image_backend=backend))
    assert agent._resolve_aspect_ratio() == "9:16"


def test_resolve_aspect_ratio_default_is_16x9_when_nothing_configured():
    agent = PipelineAgent(PipelineConfig(image_backend=None))
    assert agent._resolve_aspect_ratio() == "16:9"


def test_pipeline_config_does_not_hardcode_aspect_ratio():
    """PipelineConfig 默认必须是 None（"16:9" 会遮蔽 env）。"""
    assert PipelineConfig().aspect_ratio is None


def test_agent_resolve_aspect_ratio_prefers_task_value():
    backend = MagicMock()
    backend.default_aspect_ratio.return_value = "1:1"
    assert ImageGenAgent._resolve_aspect_ratio({"aspect_ratio": "9:16"}, backend) == "9:16"


def test_agent_resolve_aspect_ratio_uses_backend_when_task_empty():
    backend = MagicMock()
    backend.default_aspect_ratio.return_value = "4:5"
    assert ImageGenAgent._resolve_aspect_ratio({}, backend) == "4:5"


def test_agent_resolve_aspect_ratio_handles_backend_without_default():
    backend = MagicMock(spec=[])          # 没有 default_aspect_ratio 属性
    assert ImageGenAgent._resolve_aspect_ratio({}, backend) == "16:9"


def test_project_context_does_not_override_env_aspect_ratio():
    """缺陷 1：ProjectContext 默认不写 MINIMAX_IMAGE_ASPECT_RATIO。

    旧实现无条件写默认值 "16:9" → apply_env() 覆盖用户 .env 里的 9:16。
    """
    ctx = ProjectContext.from_storyboard_path(_write_storyboard(tmp_path := _tmpdir()))
    assert "MINIMAX_IMAGE_ASPECT_RATIO" not in ctx.env_overrides
    assert ctx.aspect_ratio is None


def test_project_context_with_explicit_aspect_ratio_sets_env_override():
    ctx = ProjectContext.from_storyboard_path(
        _write_storyboard(_tmpdir()), aspect_ratio="9:16",
    )
    assert ctx.env_overrides["MINIMAX_IMAGE_ASPECT_RATIO"] == "9:16"
    assert ctx.aspect_ratio == "9:16"


def test_run_pipeline_v3_propagates_aspect_ratio(tmp_path):
    """run_pipeline_v3(aspect_ratio="9:16") → image_gen task 拿到 "9:16"。"""
    from hailuo_video import pipeline as pl

    sb = _storyboard(1)
    captured: list[dict] = []

    class _Backend:
        def default_aspect_ratio(self) -> str:
            return "16:9"

        def generate(self, prompt, **opts):
            captured.append({"prompt": prompt, **opts})
            return [_fake_image_result("x")]

    backend = _Backend()
    with patch.object(pl, "PipelineAgent") as agent_cls:
        instance = agent_cls.return_value
        instance.run.return_value = pl.PipelineResult(output="out.mp4")
        pl.run_pipeline_v3(
            sb, tmp_path / "final.mp4",
            image_backend=backend,
            tts_backend=MagicMock(),
            video_backend=MagicMock(),
            aspect_ratio="9:16",
            run_dir=tmp_path / "run",
        )
    cfg = agent_cls.call_args[0][0]
    assert cfg.aspect_ratio == "9:16"


def test_run_pipeline_v3_keeps_env_aspect_ratio_when_not_given(tmp_path):
    """不传 aspect_ratio → cfg.aspect_ratio 为 None → 由 backend 的 env 决定。"""
    from hailuo_video import pipeline as pl

    sb = _storyboard(1)
    with patch.object(pl, "PipelineAgent") as agent_cls:
        agent_cls.return_value.run.return_value = pl.PipelineResult(output="out.mp4")
        pl.run_pipeline_v3(
            sb, tmp_path / "final.mp4",
            image_backend=MagicMock(),
            tts_backend=MagicMock(),
            video_backend=MagicMock(),
            run_dir=tmp_path / "run",
        )
    cfg = agent_cls.call_args[0][0]
    assert cfg.aspect_ratio is None


def test_cli_pipeline_subcommand_exposes_aspect_ratio():
    """pipeline 子命令必须有 --aspect-ratio（此前只有 image 子命令有）。"""
    from hailuo_video import cli

    parser = cli.build_parser()
    args = parser.parse_args([
        "pipeline", "sb.json", "-o", "out.mp4", "--aspect-ratio", "9:16",
    ])
    assert args.aspect_ratio == "9:16"
    # 不传时为 None → 走 env / backend 默认
    args2 = parser.parse_args(["pipeline", "sb.json", "-o", "out.mp4"])
    assert args2.aspect_ratio is None


# ============================================================
# 缺陷 2：static 降级必须显式可见
# ============================================================

def test_static_shots_are_reported_not_silent(tmp_path):
    """motion_complexity=static 的镜头要落进 PipelineResult.consistency。"""
    from hailuo_video.artifact import Artifact, Registry

    agent = PipelineAgent(PipelineConfig(run_dir=tmp_path / "run", motion_budget=3))
    sb = _storyboard(3, motion="static")
    ctx = {"storyboard": sb, "shot_count": 3, "intermediate_dir": tmp_path}

    agent._run_motion_gen_conditional(ctx)

    assert ctx["static_skipped_shots"] == [0, 1, 2]
    consistency = agent._build_consistency(ctx)
    assert consistency["static_skipped_shots"] == [0, 1, 2]
    assert consistency["static_skipped_count"] == 3


def test_static_skip_is_logged_as_event(tmp_path):
    """必须留 event（可在 logs/*.jsonl 里追溯），不能只体现在返回值。"""
    agent = PipelineAgent(PipelineConfig(run_dir=tmp_path / "run"))
    sb = _storyboard(2, motion="static")
    ctx = {"storyboard": sb, "shot_count": 2, "intermediate_dir": tmp_path}

    with patch.object(agent.logger, "event") as ev:
        agent._run_motion_gen_conditional(ctx)

    names = [c.args[0] for c in ev.call_args_list]
    assert "motion_skipped_static" in names
    kwargs = [c.kwargs for c in ev.call_args_list if c.args[0] == "motion_skipped_static"][0]
    assert kwargs["count"] == 2
    assert kwargs["shot_indices"] == [0, 1]


def test_non_static_shots_are_not_reported_as_skipped(tmp_path):
    agent = PipelineAgent(PipelineConfig(run_dir=tmp_path / "run"))
    sb = _storyboard(2, motion="simple")
    ctx = {"storyboard": sb, "shot_count": 2, "intermediate_dir": tmp_path}
    agent._run_motion_gen_conditional(ctx)
    assert ctx["static_skipped_shots"] == []


# ============================================================
# 缺陷 13：registry 落盘失败不再静默
# ============================================================

def test_registry_save_failure_is_surfaced(tmp_path):
    """registry.save_json 抛 OSError → 记进 consistency，不再静默 pass。"""
    from hailuo_video.artifact import Artifact, Registry

    agent = PipelineAgent(PipelineConfig(run_dir=tmp_path / "run"))
    reg = Registry()
    p = tmp_path / "a.jpg"
    p.write_bytes(b"\xff\xd8")
    reg.add(Artifact(artifact_id="image_0", kind="image", path=p,
                     producer="t", metadata={"shot_idx": 0}), compute_hash=False)
    agent.registry = reg

    err = ""
    try:
        reg.save_json(tmp_path / "registry.json")
    except OSError as exc:
        err = str(exc)
    assert err == "", "正常路径不应抛错"

    # 落盘失败 → consistency 必须带上错误（不静默）
    with patch.object(reg, "save_json", side_effect=OSError("disk full")):
        save_err = ""
        try:
            reg.save_json(tmp_path / "registry.json")
        except OSError as exc:
            save_err = str(exc)
    consistency = agent._build_consistency({}, registry_save_error=save_err)
    assert "disk full" in consistency["registry_save_error"]


def test_run_surfaces_registry_save_failure(tmp_path, monkeypatch):
    """端到端：run() 落盘失败 → PipelineResult.consistency 有 registry_save_error。"""
    from hailuo_video.artifact import Artifact, Registry

    agent = PipelineAgent(PipelineConfig(run_dir=tmp_path / "run"))
    sb = _storyboard(1)
    reg = Registry()
    p = tmp_path / "a.jpg"
    p.write_bytes(b"\xff\xd8")
    reg.add(Artifact(artifact_id="image_0", kind="image", path=p,
                     producer="t", metadata={"shot_idx": 0}), compute_hash=False)
    agent.registry = reg
    # 让所有 phase 走 mock（不调真 agent）
    _mock_all_agents(agent)

    with patch.object(Registry, "save_json", side_effect=OSError("disk full")):
        result = agent.run(sb, tmp_path / "final.mp4")

    assert result.consistency.get("registry_save_error"), result.consistency


# ============================================================
# 缺陷 12：concat_files 的 duration_seconds 必须是视频时长
# ============================================================

def test_concat_result_reports_video_duration_not_wallclock(tmp_path):
    """旧实现把墙钟耗时写进 duration_seconds（40.5s 的片子报 0.18s）。"""
    import lib_ffmpeg

    out = tmp_path / "final.mp4"
    out.write_bytes(b"\x00" * 10)               # 假装产物存在
    inputs = [tmp_path / "a.mp4", tmp_path / "b.mp4"]
    for f in inputs:
        f.write_bytes(b"\x00")

    with patch.object(lib_ffmpeg, "safe_run"), \
         patch.object(lib_ffmpeg, "probe_duration", return_value=40.5):
        res = lib_ffmpeg.concat_files(inputs, out)

    assert res.duration_seconds == 40.5, f"duration_seconds 应是视频时长，实际 {res.duration_seconds}"
    assert res.elapsed_seconds >= 0.0
    assert res.clip_count == 2


def test_concat_result_has_elapsed_seconds_field():
    import lib_ffmpeg
    fields = set(lib_ffmpeg.ConcatResult.__dataclass_fields__)
    assert {"duration_seconds", "elapsed_seconds"} <= fields


# ---------- local helpers ----------

def _tmpdir():
    import tempfile
    return Path(tempfile.mkdtemp(prefix="p0_stopgap_"))


def _write_storyboard(directory: Path) -> Path:
    import json
    directory.mkdir(parents=True, exist_ok=True)
    sb = {
        "title": "t",
        "style_anchor": "shot on 35mm film",
        "characters": [],
        "scenes": [],
        "shots": [{
            "index": 1, "scene_id": 1, "shot_type": "中景",
            "description": "d", "voiceover": "v",
        }],
    }
    p = directory / "storyboard.json"
    p.write_text(json.dumps(sb, ensure_ascii=False), encoding="utf-8")
    return p


def _mock_all_agents(agent: PipelineAgent) -> None:
    """把 agent lookup 全换成 mock（不调真 agent / 真 API）。"""
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
