"""V3.2 重构：quality 子模块拆分 — picker fallback 警告。

原 quality.py:313-325 抽到独立文件。
"""
from __future__ import annotations

from hailuo_video.quality.logger import get_qa_logger


def warn_picker_fallback(
    category: str,
    shot_index: int,
    requested: str,
    used: str,
) -> None:
    """picker 走 fallback 时显式 [WARN]。V2.6 quality 层要求：不要静默降级。"""
    if requested == used:
        return
    get_qa_logger().warning(
        f"[picker] shot {shot_index} {category}: "
        f"requested={requested!r}, fallback to {used!r}（picker 字典没匹配）"
    )
