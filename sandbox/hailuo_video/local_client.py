"""本地视频推理客户端（CogVideoX 后端，diffusers）。

V2-Local 起因：MiniMax Plus token plan 不带 Hailuo 视频 API 权限（D-011），
改用本地 diffusers CogVideoXPipeline 推理。零外部网络依赖。

设计原则（沿用旧 hailuo_client.py 风格）：
- 配置走环境变量，不覆盖已有
- 运行时日志落 runtime/runs.jsonl（每跑一次 inference 一行）
- 单测注入覆盖：_override_pipeline_load / _override_pipeline_call 不依赖真实 torch
- 失败结果用 VideoResult.source ∈ {"disabled"|"error"|"ok"} 表达
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

PACKAGE_ROOT = Path(__file__).resolve().parent
SANDBOX_RUNTIME = PACKAGE_ROOT / "runtime"

CST = timezone(timedelta(hours=8))

# ---------- 默认值 / 环境变量契约 ----------
DEFAULT_MODEL = "THUDM/CogVideoX-2b"
DEFAULT_DEVICE = "cpu"
DEFAULT_DTYPE = "float32"           # CPU 必 float32；CUDA 可 bf16
DEFAULT_NUM_FRAMES = 49             # ~6s @ 8fps
DEFAULT_HEIGHT = 480
DEFAULT_WIDTH = 720
DEFAULT_STEPS = 50
DEFAULT_GUIDANCE = 6.0
DEFAULT_SEED = -1                   # -1 = 随机

_DOTENV_KEYS = (
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
)


# ---------- 配置读取 ----------

def _load_dotenv() -> None:
    """读仓库根 .env。

    V3.9：委托给**统一 loader** `hailuo_video.dotenv`。
    旧实现是本仓**第二套**独立 loader + 独立白名单 —— 与 `script_engine` 那套
    各管一半，两边都漏键（见 dotenv.py 模块 docstring）。现在只有一套。
    """
    from hailuo_video.dotenv import load_dotenv
    load_dotenv()


_load_dotenv()


def _env_str(key: str, default: str) -> str:
    v = os.environ.get(key)
    return v if v else default


def _env_int(key: str, default: int) -> int:
    v = os.environ.get(key)
    if not v:
        return default
    try:
        return int(v)
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    v = os.environ.get(key)
    if not v:
        return default
    try:
        return float(v)
    except ValueError:
        return default


def model_id() -> str:
    return _env_str("LOCAL_VIDEO_MODEL", DEFAULT_MODEL)


def device() -> str:
    return _env_str("LOCAL_VIDEO_DEVICE", DEFAULT_DEVICE)


def dtype_str() -> str:
    return _env_str("LOCAL_VIDEO_DTYPE", DEFAULT_DTYPE)


def num_frames() -> int:
    return _env_int("LOCAL_VIDEO_NUM_FRAMES", DEFAULT_NUM_FRAMES)


def height() -> int:
    return _env_int("LOCAL_VIDEO_HEIGHT", DEFAULT_HEIGHT)


def width() -> int:
    return _env_int("LOCAL_VIDEO_WIDTH", DEFAULT_WIDTH)


def steps() -> int:
    return _env_int("LOCAL_VIDEO_STEPS", DEFAULT_STEPS)


def guidance() -> float:
    return _env_float("LOCAL_VIDEO_GUIDANCE", DEFAULT_GUIDANCE)


def seed() -> int:
    return _env_int("LOCAL_VIDEO_SEED", DEFAULT_SEED)


def cache_dir() -> str:
    return _env_str("LOCAL_VIDEO_CACHE_DIR", "")


def config_dict() -> dict[str, Any]:
    """当前生效的全部配置。供 doctor / ping 报告。"""
    return {
        "model": model_id(),
        "device": device(),
        "dtype": dtype_str(),
        "num_frames": num_frames(),
        "height": height(),
        "width": width(),
        "steps": steps(),
        "guidance": guidance(),
        "seed": seed(),
        "cache_dir": cache_dir() or "(default ~/.cache/huggingface)",
    }


# ---------- 结果数据类 ----------

@dataclass
class VideoResult:
    output_path: Path
    ok: bool
    source: str                       # "ok" | "error" | "disabled"
    error: str = ""
    run_id: str = ""                  # UUID4 短 id
    model: str = ""
    device: str = ""
    num_frames: int = 0
    duration_seconds: float = 0.0
    bytes: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


# ---------- 单测注入覆盖（不依赖 torch/diffusers）----------

# 签名：
# _override_pipeline_load(model_id, device, dtype, cache_dir) -> 任意 pipeline 对象
# _override_pipeline_call(pipeline, prompt, num_frames, height, width, num_steps,
#                         guidance_scale, seed, output_path) -> None
#   调用方应在 output_path 上写出 mp4 文件。
PipelineLoader = Callable[[str, str, str, str], Any]
PipelineCaller = Callable[[Any, str, int, int, int, int, float, int, Path], None]

_override_pipeline_load: PipelineLoader | None = None
_override_pipeline_call: PipelineCaller | None = None


def set_overrides(*, load: PipelineLoader | None = None, call: PipelineCaller | None = None) -> None:
    """单测设置覆盖。None = 清除该项。"""
    global _override_pipeline_load, _override_pipeline_call
    if load is not None:
        _override_pipeline_load = load
    if call is not None:
        _override_pipeline_call = call


def reset_overrides() -> None:
    """单测 teardown：清掉注入。"""
    global _override_pipeline_load, _override_pipeline_call
    _override_pipeline_load = None
    _override_pipeline_call = None


# ---------- 环境探测（不烧资源，只 import 检查）----------

def torch_available() -> bool:
    try:
        import torch  # noqa: F401
        return True
    except ImportError:
        return False


def diffusers_available() -> bool:
    try:
        import diffusers  # noqa: F401
        return True
    except ImportError:
        return False


def is_environment_ready() -> bool:
    """torch + diffusers 都装好。不检查模型权重（doctor 单独做）。"""
    return torch_available() and diffusers_available()


def warnings() -> list[str]:
    """目前没有强制必填项（不强制 Key，无端点）；只列非默认设置提示。"""
    msgs: list[str] = []
    if device() != "cpu" and device() != "cuda":
        msgs.append(f"LOCAL_VIDEO_DEVICE={device()} 既不是 cpu 也不是 cuda，请确认")
    if dtype_str() == "bfloat16" and device() == "cpu":
        msgs.append("LOCAL_VIDEO dtype=bfloat16 在 CPU 上不被支持，应改为 float32")
    return msgs


# ---------- 运行时日志（runs.jsonl）----------

def _now_iso() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


def _new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def _append_run(record: dict[str, Any], runtime_dir: Path | None = None) -> None:
    """追加一条到 runtime/runs.jsonl。绝不写 env var 值之外的敏感信息。"""
    rd = runtime_dir or SANDBOX_RUNTIME
    rd.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False)
    with (rd / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


# ---------- 核心：单条 generate ----------

def generate(
    prompt: str,
    output_path: str | Path,
    *,
    model: str | None = None,
    device_name: str | None = None,
    dtype_name: str | None = None,
    num_frames: int | None = None,
    height_px: int | None = None,
    width_px: int | None = None,
    num_steps: int | None = None,
    guidance_scale: float | None = None,
    seed_value: int | None = None,
    cache_dir_value: str | None = None,
    runtime_dir: Path | None = None,
) -> VideoResult:
    """跑一次 CogVideoX 推理，落 mp4 到 output_path。同步阻塞（CPU 上可能数十分钟）。

    失败来源 source：
      - "disabled"：prompt 为空 / 环境变量致命错（如 dtype 解析）
      - "error"：推理异常（OOM / 模型加载失败 / 写盘失败）
      - "ok"：成功
    """
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    if not prompt or not prompt.strip():
        run_id = _new_run_id()
        result = VideoResult(
            output_path=output,
            ok=False,
            source="disabled",
            error="prompt 为空",
            run_id=run_id,
        )
        _append_run(_record(run_id, prompt, result, model_id(), device(), num_frames or 0, 0.0), runtime_dir)
        return result

    # 用入参覆盖默认（入参 None = 用 env/default）
    model = model or model_id()
    dev = device_name or device()
    dtyp = dtype_name or dtype_str()
    nf = num_frames if num_frames is not None else globals()["num_frames"]()
    h = height_px if height_px is not None else globals()["height"]()
    w = width_px if width_px is not None else globals()["width"]()
    ns = num_steps if num_steps is not None else globals()["steps"]()
    gs = guidance_scale if guidance_scale is not None else globals()["guidance"]()
    sd = seed_value if seed_value is not None else globals()["seed"]()
    cd = cache_dir_value if cache_dir_value is not None else cache_dir()

    run_id = _new_run_id()
    started = time.monotonic()

    # 环境校验：dtype 配 device
    if dtyp == "bfloat16" and dev == "cpu":
        result = VideoResult(
            output_path=output,
            ok=False,
            source="disabled",
            error="dtype=bfloat16 不支持 CPU，请改 float32",
            run_id=run_id,
            model=model,
            device=dev,
            num_frames=nf,
        )
        _append_run(_record(run_id, prompt, result, model, dev, nf, time.monotonic() - started), runtime_dir)
        return result

    # 注入覆盖存在 → 不真 import torch/diffusers
    if _override_pipeline_load is not None and _override_pipeline_call is not None:
        try:
            pipeline = _override_pipeline_load(model, dev, dtyp, cd)
            _override_pipeline_call(pipeline, prompt, nf, h, w, ns, gs, sd, output)
        except Exception as exc:  # noqa: BLE001
            elapsed = time.monotonic() - started
            result = VideoResult(
                output_path=output,
                ok=False,
                source="error",
                error=f"{type(exc).__name__}: {exc}",
                run_id=run_id,
                model=model,
                device=dev,
                num_frames=nf,
                duration_seconds=elapsed,
            )
            _append_run(_record(run_id, prompt, result, model, dev, nf, elapsed), runtime_dir)
            return result
        elapsed = time.monotonic() - started
        result = VideoResult(
            output_path=output,
            ok=True,
            source="ok",
            run_id=run_id,
            model=model,
            device=dev,
            num_frames=nf,
            duration_seconds=elapsed,
            bytes=output.stat().st_size if output.exists() else 0,
            extra={"steps": ns, "guidance": gs, "seed": sd if sd >= 0 else "(random)"},
        )
        _append_run(_record(run_id, prompt, result, model, dev, nf, elapsed), runtime_dir)
        return result

    # 无覆盖：必须装好 torch + diffusers
    if not is_environment_ready():
        elapsed = time.monotonic() - started
        missing = []
        if not torch_available():
            missing.append("torch")
        if not diffusers_available():
            missing.append("diffusers")
        result = VideoResult(
            output_path=output,
            ok=False,
            source="error",
            error=f"环境未就绪：缺少依赖 {', '.join(missing)}（先跑 `pip install torch diffusers transformers accelerate sentencepiece imageio-ffmpeg safetensors`）",
            run_id=run_id,
            model=model,
            device=dev,
            num_frames=nf,
            duration_seconds=elapsed,
        )
        _append_run(_record(run_id, prompt, result, model, dev, nf, elapsed), runtime_dir)
        return result

    # 实际推理（torch + diffusers 真导入；CPU 极慢）
    try:
        import torch  # noqa: F401  (可用性已检)
        from diffusers import CogVideoXPipeline  # type: ignore

        torch_dtype = {
            "float32": torch.float32,
            "float16": torch.float16,
            "bfloat16": torch.bfloat16,
        }.get(dtyp, torch.float32)

        load_kwargs: dict[str, Any] = {"torch_dtype": torch_dtype}
        if cd:
            load_kwargs["cache_dir"] = cd
        pipeline = CogVideoXPipeline.from_pretrained(model, **load_kwargs)
        if dev != "cpu":
            pipeline.to(dev)

        gen_kwargs: dict[str, Any] = {
            "prompt": prompt,
            "num_frames": nf,
            "height": h,
            "width": w,
            "num_inference_steps": ns,
            "guidance_scale": gs,
        }
        if sd >= 0:
            gen_kwargs["generator"] = torch.Generator(device=dev).manual_seed(sd)

        frames = pipeline(**gen_kwargs).frames[0]
        _export_to_mp4(frames, output)
    except Exception as exc:  # noqa: BLE001
        elapsed = time.monotonic() - started
        result = VideoResult(
            output_path=output,
            ok=False,
            source="error",
            error=f"{type(exc).__name__}: {exc}",
            run_id=run_id,
            model=model,
            device=dev,
            num_frames=nf,
            duration_seconds=elapsed,
        )
        _append_run(_record(run_id, prompt, result, model, dev, nf, elapsed), runtime_dir)
        return result

    elapsed = time.monotonic() - started
    result = VideoResult(
        output_path=output,
        ok=True,
        source="ok",
        run_id=run_id,
        model=model,
        device=dev,
        num_frames=nf,
        duration_seconds=elapsed,
        bytes=output.stat().st_size if output.exists() else 0,
        extra={"steps": ns, "guidance": gs, "seed": sd if sd >= 0 else "(random)"},
    )
    _append_run(_record(run_id, prompt, result, model, dev, nf, elapsed), runtime_dir)
    return result


def _export_to_mp4(frames: list[Any], output: Path) -> None:
    """frames → mp4 落盘。优先 export_to_video，回退 imageio。"""
    try:
        from diffusers.utils import export_to_video  # type: ignore
        export_to_video(frames, str(output), fps=8)
        return
    except Exception:
        pass
    # 回退：imageio + ffmpeg
    try:
        import imageio.v2 as imageio  # type: ignore
        import numpy as np  # type: ignore
        writer = imageio.get_writer(str(output), fps=8, codec="libx264", quality=8)
        for f in frames:
            arr = np.asarray(f)
            writer.append_data(arr)
        writer.close()
    except Exception as exc:
        raise RuntimeError(f"mp4 导出失败：{type(exc).__name__}: {exc}") from exc


def _record(run_id: str, prompt: str, r: VideoResult, model: str, dev: str, nf: int, elapsed: float) -> dict[str, Any]:
    """构造 runs.jsonl 的一行。绝不写环境变量值（含 cache_dir）。"""
    return {
        "ts": _now_iso(),
        "run_id": run_id,
        "op": "inference",
        "ok": r.ok,
        "source": r.source,
        "error": r.error,
        "duration_seconds": round(elapsed, 3),
        "extra": {
            "prompt_chars": len(prompt),
            "model": model,
            "device": dev,
            "num_frames": nf,
            "bytes": r.bytes,
        },
    }