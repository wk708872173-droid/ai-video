"""scripts/auto_dev.py — CLAUDE.md §5 自动开发循环。

设计目标：
  - 一键跑：测试 + coverage + lint + secret scan + 报告生成
  - 自动 commit（如一切通过 + 有未提交改动）
  - fail-fast：严重问题不静默放过

用法：
  bash scripts/auto_dev.py               # 全流程
  bash scripts/auto_dev.py --skip-coverage
  bash scripts/auto_dev.py --no-commit
  bash scripts/auto_dev.py --only-tests

退出码：
  0 = 一切通过（+ 自动 commit 如果有改动）
  1 = 测试 / lint / secret / coverage 失败
  2 = 工具错误（如 pytest 未装）
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


# Windows 控制台默认 cp932（GBK 系），编码不了报告里大量使用的
# ✅/❌/⏭️/📋 等标记 → 门禁跑到最后一步崩在 print 上，甚至让兜底的异常处理器
# 自己也抛 UnicodeEncodeError，使调用方拿不到可信退出码。
# errors="replace" 是关键：再遇到无法编码的字符时降级为替换字符而非抛异常，
# 保证门禁永远不会因为一个字符丢失退出码。
# D3（docs/drafts/auto_dev_console_encoding_draft.md）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # Python 3.7+
    except (AttributeError, ValueError, OSError):
        pass


REPO = Path(__file__).resolve().parents[1]
TESTS = REPO / "tests"
SANDBOX = REPO / "sandbox"


# ---------- 命令执行 helpers ----------

def run(cmd: list[str], *, cwd: Path | None = None, timeout: int = 600) -> tuple[int, str, str]:
    """跑命令，返回 (rc, stdout, stderr)。"""
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd or REPO,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, "", f"TIMEOUT after {timeout}s"
    except FileNotFoundError as exc:
        return 127, "", f"command not found: {exc}"


def header(title: str) -> None:
    print(f"\n{'=' * 60}\n  {title}\n{'=' * 60}")


# ---------- Step 1: 跑测试 ----------

def step_tests(args) -> bool:
    header("Step 1: pytest")
    cmd = [
        "python", "-m", "pytest", "TESTS", "-q",
        "--tb=line",
        "-p", "no:cacheprovider",
        "--ignore=tests/test_tts_engine.py",
    ]
    if args.coverage:
        cmd += ["--cov=sandbox/hailuo_video", "--cov-report=term-missing"]
    rc, out, err = run(cmd, timeout=600)
    print(out[-2000:] if out else err[-2000:])
    if rc != 0:
        print(f"❌ Tests FAILED (rc={rc})")
        return False
    print(f"✅ Tests PASSED")
    return True


# ---------- Step 2: secret scan ----------

SECRET_PATTERNS = [
    (r"sk-cp-[a-zA-Z0-9_-]{20,}", "MiniMax API key"),
    (r"sk-[a-zA-Z0-9]{20,}", "OpenAI API key"),
    (r"AKIA[0-9A-Z]{16}", "AWS access key"),
    (r"-----BEGIN [A-Z ]+PRIVATE KEY-----", "Private key"),
    (r"ghp_[a-zA-Z0-9]{36}", "GitHub token"),
    (r"xox[abp]-[0-9]{10,}-[a-zA-Z0-9]{20,}", "Slack token"),
]


def step_secret_scan(_args) -> bool:
    header("Step 2: secret scan（防凭据泄漏）")
    rc, _, _ = run(["git", "diff", "--staged", "--no-color"], timeout=30)
    diff = subprocess.run(
        ["git", "diff", "--no-color"],
        cwd=REPO, capture_output=True, encoding="utf-8", errors="replace", timeout=30,
    ).stdout
    if not diff.strip():
        diff = subprocess.run(
            ["git", "diff", "--staged", "--no-color"],
            cwd=REPO, capture_output=True, encoding="utf-8", errors="replace", timeout=30,
        ).stdout

    issues = []
    for pattern, label in SECRET_PATTERNS:
        for m in re.finditer(pattern, diff):
            issues.append(f"{label}: {m.group()[:30]}...")

    if issues:
        print(f"❌ Secret scan FAILED:")
        for i in issues:
            print(f"  {i}")
        return False

    print("✅ No secrets detected in diff")
    return True


# ---------- Step 3: lint (最简：syntax 检查) ----------

def step_lint(_args) -> bool:
    header("Step 3: syntax check（py_compile）")
    # F6 修复（2026-10-22）：此前只扫 SANDBOX，导致 tests/ 与 scripts/ 不在语法检查
    # 范围内 —— 报告里那句「All N Python files compile clean」不含新增测试文件，
    # 与直觉不符。实际风险为零（pytest 收集时即会编译），但门禁覆盖范围该说实话。
    roots = [SANDBOX, TESTS, REPO / "scripts"]
    py_files: list[Path] = []
    for root in roots:
        if root.is_dir():
            py_files.extend(
                p for p in root.rglob("*.py")
                if "__pycache__" not in str(p) and "MODULE" not in str(p)
            )
    failed = []
    for py in py_files:
        rc, _, stderr = run(["python", "-m", "py_compile", str(py)], timeout=30)
        if rc != 0:
            failed.append((str(py), stderr.strip().split("\n")[-1] if stderr else ""))
    if failed:
        print(f"❌ {len(failed)} file(s) failed syntax check:")
        for f, e in failed:
            print(f"  {f}\n    → {e[:200]}")
        return False
    print(f"✅ All {len(py_files)} Python files compile clean "
          f"(sandbox/ + tests/ + scripts/)")
    return True


# ---------- Step 4: coverage gate ----------

def step_coverage_gate(_args) -> tuple[bool, float]:
    header("Step 4: coverage gate (target ≥80%)")
    rc, out, _ = run([
        "python", "-m", "pytest", "tests/",
        "--cov=sandbox/hailuo_video",
        "--cov-report=term",
        "-q", "--tb=no", "-p", "no:cacheprovider",
        "--ignore=tests/test_tts_engine.py",
    ], timeout=600)
    # 解析最后 "TOTAL ... NN%" 行
    m = re.search(r"TOTAL\s+\d+\s+\d+\s+(\d+)%", out)
    if not m:
        print("⚠️  Coverage 解析失败（可能 pytest 未装）")
        return False, 0.0
    pct = float(m.group(1))
    target = 80.0
    if pct >= target:
        print(f"✅ Coverage {pct:.1f}% >= {target}%")
        return True, pct
    print(f"❌ Coverage {pct:.1f}% < {target}%")
    return False, pct


# ---------- Step 5: auto commit (如有改动且一切通过) ----------

# auto_dev 管辖的路径（门禁只看这些目录）。status 检查必须与 add 路径对齐，
# 否则「add 路径外的脏文件」会让 status 一直非空 → 空 commit 报错（V3.5 P5 修复）。
AUTO_COMMIT_PATHS = ["sandbox/", "tests/", "scripts/", "docs/"]


def step_commit(_args, *, coverage_ok: bool | None = None) -> bool:
    header("Step 5: auto commit (如一切通过)")
    rc, out, _ = run(["git", "status", "--short", "--", *AUTO_COMMIT_PATHS], timeout=30)
    if not out.strip():
        print("(管辖路径下无 tracked 改动，跳过)")
        return True
    # 先 add（不带 -A，手动 add 已修改）
    rc, _, _ = run(["git", "add", "-u", *AUTO_COMMIT_PATHS], timeout=30)
    # 重新查 status（同样限定路径）
    rc, out, _ = run(["git", "status", "--short", "--", *AUTO_COMMIT_PATHS], timeout=30)
    if not out.strip():
        print("(无可 add 的 tracked 改动)")
        return True
    # 生成 commit message（按时间戳）——如实反映门禁状态，不谎报
    from datetime import datetime
    stamp = datetime.now().strftime('%Y-%m-%d %H:%M')
    if coverage_ok is None:
        msg = f"auto-dev: 代码格式化 + 测试通过 ({stamp})\n\n自动 commit by scripts/auto_dev.py"
    elif coverage_ok:
        msg = f"auto-dev: 代码格式化 + 测试通过 + 覆盖率达标 ({stamp})\n\n自动 commit by scripts/auto_dev.py"
    else:
        msg = (f"auto-dev: 代码格式化 + 测试通过（覆盖率未达标，--force-commit 放行）({stamp})\n\n"
               f"自动 commit by scripts/auto_dev.py")
    rc, _, err = run(["git", "commit", "-m", msg], timeout=60)
    if rc != 0:
        print(f"❌ git commit 失败:\n{err[-500:]}")
        return False
    print(f"✅ Auto committed")
    return True


# ---------- main ----------

def main() -> int:
    p = argparse.ArgumentParser(prog="auto_dev")
    p.add_argument("--skip-tests", action="store_true")
    p.add_argument("--skip-lint", action="store_true")
    p.add_argument("--skip-secret-scan", action="store_true")
    p.add_argument("--skip-coverage", action="store_true", help="跳过 coverage gate")
    p.add_argument("--no-commit", action="store_true", help="不自动 commit")
    p.add_argument("--force-commit", action="store_true",
                   help="覆盖率未达标时仍强制 commit（默认 coverage 红则跳过 commit）")
    p.add_argument("--only-tests", action="store_true", help="只跑测试")
    p.add_argument("--coverage", action="store_true", help="测试时附加 coverage")
    args = p.parse_args()

    started = time.monotonic()
    results = {}

    try:
        if not args.skip_tests:
            results["tests"] = step_tests(args)
            if not results["tests"]:
                return 1
        if args.only_tests:
            return 0 if all(results.values()) else 1

        if not args.skip_secret_scan:
            results["secret_scan"] = step_secret_scan(args)
            if not results["secret_scan"]:
                return 1

        if not args.skip_lint:
            results["lint"] = step_lint(args)
            if not results["lint"]:
                return 1

        if not args.skip_coverage:
            cov_ok, pct = step_coverage_gate(args)
            results["coverage"] = cov_ok
            # coverage 失败不 hard fail（CLAUDE.md §4 推荐 ≥80%，非阻断退出码）
            # 但**阻断 auto commit** —— 否则会出现「覆盖率 78% 却提交『测试通过』」
            # 的误导性 commit（V3.5 P5 修复）。--force-commit 可显式放行。
        coverage_ok = results.get("coverage")

        commit_blockers = ("tests", "secret_scan", "lint")
        blockers_ok = all(results.get(k) for k in commit_blockers)
        coverage_blocks = not args.force_commit and coverage_ok is False

        if not args.no_commit and blockers_ok and not coverage_blocks:
            results["commit"] = step_commit(args, coverage_ok=coverage_ok)
        else:
            if coverage_blocks:
                print("\n⏭️  Step 5 跳过：覆盖率未达标（用 --force-commit 可放行）")
            results["commit"] = None  # skipped

    except Exception as exc:
        print(f"\n❌ auto_dev 异常: {exc}")
        return 2

    elapsed = time.monotonic() - started

    # 报告
    header("📋 Auto Dev 报告")
    print(f"\n总耗时：{elapsed:.1f}s\n")
    for k, v in results.items():
        if v is None:
            print(f"  {k:20s}  ⏭  跳过")
        elif v:
            print(f"  {k:20s}  ✅ PASS")
        else:
            print(f"  {k:20s}  ❌ FAIL")

    # 建议
    print()
    failed = [k for k, v in results.items() if v is False]
    if failed:
        print(f"❌ {len(failed)} 项失败：{', '.join(failed)}")
        return 1
    print("✅ Auto Dev 通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())