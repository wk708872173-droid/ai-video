"""V3.9 — .env 加载器的**漂移检测**（这类缺陷已复发三次）。

## 历史

同一类缺陷在仓库里复发了三次，每次都是"**白名单与代码/文档不同步**"：

1. **D-018**：`image_engine` 有自己的 `_DOTENV_KEYS`，但真正生效的是
   `script_engine` 那份 → `MINIMAX_IMAGE_*` 从未生效。
2. **F3（2026-10-22）**：把 image 的 4 个键补进 `script_engine` 白名单 ——
   但 video / tts / youtube 的键仍然漏着。
3. **V3.9（本次）**：审计发现代码共读 **44** 个 key，可加载的只有 **24** 个 →
   `MINIMAX_VIDEO_*` / `EDGE_TTS_*` / `YOUTUBE_*` 等 **20 个**在 .env 里
   配了永远不生效。

## 本文件的定位

**不测"某个键能加载"**（那是功能测试），而是测**不变量**：
只要代码里读了、或者 .env.example 里写了，就必须能被加载。
新增 env key 而忘了加白名单 → 本文件立刻变红。

R4：所有测试都不读仓库里的真实 `.env`（只读 `.env.example`，那是模板）。

注意：会写 `os.environ` 的用例一律带 `isolated_env` fixture —— pytest 的
`monkeypatch.delenv(key, raising=False)` 对**不存在的键不登记 undo 记录**，
loader 后续写入的值会泄漏给其他测试（V3.9 实测：泄漏到 test_motion）。
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SANDBOX = REPO / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from hailuo_video.dotenv import (  # noqa: E402
    EXTERNAL_ENV_KEYS,
    LOADABLE_ENV_KEYS,
    candidate_paths,
    load_dotenv,
    parse_env_file,
    unknown_keys_in,
)


# ---------- 源码扫描 ----------

# 代码里读 env 的各种写法
_READ_PATTERNS = (
    re.compile(r"""os\.environ\.get\(\s*["']([A-Z_0-9]+)["']"""),
    re.compile(r"""os\.getenv\(\s*["']([A-Z_0-9]+)["']"""),
    re.compile(r"""os\.environ\[\s*["']([A-Z_0-9]+)["']\s*\]"""),
    re.compile(r"""_env_str\(\s*["']([A-Z_0-9]+)["']"""),
    re.compile(r"""_env_int\(\s*["']([A-Z_0-9]+)["']"""),
)

# 扫描时跳过的文件（本仓自身的 loader / 测试）
_SKIP = {"dotenv.py", "test_dotenv.py"}


def _source_files() -> list[Path]:
    files: list[Path] = []
    for root in (SANDBOX / "hailuo_video", REPO / "scripts"):
        if not root.is_dir():
            continue
        for p in root.rglob("*.py"):
            if "__pycache__" in str(p):
                continue
            if p.name in _SKIP:
                continue
            files.append(p)
    return files


def _read_env_keys() -> dict[str, set[str]]:
    """扫源码，返回 {文件: 该文件读取的 env key 集合}。"""
    out: dict[str, set[str]] = {}
    for p in _source_files():
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        keys: set[str] = set()
        for pat in _READ_PATTERNS:
            keys.update(pat.findall(text))
        if keys:
            out[str(p.relative_to(REPO))] = keys
    return out


def _example_keys() -> set[str]:
    """.env.example 里声明的 key（注释掉的也算）。"""
    path = REPO / ".env.example"
    text = path.read_text(encoding="utf-8")
    keys: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            line = line.lstrip("#").strip()
        if not line or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if key and re.fullmatch(r"[A-Z_0-9]+", key):
            keys.add(key)
    return keys


# ============================================================
# 不变量 1：代码里读的每个 key 都必须可加载
# ============================================================

def test_every_env_key_read_by_code_is_loadable():
    """核心不变量：代码读的 key 必须在白名单里。

    漏一个 → 这就是"配了 .env 却不生效"的下一个实例。
    """
    missing: dict[str, set[str]] = {}
    for rel, keys in _read_env_keys().items():
        gap = keys - LOADABLE_ENV_KEYS
        if gap:
            missing[rel] = gap
    assert not missing, (
        "这些 env key 被代码读取，但不在 dotenv.LOADABLE_ENV_KEYS 白名单里"
        f"（配在 .env 里会永远不生效）：{missing}"
    )


def test_code_actually_reads_env_keys():
    """扫描器本身有效（防空跑：正则改了就不报警了）。"""
    scanned = _read_env_keys()
    assert scanned, "源码扫描没扫到任何 env 读取 —— 扫描正则失效了"
    total = sum(len(k) for k in scanned.values())
    assert total >= 30, f"只扫到 {total} 个 key，扫描范围可能过窄"


# ============================================================
# 不变量 2：.env.example 里声明的每个 key 都必须可加载（或登记为外部）
# ============================================================

def test_every_documented_key_is_loadable():
    """文档写了 = 用户会配 = 必须能加载。

    `AGENTORG_LLM_TIMEOUT` 这类外部工具的 key 登记在 EXTERNAL_ENV_KEYS。
    """
    example = _example_keys()
    undocumented = example - LOADABLE_ENV_KEYS - EXTERNAL_ENV_KEYS
    assert not undocumented, (
        f".env.example 里声明了这些 key，但既不在可加载白名单也没登记为外部 key："
        f"{sorted(undocumented)}"
    )


def test_loadable_keys_are_documented():
    """白名单里的每个 key 都必须在 .env.example 里有说明（防"隐形 key"）。"""
    example = _example_keys()
    hidden = LOADABLE_ENV_KEYS - example
    assert not hidden, f"这些 key 可加载但 .env.example 里没写：{sorted(hidden)}"


def test_external_keys_are_not_in_loadable_set():
    """外部 key 不该同时出现在可加载白名单里（否则会被误加载）。"""
    overlap = EXTERNAL_ENV_KEYS & LOADABLE_ENV_KEYS
    assert not overlap, f"既是外部 key 又可加载：{sorted(overlap)}"


# ============================================================
# 行为：加载 / 不覆盖 / 合并 / 未知 key 告警
# ============================================================

def test_load_dotenv_sets_key(tmp_path, monkeypatch, isolated_env):
    env_file = tmp_path / ".env"
    env_file.write_text("MINIMAX_VIDEO_ENDPOINT=https://example.test/v\n", encoding="utf-8")
    monkeypatch.delenv("MINIMAX_VIDEO_ENDPOINT", raising=False)

    loaded = load_dotenv([env_file], warn_unknown=False)
    assert loaded["MINIMAX_VIDEO_ENDPOINT"] == "https://example.test/v"
    assert os.environ["MINIMAX_VIDEO_ENDPOINT"] == "https://example.test/v"


def test_load_dotenv_covers_previously_broken_keys(tmp_path, monkeypatch, isolated_env):
    """回归：V3.9 之前这 4 类键**从不生效**。"""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "MINIMAX_VIDEO_ENDPOINT=https://x/v\n"
        "EDGE_TTS_VOICE=zh-CN-YunxiNeural\n"
        "YOUTUBE_CLIENT_ID=cid\n"
        "DAILY_MOTION_BUDGET=3\n",
        encoding="utf-8",
    )
    for k in ("MINIMAX_VIDEO_ENDPOINT", "EDGE_TTS_VOICE", "YOUTUBE_CLIENT_ID", "DAILY_MOTION_BUDGET"):
        monkeypatch.delenv(k, raising=False)

    load_dotenv([env_file], warn_unknown=False)
    assert os.environ["MINIMAX_VIDEO_ENDPOINT"] == "https://x/v"
    assert os.environ["EDGE_TTS_VOICE"] == "zh-CN-YunxiNeural"
    assert os.environ["YOUTUBE_CLIENT_ID"] == "cid"
    assert os.environ["DAILY_MOTION_BUDGET"] == "3"


