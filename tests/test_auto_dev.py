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
    """记录所有 subprocess 调用，按命令前缀返回预设输出。

    dirty_scope 控制 git status 返回哪些脏文件：
      - "managed" → 只有管辖路径（sandbox/tests/scripts/docs）有改动
      - "outside" → 只有管辖路径**外**（sample/）有改动
      - "both"    → 两者都有
      - "clean"   → 全干净
    """

    def __init__(self, *, coverage_pct: int = 90, dirty_scope: str = "managed"):
        self.calls: list[list[str]] = []
        self.coverage_pct = coverage_pct
        self.dirty_scope = dirty_scope

    def _status_out(self, cmd: list[str]) -> str:
        """模拟 `git status --short [-- paths...]` 的输出。"""
        scoped = "--" in cmd
        managed = self.dirty_scope in ("managed", "both")
        outside = self.dirty_scope in ("outside", "both")
        lines: list[str] = []
        if managed and (not scoped or True):
            lines.append(" M tests/foo.py")
        if outside and not scoped:
            lines.append(" M sample/story/README.md")
        return ("\n".join(lines) + "\n") if lines else ""

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
            return 0, self._status_out(cmd), ""

        if joined.startswith("git add"):
            return 0, "", ""

        if joined.startswith("git commit"):
            # 真 git：暂存区为空时 commit 报错 rc=1
            if self.dirty_scope == "clean":
                return 1, "", "nothing to commit, working tree clean"
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
    fake = _FakeRun(dirty_scope="clean")
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

# ---------- 回归：管辖路径外的脏文件不应导致空 commit 失败 ----------

def test_dirty_outside_managed_paths_does_not_break_commit(monkeypatch):
    """只有 sample/ 有脏文件（add 路径外）→ 应正常跳过，不应报 FAIL。

    事故场景：git status 全仓检查非空 → 但 git add 只 add sandbox/tests/
    scripts/docs（sample/ 不在其中）→ 暂存区空 → git commit 报 rc=1
    → step_commit 返回 False → 门禁误报 FAIL。
    修法：status 检查与 add 路径对齐（-- pathspec）。
    """
    fake = _FakeRun(dirty_scope="outside")
    monkeypatch.setattr(auto_dev, "run", fake)
    assert auto_dev.step_commit(None, coverage_ok=90.0) is True
    assert not [c for c in _git_calls(fake) if "commit" in c],         "管辖路径外干净时不应尝试 commit"


def test_status_and_add_paths_are_aligned(monkeypatch):
    """两次 git status 都必须带 pathspec，与 git add 的路径一致。"""
    fake = _FakeRun(dirty_scope="both")
    monkeypatch.setattr(auto_dev, "run", fake)
    auto_dev.step_commit(None, coverage_ok=90.0)

    statuses = [c for c in _git_calls(fake) if c[1] == "status"]
    adds = [c for c in _git_calls(fake) if c[1] == "add"]
    assert len(statuses) == 2, "应有两次 status 检查"
    assert adds, "应有 git add"
    for st in statuses:
        assert "--" in st, f"status 必须限定 pathspec：{st}"
        for p in auto_dev.AUTO_COMMIT_PATHS:
            assert p in st, f"status pathspec 缺 {p}：{st}"
    for ad in adds:
        for p in auto_dev.AUTO_COMMIT_PATHS:
            assert p in ad, f"add 缺 {p}：{ad}"


def test_dirty_in_both_scopes_commits(monkeypatch):
    """管辖路径内外都有脏文件 → 只提交管辖路径内的（sample/ 不受影响）。"""
    fake = _FakeRun(dirty_scope="both")
    monkeypatch.setattr(auto_dev, "run", fake)
    assert auto_dev.step_commit(None, coverage_ok=90.0) is True
    commits = [c for c in _git_calls(fake) if "commit" in c]
    assert len(commits) == 1


def test_clean_worktree_skips_cleanly(monkeypatch):
    """全干净 → 跳过并返回 True（不报 FAIL）。"""
    fake = _FakeRun(dirty_scope="clean")
    monkeypatch.setattr(auto_dev, "run", fake)
    assert auto_dev.step_commit(None, coverage_ok=90.0) is True
    assert not [c for c in _git_calls(fake) if "commit" in c]


