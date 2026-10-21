"""REQ-V2.6 quality 单测。不依赖 MiniMax，纯 PIL/numpy。"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from hailuo_video import quality  # noqa: E402
from hailuo_video import script_engine as se  # noqa: E402


def _make_image_bytes(rgb: tuple[int, int, int], size: int = 64) -> bytes:
    """构造一张指定 RGB 的小 PNG（懒 PIL）。
    没 PIL 时返回一个 dummy bytes（很多测试不依赖内容，只看 hist）。
    """
    try:
        from PIL import Image
        img = Image.new("RGB", (size, size), rgb)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()
    except ImportError:
        return b"dummy" * 100


def _save_image(path: Path, rgb: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image
        img = Image.new("RGB", (64, 64), rgb)
        img.save(path, "PNG")
    except ImportError:
        path.write_bytes(_make_image_bytes(rgb))


# ---------- validate_storyboard ----------

def _mk_sb(*, with_char: bool = True, with_scenes: bool = True, n_shots: int = 1) -> se.Storyboard:
    chars = []
    if with_char:
        chars = [se.Character(name="农夫", appearance="宋代中年农夫，圆脸黑肤浓眉", personality=[], color_palette=["灰褐"])]
    scenes = []
    if with_scenes:
        # Storyboard 用 SceneSpec（reference 用途）
        scenes = [se.SceneSpec(id=1, location="田野", atmosphere="清晨", key_elements=["远山"])]
    shots = [
        se.Shot(
            index=i + 1, scene_id=1, shot_type="远景",
            description=f"镜头{i+1}描述", characters=["农夫"] if with_char else [],
            dialogue="", voiceover="", mood=["平和"], camera_motion="", time="清晨",
        )
        for i in range(n_shots)
    ]
    return se.Storyboard(
        title="t", characters=chars, scenes=scenes, shots=shots,
        style_anchor="水墨",
    )


def test_validate_empty_characters_returns_error():
    sb = se.Storyboard(title="t", shots=[_mk_sb().shots[0]], characters=[], scenes=[])
    rep = quality.validate_storyboard(sb)
    assert any(i.code == "no_characters" and i.level == "error" for i in rep.issues)


def test_validate_empty_shots_returns_error():
    sb = se.Storyboard(title="t", characters=[], shots=[], scenes=[])
    rep = quality.validate_storyboard(sb)
    assert any(i.code == "no_shots" for i in rep.issues)


def test_validate_missing_style_anchor_warns():
    sb = _mk_sb()
    sb.style_anchor = ""
    rep = quality.validate_storyboard(sb)
    assert any(i.code == "no_style_anchor" and i.level == "warning" for i in rep.issues)


def test_validate_undefined_character_reference_errors():
    sb = _mk_sb()
    sb.shots[0].characters = ["不存在的角色"]
    rep = quality.validate_storyboard(sb)
    assert any(i.code == "character_undefined" for i in rep.issues)


def test_validate_undefined_scene_id_warns():
    sb = _mk_sb()
    sb.shots[0].scene_id = 999
    rep = quality.validate_storyboard(sb)
    assert any(i.code == "scene_undefined" for i in rep.issues)


def test_validate_unknown_shot_type_warns():
    sb = _mk_sb()
    sb.shots[0].shot_type = "NonsenseShotType"
    rep = quality.validate_storyboard(sb)
    assert any(i.code == "shot_type_unknown" for i in rep.issues)


def test_validate_unknown_mood_warns():
    sb = _mk_sb()
    sb.shots[0].mood = ["平和", "完全不存在的mood"]
    rep = quality.validate_storyboard(sb)
    assert any(i.code == "mood_unknown" for i in rep.issues)


def test_validate_unknown_time_warns():
    sb = _mk_sb()
    sb.shots[0].time = "完全不存在的time"
    rep = quality.validate_storyboard(sb)
    assert any(i.code == "time_unknown" for i in rep.issues)


def test_validate_appearance_too_short_warns():
    sb = _mk_sb()
    sb.characters[0].appearance = "短"  # < 20 字
    rep = quality.validate_storyboard(sb)
    assert any(i.code == "appearance_too_short" for i in rep.issues)


def test_validate_full_storyboard_ok():
    sb = _mk_sb(n_shots=3)
    rep = quality.validate_storyboard(sb)
    # 无 error 即认为是关键
    assert rep.errors == []


def test_validate_report_format_renders():
    sb = _mk_sb()
    sb.shots[0].shot_type = "xxx"
    rep = quality.validate_storyboard(sb)
    out = rep.format()
    # warnings 输出可能用 ⚠ 而非 [WARN]；宽松匹配
    assert "xxx" in out or "shot_type" in out
    assert rep.warnings  # 应至少有一个 warning


# ---------- check_assets ----------

def test_check_assets_missing_everything(tmp_path):
    sb = _mk_sb()
    rep = quality.check_assets(sb, tmp_path)
    assert not rep.ok
    # 1 char × 7 sheets (4 angles + 3 expressions) + 1 scene × 3 variants = 10
    assert len(rep.all_missing) == 10


def test_check_assets_all_present(tmp_path):
    sb = _mk_sb()
    # 先建所有 sheets
    (tmp_path / "characters" / "农夫").mkdir(parents=True, exist_ok=True)
    (tmp_path / "scenes").mkdir(parents=True, exist_ok=True)
    for angle in quality.ap.DEFAULT_ANGLES:
        (tmp_path / "characters" / "农夫" / f"{angle}.jpg").write_bytes(b"x")
        (tmp_path / "characters" / "农夫" / f"{angle}.url.txt").write_text("u")
    for expr in quality.ap.DEFAULT_EXPRESSIONS:
        (tmp_path / "characters" / "农夫" / f"expression_{expr}.jpg").write_bytes(b"x")
        (tmp_path / "characters" / "农夫" / f"expression_{expr}.url.txt").write_text("u")
    for var in quality.ap.DEFAULT_SCENE_VARIANTS:
        (tmp_path / "scenes" / f"scene_0001_{var}.jpg").write_bytes(b"x")
        (tmp_path / "scenes" / f"scene_0001_{var}.url.txt").write_text("u")

    rep = quality.check_assets(sb, tmp_path)
    assert rep.ok
    assert rep.all_missing == []


def test_check_assets_format_renders(tmp_path):
    sb = _mk_sb()
    rep = quality.check_assets(sb, tmp_path)
    out = rep.format()
    assert "missing" in out.lower() or "ok" in out.lower()


# ---------- compute_color_consistency ----------

def test_consistency_two_identical_images(tmp_path):
    """完全相同的两张图 → 相似度 ≈ 1.0"""
    p1 = tmp_path / "shot_001.jpg"
    p2 = tmp_path / "shot_002.jpg"
    _save_image(p1, (255, 0, 0))
    _save_image(p2, (255, 0, 0))

    rep = quality.compute_color_consistency([p1, p2])
    assert rep.mean_similarity > 0.95
    assert rep.flagged_pairs == []


def test_consistency_very_different_images(tmp_path):
    """完全不同色的两张图 → 相似度 < 阈值 → flag"""
    p1 = tmp_path / "shot_001.jpg"
    p2 = tmp_path / "shot_002.jpg"
    _save_image(p1, (255, 0, 0))    # 全红
    _save_image(p2, (0, 0, 255))    # 全蓝

    rep = quality.compute_color_consistency([p1, p2])
    assert rep.mean_similarity < 0.5
    assert len(rep.flagged_pairs) == 1


def test_consistency_too_few_images(tmp_path):
    p1 = tmp_path / "shot_001.jpg"
    _save_image(p1, (100, 100, 100))
    rep = quality.compute_color_consistency([p1])
    assert rep.warnings  # 警告 < 2 张


def test_consistency_format_renders(tmp_path):
    p1 = tmp_path / "shot_001.jpg"
    p2 = tmp_path / "shot_002.jpg"
    _save_image(p1, (255, 0, 0))
    _save_image(p2, (0, 0, 255))
    rep = quality.compute_color_consistency([p1, p2])
    out = rep.format()
    assert "色差相似度" in out


def test_cosine_sim_zero_vectors():
    """两边都是 0 向量 → 返回 0（不抛异常）"""
    assert quality._cosine_sim([0.0] * 5, [0.0] * 5) == 0.0


def test_cosine_sim_orthogonal():
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert quality._cosine_sim(a, b) == 0.0


def test_cosine_sim_identical():
    a = [0.5, 0.3, 0.2]
    b = [0.5, 0.3, 0.2]
    assert abs(quality._cosine_sim(a, b) - 1.0) < 1e-9


def test_normalize_hist_empty():
    assert quality._normalize_hist([]) == []


# ---------- warn_picker_fallback ----------

def test_warn_picker_fallback_when_different(caplog):
    """quality 警告被 caplog 捕获。

    quality._qa_logger.propagate=False 阻止 caplog 接收；临时开启 propagate。
    """
    import logging
    target = logging.getLogger("hailuo_video.quality")
    original_propagate = target.propagate
    target.propagate = True
    try:
        caplog.set_level("WARNING", logger="hailuo_video.quality")
        quality.warn_picker_fallback("angle", 1, "side_left", "back")
        msgs = [r.getMessage() for r in caplog.records]
        assert any("shot 1 angle" in m and "side_left" in m and "back" in m for m in msgs), \
            f"warnings captured: {msgs}"
    finally:
        target.propagate = original_propagate


def test_warn_picker_fallback_when_same(caplog):
    """requested == used → 不告警"""
    import logging
    target = logging.getLogger("hailuo_video.quality")
    original_propagate = target.propagate
    target.propagate = True
    try:
        caplog.set_level("WARNING", logger="hailuo_video.quality")
        quality.warn_picker_fallback("angle", 1, "back", "back")
        msgs = [r.getMessage() for r in caplog.records]
        assert not any("shot 1 angle" in m and "side_left" in m for m in msgs)
    finally:
        target.propagate = original_propagate


# ---------- Issue dataclass ----------

def test_issue_format_basic():
    i = quality.Issue("error", "test_code", "test detail", "field.path")
    s = i.format()
    assert "test_code" in s
    assert "test detail" in s
    assert "field.path" in s


def test_validation_report_ok_property():
    r = quality.ValidationReport()
    assert r.ok  # 无 error → ok
    r.issues.append(quality.Issue("error", "x", "y"))
    assert not r.ok


def test_asset_report_ok_property():
    r = quality.AssetReport()
    assert r.ok
    c = quality.AssetCheck(character="x")
    c.missing_angles = ["front"]
    r.characters.append(c)
    assert not r.ok
    assert len(r.all_missing) == 1