def test_load_dotenv_does_not_override_process_env(tmp_path, monkeypatch, isolated_env):
    """进程 env 优先级最高（与既有语义一致）。"""
    env_file = tmp_path / ".env"
    env_file.write_text("MINIMAX_MODEL=from-file\n", encoding="utf-8")
    monkeypatch.setenv("MINIMAX_MODEL", "from-process")

    load_dotenv([env_file], warn_unknown=False)
    assert os.environ["MINIMAX_MODEL"] == "from-process"


def test_load_dotenv_merges_multiple_paths(tmp_path, monkeypatch, isolated_env):
    """多路径**合并**（旧行为：找到第一个文件就 return，后面的完全不看）。"""
    a = tmp_path / "a.env"
    a.write_text("MINIMAX_MODEL=from-a\n", encoding="utf-8")
    b = tmp_path / "b.env"
    b.write_text("MINIMAX_IMAGE_MODEL=from-b\n", encoding="utf-8")
    monkeypatch.delenv("MINIMAX_MODEL", raising=False)
    monkeypatch.delenv("MINIMAX_IMAGE_MODEL", raising=False)

    load_dotenv([a, b], warn_unknown=False)
    assert os.environ["MINIMAX_MODEL"] == "from-a"
    assert os.environ["MINIMAX_IMAGE_MODEL"] == "from-b"


