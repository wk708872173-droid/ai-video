"""pytest 全局配置。

V3.8 W1：把全局角色圣经（`sandbox/assets/character_bible/`）在测试里**关掉**。

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
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

_BIBLE_DIR = Path(tempfile.mkdtemp(prefix="hailuo_bible_test_"))
os.environ["CHARACTER_BIBLE_DIR"] = str(_BIBLE_DIR)
os.environ["CHARACTER_BIBLE_DISABLE"] = "1"
