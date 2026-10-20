"""auto_dev.py 门禁脚本测试（V3.5 P5）。

回归重点：**coverage 未达标时不得 auto commit**。

背景：2026-10-20 实际事故 —— 覆盖率 78% < 80% 门禁红，
auto_dev 仍执行了 `git commit`，把上一会话遗留的脏文件
（agents.py 改动 + 2 个脚本删除）打包提交，commit message
还写着「测试通过」，造成误导。

修法：commit 条件纳入 coverage，新增 `--force-commit` 显式放行。
本文件锁死该行为，防止回归。

测试策略：monkeypatch `run` 拦截所有 subprocess，不执行真 git / pytest。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import auto_dev  # noqa: E402


class _FakeRun:
    """记录所有 subprocess 调用，按命令前缀返回预设输出。"""

    def __init__(self, *, coverage_pct: int = 90, git_dirty: bool = True):
        self.calls: list[list[str]] = []
        self.coverage_pct = coverage_pct
        self.git_dirty = git_dirty

    def __call__(self, cmd, *, cwd=None, timeout=600):
        self.calls.append(list(cmd))
        joined = " ".join(cmd)

        if any(str(a).startswith("--cov") for a in cmd):
            out = (
                "sandbox\\hailuo_video\\agents.py   372   70   81%\n"
                f"TOTAL   5546   1017   {self.coverage_pct}%\n"
                "846 passed, 12 skipped"
            )
            return 0, out, ""

        if joined.startswith("git status"):
            return 0, (" M tests/foo.py\n" if self.git_dirty else ""), ""

        if joined.startswith("git add"):
            return 0, "", ""

        if joined.startswith("git commit"):
            return 0, "", ""

        return 0, "", ""


def _main(monkeypatch, argv: list[str]) -> int:
    """auto_dev.main() 不收 argv，走 argparse 默认读 sys.argv。"""
    monkeypatch.setattr(sys, "argv", ["auto_dev.py", *argv])
    return auto_dev.main()


def _git_calls(fake: _FakeRun) -> list[list[str]]:
    return [c for c in fake.calls if c and c[0] == "git"]


# ---------- 核心回归：coverage 红则不 commit ----------

def test_no_commit_when_coverage_below_target(monkeypatch):
    """覆盖率 78% < 80% → 不得执行 git commit（V3.5 P5 事故回归）。"""
    fake = _FakeRun(coverage_pct=78)
    monkeypatch.setattr(auto_dev, "run", fake)
    monkeypatch.setattr(auto_dev, "step_tests", lambda a: True)
    monkeypatch.setattr(auto_dev, "step_secret_scan", lambda a: True)
    monkeypatch.setattr(auto_dev, "step_lint", lambda a: True)

    rc = _main(monkeypatch, [])
    assert rc == 1, "coverage FAIL 时退出码应为 1"
    assert not _git_calls(fake), f"coverage 红时不应有任何 git 调用：{_git_calls(fake)}"


def test_no_commit_when_coverage_exactly_at_target_is_allowed(monkeypatch):
    """覆盖率 80%（== target）→ 允许 commit（边界含等号）。"""
    fake = _FakeRun(coverage_pct=80)
    monkeypatch.setattr(auto_dev, "run", fake)
    monkeypatch.setattr(auto_dev, "step_tests", lambda a: True)
    monkeypatch.setattr(auto_dev, "step_secret_scan", lambda a: True)
    monkeypatch.setattr(auto_dev, "step_lint", lambda a: True)

    _main(monkeypatch, [])
    commits = [c for c in _git_calls(fake) if "commit" in c]
    assert len(commits) == 1


def test_commit_when_coverage_ok(monkeypatch):
    """覆盖率达标 → 正常 commit。"""
    fake = _FakeRun(coverage_pct=82)
    monkeypatch.setattr(auto_dev, "run", fake)
    monkeypatch.setattr(auto_dev, "step_tests", lambda a: True)
    monkeypatch.setattr(auto_dev, "step_secret_scan", lambda a: True)
    monkeypatch.setattr(auto_dev, "step_lint", lambda a: True)

    rc = _main(monkeypatch, [])
    assert rc == 0
    commits = [c for c in _git_calls(fake) if "commit" in c]
    assert len(commits) == 1


def test_force_commit_overrides_coverage(monkeypatch):
    """--force-commit 显式放行（逃生舱）。"""
    fake = _FakeRun(coverage_pct=70)
    monkeypatch.setattr(auto_dev, "run", fake)
    monkeypatch.setattr(auto_dev, "step_tests", lambda a: True)
    monkeypatch.setattr(auto_dev, "step_secret_scan", lambda a: True)
    monkeypatch.setattr(auto_dev, "step_lint", lambda a: True)

    _main(monkeypatch, ["--force-commit"])
    commits = [c for c in _git_calls(fake) if "commit" in c]
    assert len(commits) == 1


def test_no_commit_flag_blocks_commit(monkeypatch):
    """--no-commit 即使全绿也不提交。"""
    fake = _FakeRun(coverage_pct=95)
    monkeypatch.setattr(auto_dev, "run", fake)
    monkeypatch.setattr(auto_dev, "step_tests", lambda a: True)
    monkeypatch.setattr(auto_dev, "step_secret_scan", lambda a: True)
    monkeypatch.setattr(auto_dev, "step_lint", lambda a: True)

    _main(monkeypatch, ["--no-commit"])
    assert not [c for c in _git_calls(fake) if "commit" in c]


# ---------- 硬失败短路（测试/扫描/语法红 → 立即 return 1） ----------

def test_tests_fail_short_circuits(monkeypatch):
    fake = _FakeRun()
    monkeypatch.setattr(auto_dev, "run", fake)
    monkeypatch.setattr(auto_dev, "step_tests", lambda a: False)
    assert _main(monkeypatch, []) == 1
    assert not _git_calls(fake)


def test_secret_scan_fail_short_circuits(monkeypatch):
    fake = _FakeRun()
    monkeypatch.setattr(auto_dev, "run", fake)
    monkeypatch.setattr(auto_dev, "step_tests", lambda a: True)
    monkeypatch.setattr(auto_dev, "step_secret_scan", lambda a: False)
    assert _main(monkeypatch, []) == 1
    assert not [c for c in _git_calls(fake) if "commit" in c]


def test_lint_fail_blocks_commit(monkeypatch):
    fake = _FakeRun(coverage_pct=90)
    monkeypatch.setattr(auto_dev, "run", fake)
    monkeypatch.setattr(auto_dev, "step_tests", lambda a: True)
    monkeypatch.setattr(auto_dev, "step_secret_scan", lambda a: True)
    monkeypatch.setattr(auto_dev, "step_lint", lambda a: False)
    assert _main(monkeypatch, []) == 1
    assert not [c for c in _git_calls(fake) if "commit" in c]


# ---------- coverage gate 解析 ----------

def test_coverage_gate_parses_pct(monkeypatch):
    fake = _FakeRun(coverage_pct=77)
    monkeypatch.setattr(auto_dev, "run", fake)
    ok, pct = auto_dev.step_coverage_gate(None)
    assert ok is False
    assert pct == 77.0


def test_coverage_gate_passes_at_target(monkeypatch):
    fake = _FakeRun(coverage_pct=80)
    monkeypatch.setattr(auto_dev, "run", fake)
    ok, pct = auto_dev.step_coverage_gate(None)
    assert ok is True
    assert pct == 80.0


def test_coverage_gate_fails_on_unparsable_output(monkeypatch):
    """覆盖率输出解析不出来 → 视为 FAIL（fail closed，不放行 commit）。"""
    monkeypatch.setattr(auto_dev, "run", lambda *a, **k: (0, "no coverage here", ""))
    ok, pct = auto_dev.step_coverage_gate(None)
    assert ok is False
    assert pct == 0.0


def test_coverage_gate_fails_when_pytest_missing(monkeypatch):
    monkeypatch.setattr(auto_dev, "run", lambda *a, **k: (1, "", "pytest not found"))
    ok, _ = auto_dev.step_coverage_gate(None)
    assert ok is False


# ---------- step_commit 边界 ----------

def test_step_commit_skips_when_clean(monkeypatch):
    fake = _FakeRun(git_dirty=False)
    monkeypatch.setattr(auto_dev, "run", fake)
    assert auto_dev.step_commit(None, coverage_ok=True) is True
    assert not [c for c in _git_calls(fake) if "commit" in c]


def test_step_commit_message_reflects_coverage_ok(monkeypatch):
    """覆盖率达标 → message 标注「覆盖率达标」。"""
    seen: list[str] = []
    fake = _FakeRun()

    def _capture(cmd, *, cwd=None, timeout=600):
        if cmd and cmd[0] == "git" and "commit" in cmd:
            seen.append(cmd[cmd.index("-m") + 1])
        return fake(cmd, cwd=cwd, timeout=timeout)

    monkeypatch.setattr(auto_dev, "run", _capture)
    auto_dev.step_commit(None, coverage_ok=True)
    assert seen and "覆盖率达标" in seen[0]


def test_step_commit_message_flags_low_coverage(monkeypatch):
    """--force-commit 放行时 → message 必须如实标注覆盖率未达标。"""
    seen: list[str] = []
    fake = _FakeRun()

    def _capture(cmd, *, cwd=None, timeout=600):
        if cmd and cmd[0] == "git" and "commit" in cmd:
            seen.append(cmd[cmd.index("-m") + 1])
        return fake(cmd, cwd=cwd, timeout=timeout)

    monkeypatch.setattr(auto_dev, "run", _capture)
    auto_dev.step_commit(None, coverage_ok=False)
    assert seen and "覆盖率未达标" in seen[0]


def test_step_commit_does_not_use_minus_dash_u_on_root(monkeypatch):
    """git add 必须带 -u（禁止 -A，避免误吞未跟踪产物）。"""
    fake = _FakeRun()
    monkeypatch.setattr(auto_dev, "run", fake)
    auto_dev.step_commit(None, coverage_ok=True)
    adds = [c for c in _git_calls(fake) if c[1] == "add"]
    assert adds and "-u" in adds[0]
    assert "-A" not in adds[0] and "--all" not in adds[0]


# ---------- 异常兜底 ----------

def test_main_returns_2_on_unexpected_exception(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(auto_dev, "run", _boom)
    monkeypatch.setattr(auto_dev, "step_tests", _boom)
    assert _main(monkeypatch, []) == 2


def test_only_tests_skips_git(monkeypatch):
    fake = _FakeRun()
    monkeypatch.setattr(auto_dev, "run", fake)
    monkeypatch.setattr(auto_dev, "step_tests", lambda a: True)
    assert _main(monkeypatch, ["--only-tests"]) == 0
    assert not _git_calls(fake)


@pytest.mark.parametrize("flag", ["--skip-tests", "--skip-secret-scan", "--skip-lint"])
def test_skip_flags_still_run_remaining_gate(monkeypatch, flag):
    fake = _FakeRun(coverage_pct=90)
    monkeypatch.setattr(auto_dev, "run", fake)
    monkeypatch.setattr(auto_dev, "step_tests", lambda a: True)
    monkeypatch.setattr(auto_dev, "step_secret_scan", lambda a: True)
    monkeypatch.setattr(auto_dev, "step_lint", lambda a: True)
    rc = _main(monkeypatch, [flag])
    assert rc == 0