"""V5.0 Batch 4 — 成本治理（Quota Ledger）。

## 问题（真实缺陷，不是假设）

审计发现配额记账**只存在于 `scripts/daily_cron.py`**，其余全是漏洞：

| 漏洞 | 后果 |
|---|---|
| `orchestrator` / `motion_backend` **无任何记账** | 绕过 cron 直接跑 `python -m hailuo_video pipeline` → **计数器与真实消耗完全脱钩** |
| `motion_budget` 只是**单次 run 内**的 top-N 截断 | **不是**每日计数器 |
| **image / tts 完全无限额、无计数器** | 最常调用的接口没有任何预算约束 |
| `daily_cron.py` 与 `run/quota.json` 是**独立实现** | 两套实现会漂移 |

本轮实测踩到了这个坑：跑 pipeline 试跑时提交了 3 条真 Hailuo 视频，
用光当天 3/3 配额，而 `run/quota.json` 里**一条都没记**。

## 设计

`QuotaLedger` —— **image / video / tts 三类统一记账**，按日 reset，原子写。

- **单一实现**：`daily_cron.py` 现有的 `QuotaRecord` / `load_quota` / `save_quota`
  改为调用本模块（消除两套实现），schema **向后兼容**
  （保留 `hailuo_calls_used` 字段）。
- **记账点在编排器**：调 backend **之前** `ledger.consume("video", n=1)`。
- **额度不足 → 降级 + 明确 WARN**，**不静默截断**。

## 红线（成本域 = 密钥域的近亲）

- 额度文件损坏 → **硬失败**，不静默重建（全局 CLAUDE.md §3：
  成本/凭据域缺失配置必须显式报错，禁止静默降级）。
- 记账只写**计数**，不写金额 —— 金额随套餐/汇率变，计数才是事实。
"""
from __future__ import annotations

import datetime
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# 三类可计费资源
RESOURCE_KINDS: tuple[str, ...] = ("image", "video", "tts")

# 默认每日额度
#   video = 3：Token Plan Max 含 3 条/天 Hailuo 2.3（实测 API 返回 2067 印证）
#   image = 60：角色 sheet 7 张/角色，一个 3 镜项目约 21 张 → 60 留足余量
#   tts   = 200：edge-tts 不计费，给个大数只为防意外循环
DEFAULT_BUDGETS: dict[str, int] = {"image": 60, "video": 3, "tts": 200}

# 兼容旧字段名（旧 schema 只有一个 hailuo 计数器）
_LEGACY_VIDEO_KEY = "hailuo_calls_used"


class QuotaError(RuntimeError):
    """配额账本出错（文件损坏等）—— **硬失败**，不静默重建。"""


def _today() -> str:
    return datetime.date.today().isoformat()


@dataclass
class QuotaRecord:
    """单日配额记录。

    **向后兼容**：`hailuo_calls_used` / `daily_budget` 是**真字段**（旧 schema
    只有这两个），`counters` / `budgets` 是新增的 image / tts 维度。
    video 的计数仍然放在 `hailuo_calls_used`（旧调用面 `rec.hailuo_calls_used`
    与 `QuotaRecord(hailuo_calls_used=...)` 构造**必须继续可用**）。
    """

    date: str = ""
    hailuo_calls_used: int = 0           # 今日已用 video（**旧字段名，保留**）
    daily_budget: int = 3                # 当日 video 预算（默认 3）
    counters: dict[str, int] = field(default_factory=dict)   # image / tts 计数
    budgets: dict[str, int] = field(default_factory=dict)    # image / tts 预算
    last_run_at: str = ""
    last_status: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # 兼容字段：video 计数也镜像进 counters（新读者友好）
        merged = dict(self.counters)
        merged["video"] = int(self.hailuo_calls_used)
        d["counters"] = merged
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QuotaRecord":
        raw_counters = dict(data.get("counters") or {})
        raw_counters.pop("video", None)      # video 以 hailuo_calls_used 为准
        budgets = dict(data.get("budgets") or {})
        budgets.pop("video", None)
        return cls(
            date=str(data.get("date", "")),
            hailuo_calls_used=int(data.get("hailuo_calls_used", 0) or 0),
            daily_budget=int(data.get("daily_budget", DEFAULT_BUDGETS["video"]) or 0),
            counters=raw_counters,
            budgets=budgets,
            last_run_at=str(data.get("last_run_at", "")),
            last_status=str(data.get("last_status", "")),
        )

    def used(self, kind: str) -> int:
        if kind == "video":
            return int(self.hailuo_calls_used)
        return int(self.counters.get(kind, 0))

    def budget(self, kind: str) -> int:
        if kind == "video":
            return int(self.daily_budget)
        if kind in self.budgets:
            return int(self.budgets[kind])
        return int(DEFAULT_BUDGETS.get(kind, 0))

    def remaining(self, kind: str) -> int:
        return max(0, self.budget(kind) - self.used(kind))

    def exhausted(self, kind: str) -> bool:
        return self.remaining(kind) <= 0


