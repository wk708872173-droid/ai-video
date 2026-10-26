"""GeminiImageBackend（Nano Banana, gemini-2.5-flash-image）单测。

全部走 http_post 注入缝，不发真实请求、不花钱。
重点覆盖与 MiniMax 的契约差异：
  · 响应是 camelCase inlineData（不是 Python SDK 的 inline_data）
  · 无 url 模式（永远 base64）
  · 无 n 参数（n>1 顺序多次调用）
  · thought 草稿图必须跳过
  · finishReason 非 OK 必须显式报错，绝不静默返回 0 张
"""
from __future__ import annotations

import base64
import sys
import urllib.error
from pathlib import Path

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))
from hailuo_video import image_engine as ie  # noqa: E402

FAKE_KEY = "AIzaFakeKeyForUnitTest"
JPEG = b"\xff\xd8\xff\xe0 fake jpeg bytes"
PNG = b"\x89PNG\r\n\x1a\n fake png bytes"
JPEG_B64 = base64.b64encode(JPEG).decode("ascii")


# ---------- 工具 ----------

@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """清掉 provider env，避免本机 .env / 进程 env 污染单测。"""
    for k in ie._DOTENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    yield


def _ok_response(mime: str = "image/png", data: str = JPEG_B64) -> dict:
    """官方文档形态的成功响应（camelCase）。"""
    return {
        "candidates": [{
            "content": {
                "role": "model",
                "parts": [{"inlineData": {"mimeType": mime, "data": data}}],
            },
            "finishReason": "STOP",
            "index": 0,
        }],
        "usageMetadata": {"totalTokenCount": 1326},
        "modelVersion": "gemini-2.5-flash-image",
        "responseId": "resp_123",
    }


class _Recorder:
    """记录 http_post 的每次调用，回放一串响应。"""

    def __init__(self, *responses: dict) -> None:
        self.calls: list[tuple[str, dict, dict]] = []
        self._responses = list(responses) or [_ok_response()]

    def __call__(self, url: str, body: dict, headers: dict) -> dict:
        self.calls.append((url, body, headers))
        return self._responses[min(len(self.calls) - 1, len(self._responses) - 1)]


def _backend(*responses: dict, **kwargs) -> ie.GeminiImageBackend:
    rec = _Recorder(*responses) if responses else _Recorder()
    b = ie.GeminiImageBackend(api_key_value=FAKE_KEY, http_post=rec, **kwargs)
    b.recorder = rec  # type: ignore[attr-defined]
    return b


# ---------- 配置 / 构造 ----------

def test_missing_api_key_raises():
    with pytest.raises(ie.ImageError, match="GEMINI_API_KEY 未设置"):
        ie.GeminiImageBackend()


def test_default_model_is_ga_flash_image(monkeypatch):
    b = ie.GeminiImageBackend(api_key_value=FAKE_KEY)
    assert b._model == "gemini-2.5-flash-image"
    assert ie.DEFAULT_GEMINI_IMAGE_MODEL == "gemini-2.5-flash-image"
    # -preview id 已被 Google 废弃且会拒收 imageConfig，回归保护
    assert "-preview" not in ie.DEFAULT_GEMINI_IMAGE_MODEL


def test_default_base_url(monkeypatch):
    b = ie.GeminiImageBackend(api_key_value=FAKE_KEY)
    assert b._base_url == "https://generativelanguage.googleapis.com/v1beta"


def test_is_gemini_configured_requires_key(monkeypatch):
    assert ie.is_gemini_configured() is False
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    assert ie.is_gemini_configured() is True


def test_no_minimax_length_heuristic_on_gemini_key(monkeypatch):
    """R1：不能照搬 MiniMax 的 len>=16。短 key 也应通过（长度启发式是发明规则）。"""
    b = ie.GeminiImageBackend(api_key_value="AIzaX")
    assert b._api_key == "AIzaX"


