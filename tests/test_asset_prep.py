"""REQ-V2.5 asset_prep 单测。Mock http_post 不依赖真 API。"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from hailuo_video import asset_prep as ap  # noqa: E402
from hailuo_video import image_engine as ie  # noqa: E402
from hailuo_video import script_engine as se  # noqa: E402


# ---------- 工具 ----------

def _make_image_backend_with_url(url: str = "https://fake.example.com/char.jpg") -> ie.MockImageBackend:
    """造一个返回 url 模式 bytes（fake bytes + 1 个 url）的 backend。"""
    backend = ie.MockImageBackend()
    # 改写 generate 返回 url 而不是 base64
    fake_bytes = b"\xff\xd8\xff fake jpeg 1KB" * 50  # ~600 bytes

    def fake_generate(prompt, **opts):
        return [ie.ImageResult(
            bytes=fake_bytes,
            mime_type="image/jpeg",
            prompt=prompt,
            model="image-01",
            aspect_ratio="1:1",
            n=1,
            duration_seconds=0.0,
            image_url=[url],
        )]
    backend.generate = fake_generate  # type: ignore
    return backend


def _mock_urlopen_factory(jpeg_bytes: bytes):
    """mock urllib.request.urlopen 返回假 jpeg bytes。"""
    class FakeResp:
        def __init__(self):
            pass
        def read(self):
            return jpeg_bytes
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
    def fake_urlopen(url, timeout=None):
        return FakeResp()
    return fake_urlopen


# ---------- 路径工具 ----------

def test_safe_filename_basic():
    assert ap._safe_filename("农夫") == "农夫"
    assert ap._safe_filename("A/B C") == "A_B_C"
    assert ap._safe_filename("") == "unnamed"


def test_character_path_in_characters_dir(tmp_path):
    """V2.5：每角色有独立子目录。"""
    p = ap.character_angle_path("农夫", "front", assets_dir=tmp_path)
    assert p.parent.name == "农夫"
    assert p.parent.parent == tmp_path / "characters"
    assert p.name == "front.jpg"


def test_character_url_path(tmp_path):
    """V2.5：URL path 按 angle 拆分。"""
    p = ap.character_angle_url_path("兔子", "side", assets_dir=tmp_path)
    assert p.parent.name == "兔子"
    assert p.name == "side.url.txt"


def test_has_cached_character_false_when_missing(tmp_path):
    """V2.5：检查 3 个角度都齐全才算 cache。"""
    assert ap.has_cached_character_all_angles("农夫", assets_dir=tmp_path) is False


def test_has_cached_character_true_when_all_angles_present(tmp_path):
    """V2.5：3 角度齐全才算 cache hit。"""
    sub = ap.character_subdir("农夫", tmp_path)
    sub.mkdir(parents=True)
    for a in ap.DEFAULT_ANGLES:
        ap.character_angle_path("农夫", a, tmp_path).write_bytes(b"\xff\xd8 fake")
        ap.character_angle_url_path("农夫", a, tmp_path).write_text(f"https://e/{a}")
    assert ap.has_cached_character_all_angles("农夫", tmp_path) is True


def test_has_cached_character_partial_returns_false(tmp_path):
    """只 2 角度齐全 → 不算 cache（确保下次 batch 重新生成缺的）。"""
    sub = ap.character_subdir("农夫", tmp_path)
    sub.mkdir(parents=True)
    for a in ("front", "side"):
        ap.character_angle_path("农夫", a, tmp_path).write_bytes(b"\xff\xd8 fake")
        ap.character_angle_url_path("农夫", a, tmp_path).write_text(f"https://e/{a}")
    assert ap.has_cached_character_all_angles("农夫", tmp_path) is False


def test_pick_angle_for_shot_basic():
    """shot_type → preferred angle 映射。"""
    # 远景 / 拉远 → 背面
    assert ap.pick_angle_for_shot("远景") == "back"
    assert ap.pick_angle_for_shot("拉远") == "back"
    # 中景 / 过肩 / 跟随 → 侧面（左，默认）
    assert ap.pick_angle_for_shot("中景") == "side_left"
    assert ap.pick_angle_for_shot("过肩") == "side_left"
    assert ap.pick_angle_for_shot("跟随") == "side_left"
    # 近景 / 特写 → 正面
    assert ap.pick_angle_for_shot("近景") == "front"
    assert ap.pick_angle_for_shot("特写") == "front"
    # 未知 → fallback 到 available_angles[0]
    assert ap.pick_angle_for_shot("NonsenseShotType") == "front"


def test_pick_angle_respects_available_angles():
    """首选角度不在 available_angles → 回退到 available_angles[0]。"""
    # shot_type=远景 想要 back，但只有 front/side_left → 回退 front
    assert ap.pick_angle_for_shot("远景", available_angles=("front", "side_left")) == "front"


# V2.5.2 新增：camera_motion 智能升级
def test_pick_angle_for_motion_basic():
    """camera_motion → angle（含 side_left/right 特化）。"""
    # pan_left → 看角色右 → 用 side_right
    assert ap.pick_angle_for_motion("pan_left") == "side_right"
    assert ap.pick_angle_for_motion("左摇") == "side_right"
    # pan_right → 看角色左 → 用 side_left
    assert ap.pick_angle_for_motion("pan_right") == "side_left"
    assert ap.pick_angle_for_motion("右摇") == "side_left"
    # 推近 / 拉远 / 俯拍 / 仰拍
    assert ap.pick_angle_for_motion("推近") == "front"
    assert ap.pick_angle_for_motion("zoom_in") == "front"
    assert ap.pick_angle_for_motion("拉远") == "back"
    assert ap.pick_angle_for_motion("俯拍") == "back"
    assert ap.pick_angle_for_motion("仰拍") == "front"


def test_pick_angle_for_motion_returns_none_for_unknown():
    assert ap.pick_angle_for_motion("") is None
    assert ap.pick_angle_for_motion("xyz") is None


def test_pick_angle_for_shot_motion_overrides_shot_type():
    """V2.5.2：camera_motion 优先于 shot_type。"""
    # 中景 shot → 默认 side_left；但 pan_left motion → 改用 side_right
    assert ap.pick_angle_for_shot("中景", camera_motion="pan_left") == "side_right"
    assert ap.pick_angle_for_shot("中景", camera_motion="pan_right") == "side_left"
    # 远景 → 默认 back；但 push_in motion → 改 front
    assert ap.pick_angle_for_shot("远景", camera_motion="推近") == "front"


def test_pick_angle_for_shot_motion_falls_back_when_angle_missing():
    """camera_motion 要求 side_right 但缓存只有 front + side_left → 回退。"""
    # pan_left 要 side_right，但 available 没 → 退回 shot_type 默认（中景=side_left）
    assert ap.pick_angle_for_shot(
        "中景", camera_motion="pan_left",
        available_angles=("front", "side_left"),
    ) == "side_left"


def test_build_angle_url_lookup_filters_failures():
    """只有 ok 的角度进入 lookup。"""
    ok_front = ap.AssetResult(name="A/front", image_path="/x", source="ok", url="u1")
    err_side = ap.AssetResult(name="A/side", image_path="/x", source="error", error="bad")
    ok_back = ap.AssetResult(name="A/back", image_path="/x", source="cached", url="u3")
    out = ap.build_angle_url_lookup({"A": {"front": ok_front, "side": err_side, "back": ok_back}})
    assert out == {"A": {"front": "u1", "back": "u3"}}


# AssetResult dataclass tests still apply (unchanged)


# ---------- AssetResult 数据类 ----------

def test_asset_result_ok_property():
    a = ap.AssetResult(name="x", image_path="/tmp/x.jpg", source="ok")
    assert a.ok is True
    b = ap.AssetResult(name="x", image_path="/tmp/x.jpg", source="cached")
    assert b.ok is True
    c = ap.AssetResult(name="x", image_path="/tmp/x.jpg", source="error", error="bad")
    assert c.ok is False


def test_asset_result_to_dict():
    a = ap.AssetResult(name="x", image_path="/tmp/x.jpg", url="https://e.com")
    d = a.to_dict()
    assert d["name"] == "x"
    assert d["image_path"] == "/tmp/x.jpg"


# ---------- _extract_url_expiry ----------

def test_extract_url_expiry_from_oss_url():
    """OSS URL 含 Expires=N&Signature=... 参数。返回 ISO 时间。"""
    from datetime import datetime, timezone
    url = "https://example.com/x.jpg?Expires=1790034777&Signature=abc"
    result = ap._extract_url_expiry(url)
    assert result != ""
    # 1790034777 → 2026-08-14
    assert result.startswith("2026")


def test_extract_url_expiry_returns_empty_for_no_expires():
    assert ap._extract_url_expiry("https://example.com/no-query") == ""


# ---------- generate_character_sheet (V2.5 multi-angle) ----------

def test_generate_character_sheet_success(tmp_path, monkeypatch):
    """V2.5：3 角度都生成，写入 {name}/{angle}.jpg + .url.txt。"""
    fake_jpeg = b"\xff\xd8\xff fake jpeg content"
    monkeypatch.setattr("urllib.request.urlopen", _mock_urlopen_factory(fake_jpeg))

    test_url = "https://fake.example.com/char.jpg"
    backend = _make_image_backend_with_url(test_url)
    ch = se.Character(name="农夫", appearance="宋代农夫", personality=[], color_palette=["灰褐"])

    result = ap.generate_character_sheet(ch, backend, assets_dir=tmp_path)

    # 返回 {angle: AssetResult}
    assert isinstance(result, dict)
    for angle in ap.DEFAULT_ANGLES:
        assert angle in result, f"missing angle {angle}"
        assert result[angle].ok
        assert result[angle].source == "ok"
    # 文件应该都被写入
    for angle in ap.DEFAULT_ANGLES:
        p = ap.character_angle_path("农夫", angle, tmp_path)
        assert p.exists(), f"{angle}.jpg not written"
        assert p.read_bytes() == fake_jpeg
        url_p = ap.character_angle_url_path("农夫", angle, tmp_path)
        assert "fake.example.com" in url_p.read_text()


def test_generate_character_sheet_uses_cache(tmp_path, monkeypatch):
    """V2.5：所有角度都缓存 → 不调 API。"""
    sub = ap.character_subdir("农夫", tmp_path)
    sub.mkdir(parents=True)
    for a in ap.DEFAULT_ANGLES:
        ap.character_angle_path("农夫", a, tmp_path).write_bytes(b"cached jpeg")
        ap.character_angle_url_path("农夫", a, tmp_path).write_text(f"https://cached/{a}")

    class FailingBackend(ie.ImageBackend):
        def generate(self, prompt, **opts):
            raise AssertionError("cache hit all → 不应调 API")

    ch = se.Character(name="农夫", appearance="x", personality=[], color_palette=[])
    result = ap.generate_character_sheet(ch, FailingBackend(), assets_dir=tmp_path)
    assert all(r.source == "cached" for r in result.values())


def test_generate_character_sheet_force_regenerate(tmp_path, monkeypatch):
    """--force → 忽略缓存，3 角度全部重生成。"""
    fake_jpeg = b"\xff\xd8\xff fresh jpeg"
    monkeypatch.setattr("urllib.request.urlopen", _mock_urlopen_factory(fake_jpeg))

    sub = ap.character_subdir("x", tmp_path)
    sub.mkdir(parents=True)
    for a in ap.DEFAULT_ANGLES:
        ap.character_angle_path("x", a, tmp_path).write_bytes(b"old cached")
        ap.character_angle_url_path("x", a, tmp_path).write_text("https://cached")

    backend = _make_image_backend_with_url("https://new.example.com/char.jpg")
    ch = se.Character(name="x", appearance="x", personality=[], color_palette=[])
    result = ap.generate_character_sheet(ch, backend, assets_dir=tmp_path, force_regenerate=True)
    for angle, r in result.items():
        assert r.source == "ok"
        assert ap.character_angle_path("x", angle, tmp_path).read_bytes() == fake_jpeg


def test_generate_character_sheet_partial_cache_regenerates_missing(tmp_path, monkeypatch):
    """V2.5.2：partial cache → 只重生成缺的（4 角度）。"""
    fake_jpeg = b"\xff\xd8\xff fresh"
    monkeypatch.setattr("urllib.request.urlopen", _mock_urlopen_factory(fake_jpeg))

    sub = ap.character_subdir("x", tmp_path)
    sub.mkdir(parents=True)
    # 只缓存 front + side_left（V2.5.2 起 4 角度，side_left/right 分离）
    for a in ("front", "side_left"):
        ap.character_angle_path("x", a, tmp_path).write_bytes(b"cached " + a.encode())
        ap.character_angle_url_path("x", a, tmp_path).write_text(f"https://cached/{a}")

    backend = _make_image_backend_with_url("https://new/char.jpg")
    ch = se.Character(name="x", appearance="x", personality=[], color_palette=[])
    result = ap.generate_character_sheet(ch, backend, assets_dir=tmp_path)
    # front + side_left 命中缓存
    assert result["front"].source == "cached"
    assert result["side_left"].source == "cached"
    # side_right + back 重新生成
    assert result["side_right"].source == "ok"
    assert result["back"].source == "ok"
    assert ap.character_angle_path("x", "side_right", tmp_path).read_bytes() == fake_jpeg


def test_generate_character_sheet_api_error_returns_error_results(tmp_path):
    """V2.5：backend 抛 ImageError → 3 角度全部返回 source=error（不抛）。"""

    class FailingBackend(ie.ImageBackend):
        def generate(self, prompt, **opts):
            raise ie.ImageError("simulated api failure")

    ch = se.Character(name="x", appearance="x", personality=[], color_palette=[])
    result = ap.generate_character_sheet(ch, FailingBackend(), assets_dir=tmp_path)
    for angle, r in result.items():
        assert not r.ok
        assert "simulated api failure" in r.error


def test_generate_character_sheet_url_download_failure(tmp_path, monkeypatch):
    """V2.5：url 下载失败 → 3 角度都返回 error。"""
    def boom(url, timeout=None):
        raise OSError("simulated download failure")
    monkeypatch.setattr("urllib.request.urlopen", boom)

    backend = _make_image_backend_with_url("https://fake.example.com/x.jpg")
    ch = se.Character(name="x", appearance="x", personality=[], color_palette=[])
    result = ap.generate_character_sheet(ch, backend, assets_dir=tmp_path)
    for angle, r in result.items():
        assert not r.ok
        assert "下载 url 失败" in r.error


def test_generate_character_sheet_no_url_in_response(tmp_path):
    """V2.5：response 没返回 image_url → 3 角度都返回 error result。"""
    backend = ie.MockImageBackend()

    def fake_generate(prompt, **opts):
        return [ie.ImageResult(
            bytes=b"x", mime_type="image/jpeg", prompt=prompt, model="image-01",
            aspect_ratio="1:1", n=1, duration_seconds=0.0,
            image_url=[],   # 空 url
        )]
    backend.generate = fake_generate

    ch = se.Character(name="x", appearance="x", personality=[], color_palette=[])
    result = ap.generate_character_sheet(ch, backend, assets_dir=tmp_path)
    for angle, r in result.items():
        assert not r.ok
        assert "image_url" in r.error


# ---------- prepare_all_characters ----------

def test_prepare_all_characters_empty_no_op(tmp_path):
    """storyboard.characters 空 → 返回 {} 不报错。"""
    backend = _make_image_backend_with_url()
    sb = se.Storyboard(title="t", shots=[], characters=[])
    result = ap.prepare_all_characters(sb, backend, assets_dir=tmp_path)
    assert result == {}


def test_prepare_all_characters_dedupes(tmp_path, monkeypatch):
    """V2.5：同名角色只生成一次（多次定义 → 一次 prep）。"""
    monkeypatch.setattr("urllib.request.urlopen", _mock_urlopen_factory(b"\xff\xd8 fake"))
    backend = _make_image_backend_with_url("https://e.com/x.jpg")
    sb = se.Storyboard(
        title="t", shots=[],
        characters=[
            se.Character(name="农夫", appearance="宋代", personality=[], color_palette=[]),
            se.Character(name="农夫", appearance="重复", personality=[], color_palette=[]),
            se.Character(name="兔子", appearance="灰", personality=[], color_palette=[]),
        ],
    )
    result = ap.prepare_all_characters(sb, backend, assets_dir=tmp_path)
    assert len(result) == 2   # 农夫 + 兔子
    assert "农夫" in result
    assert "兔子" in result
    # 每个角色应有 3 角度（除非全部 cache）
    for name, angles in result.items():
        assert isinstance(angles, dict)
        assert len(angles) == len(ap.DEFAULT_ANGLES)


# ---------- build_character_reference_lookup ----------

def test_build_reference_lookup_filters_failures():
    ok1 = ap.AssetResult(name="A", image_path="/x", source="ok", url="u1")
    ok2 = ap.AssetResult(name="B", image_path="/x", source="cached", url="u2")
    err = ap.AssetResult(name="C", image_path="/x", source="error", error="bad")
    no_url = ap.AssetResult(name="D", image_path="/x", source="ok")  # url 为空

    out = ap.build_character_reference_lookup({"A": ok1, "B": ok2, "C": err, "D": no_url})
    assert out == {"A": "u1", "B": "u2"}


# ---------- pipeline 集成：subject_references 字段名 ----------

def test_minimax_generate_passes_subject_references_plural(tmp_path, monkeypatch):
    """V2.5 fix：字段名是 `subject_references`（复数），不是 `subject_reference`。"""
    captured = {}

    def fake_http_post(url, body, headers):
        captured.update(body=body, url=url)
        return {"data": {"image_base64": [base64.b64encode(b"\xff\xd8 fake").decode()]}}

    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 20)
    backend = ie.MiniMaxImageBackend(
        api_key_value="x" * 20,
        response_format_value="base64",
        http_post=fake_http_post,
    )
    backend.generate(
        "test prompt",
        aspect_ratio_value="16:9",
        subject_reference="https://fake.example.com/char.jpg",
    )
    assert "subject_references" in captured["body"], f"字段应是 subject_references 复数；got: {list(captured['body'].keys())}"
    assert "subject_reference" not in captured["body"], f"单数字段不应存在"
    assert captured["body"]["subject_references"] == ["https://fake.example.com/char.jpg"]
    assert captured["body"]["prompt_optimizer"] is False


def test_minimax_generate_no_subject_reference_when_none(tmp_path, monkeypatch):
    captured = {}

    def fake_http_post(url, body, headers):
        captured.update(body=body)
        return {"data": {"image_base64": [base64.b64encode(b"\xff\xd8 fake").decode()]}}

    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 20)
    backend = ie.MiniMaxImageBackend(
        api_key_value="x" * 20,
        response_format_value="base64",
        http_post=fake_http_post,
    )
    backend.generate("test", subject_reference=None)
    assert "subject_references" not in captured["body"]
    assert "prompt_optimizer" not in captured["body"]