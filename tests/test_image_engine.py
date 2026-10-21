"""REQ-IMAGE MiniMax 图像生成单测。全部 MockBackend，不依赖真 API。"""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from hailuo_video import image_engine as ie  # noqa: E402


# ---------- env fixture ----------

@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """env 干净；默认无 Key。"""
    for k in ie._DOTENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    yield


# ---------- 配置 ----------

def test_is_image_configured_requires_key():
    assert ie.is_image_configured() is False


def test_is_image_configured_requires_min_key_length():
    os.environ["MINIMAX_API_KEY"] = "short"
    assert ie.is_image_configured() is False
    os.environ["MINIMAX_API_KEY"] = "x" * 20
    assert ie.is_image_configured() is True


def test_configuration_warnings_short_key():
    os.environ["MINIMAX_API_KEY"] = "abc"
    warns = ie.configuration_warnings()
    assert any("长度" in w for w in warns)


def test_configuration_warnings_bad_response_format():
    os.environ["MINIMAX_API_KEY"] = "x" * 20
    os.environ["MINIMAX_IMAGE_RESPONSE_FORMAT"] = "xml"
    warns = ie.configuration_warnings()
    assert any("response_format" in w.lower() for w in warns)


def test_configuration_warnings_bad_aspect_ratio():
    os.environ["MINIMAX_API_KEY"] = "x" * 20
    os.environ["MINIMAX_IMAGE_ASPECT_RATIO"] = "32:9"
    warns = ie.configuration_warnings()
    assert any("aspect_ratio" in w.lower() for w in warns)


def test_configuration_warnings_clean():
    os.environ["MINIMAX_API_KEY"] = "x" * 20
    warns = ie.configuration_warnings()
    assert warns == []


# ---------- MockImageBackend ----------

def test_mock_backend_default_format_base64():
    backend = ie.MockImageBackend()
    results = backend.generate("hello")
    assert len(results) == 1
    r = results[0]
    # 返回真 JPEG 字节（懒生成；下游 ffmpeg merge 可解析）
    assert r.bytes == ie.MockImageBackend().next_bytes
    assert r.bytes[:2] == b"\xff\xd8"  # JPEG magic
    assert r.image_base64 == [base64.b64encode(r.bytes).decode("ascii")]
    assert r.image_url == []


def test_mock_backend_url_format():
    backend = ie.MockImageBackend()
    backend.response_format = "url"
    results = backend.generate("hello")
    r = results[0]
    assert r.image_url  # non-empty list
    assert r.image_base64 == []


def test_mock_backend_records_calls():
    backend = ie.MockImageBackend()
    backend.generate("a cat", n_value=3)
    assert backend.calls == [{"prompt": "a cat", "n_value": 3}]


def test_mock_backend_n_count():
    backend = ie.MockImageBackend()
    results = backend.generate("hello", n_value=3)
    assert len(results) == 3
    for r in results:
        assert r.n == 3


# ---------- MiniMaxImageBackend 构造校验 ----------

def test_minimax_image_no_key_raises():
    with pytest.raises(ie.ImageError, match="MINIMAX_API_KEY 未设置"):
        ie.MiniMaxImageBackend()


def test_minimax_image_short_key_raises():
    with pytest.raises(ie.ImageError, match="长度.*< 16"):
        ie.MiniMaxImageBackend(api_key_value="short")


def test_minimax_image_bad_response_format_raises():
    with pytest.raises(ie.ImageError, match="response_format"):
        ie.MiniMaxImageBackend(api_key_value="x" * 20, response_format_value="xml")


def test_minimax_image_uses_default_model():
    backend = ie.MiniMaxImageBackend(api_key_value="x" * 20)
    assert backend._model == ie.DEFAULT_IMAGE_MODEL
    assert backend._response_format == ie.DEFAULT_RESPONSE_FORMAT


# ---------- _parse_response 各种 schema ----------

def _backend() -> ie.MiniMaxImageBackend:
    return ie.MiniMaxImageBackend(
        api_key_value="x" * 20,
        response_format_value="base64",
    )


def test_parse_response_base64_at_data():
    payload = {"data": {"image_base64": [base64.b64encode(b"jpeg-bytes-1").decode("ascii")]}}
    results = _backend()._parse_response(
        payload, "p", "image-01", "16:9", 1, "base64", 0.0,
    )
    assert len(results) == 1
    assert results[0].bytes == b"jpeg-bytes-1"


def test_parse_response_base64_at_root():
    """schema 变体：`{"image_base64": [...]}` 不在 data 内。"""
    payload = {"image_base64": [base64.b64encode(b"abc").decode("ascii")]}
    results = _backend()._parse_response(
        payload, "p", "image-01", "16:9", 1, "base64", 0.0,
    )
    assert results[0].bytes == b"abc"


