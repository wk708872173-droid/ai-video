"""V3.9 Batch D — BUG-119：`last_frame_image` 死代码清理。

## 背景

`logs/_verify_last_frame_image_20261016/SUMMARY.md`（2026-10-16 实测）：

> | `last_frame_image` | **被拒（2013）** | 已写代码但**永远走不到** |
> "必须做的清理：motion_backend.py 移除 last_frame_image 相关代码
>  （_generate_i2v 的分支、_submit 的 last_frame 拼装）；移除 docstring 中
>  关于 last_frame 的误导描述"

留着这段代码的代价：① 有人误传 `last_frame_image` 会换来一个 2013 的 4xx；
② docstring 里的"双锚定"会让下一个人以为 first+last 双锚定是可用路径
（DeepSeek Q5 的假设已被实测推翻）。

R4：测试不调真 API（用注入的 `urlopen` 替身 / 替换掉 `_submit` 等方法）。
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from hailuo_video import motion_backend as mb  # noqa: E402


def _backend(monkeypatch) -> "mb.MiniMaxMotionBackend":
    """造一个后端（key 从 env 读 —— 后端没有 api_key_value 形参）。"""
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 20)
    return mb.MiniMaxMotionBackend()


def _stub_pipeline(monkeypatch, calls: list[dict]) -> None:
    """把 submit/poll/retrieve/download 全部替换掉（不打真 API）。"""
    def fake_submit(self, prompt, duration, resolution, *, first_frame_image="", seed=None):
        calls.append({"first": first_frame_image})
        return "t1"

    monkeypatch.setattr(mb.MiniMaxMotionBackend, "_submit", fake_submit)
    monkeypatch.setattr(mb.MiniMaxMotionBackend, "_poll_until_ready",
                        lambda self, tid: "f1")
    monkeypatch.setattr(mb.MiniMaxMotionBackend, "_fetch_download_url",
                        lambda self, fid: "https://x/v.mp4")
    monkeypatch.setattr(mb.MiniMaxMotionBackend, "_download",
                        lambda self, url, out: Path(out).write_bytes(b"x"))


# ---------- 死代码已移除 ----------

def test_submit_has_no_last_frame_parameter():
    """`_submit` 不得再有 last_frame_image 参数。"""
    sig = inspect.signature(mb.MiniMaxMotionBackend._submit)
    assert "last_frame_image" not in sig.parameters


def test_generate_i2v_has_no_last_frame_parameter():
    """`_generate_i2v` 不得再有 last_frame_image 参数。"""
    sig = inspect.signature(mb.MiniMaxMotionBackend._generate_i2v)
    assert "last_frame_image" not in sig.parameters


def test_submit_never_puts_last_frame_in_body(monkeypatch):
    """请求体里绝不能出现 last_frame_image（会被 API 2013 拒）。"""
    captured: dict = {}

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"task_id": "t1"}'

    def fake_urlopen(req, timeout=None):
        captured["body"] = req.data.decode("utf-8")
        return _Resp()

    monkeypatch.setattr(mb.urllib.request, "urlopen", fake_urlopen)
    backend = _backend(monkeypatch)
    backend._submit("p", 6, "1080P", first_frame_image="https://x/f.jpg")
    assert "last_frame_image" not in captured["body"]
    assert "first_frame_image" in captured["body"]


def test_last_frame_only_falls_back_to_t2v(monkeypatch, capsys):
    """只传 last_frame_image → 告警 + 回退 T2V（不再发那个必被拒的字段）。"""
    calls: list[dict] = []
    _stub_pipeline(monkeypatch, calls)
    backend = _backend(monkeypatch)

    backend.generate("p", output_path="/tmp/_v38_i2v.mp4",
                     last_frame_image="https://x/l.jpg")

    err = capsys.readouterr().err
    assert "last_frame_image" in err, "误传 last_frame 必须告警（R4：不静默忽略）"
    assert "2013" in err, "告警要说明原因（API 拒绝）"
    assert calls and calls[0]["first"] == "", "应回退到 T2V（不带 first_frame）"


def test_first_frame_still_triggers_i2v(monkeypatch):
    """first_frame_image 单锚定仍然正常走 I2V（这是被验证可用的路径）。"""
    calls: list[dict] = []
    _stub_pipeline(monkeypatch, calls)
    backend = _backend(monkeypatch)

    res = backend.generate("p", output_path="/tmp/_v38_i2v2.mp4",
                           first_frame_image="https://x/f.jpg")
    assert calls[0]["first"] == "https://x/f.jpg"
    assert res.extra["mode"] == "i2v"
    assert "last_frame_image" not in res.extra


def test_i2v_requires_first_frame(monkeypatch):
    """I2V 必须有 first_frame_image（错误信息要说清楚）。"""
    backend = _backend(monkeypatch)
    with pytest.raises(mb.MotionError, match="first_frame_image"):
        backend._generate_i2v(prompt="p", duration=6, resolution="1080P",
                              output_path="/tmp/_v38_i2v3.mp4")


def test_docstrings_do_not_promise_double_anchoring():
    """docstring 不得再**承诺**"双锚定"（已被实测推翻）。"""
    doc = inspect.getdoc(mb.MiniMaxMotionBackend._generate_i2v) or ""
    if "双锚定" in doc:
        assert any(w in doc for w in ("不可行", "别再走", "不支持")), (
            "docstring 提到双锚定却没有说明它不可行")
    assert "first_frame_image" in doc


def test_sys_is_imported_at_module_level():
    """`sys` 必须在模块顶层 import —— 告警走 sys.stderr。

    （V3.9 自测踩到：motion_backend 原先只在异常处理块里 `import sys`，
    模块级用 sys.stderr 会 NameError。）
    """
    assert "sys" in mb.__dict__, "motion_backend 模块级没有 import sys"


def test_no_last_frame_in_request_body_anywhere():
    """兜底：全仓 motion_backend 的请求体拼装里不得再出现 last_frame。"""
    src = inspect.getsource(mb)
    # 只允许出现在"读取 + 告警"的语境里
    for line in src.splitlines():
        if "last_frame_image" not in line:
            continue
        stripped = line.strip()
        assert not stripped.startswith("body["), f"请求体里仍有 last_frame：{stripped}"
        assert not stripped.startswith('"last_frame_image"'), \
            f"请求体里仍有 last_frame：{stripped}"