def test_configuration_warnings_flags_bad_aspect_ratio(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("GEMINI_IMAGE_ASPECT_RATIO", "32:9")
    warns = ie.gemini_configuration_warnings()
    assert any("GEMINI_IMAGE_ASPECT_RATIO" in w for w in warns)


def test_configuration_warnings_flags_missing_key():
    warns = ie.gemini_configuration_warnings()
    assert any("GEMINI_API_KEY 未设置" in w for w in warns)


def test_supports_local_refs_flag():
    assert ie.GeminiImageBackend(api_key_value=FAKE_KEY).supports_local_refs is True
    assert ie.MockImageBackend().supports_local_refs is False
    assert ie.MiniMaxImageBackend(api_key_value="x" * 20).supports_local_refs is False


# ---------- _parse_response ----------

def test_parse_response_happy_path():
    b = _backend()
    r = b._parse_response(_ok_response(), "p", "m", "16:9", 0.1)
    assert r.bytes == JPEG
    assert r.mime_type == "image/png"
    assert r.image_base64 == [JPEG_B64]
    assert r.image_url == []          # Gemini 无 url 模式
    assert r.request_id == "resp_123"
    assert r.extra["finishReason"] == "STOP"


def test_parse_response_preserves_png_mime():
    """输出可能是 jpeg 或 png，不能硬编码。"""
    b = _backend()
    r = b._parse_response(_ok_response(mime="image/jpeg"), "p", "m", "16:9", 0.1)
    assert r.mime_type == "image/jpeg"


def test_parse_response_sniffs_mime_when_missing():
    b = _backend()
    payload = _ok_response()
    payload["candidates"][0]["content"]["parts"][0]["inlineData"].pop("mimeType")
    r = b._parse_response(payload, "p", "m", "16:9", 0.1)
    assert r.mime_type == "image/jpeg"  # JPEG magic


def test_parse_response_sniffs_png_magic():
    b = _backend()
    payload = _ok_response(data=base64.b64encode(PNG).decode("ascii"))
    payload["candidates"][0]["content"]["parts"][0]["inlineData"].pop("mimeType")
    r = b._parse_response(payload, "p", "m", "16:9", 0.1)
    assert r.mime_type == "image/png"
    assert r.bytes == PNG


def test_parse_response_skips_thought_draft_parts():
    """thought:true 是草稿图，不是成图，必须跳过。"""
    b = _backend()
    draft = base64.b64encode(b"\x89PNG draft").decode("ascii")
    payload = {
        "candidates": [{
            "content": {"parts": [
                {"inlineData": {"mimeType": "image/png", "data": draft}, "thought": True},
                {"text": "thinking..."},
                {"inlineData": {"mimeType": "image/png", "data": JPEG_B64}},
            ]},
            "finishReason": "STOP",
        }],
    }
    r = b._parse_response(payload, "p", "m", "16:9", 0.1)
    assert r.bytes == JPEG                       # 成图，不是草稿
    assert r.extra["parts_total"] == 3
    assert r.extra["parts_used"] == 1


def test_parse_response_all_thought_raises():
    b = _backend()
    payload = {
        "candidates": [{
            "content": {"parts": [
                {"inlineData": {"mimeType": "image/png", "data": JPEG_B64}, "thought": True},
            ]},
            "finishReason": "STOP",
        }],
    }
    with pytest.raises(ie.ImageError, match="0 张"):
        b._parse_response(payload, "p", "m", "16:9", 0.1)


@pytest.mark.parametrize("reason", ["IMAGE_SAFETY", "PROHIBITED_CONTENT", "RECITATION"])
def test_parse_response_finish_reason_raises(reason):
    """安全/版权拦截必须显式报错，绝不静默返回 0 张（R4）。"""
    b = _backend()
    payload = _ok_response()
    payload["candidates"][0]["finishReason"] = reason
    with pytest.raises(ie.ImageError, match=reason):
        b._parse_response(payload, "p", "m", "16:9", 0.1)


def test_parse_response_finish_reason_stop_ok():
    b = _backend()
    payload = _ok_response()
    payload["candidates"][0]["finishReason"] = "STOP"
    assert b._parse_response(payload, "p", "m", "16:9", 0.1).ok


def test_parse_response_error_envelope():
    b = _backend()
    payload = {"error": {"code": 400, "message": "bad request", "status": "INVALID_ARGUMENT"}}
    with pytest.raises(ie.ImageError, match="400"):
        b._parse_response(payload, "p", "m", "16:9", 0.1)


def test_parse_response_no_candidates_with_block_reason():
    b = _backend()
    payload = {"promptFeedback": {"blockReason": "SAFETY"}}
    with pytest.raises(ie.ImageError, match="SAFETY"):
        b._parse_response(payload, "p", "m", "16:9", 0.1)


def test_parse_response_no_candidates_raises():
    b = _backend()
    with pytest.raises(ie.ImageError, match="无 candidates"):
        b._parse_response({"usageMetadata": {}}, "p", "m", "16:9", 0.1)


def test_parse_response_rejects_snake_case_sdk_field():
    """`inline_data` 是 Python SDK 写法，REST 不认——不能同时兼容两套而含糊其辞。"""
    b = _backend()
    payload = {
        "candidates": [{
            "content": {"parts": [{"inline_data": {"mime_type": "image/png", "data": JPEG_B64}}]},
            "finishReason": "STOP",
        }],
    }
    with pytest.raises(ie.ImageError, match="0 张"):
        b._parse_response(payload, "p", "m", "16:9", 0.1)


# ---------- generate() 请求体 ----------

def test_generate_request_payload_contract():
    """最重要的断言：请求体的字段名/嵌套/取值。"""
    b = _backend()
    b.generate("a cat", aspect_ratio_value="9:16")
    url, body, headers = b.recorder.calls[0]
    assert url == (
        "https://generativelanguage.googleapis.com/v1beta"
        "/models/gemini-2.5-flash-image:generateContent"
    )
    assert headers["x-goog-api-key"] == FAKE_KEY
    assert headers["Content-Type"] == "application/json"
    gen = body["generationConfig"]
    assert gen["responseModalities"] == ["IMAGE"]
    assert gen["imageConfig"]["aspectRatio"] == "9:16"
    # 本模型会拒收 imageSize
    assert "imageSize" not in gen["imageConfig"]
    # 比例必须放 config，不能写进 prompt 文本
    text_parts = [p["text"] for p in body["contents"][0]["parts"] if "text" in p]
    assert text_parts == ["a cat"]
    assert "9:16" not in text_parts[0]


def test_generate_t2i_returns_bytes():
    b = _backend()
    results = b.generate("a cat")
    assert len(results) == 1
    assert results[0].bytes == JPEG
    assert results[0].ok


def test_generate_n_makes_separate_calls():
    """Gemini 无 n 参数：n>1 必须发起 n 次调用。"""
    b = _backend()
    results = b.generate("a cat", n_value=3)
    assert len(results) == 3
    assert len(b.recorder.calls) == 3
    # 每次 body 独立，不能互相污染
    assert all("n" not in body for _, body, _ in b.recorder.calls)


def test_generate_accepts_gemini_only_aspect_ratios():
    b = _backend()
    assert b.generate("x", aspect_ratio_value="21:9")[0].ok      # Gemini 有、minimax 没有
    with pytest.raises(ie.ImageError, match="不在 Gemini 支持集"):
        _backend().generate("x", aspect_ratio_value="32:9")


def test_generate_response_format_url_warns_but_continues():
    """asset_prep 4 处都传 response_format_value="url"。Gemini 无 url 模式：
    必须显式告警（不能静默忽略），但不该硬失败。"""
    b = _backend()
    results = b.generate("x", response_format_value="url")
    assert results[0].bytes == JPEG
    assert results[0].image_url == []


def test_generate_response_format_url_emits_warning(capsys):
    b = _backend()
    b.generate("x", response_format_value="url")
    assert "WARN" in capsys.readouterr().err


def test_generate_empty_prompt_raises():
    with pytest.raises(ie.ImageError, match="prompt 为空"):
        _backend().generate("   ")


def test_generate_zero_n_raises():
    with pytest.raises(ie.ImageError, match="n=0"):
        _backend().generate("x", n_value=0)


def test_generate_http_error_raises():
    def boom(url, body, headers):
        raise urllib.error.HTTPError(url, 400, "Bad Request", {}, None)

    b = ie.GeminiImageBackend(api_key_value=FAKE_KEY, http_post=boom)
    with pytest.raises(ie.ImageError, match="HTTP 400"):
        b.generate("x")


def test_generate_url_error_raises():
    def boom(url, body, headers):
        raise urllib.error.URLError("proxy refused")

    b = ie.GeminiImageBackend(api_key_value=FAKE_KEY, http_post=boom)
    with pytest.raises(ie.ImageError, match="URLError"):
        b.generate("x")


def test_generate_non_dict_raises():
    b = ie.GeminiImageBackend(api_key_value=FAKE_KEY, http_post=lambda u, b, h: ["nope"])
    with pytest.raises(ie.ImageError, match="非 dict"):
        b.generate("x")


def test_generate_does_not_leak_api_key(capsys):
    b = _backend()
    b.generate("x", response_format_value="url")
    err = capsys.readouterr().err
    assert FAKE_KEY not in err


# ---------- generate() i2i ----------

def test_generate_i2i_local_file_order_and_content(tmp_path):
    """i2i：参考图 inlineData 在前，指令 text 在后；字节与文件一致。"""
    ref = tmp_path / "front.jpg"
    ref.write_bytes(JPEG)
    b = _backend()
    b.generate("keep the same character", subject_reference=str(ref))
    parts = b.recorder.calls[0][1]["contents"][0]["parts"]
    assert len(parts) == 2
    assert parts[0]["inlineData"]["mimeType"] == "image/jpeg"
    assert base64.b64decode(parts[0]["inlineData"]["data"]) == JPEG
    assert parts[1]["text"] == "keep the same character"   # 顺序断言


def test_generate_i2i_multiple_refs(tmp_path):
    a, bfile = tmp_path / "a.jpg", tmp_path / "b.png"
    a.write_bytes(JPEG)
    bfile.write_bytes(PNG)
    b = _backend()
    b.generate("x", subject_reference=[str(a), str(bfile)])
    parts = b.recorder.calls[0][1]["contents"][0]["parts"]
    assert [p["inlineData"]["mimeType"] for p in parts[:-1]] == ["image/jpeg", "image/png"]
    assert parts[-1]["text"] == "x"


def test_generate_i2i_http_url_ref(tmp_path, monkeypatch):
    captured: dict = {}

    def fake_urlopen(url, timeout=None):
        captured["url"] = url

        class R:
            headers = {"Content-Type": "image/jpeg"}
            def read(self):
                return JPEG
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        return R()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    b = _backend()
    b.generate("x", subject_reference="https://e.com/ref.jpg")
    parts = b.recorder.calls[0][1]["contents"][0]["parts"]
    assert captured["url"] == "https://e.com/ref.jpg"
    assert base64.b64decode(parts[0]["inlineData"]["data"]) == JPEG


def test_generate_i2i_missing_local_ref_raises(tmp_path):
    with pytest.raises(ie.ImageError, match="本地文件不存在"):
        _backend().generate("x", subject_reference=str(tmp_path / "nope.jpg"))


def test_generate_refs_not_shared_across_n_calls(tmp_path):
    """n>1 时每次调用都要有独立的 parts 列表。"""
    ref = tmp_path / "front.jpg"
    ref.write_bytes(JPEG)
    b = _backend()
    b.generate("x", n_value=2, subject_reference=str(ref))
    for _, body, _ in b.recorder.calls:
        assert len(body["contents"][0]["parts"]) == 2


# ---------- load_reference_bytes ----------

def test_load_reference_bytes_local_file(tmp_path):
    p = tmp_path / "a.jpg"
    p.write_bytes(PNG)
    mime, data = ie.load_reference_bytes(str(p))
    assert (mime, data) == ("image/png", PNG)


def test_load_reference_bytes_missing_local(tmp_path):
    with pytest.raises(ie.ImageError, match="本地文件不存在"):
        ie.load_reference_bytes(str(tmp_path / "nope.jpg"))


def test_load_reference_bytes_empty_local(tmp_path):
    p = tmp_path / "empty.jpg"
    p.write_bytes(b"")
    with pytest.raises(ie.ImageError, match="本地文件为空"):
        ie.load_reference_bytes(str(p))


def test_load_reference_bytes_blank_ref():
    with pytest.raises(ie.ImageError, match="reference 为空"):
        ie.load_reference_bytes("   ")


def test_load_reference_bytes_http_uses_content_type(monkeypatch):
    class R:
        headers = {"Content-Type": "image/png; charset=binary"}
        def read(self):
            return b"whatever"
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda u, timeout=None: R())
    mime, _ = ie.load_reference_bytes("https://e.com/a.png")
    assert mime == "image/png"


