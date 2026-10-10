"""V3.2 重构：quality 子模块拆分 — _qa_logger 单例。

原 quality.py:31-38 抽到独立文件。
避免循环依赖：logger.py 不 import 其他 quality 子模块。
"""
from __future__ import annotations

import logging
import sys


# 默认 logger（CLI 复用）
_qa_logger = logging.getLogger("hailuo_video.quality")
if not _qa_logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    _qa_logger.addHandler(_handler)
    _qa_logger.setLevel(logging.INFO)
    _qa_logger.propagate = False


def get_qa_logger() -> logging.Logger:
    """暴露给其他 quality 子模块 + asset_prep 复用的 logger accessor。"""
    return _qa_logger
