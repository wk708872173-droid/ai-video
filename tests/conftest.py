"""pytest 全局配置。

## 1. V3.8 W1：把全局角色圣经（`sandbox/assets/character_bible/`）在测试里**关掉**。

原因：角色圣经是跨项目共享的本地缓存，默认落在仓库内 `sandbox/assets/character_bible/`。
如果测试直接写那里，会：
  1. 污染工作树（.gitignore 排除了，但仍是脏目录）；
  2. 让测试之间互相影响 —— 一个测试写入的 reference 会被另一个测试命中
     （同 appearance → 同身份键），导致"应重新生成"这类断言随机失败。

因此这里在 session 级：
  - `CHARACTER_BIBLE_DIR` → 临时目录（万一有代码路径绕过 env 也不会写进仓库）；
  - `CHARACTER_BIBLE_DISABLE=1` → 关闭圣经（默认行为回到"纯项目隔离"）。

需要验证圣经本身的测试（tests/test_character_bible.py）自己 monkeypatch
`asset_prep._make_bible` 显式开启，不受这里影响。

## 2. V3.9：`isolated_env` fixture —— 防止 env 写入泄漏到其他测试。

背景（V3.9 实测）：pytest 的 `monkeypatch.delenv(key, raising=False)` 在 key
**不存在**时**不登记 undo 记录**（`delitem` 里 `if name not in dic: if raising:
raise` —— 不 raise 就直接返回，什么都不记）。于是：

```python
monkeypatch.delenv("MINIMAX_VIDEO_ENDPOINT", raising=False)   # key 原本不存在 → 无 undo 记录
load_dotenv([env_file])                                        # 写入 os.environ
# ← 测试结束，key 仍留在 os.environ 里，泄漏给后续测试
```

实测后果：`tests/test_dotenv.py` 写入的 `MINIMAX_VIDEO_ENDPOINT` 泄漏到
`tests/test_motion.py`，让后者拿到错误的端点 URL 而失败。

本 fixture 对 `os.environ` 做**完整快照 / 还原**，杜绝这类泄漏。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_BIBLE_DIR = Path(tempfile.mkdtemp(prefix="hailuo_bible_test_"))
os.environ["CHARACTER_BIBLE_DIR"] = str(_BIBLE_DIR)
os.environ["CHARACTER_BIBLE_DISABLE"] = "1"


@pytest.fixture
def isolated_env():
    """完整快照 / 还原 `os.environ`。

    与 `monkeypatch` 的区别：monkeypatch 只还原**它自己改过的键**，
    而 `monkeypatch.delenv(key, raising=False)` 对不存在的键不登记 undo
    → 后续被代码写入的键会泄漏。本 fixture 兜住所有情况。

    用法：
        def test_x(isolated_env):
            os.environ["FOO"] = "bar"
            ...
            # 测试结束后 os.environ 完整还原
    """
    saved = dict(os.environ)
    try:
        yield os.environ
    finally:
        os.environ.clear()
        os.environ.update(saved)
