"""本地视频推理状态持久化：最近一次 run 的状态 + 原子写。

不污染 src/agentorg/runtime/，文件落 sandbox/hailuo_video/.state.json。
R1：原子写（tempfile + os.replace），防止崩溃半写。

字段含义（V2-Local，替代旧 task_id 语义）：
  last_run_id       本次推理 UUID
  last_prompt       提示词原文
  last_output_path  落盘 mp4 路径
  last_status       "succeeded" | "failed" | ""
  last_model        使用的 HF model id
  last_device       cpu / cuda
  last_num_frames   帧数
  last_duration_seconds 推理耗时（秒）
  last_updated      ISO 时间戳（CST, +08:00）
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from hailuo_video.local_client import PACKAGE_ROOT

CST = timezone(timedelta(hours=8))
DEFAULT_STATE_PATH = PACKAGE_ROOT / ".state.json"


@dataclass
class State:
    last_run_id: str = ""
    last_prompt: str = ""
    last_output_path: str = ""
    last_status: str = ""            # "succeeded" | "failed" | ""
    last_model: str = ""
    last_device: str = ""
    last_num_frames: int = 0
    last_duration_seconds: float = 0.0
    last_updated: str = ""           # ISO timestamp

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "State":
        valid_keys = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in valid_keys})


def load_state(path: Path = DEFAULT_STATE_PATH) -> State:
    """读 state.json。文件不存在 / 解析失败 → 返回空 State（不抛）。"""
    if not path.is_file():
        return State()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return State()
    if not isinstance(data, dict):
        return State()
    return State.from_dict(data)


def _now_iso() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


def save_state(state: State, path: Path = DEFAULT_STATE_PATH) -> Path:
    """原子写：先写同目录 .tmp，再 os.replace。崩溃不会留半成品。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state.to_dict(), fh, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return path


def update_last(
    *,
    run_id: str = "",
    prompt: str = "",
    output_path: str = "",
    status: str = "",
    model: str = "",
    device: str = "",
    num_frames: int = 0,
    duration_seconds: float = 0.0,
    path: Path = DEFAULT_STATE_PATH,
) -> State:
    """覆盖式更新：传入的非空字段会被写入。返回更新后的 state。"""
    state = load_state(path)
    if run_id:
        state.last_run_id = run_id
    if prompt:
        state.last_prompt = prompt
    if output_path:
        state.last_output_path = output_path
    if status:
        state.last_status = status
    if model:
        state.last_model = model
    if device:
        state.last_device = device
    if num_frames > 0:
        state.last_num_frames = num_frames
    if duration_seconds > 0:
        state.last_duration_seconds = duration_seconds
    state.last_updated = _now_iso()
    save_state(state, path)
    return state