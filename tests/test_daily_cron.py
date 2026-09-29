"""V3.2 REQ-305 daily_cron 单测。

设计：
  - 注入 `_runner` mock：模拟 pipeline 跑通 / 失败
  - 单元覆盖：load_quota / save_quota / quota_remaining / run_once / CLI main
  - 不调真 subprocess，不依赖真 storyboard
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import daily_cron as dc  # noqa: E402
from daily_cron import (  # noqa: E402
    DailyCronError,
    DailyReport,
    QuotaRecord,
    _build_argparser,
    load_quota,
    main,
    quota_remaining,
    run_once,
    save_quota,
    set_runner,
)


# ---------- quota 管理 ----------

def test_load_quota_missing_file_returns_today(tmp_path):
    rec = load_quota(tmp_path / "no_quota.json")
    assert rec.date  # 非空
    assert rec.hailuo_calls_used == 0
    assert rec.daily_budget == 3


def test_load_quota_existing_today(tmp_path):
    p = tmp_path / "quota.json"
    p.write_text(json.dumps({"date": dc._today(), "hailuo_calls_used": 1,
                              "daily_budget": 3}), encoding="utf-8")
    rec = load_quota(p)
    assert rec.hailuo_calls_used == 1


def test_load_quota_cross_date_resets(tmp_path):
    p = tmp_path / "quota.json"
    p.write_text(json.dumps({"date": "2020-01-01", "hailuo_calls_used": 99,
                              "daily_budget": 3}), encoding="utf-8")
    rec = load_quota(p)
    assert rec.hailuo_calls_used == 0  # 跨日重置
    assert rec.date == dc._today()


def test_load_quota_corrupt_raises(tmp_path):
    p = tmp_path / "quota.json"
    p.write_text("not json", encoding="utf-8")
    with pytest.raises(DailyCronError, match="quota.json 解析失败"):
        load_quota(p)


def test_save_quota_atomic(tmp_path):
    p = tmp_path / "quota.json"
    rec = QuotaRecord(date=dc._today(), hailuo_calls_used=2, daily_budget=3)
    save_quota(p, rec)
    assert p.is_file()
    raw_data = json.loads(p.read_text(encoding="utf-8"))
    assert raw_data["hailuo_calls_used"] == 2


def test_quota_remaining():
    assert quota_remaining(QuotaRecord(hailuo_calls_used=0, daily_budget=3)) == 3
    assert quota_remaining(QuotaRecord(hailuo_calls_used=3, daily_budget=3)) == 0
    assert quota_remaining(QuotaRecord(hailuo_calls_used=5, daily_budget=3)) == 0  # 不能负


# ---------- run_once ----------

class _FakeRunner:
    """注入 _runner：可预设 return code + stdout/stderr。"""
    def __init__(self, rc: int = 0, stdout: str = "ok", stderr: str = "") -> None:
        self.rc = rc
        self.stdout = stdout
        self.stderr = stderr
        self.calls: list[list[str]] = []

    def __call__(self, cmd):
        self.calls.append(list(cmd))
        return self.rc, self.stdout, self.stderr


@pytest.fixture
def fake_runner():
    r = _FakeRunner(rc=0, stdout="pipeline ran", stderr="")
    set_runner(r)
    yield r
    set_runner(None)


@pytest.fixture
def storyboard(tmp_path):
    p = tmp_path / "storyboard.json"
    p.write_text(json.dumps({"title": "x", "characters": [], "scenes": [],
                              "shots": [{"index": 1, "scene_id": 1, "shot_type": "中景",
                                          "description": "d", "characters": [],
                                          "dialogue": "", "voiceover": "", "mood": [],
                                          "camera_motion": "", "subject_action": "x",
                                          "motion_complexity": "static", "duration": 5}]}),
                 encoding="utf-8")
    return p


def test_run_once_success_increments_quota(fake_runner, tmp_path, storyboard):
    qf = tmp_path / "quota.json"
    rf = tmp_path / "daily_report.md"
    rec_before = QuotaRecord(date=dc._today(), hailuo_calls_used=0, daily_budget=3)

    report = run_once(
        motion_budget=3, storyboard=storyboard,
        quota_file=qf, report_file=rf,
        pipeline_cmd=["python", "-m", "hailuo_video", "pipeline"],
    )
    assert report.pipeline_ran is True
    assert report.pipeline_exit_code == 0
    assert report.quota_status == "ok"

    # quota 计数 +1
    saved = json.loads(qf.read_text(encoding="utf-8"))
    assert saved["hailuo_calls_used"] == 1

    # 日报存在
    assert rf.is_file()
    md = rf.read_text(encoding="utf-8")
    assert "Daily Pipeline Report" in md
    assert "1/3" in md  # budget_used_after / budget_total


def test_run_once_exhausted_quota_skips(fake_runner, tmp_path, storyboard):
    qf = tmp_path / "quota.json"
    rf = tmp_path / "daily_report.md"
    # 预算用尽
    rec = QuotaRecord(date=dc._today(), hailuo_calls_used=3, daily_budget=3)
    save_quota(qf, rec)

    report = run_once(
        motion_budget=3, storyboard=storyboard,
        quota_file=qf, report_file=rf,
        pipeline_cmd=["python", "-m", "hailuo_video", "pipeline"],
    )
    assert report.pipeline_ran is False
    assert report.quota_status == "exhausted"
    # 不调 _runner
    assert fake_runner.calls == []

    # 日报应有 [WARN]
    md = rf.read_text(encoding="utf-8")
    assert "已用尽" in md

    # quota 计数不变
    saved = json.loads(qf.read_text(encoding="utf-8"))
    assert saved["hailuo_calls_used"] == 3


def test_run_once_pipeline_fail_raises_and_no_increment(tmp_path, storyboard):
    qf = tmp_path / "quota.json"
    rf = tmp_path / "daily_report.md"
    set_runner(_FakeRunner(rc=1, stderr="boom"))

    with pytest.raises(DailyCronError, match="pipeline 失败 rc=1"):
        run_once(
            motion_budget=3, storyboard=storyboard,
            quota_file=qf, report_file=rf,
            pipeline_cmd=["python", "-m", "hailuo_video", "pipeline"],
        )
    # quota 不应计数（防止 quota 被错扣）
    saved = json.loads(qf.read_text(encoding="utf-8"))
    assert saved["hailuo_calls_used"] == 0
    # 日报应记录 fail
    md = rf.read_text(encoding="utf-8")
    assert "pipeline_failed" in md

    set_runner(None)


def test_run_once_temp_budget_override(fake_runner, tmp_path, storyboard):
    """--motion-budget N 临时覆盖 quota 中 budget。"""
    qf = tmp_path / "quota.json"
    rf = tmp_path / "daily_report.md"
    rec = QuotaRecord(date=dc._today(), hailuo_calls_used=2, daily_budget=3)
    save_quota(qf, rec)

    report = run_once(
        motion_budget=5, storyboard=storyboard,
        quota_file=qf, report_file=rf,
        pipeline_cmd=["python", "-m", "hailuo_video", "pipeline"],
    )
    assert report.pipeline_ran is True  # 5-2=3 剩余
    # quota 中 budget 已被更新
    saved = json.loads(qf.read_text(encoding="utf-8"))
    assert saved["daily_budget"] == 5


# ---------- CLI main ----------

def test_main_returns_1_on_daily_cron_error(tmp_path, monkeypatch):
    qf = tmp_path / "no_quota"
    rf = tmp_path / "no_report"
    # quota 解析错 → 走 DailyCronError 路径
    qf = tmp_path / "corrupt.json"
    qf.write_text("not json", encoding="utf-8")
    rf = tmp_path / "report.md"
    rc = main([
        "--storyboard", str(tmp_path / "no_sb.json"),
        "--quota-file", str(qf),
        "--report-file", str(rf),
        "--pipeline-cmd", "fake cmd",
    ])
    assert rc == 1


def test_main_success_path(fake_runner, tmp_path, storyboard):
    rc = main([
        "--motion-budget", "3",
        "--storyboard", str(storyboard),
        "--quota-file", str(tmp_path / "q.json"),
        "--report-file", str(tmp_path / "r.md"),
        "--pipeline-cmd", "python -m hailuo_video pipeline",
    ])
    assert rc == 0
    # quota 应 +1
    saved = json.loads((tmp_path / "q.json").read_text(encoding="utf-8"))
    assert saved["hailuo_calls_used"] == 1


def test_argparser_defaults():
    p = _build_argparser()
    ns = p.parse_args([])
    assert ns.motion_budget == 3
    assert ns.storyboard == "storyboard_template.json"
    assert ns.quota_file == "run/quota.json"
    assert ns.report_file == "logs/daily_report.md"
    assert ns.pipeline_cmd == ""


def test_report_to_markdown_contains_required_fields(tmp_path):
    r = DailyReport(
        date="2026-09-29",
        budget_total=3,
        budget_used_before=1,
        budget_used_after=2,
        pipeline_ran=True,
        pipeline_cmd="python -m hailuo_video pipeline storyboard.json",
        pipeline_exit_code=0,
        pipeline_stdout_tail="hello",
        pipeline_stderr_tail="",
        quota_status="ok",
        timestamp="2026-09-29T09:00:00",
        notes=["note1"],
    )
    md = r.to_markdown()
    assert "# Daily Pipeline Report — 2026-09-29" in md
    assert "2/3 hailuo calls used today" in md
    assert "**Budget**" in md  # bolded label
    assert "note1" in md
    assert "hello" in md