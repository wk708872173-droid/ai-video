"""python -m hailuo_video 入口。处理 sys.path 注入 + Windows UTF-8 输出。

⚠️ Windows 默认 cp932/cp936 在中文 console 打印 CJK 会 UnicodeEncodeError（L-009）。
sandbox/ 不走全局 pyproject，这里集中 shim 一次。
V2-Local：入口协议不变，CLI 内部从 HTTP 改为 diffusers 本地推理。
"""
from __future__ import annotations

import io
import sys
from pathlib import Path


def _force_utf8_stdio() -> None:
    """把 stdout / stderr 重新打包为 UTF-8。

    测试环境走 pytest capsys（in-memory UTF-8）不受影响；
    真实 Windows console（CJK 代码页）打印 / argparse help 都能通过。
    """
    if sys.platform != "win32":
        return
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None or not hasattr(stream, "buffer"):
            continue
        try:
            # Python 3.7+：TextIOWrapper.reconfigure
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            setattr(sys, name, io.TextIOWrapper(stream.buffer, encoding="utf-8"))


_force_utf8_stdio()

_root = Path(__file__).resolve().parent.parent  # sandbox/
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from hailuo_video.cli import main  # noqa: E402

sys.exit(main())