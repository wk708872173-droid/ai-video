"""V5.0 Batch 3 — 人机协同节点（Checkpoint）。

## 覆盖

1. **停下**：跑到检查点 → `source == "checkpoint"`、`ok is True`、产出确认报告。
2. **续跑**：报告里的 `resume_from` 指向下一个 phase，且**能被 CLI 接受**
   （`--resume-from` 的 choices 此前是写死的，缺 `script`/`motion_gen`/`editing`，
   checkpoint 的续跑起点 `script` 会被 argparse 直接拒掉）。
3. **默认策略**：CLI 默认只开 `prep`；`none` 全关；库层默认关闭
   （`daily_cron` 显式关掉 —— 无人值守生产不能等人确认）。
4. **契约变更**：`PipelineResult.ok` 现在对 `"checkpoint"` 也为 True
   （成功暂停 ≠ 失败）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SANDBOX = REPO_ROOT / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from hailuo_video.checkpoint import (  # noqa: E402
    DEFAULT_CHECKPOINT_PHASES,
    Checkpoint,
    CheckpointStore,
    build_report,
    next_phase_after,
    parse_checkpoint_phases,
)
from hailuo_video.orchestrator import DEFAULT_DAG, PipelineAgent, PipelineConfig  # noqa: E402


# ---------- 参数解析 ----------

def test_default_is_prep_only():
    assert DEFAULT_CHECKPOINT_PHASES == ("prep",)
    assert parse_checkpoint_phases(None) == ("prep",)


def test_none_disables_all():
    assert parse_checkpoint_phases("none") == ()
    assert parse_checkpoint_phases("off") == ()
    assert parse_checkpoint_phases("OFF") == ()


def test_parses_comma_list():
    assert parse_checkpoint_phases("prep,editing") == ("prep", "editing")


def test_empty_string_falls_back_to_default():
    assert parse_checkpoint_phases("") == ("prep",)


def test_next_phase_after():
    assert next_phase_after(DEFAULT_DAG, "prep") == "script"
    assert next_phase_after(DEFAULT_DAG, "qa") == ""


# ---------- 存储 ----------

def test_store_roundtrip(tmp_path):
    store = CheckpointStore(tmp_path)
    cp = Checkpoint(phase="prep", question="q?", report_path="r.md",
                    resume_from="script", approved=None)
    store.append(cp)
    got = store.latest()
    assert got is not None
    assert got.phase == "prep"
    assert got.resume_from == "script"
    assert got.approved is None
    assert store.pending()


def test_store_latest_by_phase(tmp_path):
    store = CheckpointStore(tmp_path)
    store.append(Checkpoint(phase="prep"))
    store.append(Checkpoint(phase="editing"))
    assert store.latest("prep").phase == "prep"
    assert store.latest().phase == "editing"


# ---------- 报告 ----------

def _sb():
    from hailuo_video.script_engine import Character, SceneSpec, Shot, Storyboard

    return Storyboard(
        title="t",
        characters=[
            Character(name="小黄",
                      appearance="一只毛色浅黄的中型犬，胸口有一块 V 形白斑",
                      personality=["机警"], color_palette=["浅黄", "白"]),
            Character(name="阿黑",
                      appearance="纯黑短毛中型犬",
                      personality=["沉稳"], color_palette=["纯黑"]),
        ],
        scenes=[SceneSpec(id=1, location="旧巷", atmosphere="黄昏")],
        shots=[Shot(index=1, scene_id=1, shot_type="中景", description="d")],
        style_anchor="写实",
    )


def test_report_contains_characters_and_ambiguity():
    """报告必须回答「角色长相对不对」—— 含角色表 + 名字歧义告警。"""
    md, summary = build_report("prep", _sb(), registry=None)
    assert "小黄" in md
    assert "阿黑" in md
    assert "歧义" in md
    assert summary["character_count"] == 2
    assert summary["ambiguity_warnings"], "「小黄」是已知歧义名，必须告警"


def test_report_without_ambiguity_says_ok():
    from hailuo_video.script_engine import Character, SceneSpec, Shot, Storyboard

    sb = Storyboard(
        title="t",
        characters=[Character(name="Puppy",
                              appearance="brown puppy", personality=[], color_palette=[])],
        scenes=[SceneSpec(id=1, location="x")],
        shots=[Shot(index=1, scene_id=1, shot_type="中景", description="d")],
    )
    md, summary = build_report("prep", sb, registry=None)
    assert "通过" in md
    assert not summary["ambiguity_warnings"]


# ---------- 编排器：真的停下 ----------

def test_pipeline_config_defaults_to_no_checkpoint():
    """库层默认关闭 —— 既有调用方行为不变。"""
    assert PipelineConfig().checkpoint_phases == ()


def test_checkpoint_phase_stops_pipeline(tmp_path):
    """跑到 prep 之后停下：source=checkpoint、ok=True、产出报告。"""
    from hailuo_video import orchestrator as orch

    sb = _sb()
    agent = PipelineAgent(PipelineConfig(
        run_dir=tmp_path / "run",
        checkpoint_phases=("prep",),
        skip_asset_prep=False,
    ))
    # 直接调 runner 的 phase 循环太重 → 用 _maybe_checkpoint 单测 + 集成断言
    from hailuo_video.orchestrator import Phase
    phase = next(p for p in DEFAULT_DAG if p.name == "prep")
    ctx = {"storyboard": sb, "shot_count": 1, "intermediate_dir": tmp_path}
    result = agent._maybe_checkpoint(phase, ctx, [], 0.0, tmp_path / "final.mp4")
    assert result is not None
    assert result.source == "checkpoint"
    assert result.ok is True, "checkpoint 是成功暂停，不是失败"
    assert (tmp_path / "run" / "checkpoints" / "prep.md").is_file()
    cp = CheckpointStore(tmp_path / "run").latest()
    assert cp is not None
    assert cp.resume_from == "script"


def test_non_checkpoint_phase_returns_none(tmp_path):
    from hailuo_video.orchestrator import Phase
    agent = PipelineAgent(PipelineConfig(
        run_dir=tmp_path / "run", checkpoint_phases=("prep",),
    ))
    phase = next(p for p in DEFAULT_DAG if p.name == "image_gen")
    ctx = {"storyboard": _sb(), "shot_count": 1, "intermediate_dir": tmp_path}
    assert agent._maybe_checkpoint(phase, ctx, [], 0.0, tmp_path / "f.mp4") is None


def test_no_checkpoint_config_never_stops(tmp_path):
    from hailuo_video.orchestrator import Phase
    agent = PipelineAgent(PipelineConfig(run_dir=tmp_path / "run"))
    phase = next(p for p in DEFAULT_DAG if p.name == "prep")
    ctx = {"storyboard": _sb(), "shot_count": 1, "intermediate_dir": tmp_path}
    assert agent._maybe_checkpoint(phase, ctx, [], 0.0, tmp_path / "f.mp4") is None


def test_checkpoint_logs_event(tmp_path):
    from hailuo_video.orchestrator import Phase
    agent = PipelineAgent(PipelineConfig(
        run_dir=tmp_path / "run", checkpoint_phases=("prep",),
    ))
    phase = next(p for p in DEFAULT_DAG if p.name == "prep")
    ctx = {"storyboard": _sb(), "shot_count": 1, "intermediate_dir": tmp_path}
    agent._maybe_checkpoint(phase, ctx, [], 0.0, tmp_path / "f.mp4")
    log = tmp_path / "run" / "log.jsonl"
    events = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    assert any(e.get("event_type") == "checkpoint" for e in events)


# ---------- CLI ----------

def test_cli_has_checkpoint_option():
    from hailuo_video import cli
    args = cli.build_parser().parse_args(["pipeline", "sb.json", "-o", "o.mp4"])
    assert args.checkpoint is None      # None → 解析成 ("prep",)


def test_cli_resume_from_accepts_every_dag_phase():
    """`--resume-from` 的 choices 必须覆盖 DEFAULT_DAG 的每个 phase。

    旧实现写死 ["prep","image_gen","video_synth","tts","concat","qa"] ——
    缺 script / motion_gen / editing，还留着 V3.2 已改名的 "concat"。
    后果：checkpoint 的续跑起点（prep 之后 = script）会被 argparse 直接拒掉。
    """
    from hailuo_video import cli
    parser = cli.build_parser()
    for phase in DEFAULT_DAG:
        args = parser.parse_args(
            ["pipeline", "sb.json", "-o", "o.mp4", "--resume-from", phase.name]
        )
        assert args.resume_from == phase.name


def test_cli_resume_from_rejects_unknown_phase():
    from hailuo_video import cli
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(
            ["pipeline", "sb.json", "-o", "o.mp4", "--resume-from", "concat"]
        )


def test_daily_cron_disables_checkpoint():
    """无人值守生产不能停在等人确认上 —— cron 必须显式 --checkpoint none。"""
    cron = (REPO_ROOT / "scripts" / "daily_cron.py").read_text(encoding="utf-8")
    assert '"--checkpoint", "none"' in cron, (
        "daily_cron 必须显式 --checkpoint none，否则 cron 会跑完 prep 就停、"
        "看起来成功但没有成片"
    )


# ---------- 连带修复：motion_backend 注入 + 成片落到请求的路径 ----------

def test_cli_has_motion_backend_option():
    """`--motion-backend mock` —— 试跑整条链路不能烧 Hailuo 额度。"""
    from hailuo_video import cli
    args = cli.build_parser().parse_args(
        ["pipeline", "sb.json", "-o", "o.mp4", "--motion-backend", "mock"]
    )
    assert args.motion_backend == "mock"


def test_default_motion_backend_is_minimax():
    """默认仍是真后端 —— 不改变生产行为。"""
    from hailuo_video import cli
    args = cli.build_parser().parse_args(["pipeline", "sb.json", "-o", "o.mp4"])
    assert args.motion_backend == "minimax"


def test_motion_gen_task_carries_motion_backend(tmp_path):
    """**回归护栏**：motion_gen phase 自己造 task（不走 _build_task），
    必须显式带上 motion_backend —— 漏了它 `--motion-backend mock` 形同虚设，
    照样提交真 Hailuo 任务烧额度（本轮实测踩到：3 条真视频被提交）。
    """
    from hailuo_video import motion_backend as mb
    from hailuo_video.script_engine import SceneSpec, Shot, Storyboard

    sb = Storyboard(
        title="t", characters=[],
        scenes=[SceneSpec(id=1, location="x")],
        shots=[Shot(index=1, scene_id=1, shot_type="中景", description="d",
                    motion_complexity="simple")],
    )
    sentinel = mb.MockMotionBackend()
    agent = PipelineAgent(PipelineConfig(
        run_dir=tmp_path / "run", motion_backend=sentinel,
    ))
    ctx = {"storyboard": sb, "shot_count": 1, "intermediate_dir": tmp_path}
    # 造一张假图，让 phase 能找到 image artifact
    img = tmp_path / "img.jpg"
    img.write_bytes(b"x")
    from hailuo_video.artifact import Artifact
    agent.registry.add(Artifact(
        artifact_id="image_0", kind="image", path=img, producer="test",
        metadata={"shot_idx": 0},
    ))
    agent._run_motion_gen_conditional(ctx)
    assert sentinel.calls, "mock motion backend 没被调用 —— task 没带上 motion_backend"


def test_motion_backend_config_defaults_to_none():
    assert PipelineConfig().motion_backend is None


def test_pipeline_result_bytes_reflects_real_file(tmp_path):
    """`bytes` 此前**从未赋值**（恒 0）→ CLI 永远打印 "(0 bytes)"。"""
    from hailuo_video.orchestrator import PipelineResult
    out = tmp_path / "f.mp4"
    out.write_bytes(b"0" * 1234)
    r = PipelineResult(output=str(out), bytes=out.stat().st_size)
    assert r.bytes == 1234
    assert r.to_dict()["bytes"] == 1234


def test_cli_fails_when_output_missing(tmp_path, capsys):
    """R4：0 字节 / 文件不存在**不许报 OK**。"""
    from hailuo_video import cli

    args = cli.build_parser().parse_args(
        ["pipeline", "sb.json", "-o", str(tmp_path / "nope.mp4")]
    )
    args.image_backend = "mock"
    args.tts_backend = "mock"
    args.motion_backend = "mock"
    args.voice = None
    args.video_seconds = 5
    args.video_fps = 8
    args.video_effect = "cycle"
    args.assets_dir = str(tmp_path / "assets")
    args.skip_asset_prep = False
    args.keep_intermediate = True
    args.parallel = 1
    args.motion_budget = 0
    args.aspect_ratio = None
    args.knowledge_path = None
    args.run_dir = str(tmp_path / "run")
    args.resume_from = None
    args.checkpoint = "none"

    sb = tmp_path / "sb.json"
    sb.write_text(json.dumps({
        "title": "t", "style_anchor": "写实",
        "characters": [],
        "scenes": [{"id": 1, "location": "x", "atmosphere": "y"}],
        "shots": [{"index": 1, "scene_id": 1, "shot_type": "中景",
                   "description": "d", "motion_complexity": "static"}],
    }, ensure_ascii=False), encoding="utf-8")
    args.storyboard = str(sb)

    rc = cli.cmd_pipeline(args)
    err = capsys.readouterr().err
    # motion_budget=0 + static → 没有真视频；editing 仍应产出 Ken Burns 成片
    # 若产物没落到请求路径，必须显式失败（而不是 [OK] + 0 bytes）
    assert "0 bytes" not in err or rc != 0, (
        "CLI 对 0 字节产物报了 OK —— 违反 R4"
    )
    if rc == 0:
        assert (tmp_path / "nope.mp4").is_file(), "rc=0 但产物不在请求的路径上"