def test_load_reference_bytes_http_non_image_ctype_sniffs(monkeypatch):
    class R:
        headers = {"Content-Type": "text/html"}
        def read(self):
            return JPEG
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda u, timeout=None: R())
    mime, _ = ie.load_reference_bytes("https://e.com/a")
    assert mime == "image/jpeg"   # 嗅探兜底，不信 Content-Type


def test_load_reference_bytes_http_404(monkeypatch):
    def boom(url, timeout=None):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", boom)
    with pytest.raises(ie.ImageError, match="HTTP 404"):
        ie.load_reference_bytes("https://e.com/gone.jpg")


# ---------- MiniMax fail-closed 回归 ----------

def test_minimax_rejects_local_ref_with_clear_error():
    """R4：MiniMax 收到本地 ref 必须显式报错，不能静默丢弃。"""
    b = ie.MiniMaxImageBackend(api_key_value="x" * 20, http_post=lambda u, b, h: {})
    with pytest.raises(ie.ImageError, match="只支持 http"):
        b.generate("x", subject_reference="assets/characters/x/front.jpg")


def test_minimax_still_accepts_http_refs():
    b = ie.MiniMaxImageBackend(api_key_value="x" * 20, http_post=lambda u, b, h: {
        "data": {"image_urls": ["https://e.com/a.jpg"]},
    })
    results = b.generate(
        "x",
        subject_reference=["https://e.com/a.jpg"],
        response_format_value="url",
    )
    assert results[0].image_url == ["https://e.com/a.jpg"]


