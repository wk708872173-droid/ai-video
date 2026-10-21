"""本地视频 batch 产出 manifest：CSV 格式汇总每次 generate 的实际结果。

V2-Local：每列独立写入，UTF-8 with newline=""（Windows 友好）。
列变化：去掉 task_id（无远程任务），加 model / device / num_frames。
"""
from __future__ import annotations

import csv
from pathlib import Path

from hailuo_video.batch import BatchItem, BatchSummary


MANIFEST_HEADER = [
    "index",
    "prompt",
    "status",              # skipped | succeeded | failed
    "output_path",
    "bytes",
    "duration_seconds",
    "model",
    "device",
    "num_frames",
    "source",              # ok | error | disabled
    "error",
]


def _item_status(item: BatchItem) -> str:
    if item.result is None:
        return "skipped"
    return "succeeded" if item.result.ok else "failed"


def _item_bytes(item: BatchItem) -> int:
    if not item.output_path.exists():
        return 0
    try:
        return item.output_path.stat().st_size
    except OSError:
        return 0


def write_manifest(summary: BatchSummary, path: str | Path) -> Path:
    """把 BatchSummary 写到 manifest.csv。返回写入的 Path。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(MANIFEST_HEADER)
        for item in summary.items:
            r = item.result
            w.writerow([
                item.index,
                item.prompt,
                _item_status(item),
                str(item.output_path),
                _item_bytes(item),
                getattr(r, "duration_seconds", 0.0) if r else 0.0,
                getattr(r, "model", "") if r else "",
                getattr(r, "device", "") if r else "",
                getattr(r, "num_frames", 0) if r else 0,
                getattr(r, "source", "") if r else "",
                getattr(r, "error", "") if r else "",
            ])
    return target