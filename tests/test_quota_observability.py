"""V5.0 Batch 4 — 成本治理（Quota Ledger）+ 可观测性（Run 上下文 / 指标）。

## 覆盖

1. **配额账本**：三类资源（image/video/tts）统一记账、按日 reset、原子写、
   跨日重置、**损坏 → 硬失败**（不静默重建）。
2. **记账点在编排器**：调 backend **之前** consume；额度不足 → 跳过 + 明确告警。
3. **绕过 cron 也记账**（本轮实测的漏洞）。
4. **可观测性**：run_id 贯穿日志、agent 级事件、指标聚合、成本估算。
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

from hailuo_video.observability import (  # noqa: E402
    Metrics,
    PhaseMetric,
    RunContext,
    aggregate,
    estimate_cost,
    make_run_id,
    write_run_meta,
)
from hailuo_video.orchestrator import PipelineAgent, PipelineConfig, PipelineStep  # noqa: E402
from hailuo_video.quota import (  # noqa: E402
    DEFAULT_BUDGETS,
    QuotaError,
    QuotaLedger,
    QuotaRecord,
    load_quota,
    save_quota,
)


# ---------- 账本基本行为 ----------

def test_new_ledger_has_all_three_kinds(tmp_path):
    ledger = QuotaLedger(tmp_path / "q.json")
    st = ledger.status()
    assert set(st["used"]) == {"image", "video", "tts"}
    assert st["budget"]["video"] == DEFAULT_BUDGETS["video"]


def test_consume_increments_and_persists(tmp_path):
    p = tmp_path / "q.json"
    ledger = QuotaLedger(p)
    assert ledger.consume("video") is True
    assert ledger.consume("image", 3) is True
    assert ledger.load().used("video") == 1
    assert ledger.load().used("image") == 3
    # 已落盘（重新打开也读得到）
    assert QuotaLedger(p).load().used("image") == 3


def test_consume_refuses_when_exhausted(tmp_path):
    ledger = QuotaLedger(tmp_path / "q.json", budgets={"video": 2})
    assert ledger.consume("video") is True
    assert ledger.consume("video") is True
    assert ledger.consume("video") is False, "额度用尽后必须拒绝记账"
    assert ledger.load().used("video") == 2, "被拒绝的调用不该被计数"


def test_legacy_schema_is_readable(tmp_path):
    """旧 schema（只有 hailuo_calls_used）必须能读 —— 向后兼容。"""
    import datetime

    p = tmp_path / "q.json"
    p.write_text(json.dumps({
        "date": datetime.date.today().isoformat(),
        "hailuo_calls_used": 2, "daily_budget": 3,
    }), encoding="utf-8")
    rec = QuotaLedger(p).load()
    assert rec.hailuo_calls_used == 2
    assert rec.used("video") == 2


def test_cross_day_resets_counters(tmp_path):
    p = tmp_path / "q.json"
    p.write_text(json.dumps({
        "date": "2000-01-01", "hailuo_calls_used": 3, "daily_budget": 3,
    }), encoding="utf-8")
    rec = QuotaLedger(p).load()
    assert rec.used("video") == 0, "跨日必须重置计数"
    assert rec.daily_budget == 3, "跨日必须保留预算设置"


def test_corrupt_ledger_raises_hard_error(tmp_path):
    """成本域硬失败：损坏的账本**不能**静默重建
    （静默重建 = 把"已用 3 条"当成"已用 0 条" → 直接超支）。"""
    p = tmp_path / "q.json"
    p.write_text("not json", encoding="utf-8")
    with pytest.raises(QuotaError):
        QuotaLedger(p).load()


def test_legacy_constructor_still_works():
    """旧调用面 `QuotaRecord(hailuo_calls_used=...)` 必须继续可用。"""
    rec = QuotaRecord(date="2026-11-03", hailuo_calls_used=2, daily_budget=3)
    assert rec.hailuo_calls_used == 2
    assert rec.used("video") == 2
    assert rec.remaining("video") == 1


def test_save_quota_roundtrip_keeps_legacy_field(tmp_path):
    p = tmp_path / "q.json"
    rec = QuotaRecord(date="2026-11-03", hailuo_calls_used=1, daily_budget=3)
    save_quota(p, rec)
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert raw["hailuo_calls_used"] == 1
    assert load_quota(p).hailuo_calls_used == 1


# ---------- 编排器记账 ----------

def _sb(n=1):
    from hailuo_video.script_engine import SceneSpec, Shot, Storyboard
    return Storyboard(
        title="t", characters=[],
        scenes=[SceneSpec(id=1, location="x")],
        shots=[Shot(index=i + 1, scene_id=1, shot_type="中景", description="d",
                    motion_complexity="simple") for i in range(n)],
    )


def test_orchestrator_consumes_image_quota(tmp_path):
    """编排器在调 image backend **之前**记账。"""
    from hailuo_video import image_engine as ie

    backend = ie.MockImageBackend()
    agent = PipelineAgent(PipelineConfig(
        run_dir=tmp_path / "run", image_backend=backend,
        quota_path=tmp_path / "q.json",
    ))
    ctx = {"storyboard": _sb(2), "shot_count": 2, "intermediate_dir": tmp_path}
    agent._build_task("image_gen", 0, ctx, None)
    agent._run_agent_safe("image_gen", agent._build_task("image_gen", 0, ctx, None),
                          "image_gen", 0)
    assert QuotaLedger(tmp_path / "q.json").load().used("image") == 1


def test_orchestrator_skips_when_quota_exhausted(tmp_path):
    """额度用尽 → 跳过该 agent + **明确**记 reason（不静默截断）。"""
    from hailuo_video import image_engine as ie

    backend = ie.MockImageBackend()
    qpath = tmp_path / "q.json"
    ledger = QuotaLedger(qpath, budgets={"image": 0})
    ledger.consume("image", 0)
    ledger.load().budgets["image"] = 0
    ledger.load().counters["image"] = 0
    # 直接把预算改成 0
    rec = ledger.load()
    rec.budgets["image"] = 0
    ledger.save()

    agent = PipelineAgent(PipelineConfig(
        run_dir=tmp_path / "run", image_backend=backend, quota_path=qpath,
    ))
    ctx = {"storyboard": _sb(1), "shot_count": 1, "intermediate_dir": tmp_path}
    task = agent._build_task("image_gen", 0, ctx, None)
    step = agent._run_agent_safe("image_gen", task, "image_gen", 0)
    assert step.ok is False
    assert "quota_exhausted" in step.error
    assert not backend.calls, "额度用尽时不该调 backend"


def test_quota_exhausted_recorded_in_consistency(tmp_path):
    from hailuo_video import image_engine as ie

    qpath = tmp_path / "q.json"
    rec = QuotaRecord(date="2026-11-03", hailuo_calls_used=0, daily_budget=3)
    rec.budgets["image"] = 0
    save_quota(qpath, rec)

    agent = PipelineAgent(PipelineConfig(
        run_dir=tmp_path / "run", image_backend=ie.MockImageBackend(), quota_path=qpath,
    ))
    ctx = {"storyboard": _sb(1), "shot_count": 1, "intermediate_dir": tmp_path}
    agent._run_agent_safe("image_gen", agent._build_task("image_gen", 0, ctx, None),
                          "image_gen", 0)
    consistency = agent._build_consistency(ctx)
    assert consistency.get("quota_exhausted_count") == 1


def test_quota_disabled_by_default(tmp_path):
    """库层默认不记账（既有调用方行为不变）。"""
    assert PipelineConfig().quota_path is None


def test_cli_quota_file_default():
    from hailuo_video import cli
    args = cli.build_parser().parse_args(["pipeline", "sb.json", "-o", "o.mp4"])
    assert args.quota_file == "run/quota.json", (
        "CLI 默认必须与 daily_cron 用同一份账本 → 手动跑与定时跑都记账"
    )


def test_daily_cron_uses_same_ledger_module():
    """daily_cron 与编排器必须共用**同一份**账本实现（两套实现会漂移）。"""
    cron = (REPO_ROOT / "scripts" / "daily_cron.py").read_text(encoding="utf-8")
    assert "from hailuo_video.quota import" in cron
    assert "class QuotaRecord" not in cron, "daily_cron 不该再自带一份 QuotaRecord"


def test_daily_cron_does_not_double_count():
    """编排器已记账时，cron 不能再 +1（否则跑一次算 2 → 额度提前耗尽）。"""
    cron = (REPO_ROOT / "scripts" / "daily_cron.py").read_text(encoding="utf-8")
    assert "rec.hailuo_calls_used += 1" not in cron, (
        "cron 不能再无条件 +1 —— 编排器已经按实际调用记账了"
    )
    assert "after.hailuo_calls_used <= rec.hailuo_calls_used" in cron


# ---------- 可观测性 ----------

def test_run_id_is_unique_per_run():
    a = make_run_id("proj1", seed="run")
    b = make_run_id("proj1", seed="run")
    assert a != b, "不同 run 必须有不同的 run_id"
    assert a.startswith("proj1-")


def test_run_context_log_fields():
    ctx = RunContext(run_id="r1", project_id="p1")
    assert ctx.log_fields() == {"run_id": "r1", "project_id": "p1"}


def test_aggregate_metrics():
    steps = [
        PipelineStep(name="image_gen_001", index=0, ok=True, duration_seconds=1.0),
        PipelineStep(name="image_gen_002", index=1, ok=False, duration_seconds=3.0),
        PipelineStep(name="tts_001", index=0, ok=True, duration_seconds=0.5),
    ]
    m = aggregate(steps, run_id="r1")
    assert m.total_steps == 3
    assert m.ok_steps == 2
    assert m.failed_steps == 1
    assert m.phases["image_gen"].calls == 2
    assert m.phases["image_gen"].success_rate == 0.5
    assert m.phases["tts"].calls == 1
    d = m.to_dict()
    assert d["run_id"] == "r1"
    assert d["success_rate"] == pytest.approx(2 / 3, abs=1e-3)


def test_estimate_cost_is_relative():
    c = estimate_cost(image_calls=3, video_seconds=6.0, tts_calls=2)
    assert c["unit"] == "relative"
    assert c["image"] == 3.0
    assert c["video"] == 6.0
    assert c["tts"] == 0.0
    assert c["total"] == 9.0


def test_write_run_meta_is_backward_compatible(tmp_path):
    """老 registry.json（无 meta 块）→ 加 meta 后仍能读。"""
    p = tmp_path / "registry.json"
    p.write_text(json.dumps({"artifacts": {"a": {"artifact_id": "a"}}}),
                 encoding="utf-8")
    write_run_meta(p, {"run_id": "r1"})
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["meta"]["run_id"] == "r1"
    assert "a" in data["artifacts"], "原有 artifacts 不能丢"


def test_orchestrator_emits_agent_completed_event(tmp_path):
    """`log.py` 的 docstring 早就声称有 agent_completed，实际从未 emit —— 补上。"""
    from hailuo_video import image_engine as ie

    agent = PipelineAgent(PipelineConfig(
        run_dir=tmp_path / "run", image_backend=ie.MockImageBackend(),
    ))
    ctx = {"storyboard": _sb(1), "shot_count": 1, "intermediate_dir": tmp_path}
    agent._run_agent_safe("image_gen", agent._build_task("image_gen", 0, ctx, None),
                          "image_gen", 0)
    log = tmp_path / "run" / "log.jsonl"
    events = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    ev = [e for e in events if e.get("event_type") == "agent_completed"]
    assert ev, "log.jsonl 里没有 agent_completed 事件"
    payload = ev[0].get("payload", {})
    assert payload.get("agent") == "image_gen"
    assert payload.get("run_id"), "事件必须带 run_id（串起三套日志）"


def test_all_log_events_carry_run_id(tmp_path):
    """每个事件都应带 run_id —— 否则拼不出完整链路。"""
    from hailuo_video import image_engine as ie

    agent = PipelineAgent(PipelineConfig(
        run_dir=tmp_path / "run", image_backend=ie.MockImageBackend(),
    ))
    ctx = {"storyboard": _sb(1), "shot_count": 1, "intermediate_dir": tmp_path}
    agent._run_agent_safe("image_gen", agent._build_task("image_gen", 0, ctx, None),
                          "image_gen", 0)
    log = tmp_path / "run" / "log.jsonl"
    events = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    assert events
    for e in events:
        assert e.get("payload", {}).get("run_id"), f"事件缺 run_id: {e}"
