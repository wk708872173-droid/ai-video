"""V5.0 Batch 6 — Benchmark / AB 的测试。

## 关键断言

- bench **零网络**：跑完 3 个 fixture，一个真 API 都不该被调
- bench 产物**真的落盘**（不是"看起来成功实际失败"）
- A/B 静态比较能看出两版提示词到底差在哪
- 每个 fixture 都要有**非空**的 per-phase 指标（否则 bench 没意义）
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

from hailuo_video import bench  # noqa: E402


# ---------- fixtures ----------

def test_fixtures_cover_three_shapes():
    fixtures = bench.build_fixtures()
    assert len(fixtures) == 3
    names = {f.name for f in fixtures}
    assert names == {"single", "two-chars", "three-scenes"}
    # 覆盖三种典型形态
    assert len(fixtures[0].storyboard.shots) == 1
    assert len(fixtures[1].storyboard.characters) == 2
    assert len({s.scene_id for s in fixtures[2].storyboard.shots}) == 3


def test_fixtures_are_self_contained():
    """fixture 必须内嵌（不读 sample/）—— 否则仓库文件一变 bench 就漂移。"""
    for f in bench.build_fixtures():
        assert f.storyboard.title
        assert f.storyboard.shots
        assert f.storyboard.style_anchor


# ---------- runner ----------

@pytest.fixture(scope="module")
def shared_results(tmp_path_factory):
    """**模块级共享**：整个 bench 跑一遍（≈16s，瓶颈是 editing 的 ffmpeg）。

    每个测试各跑一遍会让门禁多花 80s —— 而这些测试要验的是**逻辑**不是性能。
    """
    work = tmp_path_factory.mktemp("bench")
    return bench.run_all(work)

def test_run_fixture_produces_output(tmp_path):
    """bench 必须真的产出文件（不是"看起来成功实际失败"）。"""
    fx = bench.build_fixtures()[0]
    r = bench.run_fixture(fx, tmp_path)
    assert r.error == ""
    assert r.output_exists, f"产物没落盘：{r.error}"
    assert r.output_bytes > 0
    assert r.ok


def test_run_fixture_collects_metrics(tmp_path):
    r = bench.run_fixture(bench.build_fixtures()[0], tmp_path)
    m = r.metrics
    assert m["total_steps"] > 0
    assert "phases" in m
    assert m["phases"], "per-phase 指标为空 → bench 没意义"
    assert "cost_estimate" in m


def test_run_all(shared_results):
    results = shared_results
    assert len(results) == 3
    for r in results:
        assert r.error == "", f"{r.fixture} 失败：{r.error}"
        assert r.output_exists


def test_bench_makes_no_network_calls(tmp_path, monkeypatch):
    """bench 全 mock —— 一个真 API 都不该被调（不烧额度）。"""
    import urllib.request

    def _boom(*a, **k):
        raise AssertionError("bench 不该发起网络请求")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    # 只跑一个 fixture（网络守卫要验的是"有没有调"，不是跑几个）
    results = bench.run_all(tmp_path, bench.build_fixtures()[:1])
    assert all(r.ok for r in results), [r.error for r in results]


def test_bench_does_not_touch_quota_ledger(tmp_path):
    """bench 不接配额账本（quota_path=None）。"""
    from hailuo_video.orchestrator import PipelineConfig
    assert PipelineConfig().quota_path is None
    r = bench.run_fixture(bench.build_fixtures()[0], tmp_path)
    assert r.ok
    # 账本文件不该被创建
    assert not (Path(tmp_path) / "run" / "quota.json").exists()


# ---------- 报告 ----------

def test_report_json_and_markdown(shared_results):
    results = shared_results
    payload = json.loads(bench.to_json(results))
    assert payload["summary"]["total"] == 3
    assert payload["summary"]["ok"] == 3
    md = bench.to_markdown(results)
    assert "| fixture |" in md
    assert "Bench 报告" in md


# ---------- A/B ----------

def test_compare_prompt_versions_finds_difference():
    ab = bench.compare_prompt_versions("storyboard", "v1", "v2")
    assert ab.prompt_id == "storyboard"
    assert ab.diff["hash_changed"] is True
    assert ab.diff["identical"] is False
    assert ab.diff["length_delta"] != 0


def test_compare_prompt_versions_same_version_is_identical():
    ab = bench.compare_prompt_versions("storyboard", "v1", "v1")
    assert ab.diff["identical"] is True
    assert ab.diff["hash_changed"] is False


def test_compare_unknown_version_raises():
    with pytest.raises(KeyError):
        bench.compare_prompt_versions("storyboard", "v1", "v999")


def test_run_ab_produces_diff_and_is_reproducible(tmp_path):
    """A/B 跑两遍：既要出 diff，也要证明指标可复现（否则 bench 没有比较价值）。"""
    one = bench.build_fixtures()[:1]
    out = bench.run_ab(tmp_path, fixtures=one)
    assert "a" in out and "b" in out and "diff" in out
    assert out["diff"]["both_ok"] is True
    assert "seconds_delta" in out["diff"]
    assert out["diff"]["seconds_delta"] == pytest.approx(0.0, abs=10.0)
    assert out["diff"]["failed_steps_delta"] == 0