def test_load_dotenv_path_priority(tmp_path, monkeypatch, isolated_env):
    """列表靠前的路径赢（同 key）。"""
    a = tmp_path / "a.env"
    a.write_text("MINIMAX_MODEL=from-a\n", encoding="utf-8")
    b = tmp_path / "b.env"
    b.write_text("MINIMAX_MODEL=from-b\n", encoding="utf-8")
    monkeypatch.delenv("MINIMAX_MODEL", raising=False)

    load_dotenv([a, b], warn_unknown=False)
    assert os.environ["MINIMAX_MODEL"] == "from-a"


def test_unknown_key_is_warned(tmp_path, monkeypatch, capsys, isolated_env):
    """白名单外的 key → 告警（多半是拼错），且**只打 key 名不打值**。"""
    env_file = tmp_path / ".env"
    env_file.write_text("MINIMAX_APIKEY=super-secret-value\n", encoding="utf-8")

    load_dotenv([env_file], warn_unknown=True)
    err = capsys.readouterr().err
    assert "MINIMAX_APIKEY" in err
    assert "super-secret-value" not in err, "告警里绝不能出现 .env 的值"


def test_unknown_keys_in_lists_typos(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("MINIMAX_APIKEY=x\nMINIMAX_API_KEY=y\n", encoding="utf-8")
    unknown = unknown_keys_in(env_file)
    assert unknown == ["MINIMAX_APIKEY"]
    assert "MINIMAX_API_KEY" not in unknown


def test_parse_env_file_handles_quotes_and_comments(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# 注释\n"
        "\n"
        'A="quoted"\n'
        "B='single'\n"
        "C=plain  # 行尾注释不剥离（保持朴素解析）\n",
        encoding="utf-8",
    )
    parsed = parse_env_file(env_file)
    assert parsed["A"] == "quoted"
    assert parsed["B"] == "single"
    assert parsed["C"].startswith("plain")


def test_parse_env_file_missing_returns_empty(tmp_path):
    assert parse_env_file(tmp_path / "nope.env") == {}


# ============================================================
# 单一 loader：不再有第二份实现
# ============================================================

def test_only_one_dotenv_loader_implementation():
    """全仓只应有一个 .env 加载实现（两份白名单 = 漏键的根源）。"""
    hits = []
    for p in _source_files():
        text = p.read_text(encoding="utf-8")
        if "def _load_dotenv" in text and p.name != "dotenv.py":
            hits.append(str(p.relative_to(REPO)))
    assert len(hits) <= 2, (
        f"发现多份 _load_dotenv 实现（每份都带自己的白名单 → 必然漏键）：{hits}"
    )


def test_script_engine_and_local_client_delegate_to_shared_loader():
    """两处薄封装都必须委托给 hailuo_video.dotenv.load_dotenv。"""
    import hailuo_video.local_client as lc
    import hailuo_video.script_engine as se

    for mod in (se, lc):
        src = inspect_source(mod)
        assert "hailuo_video.dotenv" in src, f"{mod.__name__}._load_dotenv 没委托给共享 loader"


def inspect_source(mod) -> str:
    import inspect
    return inspect.getsource(mod._load_dotenv)


def test_candidate_paths_include_repo_root():
    paths = [str(p) for p in candidate_paths()]
    assert any(p.endswith(".env") for p in paths)
    assert len(paths) >= 2


def test_script_engine_dotenv_keys_alias_matches_shared_whitelist():
    """兼容别名 `_DOTENV_KEYS` 必须与共享白名单一致（否则又是一次分叉）。"""
    import hailuo_video.script_engine as se

    assert set(se._DOTENV_KEYS) == set(LOADABLE_ENV_KEYS)