def test_parse_response_base64_double_data_nesting():
    """schema 变体：`{"data": {"data": {"image_base64": [...]}}}`。"""
    payload = {
        "data": {
            "data": {"image_base64": [base64.b64encode(b"deep").decode("ascii")]}
        }
    }
    results = _backend()._parse_response(
        payload, "p", "image-01", "16:9", 1, "base64", 0.0,
    )
    assert results[0].bytes == b"deep"


def test_parse_response_url_format():
    backend = ie.MiniMaxImageBackend(api_key_value="x" * 20, response_format_value="url")
    payload = {"data": {"image_url": ["https://cdn.example.com/x.jpg"]}}
    results = backend._parse_response(
        payload, "p", "image-01", "16:9", 1, "url", 0.0,
    )
    assert results[0].image_url == ["https://cdn.example.com/x.jpg"]
    assert results[0].bytes == b""  # url 模式不解码字节（不下载）


def test_parse_response_base64_bad_structure_raises():
    payload = {"data": {"wrong_key": ["x"]}}
    with pytest.raises(ie.ImageError, match="base64 响应结构不符"):
        _backend()._parse_response(
            payload, "p", "image-01", "16:9", 1, "base64", 0.0,
        )


def test_parse_response_empty_list_raises():
    payload = {"data": {"image_base64": []}}
    with pytest.raises(ie.ImageError, match="0 张"):
        _backend()._parse_response(
            payload, "p", "image-01", "16:9", 1, "base64", 0.0,
        )


def test_parse_response_n_results():
    payload = {
        "data": {
            "image_base64": [
                base64.b64encode(b"img1").decode("ascii"),
                base64.b64encode(b"img2").decode("ascii"),
                base64.b64encode(b"img3").decode("ascii"),
            ]
        }
    }
    results = _backend()._parse_response(
        payload, "p", "image-01", "16:9", 3, "base64", 0.0,
    )
    assert len(results) == 3
    assert [r.bytes for r in results] == [b"img1", b"img2", b"img3"]


def test_parse_response_mime_guessing_jpeg():
    jpeg_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF..."
    payload = {"data": {"image_base64": [base64.b64encode(jpeg_bytes).decode("ascii")]}}
    results = _backend()._parse_response(
        payload, "p", "image-01", "16:9", 1, "base64", 0.0,
    )
    assert results[0].mime_type == "image/jpeg"


def test_parse_response_mime_guessing_png():
    png_bytes = b"\x89PNG\r\n\x1a\nrest"
    payload = {"data": {"image_base64": [base64.b64encode(png_bytes).decode("ascii")]}}
    results = _backend()._parse_response(
        payload, "p", "image-01", "16:9", 1, "base64", 0.0,
    )
    assert results[0].mime_type == "image/png"


# ---------- MiniMaxImageBackend.generate with injected http_post ----------

def test_generate_with_injected_post_base64():
    sentinel_bytes = b"\xff\xd8\xff\xe0 sential"
    backend = ie.MiniMaxImageBackend(
        api_key_value="x" * 20,
        response_format_value="base64",
        http_post=lambda url, body, headers: {
            "data": {"image_base64": [base64.b64encode(sentinel_bytes).decode("ascii")]}
        },
    )
    results = backend.generate("a cat")
    assert len(results) == 1
    assert results[0].bytes == sentinel_bytes


def test_generate_with_injected_post_url():
    backend = ie.MiniMaxImageBackend(
        api_key_value="x" * 20,
        response_format_value="url",
        http_post=lambda url, body, headers: {
            "data": {"image_url": ["https://cdn.example.com/x.jpg"]}
        },
    )
    results = backend.generate("a cat")
    assert results[0].image_url == ["https://cdn.example.com/x.jpg"]


def test_generate_empty_prompt_raises():
    backend = ie.MiniMaxImageBackend(api_key_value="x" * 20)
    with pytest.raises(ie.ImageError, match="prompt 为空"):
        backend.generate("")
    with pytest.raises(ie.ImageError, match="prompt 为空"):
        backend.generate("   \n  ")


def test_generate_sends_correct_payload(monkeypatch):
    """验证请求 payload 字段 + 端点。"""
    captured: dict = {}

    def fake_post(url, body, headers):
        captured["url"] = url
        captured["body"] = body
        captured["headers"] = headers
        return {"data": {"image_base64": [base64.b64encode(b"x").decode("ascii")]}}

    backend = ie.MiniMaxImageBackend(
        api_key_value="x" * 20,
        base_url_value="https://api.minimaxi.com/v1",
        model_value="image-01",
        response_format_value="base64",
        http_post=fake_post,
    )
    import os
    os.environ["MINIMAX_IMAGE_ASPECT_RATIO"] = "4:3"
    os.environ["MINIMAX_IMAGE_N"] = "2"
    backend.generate("a cat")

    assert captured["url"] == "https://api.minimaxi.com/v1/image_generation"
    assert captured["body"]["model"] == "image-01"
    assert captured["body"]["prompt"] == "a cat"
    assert captured["body"]["aspect_ratio"] == "4:3"
    assert captured["body"]["response_format"] == "base64"
    assert captured["body"]["n"] == 2
    assert captured["headers"]["Authorization"].startswith("Bearer ")