def _args(**kwargs):
    """step_tests 只读 args.coverage —— 用最小 namespace 即可。"""
    import argparse
    kwargs.setdefault("coverage", False)
    return argparse.Namespace(**kwargs)



# ---------- V3.9：门禁的 pytest 调用必须是**路径**，不是字面量 "TESTS" ----------

def test_step_tests_passes_real_tests_path(monkeypatch):
    """V3.9 回归：pytest 的路径参数必须是 `str(TESTS)`。

    此前传的是字面量字符串 "TESTS"（变量名漏进了命令行）→ pytest 收到一个
    不存在的路径 → 回退到 pyproject 的 testpaths，**但不会加载 tests/conftest.py**
    （conftest 按 args 的目录链加载）→ 门禁跑测试时 conftest 里的 fixture 与
    session 级环境准备被静默跳过。
    实测：V3.9 加的 `isolated_env` fixture 在门禁里直接报 "fixture not found"。
    """
    fake = _FakeRun()
    monkeypatch.setattr(auto_dev, "run", fake)
    monkeypatch.setattr(
        auto_dev, "step_secret_scan", lambda a: True, raising=False
    )
    auto_dev.step_tests(_args())

    pytest_calls = [c for c in fake.calls if "pytest" in c]
    assert pytest_calls, "没跑 pytest"
    cmd = pytest_calls[0]
    assert "TESTS" not in cmd, f"命令里仍有字面量 TESTS：{cmd}"
    assert str(auto_dev.TESTS) in cmd, f"pytest 没拿到真实 tests 路径：{cmd}"
    assert Path(str(auto_dev.TESTS)).is_dir()


def test_step_tests_uses_a_path_that_exists(monkeypatch):
    """pytest 的每个路径参数都必须真实存在（否则 conftest 不会被加载）。"""
    fake = _FakeRun()
    monkeypatch.setattr(auto_dev, "run", fake)
    auto_dev.step_tests(_args())
    cmd = [c for c in fake.calls if "pytest" in c][0]
    for arg in cmd:
        if arg.startswith("--") or arg.startswith("-"):
            continue
        if arg in ("python", "-m", "pytest", "q"):
            continue
        p = Path(arg)
        if p.suffix == ".py" or p.is_dir():
            assert p.exists(), f"pytest 参数 {arg!r} 不存在"


# ---------- V3.9：secret scan 不得把凭据本身打进日志（Floor Rule 4） ----------

def test_secret_scan_redacts_the_secret_value(monkeypatch, capsys):
    """旧实现打的是 `m.group()[:30]` —— 那就是密钥的前 30 个字符。

    Floor Rule 4：凭据在日志 / 报告里只能是 ***REDACTED***。
    """
    secret = "sk-cp-" + "A" * 40
    fake = _FakeRun()
    monkeypatch.setattr(auto_dev, "run", fake)
    # 让 diff 里含一个"真格式"的假密钥
    monkeypatch.setattr(
        auto_dev.subprocess, "run",
        lambda *a, **k: type("R", (), {"stdout": "diff " + secret + "\n"})(),
    )
    assert auto_dev.step_secret_scan(None) is False
    err = capsys.readouterr().out
    assert "***REDACTED***" in err
    assert secret not in err, "secret scan 的输出里出现了凭据本身"


def test_secret_scan_falls_back_to_whole_tree(monkeypatch):
    """diff 为空（CI 干净工作树）→ 退化为整仓扫描，而不是空转。"""
    assert hasattr(auto_dev, "_scan_tracked_files")
    text = auto_dev._scan_tracked_files()
    assert isinstance(text, str)
    # tests/ 被排除（那里有故意的假凭据）
    assert "--- tests/" not in text
    # 但源码被扫到了
    assert "--- sandbox/" in text or "--- scripts/" in text


def test_secret_scan_whole_tree_excludes_tests_fixtures():
    """整仓扫描不得被 tests/ 里的假凭据触发（否则 CI 永远红）。"""
    text = auto_dev._scan_tracked_files()
    import re
    assert not re.search(r"sk-cp-[a-zA-Z0-9_-]{20,}", text), "tests/ 的假凭据漏进整仓扫描了"