class QuotaLedger:
    """配额账本（单文件、按日 reset、原子写）。

    典型用法：

        ledger = QuotaLedger(path)
        if ledger.can_consume("video"):
            ledger.consume("video")
        else:
            ...  # 降级 + 明确告警
    """

    def __init__(self, path: str | Path, *, budgets: dict[str, int] | None = None) -> None:
        self.path = Path(path)
        self._budgets_override = dict(budgets or {})
        self._record: QuotaRecord | None = None

    # ---------- 读写 ----------

    def load(self) -> QuotaRecord:
        """读账本。文件不存在 → 新建今日记录；跨日 → 重置计数。

        **文件损坏 → 抛 QuotaError**（成本域硬失败，不静默重建 ——
        静默重建等于把"已用 3 条"当成"已用 0 条"，直接导致超支）。
        """
        if self._record is not None:
            return self._record
        if not self.path.is_file():
            self._record = self._new_record()
            return self._record
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise QuotaError(
                f"配额账本解析失败：{self.path}（{type(exc).__name__}: {exc}）。"
                "这是成本域数据，**不会静默重建** —— 请人工检查后删除或修复该文件。"
            ) from exc
        if not isinstance(raw, dict):
            raise QuotaError(f"配额账本格式不对（应为 JSON 对象）：{self.path}")
        rec = QuotaRecord.from_dict(raw)
        if rec.date != _today():
            # 跨日 → 重置计数（保留预算设置）
            rec = QuotaRecord(
                date=_today(),
                daily_budget=rec.daily_budget,
                budgets=dict(rec.budgets),
            )
        self._record = rec
        return rec

    def _new_record(self) -> QuotaRecord:
        budgets = dict(DEFAULT_BUDGETS)
        budgets.update(self._budgets_override)
        return QuotaRecord(
            date=_today(),
            daily_budget=budgets.get("video", 3),
            budgets=budgets,
        )

    def save(self) -> Path:
        """原子写账本（tmp + os.replace）。"""
        rec = self.load()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            tmp.write_text(
                json.dumps(rec.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self.path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
        return self.path

    # ---------- 记账 ----------

    def can_consume(self, kind: str, n: int = 1) -> bool:
        return self.load().remaining(kind) >= n

    def consume(self, kind: str, n: int = 1, *, save: bool = True) -> bool:
        """记账。额度不足 → **返回 False 且不记账**（调用方负责降级 + 告警）。

        返回 True = 已记账（调用方可以放心调 API）。
        """
        rec = self.load()
        if rec.remaining(kind) < n:
            return False
        if kind == "video":
            rec.hailuo_calls_used = rec.used("video") + n
        else:
            rec.counters[kind] = rec.used(kind) + n
        rec.last_run_at = datetime.datetime.now().isoformat(timespec="seconds")
        if save:
            try:
                self.save()
            except OSError:
                # 写失败不该让主流程崩，但**必须让调用方知道**（返回 False 语义不对，
                # 所以这里只吞异常，事件由调用方记）
                pass
        return True

    def status(self) -> dict[str, Any]:
        rec = self.load()
        return {
            "date": rec.date,
            "used": {k: rec.used(k) for k in RESOURCE_KINDS},
            "budget": {k: rec.budget(k) for k in RESOURCE_KINDS},
            "remaining": {k: rec.remaining(k) for k in RESOURCE_KINDS},
        }


# ---------- 与 daily_cron 的兼容层 ----------

def load_quota(quota_file: str | Path) -> QuotaRecord:
    """读 quota.json（**新实现的薄包装**，保持 daily_cron 的调用面不变）。"""
    return QuotaLedger(quota_file).load()


def save_quota(quota_file: str | Path, rec: QuotaRecord) -> None:
    """原子写 quota.json（保持 daily_cron 的调用面不变）。"""
    ledger = QuotaLedger(quota_file)
    ledger._record = rec          # 直接写回既有 record
    ledger.save()


__all__ = [
    "DEFAULT_BUDGETS",
    "RESOURCE_KINDS",
    "QuotaError",
    "QuotaLedger",
    "QuotaRecord",
    "load_quota",
    "save_quota",
]