# ---------- 工厂 ----------

def test_make_backend_gemini(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    assert isinstance(ie.make_backend("gemini"), ie.GeminiImageBackend)


def test_make_backend_auto_still_minimax(monkeypatch):
    """不给用户脚下换默认 provider（Gemini 单张 ~$0.039）。"""
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 20)
    assert isinstance(ie.make_backend("auto"), ie.MiniMaxImageBackend)


def test_make_backend_unknown_raises():
    with pytest.raises(ie.ImageError, match="未知 backend"):
        ie.make_backend("openai")


# ---------- CLI ----------

from hailuo_video.cli import main as cli_main  # noqa: E402


def test_cli_image_gemini_no_key_returns_2(capsys):
    rc = cli_main(["image", "a cat", "-o", "/tmp/out.jpg", "--backend", "gemini"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "GEMINI_API_KEY 未设置" in err


def _help(argv: list[str], capsys) -> str:
    """`--help` 会 SystemExit(0)，捕获它再取输出。"""
    with pytest.raises(SystemExit):
        cli_main(argv)
    return capsys.readouterr().out


def test_cli_image_gemini_accepted_by_choices(capsys):
    """gemini 已进 argparse choices（不因非法值报 SystemExit(2)）。"""
    assert "gemini" in _help(["image", "--help"], capsys)


def test_cli_pipeline_and_prep_accept_gemini(capsys):
    for sub in ("pipeline", "prep"):
        assert "gemini" in _help([sub, "--help"], capsys), f"{sub} 的 --help 未列出 gemini"


def test_cli_image_backend_rejects_unknown_choice():
    with pytest.raises(SystemExit):
        cli_main(["image", "x", "-o", "/tmp/o.jpg", "--backend", "nonsense"])


def test_cli_pipeline_and_prep_accept_gemini(capsys):
    for sub in ("pipeline", "prep"):
        assert "gemini" in _help([sub, "--help"], capsys), f"{sub} 的 --help 未列出 gemini"


def test_cli_image_mock_still_works(tmp_path, monkeypatch):
    """回归：重构 cmd_image 分支后 mock 路径不受影响。"""
    out = tmp_path / "out.jpg"
    rc = cli_main(["image", "a cat", "-o", str(out), "--backend", "mock"])
    assert rc == 0
    assert out.exists()