def test_generate_http_error_raises():
    import urllib.error

    def boom(url, body, headers):
        raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)

    backend = ie.MiniMaxImageBackend(api_key_value="x" * 20, http_post=boom)
    with pytest.raises(ie.ImageError, match="HTTP 401"):
        backend.generate("a cat")


def test_generate_url_error_raises():
    import urllib.error

    def boom(url, body, headers):
        raise urllib.error.URLError("Connection refused")

    backend = ie.MiniMaxImageBackend(api_key_value="x" * 20, http_post=boom)
    with pytest.raises(ie.ImageError, match="URLError"):
        backend.generate("a cat")


def test_generate_non_dict_response_raises():
    backend = ie.MiniMaxImageBackend(
        api_key_value="x" * 20,
        http_post=lambda url, body, headers: "<html>bad gateway</html>",
    )
    with pytest.raises(ie.ImageError, match="非 dict"):
        backend.generate("a cat")


def test_generate_does_not_leak_api_key(caplog):
    backend = ie.MiniMaxImageBackend(
        api_key_value="secret-image-key-DO-NOT-LEAK-1234567890",
        response_format_value="base64",
        http_post=lambda url, body, headers: {
            "data": {"image_base64": [base64.b64encode(b"x").decode("ascii")]}
        },
    )
    with caplog.at_level("DEBUG"):
        backend.generate("a cat")
    assert "secret-image-key" not in caplog.text


# ---------- make_backend ----------

def test_make_backend_mock():
    backend = ie.make_backend("mock")
    assert isinstance(backend, ie.MockImageBackend)


def test_make_backend_minimax():
    os.environ["MINIMAX_API_KEY"] = "x" * 20
    backend = ie.make_backend("minimax")
    assert isinstance(backend, ie.MiniMaxImageBackend)


def test_make_backend_unknown_raises():
    with pytest.raises(ie.ImageError, match="未知 backend"):
        ie.make_backend("openai")


# ---------- write_image / image_extension ----------

def test_write_image_bytes(tmp_path):
    r = ie.ImageResult(bytes=b"\xff\xd8\xff jpeg", mime_type="image/jpeg")
    path = ie.write_image(r, tmp_path / "out.jpg")
    assert path.read_bytes() == b"\xff\xd8\xff jpeg"


def test_write_image_creates_dirs(tmp_path):
    r = ie.ImageResult(bytes=b"x", mime_type="image/jpeg")
    path = ie.write_image(r, tmp_path / "sub" / "out.jpg")
    assert path.read_bytes() == b"x"


def test_image_extension_lookup():
    assert ie.image_extension("image/jpeg") == ".jpg"
    assert ie.image_extension("image/png") == ".png"
    assert ie.image_extension("image/webp") == ".webp"
    assert ie.image_extension("unknown") == ".img"


# ---------- CLI ----------

from hailuo_video.cli import main as cli_main


def test_cli_image_no_key(capsys):
    rc = cli_main(["image", "a cat", "-o", "/tmp/out.jpg"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "MINIMAX_API_KEY 未设置" in err or "长度不足" in err


def test_cli_image_empty_prompt(capsys, monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 20)
    rc = cli_main(["image", "", "-o", "/tmp/out.jpg"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "prompt 为空" in err


def test_cli_image_mock_backend_success(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 20)
    out = tmp_path / "out.jpg"
    rc = cli_main(["image", "a cat", "-o", str(out), "--backend", "mock"])
    err = capsys.readouterr().err
    assert rc == 0
    assert "[OK]" in err
    assert out.exists()
    # mock 输出真 JPEG 字节
    assert out.read_bytes()[:2] == b"\xff\xd8"


def test_cli_image_mock_n_creates_directory(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 20)
    out_dir = tmp_path / "imgs"
    rc = cli_main(["image", "a cat", "-o", str(out_dir), "--backend", "mock", "--n", "3"])
    err = capsys.readouterr().err
    assert rc == 0
    files = sorted(out_dir.iterdir())
    assert len(files) == 3
    assert files[0].name == "000.jpg"
    assert files[1].name == "001.jpg"
    assert files[2].name == "002.jpg"


def test_cli_image_unknown_backend(capsys, tmp_path):
    """argparse choices 校验 → SystemExit(2)"""
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["image", "a cat", "-o", str(tmp_path / "x.jpg"), "--backend", "nonsense"])
    assert exc_info.value.code == 2


def test_cli_image_help_renders_chinese(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["image", "--help"])
    assert exc_info.value.code == 0
    out = capsys.readouterr()
    assert "prompt" in out.out
    assert "aspect_ratio" in out.out or "aspect-ratio" in out.out or "16:9" in out.out