"""V3.9 — 统一 .env 加载器（单一 loader + 单一白名单）。

## 为什么要重做

D-018 / F3（2026-10-22）已经因为"**两套独立 loader + 各自白名单**"漏过一次键：
`MINIMAX_IMAGE_*` 从未生效。本轮审计发现**同一类缺陷第三次复发**，而且这次
漏得更多：

- 代码里共读取 **44** 个 env key；
- `script_engine._DOTENV_KEYS` 白名单 14 个 + `local_client._DOTENV_KEYS` 10 个
  = **只有 24 个能从 .env 加载**；
- 剩下 **20 个**在 `.env` 里配了**永远不生效**，包括：
  - `MINIMAX_VIDEO_ENDPOINT` / `MINIMAX_VIDEO_QUERY_ENDPOINT` /
    `MINIMAX_FILE_RETRIEVE_ENDPOINT` / `MINIMAX_VIDEO_MODEL` /
    `MINIMAX_VIDEO_POLL_TIMEOUT` / `MINIMAX_VIDEO_POLL_INTERVAL` /
    `MINIMAX_VIDEO_RESOLUTION` / `MINIMAX_VIDEO_DURATION`
    → **Hailuo 视频端点无法配置**（想切国际域只能改代码常量）
  - `EDGE_TTS_VOICE` / `EDGE_TTS_RATE` / `EDGE_TTS_PITCH` / `EDGE_TTS_VOLUME`
    → **配音音色 / 语速配置失效**
  - `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET` / `YOUTUBE_REFRESH_TOKEN`
    → **YouTube 凭据从 .env 加载不了**：用户照 `.env.example` 填了，代码仍报
    "凭据缺失"（而 PRD 一直把它挂在"待用户提供凭据"上）
  - `CHARACTER_BIBLE_*` / `DAILY_*`（V3.8 / daily_cron）

实测（V3.9，`.env` 写入 4 个 key 后 import）：
`MINIMAX_VIDEO_ENDPOINT` / `EDGE_TTS_VOICE` / `YOUTUBE_CLIENT_ID` → **未加载**；
只有 `LOCAL_VIDEO_DEVICE` 生效（因为 `local_client.py` 有第二套 loader）。

## 设计

1. **单一 loader**（本模块）+ **单一白名单**（`LOADABLE_ENV_KEYS`）——
   `script_engine` 与 `local_client` 都改用它，删掉各自的副本。
2. **多路径合并**：所有候选路径都扫（路径优先级决定同 key 谁赢），
   不再"找到第一个文件就 return"（旧行为：cwd/.env 存在时，仓库根 .env 完全不看）。
3. **未知 key 告警**：`.env` 里有白名单外的 key → 告警（多半是拼错，
   例如 `MINIMAX_APIKEY`）。**只打印 key 名，绝不打印值**。
4. **漂移由测试守住**（`tests/test_dotenv.py`）：
   - `.env.example` 里声明的每个 key 都必须可加载
   - 代码里 `os.environ.get("X")` 的每个 X 都必须可加载（正则扫源码）
   - 白名单里的每个 key 都必须在 `.env.example` 里有说明（防"隐形 key"）

本模块**只依赖 stdlib**，且**不 import 本包内任何模块** —— 避免循环依赖
（`local_client` → `dotenv` → 无 → `script_engine` → `local_client`）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# ---------- 单一白名单 ----------
# 凡是代码里 os.environ.get / os.getenv / _env_str / _env_int 读到的 key，
# 都必须在这里 —— 测试会正则扫源码守住这条不变量。
LOADABLE_ENV_KEYS: frozenset[str] = frozenset({
    # —— MiniMax 文本（LLM）——
    "MINIMAX_API_KEY",
    "MINIMAX_BASE_URL",
    "MINIMAX_MODEL",
    # —— MiniMax 图像 ——
    "MINIMAX_IMAGE_MODEL",
    "MINIMAX_IMAGE_RESPONSE_FORMAT",
    "MINIMAX_IMAGE_ASPECT_RATIO",
    "MINIMAX_IMAGE_N",
    # —— MiniMax 视频（Hailuo）——
    "MINIMAX_VIDEO_ENDPOINT",
    "MINIMAX_VIDEO_QUERY_ENDPOINT",
    "MINIMAX_FILE_RETRIEVE_ENDPOINT",
    "MINIMAX_VIDEO_MODEL",
    "MINIMAX_VIDEO_POLL_TIMEOUT",
    "MINIMAX_VIDEO_POLL_INTERVAL",
    "MINIMAX_VIDEO_RESOLUTION",
    "MINIMAX_VIDEO_DURATION",
    # —— Google Gemini（Nano Banana 出图 / VLM 评审）——
    "GEMINI_API_KEY",
    "GEMINI_BASE_URL",
    "GEMINI_IMAGE_MODEL",
    "GEMINI_IMAGE_ASPECT_RATIO",
    "GEMINI_IMAGE_N",
    "GEMINI_VLM_MODEL",
    # —— Edge-TTS 配音 ——
    "EDGE_TTS_VOICE",
    "EDGE_TTS_RATE",
    "EDGE_TTS_PITCH",
    "EDGE_TTS_VOLUME",
    # —— YouTube 发布（REQ-401）——
    "YOUTUBE_CLIENT_ID",
    "YOUTUBE_CLIENT_SECRET",
    "YOUTUBE_REFRESH_TOKEN",
    # —— 本地视频推理（CogVideoX）——
    "LOCAL_VIDEO_MODEL",
    "LOCAL_VIDEO_DEVICE",
    "LOCAL_VIDEO_DTYPE",
    "LOCAL_VIDEO_NUM_FRAMES",
    "LOCAL_VIDEO_HEIGHT",
    "LOCAL_VIDEO_WIDTH",
    "LOCAL_VIDEO_STEPS",
    "LOCAL_VIDEO_GUIDANCE",
    "LOCAL_VIDEO_SEED",
    "LOCAL_VIDEO_CACHE_DIR",
    # —— 项目上下文（ADR-007）——
    "HAILUO_VIDEO_PROJECT_ID",
    # —— V3.8 全局角色圣经 ——
    "CHARACTER_BIBLE_DIR",
    "CHARACTER_BIBLE_DISABLE",
    # —— daily_cron 调度 ——
    "DAILY_STORYBOARD",
    "DAILY_MOTION_BUDGET",
})

# `.env.example` 里声明、但**本仓代码不读**的 key（外部工具用）。
# 允许存在，但会在漂移测试里被显式登记，避免"看起来该生效其实没人读"。
EXTERNAL_ENV_KEYS: frozenset[str] = frozenset({
    "AGENTORG_LLM_TIMEOUT",   # 外部 AgentOrg 工具消费
})

# 加载器自身的路径优先级（前者覆盖后者）
def candidate_paths() -> list[Path]:
    """候选 .env 路径（按优先级）。

    1. 当前工作目录 —— CLI 用户通常 cd 到项目根
    2. 仓库根（包在 sandbox/ 下，故往上两级）
    3. `~/ai_video/.env` —— 兜底
    """
    package_root = Path(__file__).resolve().parent          # sandbox/hailuo_video
    repo_root = package_root.parent.parent                   # 仓库根
    return [
        Path.cwd() / ".env",
        repo_root / ".env",
        Path.home() / "ai_video" / ".env",
    ]


def parse_env_file(path: Path | str) -> dict[str, str]:
    """解析 .env 文件 → {key: value}。文件不存在返回 {}。

    规则：空行与 `#` 开头跳过；按**第一个** `=` 切分；值去首尾空白与成对引号。
    """
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    out: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if not key:
            continue
        out[key] = val.strip().strip('"').strip("'")
    return out


def unknown_keys_in(path: Path | str) -> list[str]:
    """.env 里白名单外的 key 名（**不含值**）。多半是拼错。"""
    parsed = parse_env_file(path)
    return sorted(
        k for k in parsed
        if k not in LOADABLE_ENV_KEYS and k not in EXTERNAL_ENV_KEYS
    )


_warned_unknown: set[str] = set()


def load_dotenv(
    paths: list[Path | str] | None = None,
    *,
    warn_unknown: bool = True,
    override: bool = False,
) -> dict[str, str]:
    """把 .env 里的键载入 os.environ。返回本次实际载入的 {key: value}。

    - **不覆盖**已存在的进程环境变量（与既有语义一致：进程 env 优先级最高）。
      `override=True` 可翻转。
    - **合并**所有候选路径（路径优先级：列表靠前者赢），不再"找到第一个就 return"。
    - 白名单外的 key → 告警（只打 key 名，不打值）。
    """
    candidates = [Path(p) for p in paths] if paths is not None else candidate_paths()
    loaded: dict[str, str] = {}
    unknown: set[str] = set()

    for path in candidates:
        parsed = parse_env_file(path)
        for key, val in parsed.items():
            if key not in LOADABLE_ENV_KEYS:
                unknown.add(key)
                continue
            if key in os.environ and not override:
                continue
            os.environ[key] = val
            loaded[key] = val

    if warn_unknown and unknown:
        for key in sorted(unknown):
            if key in _warned_unknown:
                continue
            _warned_unknown.add(key)
            print(
                f"[dotenv] [WARN] .env 里的 {key!r} 不在可加载白名单中 —— "
                f"本仓代码不读它（多半是拼错了）。已忽略。",
                file=sys.stderr,
            )
    return loaded


__all__ = [
    "EXTERNAL_ENV_KEYS",
    "LOADABLE_ENV_KEYS",
    "candidate_paths",
    "load_dotenv",
    "parse_env_file",
    "unknown_keys_in",
]
