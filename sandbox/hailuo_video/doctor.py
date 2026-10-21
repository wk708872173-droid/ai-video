"""本地视频 doctor：诊断环境 + 配置是否就绪，不消耗资源。

V2-Local 替换 V0.5 的远端 endpoint 探测，改检查：
  - torch / diffusers 是否装好
  - device=cuda 时 CUDA 是否可用
  - dtype 与 device 兼容性
  - HF 模型权重是否已缓存
  - cache_dir 所在磁盘空间

原则：
  - 不烧资源（不真 import 大模型、不下载权重）
  - 不打印 env var 值（与旧规则一致：仅元信息 + 长度/存在）
  - 不暴露文件路径细节（只报告可用性）
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from hailuo_video.local_client import (
    SANDBOX_RUNTIME,
    cache_dir,
    config_dict,
    diffusers_available,
    dtype_str,
    is_environment_ready,
    model_id,
    device as device_fn,
    torch_available,
)


def _check_torch() -> dict[str, Any]:
    if not torch_available():
        return {"check": "torch_installed", "ok": False, "detail": "未安装 torch（pip install torch）"}
    try:
        import torch  # type: ignore
        ver = getattr(torch, "__version__", "?")
        return {"check": "torch_installed", "ok": True, "detail": f"已装 torch=={ver}"}
    except Exception as exc:  # noqa: BLE001
        return {"check": "torch_installed", "ok": False, "detail": f"import 失败：{type(exc).__name__}"}


def _check_diffusers() -> dict[str, Any]:
    if not diffusers_available():
        return {"check": "diffusers_installed", "ok": False, "detail": "未安装 diffusers（pip install diffusers transformers accelerate sentencepiece imageio-ffmpeg safetensors）"}
    try:
        import diffusers  # type: ignore
        ver = getattr(diffusers, "__version__", "?")
        return {"check": "diffusers_installed", "ok": True, "detail": f"已装 diffusers=={ver}"}
    except Exception as exc:  # noqa: BLE001
        return {"check": "diffusers_installed", "ok": False, "detail": f"import 失败：{type(exc).__name__}"}


def _check_device() -> dict[str, Any]:
    dev = device_fn()
    if dev == "cpu":
        return {"check": "device", "ok": True, "detail": "device=cpu（CPU 推理极慢，R1）"}
    if dev == "cuda":
        if not torch_available():
            return {"check": "device", "ok": False, "detail": "device=cuda 但 torch 未装"}
        try:
            import torch  # type: ignore
            avail = bool(torch.cuda.is_available())
            if not avail:
                return {"check": "device", "ok": False, "detail": "device=cuda 但 torch.cuda.is_available()=False"}
            name = torch.cuda.get_device_name(0) if torch.cuda.device_count() else "?"
            return {"check": "device", "ok": True, "detail": f"cuda 可用：{name}"}
        except Exception as exc:  # noqa: BLE001
            return {"check": "device", "ok": False, "detail": f"cuda 检测异常：{type(exc).__name__}: {exc}"}
    return {"check": "device", "ok": False, "detail": f"LOCAL_VIDEO_DEVICE={dev}（必须 cpu 或 cuda）"}


def _check_dtype() -> dict[str, Any]:
    d = dtype_str()
    dev = device_fn()
    if d not in ("float32", "float16", "bfloat16"):
        return {"check": "dtype", "ok": False, "detail": f"LOCAL_VIDEO_DTYPE={d} 非法（float32/float16/bfloat16）"}
    if d == "bfloat16" and dev == "cpu":
        return {"check": "dtype", "ok": False, "detail": "dtype=bfloat16 在 CPU 上不被支持，请改 float32"}
    return {"check": "dtype", "ok": True, "detail": f"dtype={d} 与 device={dev} 兼容"}


def _check_model_cached() -> dict[str, Any]:
    """检查 HF 缓存目录是否已有该 model 的权重。"""
    cd = cache_dir()
    # 默认缓存位置
    candidates: list[Path] = []
    if cd:
        candidates.append(Path(cd) / f"models--{model_id().replace('/', '--')}")
    # HF 默认 ~/.cache/huggingface/hub
    candidates.append(Path.home() / ".cache" / "huggingface" / "hub" / f"models--{model_id().replace('/', '--')}")
    # diffusers 默认缓存（有时候在 hub 的子目录）
    hit = None
    for c in candidates:
        if c.is_dir() and any(c.iterdir()):
            hit = c
            break
    if hit:
        return {"check": "model_cached", "ok": True, "detail": f"已缓存（{model_id()}）"}
    return {
        "check": "model_cached",
        "ok": False,
        "detail": f"未缓存 {model_id()}（首次跑会从 HF 下载 ~10GB；可设 HF_ENDPOINT=https://hf-mirror.com）",
    }


def _check_disk_space() -> dict[str, Any]:
    """检查 cache_dir 所在盘的可用空间，< 15GB 警告。"""
    cd = cache_dir()
    target = Path(cd) if cd else Path.home() / ".cache"
    if not target.exists():
        target = target.parent
    try:
        usage = shutil.disk_usage(target)
        free_gb = usage.free / (1024 ** 3)
        ok = free_gb >= 15
        return {
            "check": "disk_space",
            "ok": ok,
            "detail": f"{target} 可用 {free_gb:.1f} GB" + ("" if ok else "（< 15 GB，模型权重可能下载失败）"),
        }
    except OSError as exc:
        return {"check": "disk_space", "ok": False, "detail": f"磁盘查询失败：{exc}"}


def diagnose() -> dict[str, Any]:
    """返回诊断结果字典。供 CLI 漂亮打印。

    configured 字段语义：环境是否就绪（torch+diffusers+device 兼容）。
    """
    result: dict[str, Any] = {
        "configured": is_environment_ready(),
        "warnings": [],
        "checks": [],
    }
    checks = [
        _check_torch(),
        _check_diffusers(),
        _check_device(),
        _check_dtype(),
        _check_model_cached(),
        _check_disk_space(),
    ]
    result["checks"] = checks

    # 收集 warnings（不破坏 ok 的项的提示）
    if not _check_model_cached()["ok"]:
        result["warnings"].append("模型权重未缓存；首次 generate 会自动下载")
    if device_fn() == "cpu":
        result["warnings"].append("device=cpu 推理极慢（5s 视频可能 30min+）；R1 已知")
    return result


def format_diagnose(d: dict[str, Any]) -> str:
    """人类可读。✓ / ✗ + check + detail。"""
    lines: list[str] = []
    lines.append(f"[DOCTOR] configured={'True' if d.get('configured') else 'False'}")
    warns = d.get("warnings") or []
    if warns:
        lines.append("[DOCTOR] 提示：")
        for w in warns:
            lines.append(f"  - {w}")
    for c in d.get("checks") or []:
        marker = "✓" if c.get("ok") else "✗"
        lines.append(f"  {marker} {c.get('check'):<26}  {c.get('detail', '')}")
    return "\n".join(lines)