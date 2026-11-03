"""V3.2 REQ-305 每日调度层 — `python scripts/daily_cron.py [--once]`。

设计：
  - 读 run/quota.json：今日已用 Hailuo 调用次数
  - 若剩余 > 0：跑 `python -m hailuo_video pipeline storyboard.json --motion-budget 1`
  - 若配额用尽：写 [WARN] 到 logs/daily_report.md 并跳过
  - 每次跑完更新 quota.json（追加一次调用）
  - 失败抛硬错（CLAUDE.md R4：不静默降级）；脚本退出码 ≠ 0

CLI:
  --once                 — 单次跑（默认；用于手动触发 / 单测）
  --motion-budget N     — 覆盖每日预算（默认读 env: DAILY_MOTION_BUDGET 或硬编码 3）
  --storyboard PATH     — 覆盖 storyboard 路径（默认 env: DAILY_STORYBOARD 或 storyboard_template.json）
  --quota-file PATH     — 覆盖 quota 文件位置（默认 run/quota.json）
  --report-file PATH    — 覆盖日报位置（默认 logs/daily_report.md）
  --pipeline-cmd STR    — 覆盖 pipeline 命令（默认 `python -m hailuo_video pipeline`）

退出码：
  0 — 成功（pipeline 跑了或配额用尽正常跳过）
  1 — quota 校验错 / pipeline 命令错
  2 — pipeline 调用本身返回非 0
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# V5.0 Batch 4：配额账本收敛到**单一实现**（`sandbox/hailuo_video/quota.py`）。
#   此前本文件自带一份 QuotaRecord / load_quota / save_quota ——
#   与编排器侧的记账是两套实现，会漂移（cron 记了、手动跑没记）。
#   现在两边共用一份账本：`run/quota.json`。
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sandbox"))
from hailuo_video.quota import (  # noqa: E402
    QuotaError,
    QuotaRecord,
    save_quota,
)
from hailuo_video.quota import load_quota as _load_quota_raw  # noqa: E402


# ---------- Errors / Data ----------

class DailyCronError(RuntimeError):
    """daily_cron 调度失败。"""


@dataclass
class DailyReport:
    """logs/daily_report.md 写入内容。"""
    date: str = ""
    budget_total: int = 3
    budget_used_before: int = 0
    budget_used_after: int = 0
    pipeline_ran: bool = False
    pipeline_cmd: str = ""
    pipeline_exit_code: int = -1
    pipeline_stdout_tail: str = ""
    pipeline_stderr_tail: str = ""
    quota_status: str = ""
    timestamp: str = ""
    notes: list[str] = field(default_factory=list)

    def to_markdown(self) -> str:
        lines: list[str] = []
        lines.append(f"# Daily Pipeline Report — {self.date}")
        lines.append("")
        lines.append(f"- **Generated**: {self.timestamp}")
        lines.append(f"- **Budget**: {self.budget_used_after}/{self.budget_total} hailuo calls used today")
        lines.append(f"- **Pipeline ran**: {'yes' if self.pipeline_ran else 'no'}")
        lines.append(f"- **Quota status**: {self.quota_status}")
        lines.append(f"- **Pipeline exit code**: {self.pipeline_exit_code}")
        if self.pipeline_cmd:
            lines.append(f"- **Command**: `{self.pipeline_cmd}`")
        lines.append("")
        if self.pipeline_stdout_tail:
            lines.append("## Pipeline stdout (tail)")
            lines.append("```")
            lines.append(self.pipeline_stdout_tail[-1000:])
            lines.append("```")
            lines.append("")
        if self.pipeline_stderr_tail:
            lines.append("## Pipeline stderr (tail)")
            lines.append("```")
            lines.append(self.pipeline_stderr_tail[-1000:])
            lines.append("```")
            lines.append("")
        if self.notes:
            lines.append("## Notes")
            for n in self.notes:
                lines.append(f"- {n}")
            lines.append("")
        return "\n".join(lines)


# ---------- 配额管理 ----------

def _today() -> str:
    """YYYY-MM-DD 本地日期。"""
    return dt.date.today().isoformat()


def quota_remaining(rec: QuotaRecord) -> int:
    """剩余 video 额度（新账本 API；旧字段名仍兼容）。"""
    return rec.remaining("video")


def load_quota(quota_file: str | Path) -> QuotaRecord:
    """读 quota.json；损坏 → DailyCronError（**保持本脚本原有的错误契约**）。

    V5.0 Batch 4：账本实现搬到 `hailuo_video.quota`，错误类型变成 `QuotaError`。
    这里转一层，让调用方（`main` / 单测）的 except 分支不用改。
    """
    try:
        return _load_quota_raw(quota_file)
    except QuotaError as exc:
        raise DailyCronError(f"quota.json 解析失败：{exc}") from exc


# ---------- 日报 ----------

def write_report(report_file: str | Path, report: DailyReport) -> None:
    """追加写日报（不覆盖旧日报）。"""
    p = Path(report_file)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(report.to_markdown())
        f.write("\n\n---\n\n")


# ---------- Pipeline 调用（可注入 mock） ----------

RunPipelineFn = "callable[[list[str]], tuple[int, str, str]]"


def _default_run_pipeline(cmd: list[str]) -> tuple[int, str, str]:
    """真跑 subprocess.run。"""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DailyCronError(f"pipeline 子进程失败：{type(exc).__name__}: {exc}") from exc
    return proc.returncode, proc.stdout, proc.stderr


_runner = _default_run_pipeline


def set_runner(fn) -> None:
    """注入 / 还原 pipeline runner（单测）。"""
    global _runner
    _runner = fn if fn is not None else _default_run_pipeline


# ---------- 主调度 ----------

def run_once(
    *,
    motion_budget: int,
    storyboard: str | Path,
    quota_file: str | Path,
    report_file: str | Path,
    pipeline_cmd: list[str],
) -> DailyReport:
    """单次调度：检查配额 → 跑 pipeline（若无 budget 则跳过）→ 更新 quota → 写日报。

    失败抛 DailyCronError。
    """
    rec = load_quota(quota_file)
    remaining = quota_remaining(rec)
    today = _today()
    now_iso = dt.datetime.now().isoformat(timespec="seconds")
    report = DailyReport(
        date=today,
        budget_total=rec.daily_budget,
        budget_used_before=rec.hailuo_calls_used,
        timestamp=now_iso,
        pipeline_cmd=" ".join(pipeline_cmd + [str(storyboard)]),
    )

    if rec.daily_budget != motion_budget:
        # 用户临时覆盖 budget → 更新记录但不修改既有计数
        rec.daily_budget = motion_budget
        remaining = quota_remaining(rec)
        report.notes.append(f"daily_budget 临时覆盖为 {motion_budget}")

    if remaining <= 0:
        report.pipeline_ran = False
        report.quota_status = "exhausted"
        report.notes.append(f"[WARN] 今日预算 {rec.daily_budget} 次已用尽（Hailuo Max 套餐限制）；跳过 pipeline")
        rec.last_status = "skipped_quota"
        rec.last_run_at = now_iso
        save_quota(quota_file, rec)
        write_report(report_file, report)
        return report

    # 配额允许 → 跑 pipeline
    # V5.0 Batch 3：显式 `--checkpoint none` —— 无人值守生产**不能**停在
    #   等人确认上（客户第 1 优先级）。CLI 的默认值是 prep（交互式更安全），
    #   定时任务必须显式关掉，否则 cron 会"跑完 prep 就停"——
    #   看起来成功（exit 3）但没有成片，属于典型的"看起来成功实际失败"。
    cmd = pipeline_cmd + [
        str(storyboard), "--motion-budget", "1", "--checkpoint", "none",
    ]   # 单次 ≤ 1（避免一次跑光预算）
    try:
        rc, out, err = _runner(cmd)
    except DailyCronError:
        raise
    report.pipeline_ran = True
    report.pipeline_exit_code = rc
    report.pipeline_stdout_tail = out or ""
    report.pipeline_stderr_tail = err or ""

    if rc != 0:
        # pipeline 失败：不计数（防止 quota 被"扣掉"但产物不存在）
        report.quota_status = "pipeline_failed"
        report.notes.append(f"[FAIL] pipeline exit code={rc}：{err[-200:] if err else ''}")
        rec.last_status = "fail"
        rec.last_run_at = now_iso
        save_quota(quota_file, rec)
        write_report(report_file, report)
        raise DailyCronError(f"pipeline 失败 rc={rc}")

    # pipeline 成功 → 记账。
    #   V5.0 Batch 4：编排器现在**按实际调用次数**记账（每条真 Hailuo 视频记 1），
    #   cron 若再无条件 +1 就是**重复计数**（跑一次算 2）→ 额度提前耗尽。
    #   所以 cron 变成**兜底记账者**：
    #     - 编排器已经记了（账本变了）→ 不重复记
    #     - 编排器没记（本次没生成真视频 / 用了别的 quota 文件）→ cron 兜底 +1
    #   这保持了旧语义（"跑一次 pipeline 记一次"），又消除了双重计数。
    after = load_quota(quota_file)
    # 保留本次的临时 budget 覆盖（它只在内存里，账本文件里还是旧值）
    after.daily_budget = rec.daily_budget
    if after.hailuo_calls_used <= rec.hailuo_calls_used:
        after.hailuo_calls_used = rec.hailuo_calls_used + 1
    rec = after
    rec.last_status = "ok"
    rec.last_run_at = now_iso
    save_quota(quota_file, rec)
    report.budget_used_after = rec.hailuo_calls_used
    report.quota_status = "ok"
    report.notes.append(f"[OK] pipeline 成功；今日剩余 {quota_remaining(rec)} 次")
    write_report(report_file, report)
    return report


# ---------- CLI ----------

def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="daily_cron",
        description="V3.2 REQ-305 每日调度层（配额保护 + 日报）",
    )
    # 注：原 --once store_true 与"action=append"在 Python 3.14 argparse 下会触发
    # 'expected one argument' 的回归 bug（store_true 抢占了 append 的下一个 token）。
    # 简化为：脚本永远是"单次跑"模式（保留扩展为 --loop 周期模式的注释空间）
    # pipeline-cmd 也改用空格分隔的字符串（同样绕开 3.14 argparse 的 action=append 回归）
    p.add_argument("--motion-budget", type=int,
                   default=int(os.environ.get("DAILY_MOTION_BUDGET", "3")),
                   help="每日 Hailuo 预算（默认 env DAILY_MOTION_BUDGET 或 3）")
    p.add_argument("--storyboard", default=os.environ.get("DAILY_STORYBOARD", "storyboard_template.json"),
                   help="storyboard 路径（默认 env DAILY_STORYBOARD 或 storyboard_template.json）")
    p.add_argument("--quota-file", default="run/quota.json",
                   help="配额文件位置（默认 run/quota.json）")
    p.add_argument("--report-file", default="logs/daily_report.md",
                   help="日报文件位置（默认 logs/daily_report.md）")
    p.add_argument("--pipeline-cmd", default="",
                   help="pipeline 命令（空格分隔；默认 `python -m hailuo_video pipeline`）")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)
    cmd = args.pipeline_cmd.split() if args.pipeline_cmd else ["python", "-m", "hailuo_video", "pipeline"]
    try:
        report = run_once(
            motion_budget=args.motion_budget,
            storyboard=args.storyboard,
            quota_file=args.quota_file,
            report_file=args.report_file,
            pipeline_cmd=cmd,
        )
    except DailyCronError as exc:
        print(f"[FAIL] daily_cron：{exc}", file=sys.stderr)
        return 1

    status = "ran" if report.pipeline_ran else "skipped"
    print(f"[OK] daily_cron：{status}（budget {report.budget_used_after}/{report.budget_total}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())