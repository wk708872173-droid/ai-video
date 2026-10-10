"""V3.2 重构：editing 子模块拆分 — errors。

原 editing_engine.py:26-27 EditingError 抽到独立文件。
"""
from __future__ import annotations


class EditingError(RuntimeError):
    """editing 后处理调用 / 解析失败。"""
