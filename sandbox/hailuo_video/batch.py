"""本地视频 Batch 编排：读 prompts.txt → 顺序 generate → 落 ./out/。

V2-Local：
- 每条 generate 走 local_client.generate（同步，CPU 上可能数十分钟）
- 单进程顺序跑；并发不在 V2 范围内（CPU 推理 OOM 风险 + 不明显提速）
- 文件命名 `<idx:04d>_<sha1[:8]>.mp4` 保留（与 V0.x 兼容）
- R1：每次 generate 已在 local_client 落 runs.jsonl，batch 不重复写
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from hailuo_video.local_client import VideoResult, generate as _client_generate


# ---------- 数据类 ----------

@dataclass
class BatchItem:
    index: int
    prompt: str
    output_path: Path
    result: VideoResult | None = None  # None = 跳过（已存在）


@dataclass
class BatchSummary:
    total: int = 0               # 实际评估数（= len(items)）；stop_on_failure 时 < 范围
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    failures: list[tuple[int, str, str]] = field(default_factory=list)  # (index, prompt, error)
    items: list[BatchItem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failed == 0

    def exit_code(self) -> int:
        """0 = 全成功或全跳过；1 = 有失败。"""
        if self.failed > 0:
            return 1
        return 0


# ---------- 文件加载 ----------

def load_prompts(path: str | Path) -> list[str]:
    """从 .txt 加载 prompts。
    规则：一行一个 prompt；# 开头视为注释；空行/纯空白跳过；首尾 strip。
    抛 FileNotFoundError 由调用方翻译为友好错误。
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"prompts 文件不存在: {path}")
    out: list[str] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.append(stripped)
    return out


def _hash_prompt(prompt: str) -> str:
    return hashlib.sha1(prompt.encode("utf-8")).hexdigest()


def _output_path(index: int, prompt: str, output_dir: Path) -> Path:
    return output_dir / f"{index:04d}_{_hash_prompt(prompt)[:8]}.mp4"


# ---------- 编排主入口 ----------

def run_batch(
    prompts: Sequence[str],
    output_dir: str | Path,
    *,
    skip_existing: bool = True,
    stop_on_failure: bool = False,
    start_from: int = 0,
    count: int | None = None,
    # 单条 generate 入参（透传给 local_client.generate）
    num_frames: int | None = None,
    height: int | None = None,
    width: int | None = None,
    steps: int | None = None,
    guidance: float | None = None,
    seed: int | None = None,
) -> BatchSummary:
    """跑一个 batch，返回 BatchSummary。

    V2 顺序跑；单条失败默认继续，stop_on_failure=True 立即停。
    不会抛异常（除了 FileNotFoundError、output_dir 创建失败这种调用方错）。
    """
    if not prompts:
        raise ValueError("prompts 列表为空，batch 无任务可跑")

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    start = max(0, start_from)
    end = len(prompts) if count is None else min(len(prompts), start + max(0, count))
    if start >= end:
        raise ValueError(f"start_from={start} >= 有效范围 ({end - start} 个 prompt)，无可跑任务")

    items: list[BatchItem] = []
    skipped_indices: set[int] = set()
    for idx in range(start, end):
        prompt = prompts[idx]
        out_path = _output_path(idx, prompt, out_dir)
        item = BatchItem(index=idx, prompt=prompt, output_path=out_path)
        items.append(item)
        if skip_existing and out_path.exists():
            skipped_indices.add(idx)

    summary = BatchSummary(skipped=len(skipped_indices))
    to_run = [i for i in range(len(items)) if i not in skipped_indices]

    gen_kwargs: dict[str, object] = {
        "num_frames": num_frames,
        "height_px": height,
        "width_px": width,
        "num_steps": steps,
        "guidance_scale": guidance,
        "seed_value": seed,
    }
    # 去掉 None，让 local_client 走 env/default
    gen_kwargs = {k: v for k, v in gen_kwargs.items() if v is not None}

    for i in to_run:
        item = items[i]
        try:
            result = _client_generate(item.prompt, item.output_path, **gen_kwargs)
        except Exception as exc:  # noqa: BLE001
            result = VideoResult(
                output_path=item.output_path,
                ok=False,
                source="error",
                error=f"{type(exc).__name__}: {exc}",
            )
        item.result = result
        _record_outcome(summary, item)
        if stop_on_failure and summary.failed > 0:
            break

    summary.items = items
    summary.total = sum(1 for item in items if item.result is not None)
    return summary


def _record_outcome(summary: BatchSummary, item: BatchItem) -> None:
    """累计 succeeded / failed / failures。"""
    r = item.result
    if r is None:
        return
    if r.ok:
        summary.succeeded += 1
    else:
        summary.failed += 1
        summary.failures.append((item.index, item.prompt, r.error))