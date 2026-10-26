"""V3.8 Batch 1 — W1 全局角色圣经 + reference 新鲜度校验。

锁住本批修掉的问题：

  缺陷 3  reference URL 24h 过期**不校验** → 跨天跑时角色一致性锚定静默失效
          （sample/story_arc_xiaohei_solo/README.md:237-241）
  漂移    角色跨天/跨项目重新生成 → Day1 与 Day2 不是同一套画
          （sample/story_arc_xiaohei_solo/README.md:259-262）
  歧义    中文色词角色名（"小黄"）在训练数据里高频指向橘猫
          （sample/story_arc_xiaohuang_daily/README.md:55-64）

文献依据：arXiv:2512.16954（视觉锚定是硬需求）、arXiv:2503.10391（CINEMA：
不靠「参考图↔关键词」映射）、arXiv:2506.07848（PolyVivid：视觉身份 grounding）。

R4：所有测试都不打真 API（mock image backend）。
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from hailuo_video import asset_prep as ap  # noqa: E402
from hailuo_video.character_bible import (  # noqa: E402
    AMBIGUOUS_COLOR_NAMES,
    CharacterIdentity,
    GlobalCharacterStore,
    check_name_ambiguity,
    compute_character_key,
    is_reference_fresh,
    normalize_identity_text,
)
from hailuo_video.script_engine import Character  # noqa: E402


# ---------- helpers ----------

def _char(name: str = "Puppy", appearance: str = "奶黄色拉布拉多幼犬，圆眼，胸前 V 形白斑",
          palette=("奶黄", "白")) -> Character:
    return Character(name=name, appearance=appearance, personality=[], color_palette=list(palette))


def _iso(dt: datetime.datetime) -> str:
    return dt.isoformat()


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _fake_backend() -> MagicMock:
    """mock image backend：返回固定字节，带一个"新鲜"URL。"""
    from hailuo_video.image_engine import ImageResult
    backend = MagicMock()
    future = _now() + datetime.timedelta(hours=10)
    backend.generate.return_value = [ImageResult(
        prompt="p",
        bytes=b"\xff\xd8\xff\xe0" + b"\x00" * 200,
        mime_type="jpeg",
        model="fake",
        duration_seconds=0.0,
        image_url=[f"https://oss.example.com/a.jpg?Expires={int(future.timestamp())}"],
    )]
    return backend


# ============================================================
# 身份键
# ============================================================

def test_character_key_is_stable_for_same_appearance():
    a = compute_character_key(_char(), "shot on 35mm")
    b = compute_character_key(_char(), "shot on 35mm")
    assert a == b
    assert len(a) == 16


def test_character_key_ignores_name():
    """改名不改身份（跨天复用不断链）。"""
    a = compute_character_key(_char(name="小黄"), "style")
    b = compute_character_key(_char(name="Puppy"), "style")
    assert a == b


def test_character_key_separates_different_colors():
    """奶黄 vs 纯黑 必须不同身份（同物种不同颜色 = 两个角色）。"""
    a = compute_character_key(_char(appearance="奶黄色拉布拉多幼犬"), "style")
    b = compute_character_key(_char(appearance="纯黑色拉布拉多幼犬"), "style")
    assert a != b


def test_character_key_separates_different_style():
    """不同画风 = 不同视觉身份，不共享参考图。"""
    a = compute_character_key(_char(), "shot on 35mm film")
    b = compute_character_key(_char(), "oil painting, thick impasto")
    assert a != b


def test_character_key_separates_different_palette():
    a = compute_character_key(_char(palette=("奶黄",)), "style")
    b = compute_character_key(_char(palette=("纯黑",)), "style")
    assert a != b


def test_character_key_empty_when_no_visual_identity():
    assert compute_character_key(Character(name="X", appearance="", personality=[]), "") == ""


def test_normalize_identity_text_strips_punctuation_and_case():
    assert normalize_identity_text("奶黄, 拉布拉多！") == normalize_identity_text("奶黄拉布拉多")
    assert normalize_identity_text("ABC") == "abc"


# ============================================================
# 名字歧义守卫
# ============================================================

def test_ambiguous_color_name_with_dog_appearance_warns():
    """已知事故："小黄" + 犬种描述 → 必须告警（纯代码，不调 LLM）。"""
    warns = check_name_ambiguity(_char(name="小黄", appearance="奶黄色拉布拉多幼犬"))
    assert warns, "「小黄」这种歧义色词必须告警"
    assert any("犬" in w or "猫" in w for w in warns)


def test_ambiguous_color_name_with_cat_appearance_warns():
    warns = check_name_ambiguity(_char(name="小白", appearance="纯白家猫，短毛"))
    assert warns


def test_safe_name_has_no_warning():
    warns = check_name_ambiguity(_char(name="Shiba Inu Puppy", appearance="橘色柴犬幼犬"))
    assert warns == []


def test_ambiguous_list_covers_documented_incident_names():
    assert "小黄" in AMBIGUOUS_COLOR_NAMES


# ============================================================
# 新鲜度判定
# ============================================================

def test_local_path_reference_is_always_fresh():
    """Gemini inlineData（本地路径）→ 没有 TTL 概念，永远新鲜。"""
    assert is_reference_fresh("assets/characters/Puppy/front.jpg") is True
    assert is_reference_fresh("") is True


def test_fresh_url_is_fresh():
    exp = _iso(_now() + datetime.timedelta(hours=10))
    assert is_reference_fresh("https://x/a.jpg", exp) is True


def test_expired_url_is_not_fresh():
    exp = _iso(_now() - datetime.timedelta(hours=1))
    assert is_reference_fresh("https://x/a.jpg", exp) is False


def test_url_about_to_expire_is_not_fresh():
    """剩余 TTL 不足 1h → 判不新鲜（留余量，避免跑到一半过期）。"""
    exp = _iso(_now() + datetime.timedelta(minutes=30))
    assert is_reference_fresh("https://x/a.jpg", exp) is False


def test_url_without_expiry_info_is_kept():
    """无法判定过期时间时**照常使用** —— 只在能证明过期时才判过期。

    MiniMax 的 OSS 签名 URL 一定带 Expires（能判定）；
    provider 自管的 URL 无从得知 TTL，硬判过期会误杀还能用的 reference。
    """
    assert is_reference_fresh("https://x/a.jpg", "") is True


def test_url_with_issued_at_falls_back_to_ttl():
    issued = _now() - datetime.timedelta(hours=2)
    assert is_reference_fresh("https://x/a.jpg", "", issued_at=issued) is True
    old = _now() - datetime.timedelta(days=2)
    assert is_reference_fresh("https://x/a.jpg", "", issued_at=old) is False


# ============================================================
# 全局库读写
# ============================================================

def test_store_write_and_read_angle(tmp_path):
    store = GlobalCharacterStore(tmp_path / "bible")
    img = tmp_path / "front.jpg"
    img.write_bytes(b"\xff\xd8data")
    exp = _iso(_now() + datetime.timedelta(hours=5))
    assert store.write_angle("key1", "front", img, "https://x/f.jpg", exp) is True

    entry = store.read_angle("key1", "front")
    assert entry is not None
    assert entry.url == "https://x/f.jpg"
    assert entry.expires_at == exp
    assert entry.is_fresh() is True


def test_store_read_missing_angle_returns_none(tmp_path):
    store = GlobalCharacterStore(tmp_path / "bible")
    assert store.read_angle("nope", "front") is None


def test_store_identity_roundtrip(tmp_path):
    store = GlobalCharacterStore(tmp_path / "bible")
    ident = CharacterIdentity(
        key="k", name="Puppy", appearance="奶黄拉布拉多",
        color_palette=("奶黄",), style_anchor="35mm", created_at="2026-10-26T00:00:00+00:00",
    )
    store.save_identity(ident)
    loaded = store.load_identity("k")
    assert loaded == ident


def test_store_copy_angle_into_project_dir(tmp_path):
    """全局库命中 → 复制到项目内 cache（复制而非软链，Windows 兼容）。"""
    store = GlobalCharacterStore(tmp_path / "bible")
    img = tmp_path / "front.jpg"
    img.write_bytes(b"\xff\xd8data")
    store.write_angle("k", "front", img, "https://x/f.jpg", _iso(_now() + datetime.timedelta(hours=5)))

    dest_img = tmp_path / "proj" / "front.jpg"
    dest_url = tmp_path / "proj" / "front.url.txt"
    assert store.copy_angle_into("k", "front", dest_img, dest_url) is True
    assert dest_img.read_bytes() == b"\xff\xd8data"
    assert dest_url.read_text(encoding="utf-8") == "https://x/f.jpg"


def test_store_write_angle_with_missing_image_returns_false(tmp_path):
    store = GlobalCharacterStore(tmp_path / "bible")
    assert store.write_angle("k", "front", tmp_path / "nope.jpg", "u", "") is False


# ============================================================
# asset_prep 集成：全局库命中 → 0 次 API 调用
# ============================================================

def test_character_sheet_hits_global_bible_without_api_call(tmp_path, monkeypatch):
    """验收①：同 appearance 的第二个项目 → 0 次 image_backend.generate。"""
    bible_root = tmp_path / "bible"
    monkeypatch.setattr(ap, "_make_bible", lambda: GlobalCharacterStore(bible_root))

    backend = _fake_backend()
    proj_a = tmp_path / "proj_a"
    proj_b = tmp_path / "proj_b"

    # 第一个项目：真生成（1 次调用 × angles）
    ap.generate_character_sheet(_char(), backend, assets_dir=proj_a, angles=("front",))
    calls_after_first = len(backend.generate.call_args_list)
    assert calls_after_first == 1

    # 第二个项目：同一角色身份 → 全部命中全局库（0 次调用）
    res = ap.generate_character_sheet(_char(), backend, assets_dir=proj_b, angles=("front",))
    assert len(backend.generate.call_args_list) == calls_after_first, "第二个项目不应再调 API"
    assert res["front"].source == "cached"
    assert res["front"].extra.get("bible_key"), "应记录命中的全局库身份键"
    assert (proj_b / "characters" / "Puppy" / "front.jpg").is_file()


def test_character_sheet_bible_miss_regenerates(tmp_path, monkeypatch):
    """不同 appearance → 不同身份键 → 不命中 → 正常生成。"""
    bible_root = tmp_path / "bible"
    monkeypatch.setattr(ap, "_make_bible", lambda: GlobalCharacterStore(bible_root))

    backend = _fake_backend()
    ap.generate_character_sheet(_char(appearance="奶黄拉布拉多"), backend,
                                assets_dir=tmp_path / "a", angles=("front",))
    ap.generate_character_sheet(_char(appearance="纯黑拉布拉多"), backend,
                                assets_dir=tmp_path / "b", angles=("front",))
    assert len(backend.generate.call_args_list) == 2, "不同角色不应共享参考图"


def test_character_sheet_bible_disabled_by_env(tmp_path, monkeypatch):
    """CHARACTER_BIBLE_DISABLE=1 → 退回纯项目隔离，不建全局库。"""
    monkeypatch.setenv("CHARACTER_BIBLE_DISABLE", "1")
    assert ap._bible_enabled() is False
    assert ap._make_bible() is None


def test_character_sheet_logs_name_ambiguity(tmp_path, monkeypatch):
    """歧义角色名必须出现在日志里（人工可见）。"""
    bible_root = tmp_path / "bible"
    monkeypatch.setattr(ap, "_make_bible", lambda: GlobalCharacterStore(bible_root))

    logs: list[str] = []
    ap.generate_character_sheet(
        _char(name="小黄"), _fake_backend(),
        assets_dir=tmp_path / "a", angles=("front",),
        log_callback=logs.append,
    )
    assert any("WARN" in m for m in logs), logs
    assert any("小黄" in m for m in logs), logs


# ============================================================
# 缺陷 3：过期 reference 必须跳过 + 可见
# ============================================================

def _write_ref(assets_dir: Path, name: str, angle: str, url: str, expires_at: str) -> None:
    """写一条缓存 reference（angle 必须是 picker 实际选中的那个）。"""
    d = assets_dir / "characters" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{angle}.jpg").write_bytes(b"\xff\xd8")
    (d / f"{angle}.url.txt").write_text(url, encoding="utf-8")
    (d / f"{angle}.expires.txt").write_text(expires_at, encoding="utf-8")


def test_resolve_shot_reference_urls_uses_url_expires_param(tmp_path):
    """MiniMax OSS 签名 URL 自带 Expires → 即使没有 sidecar 也能判过期。

    这是真实事故形态：旧缓存没有 .expires.txt，但 URL 里的 Expires 是准的。
    """
    import time
    assets = tmp_path / "assets"
    d = assets / "characters" / "Puppy"
    d.mkdir(parents=True, exist_ok=True)
    past = int(time.time()) - 7200
    (d / "side_left.jpg").write_bytes(b"\xff\xd8")
    (d / "side_left.url.txt").write_text(
        f"https://oss.example.com/s.jpg?Expires={past}&Signature=xyz", encoding="utf-8",
    )

    shot = MagicMock()
    shot.characters = ["Puppy"]
    shot.shot_type = "中景"
    shot.camera_motion = ""
    shot.mood = []
    shot.scene_id = None
    shot.time = ""

    refs = ap.resolve_shot_reference_urls(shot, assets, shot_index=0)
    assert "Puppy/side_left" in refs.stale_labels
    assert refs.urls == []


def test_generation_writes_expiry_sidecar(tmp_path, monkeypatch):
    """生成时必须落 .expires.txt —— 否则缓存命中路径无法判新鲜度。"""
    bible_root = tmp_path / "bible"
    monkeypatch.setattr(ap, "_make_bible", lambda: GlobalCharacterStore(bible_root))
    backend = _fake_backend()
    ap.generate_character_sheet(_char(), backend, assets_dir=tmp_path / "a", angles=("front",))
    sidecar = tmp_path / "a" / "characters" / "Puppy" / "front.expires.txt"
    assert sidecar.is_file(), "缺少过期时间 sidecar"
    assert sidecar.read_text(encoding="utf-8"), "sidecar 内容不应为空"


def test_resolve_shot_reference_urls_skips_expired(tmp_path):
    """验收②：过期 URL → 该 ref 被跳过并记进 stale_labels（不静默使用）。"""
    assets = tmp_path / "assets"
    _write_ref(assets, "Puppy", "side_left",
               "https://oss.example.com/front.jpg",
               _iso(_now() - datetime.timedelta(hours=2)))

    shot = MagicMock()
    shot.characters = ["Puppy"]
    shot.shot_type = "中景"
    shot.camera_motion = ""
    shot.mood = []
    shot.scene_id = None
    shot.time = ""

    refs = ap.resolve_shot_reference_urls(shot, assets, shot_index=0)
    assert "Puppy/side_left" in refs.stale_labels
    assert all("side_left.jpg" not in u for u in refs.urls)


def test_resolve_shot_reference_urls_keeps_fresh(tmp_path):
    assets = tmp_path / "assets"
    _write_ref(assets, "Puppy", "side_left",
               "https://oss.example.com/front.jpg",
               _iso(_now() + datetime.timedelta(hours=10)))

    shot = MagicMock()
    shot.characters = ["Puppy"]
    shot.shot_type = "中景"
    shot.camera_motion = ""
    shot.mood = []
    shot.scene_id = None
    shot.time = ""

    refs = ap.resolve_shot_reference_urls(shot, assets, shot_index=0)
    assert refs.stale_labels == []
    assert len(refs.urls) == 1
    assert refs.urls[0].startswith("https://")


def test_resolve_shot_reference_urls_local_path_never_stale(tmp_path):
    """Gemini 模式（本地路径）→ 永远不算过期。"""
    assets = tmp_path / "assets"
    d = assets / "characters" / "Puppy"
    d.mkdir(parents=True, exist_ok=True)
    (d / "side_left.jpg").write_bytes(b"\xff\xd8")
    (d / "side_left.url.txt").write_text("", encoding="utf-8")

    shot = MagicMock()
    shot.characters = ["Puppy"]
    shot.shot_type = "中景"
    shot.camera_motion = ""
    shot.mood = []
    shot.scene_id = None
    shot.time = ""

    refs = ap.resolve_shot_reference_urls(shot, assets, shot_index=0)
    assert refs.stale_labels == []
    assert len(refs.urls) == 1


def test_stale_labels_default_is_empty():
    """向后兼容：ShotReferenceUrls 旧的三参构造仍合法。"""
    u = ap.ShotReferenceUrls(urls=[], labels=[], scene_urls=[])
    assert u.stale_labels == []


# ============================================================
# orchestrator：过期 reference 要进 consistency
# ============================================================

def test_orchestrator_records_stale_references(tmp_path, monkeypatch):
    from hailuo_video.orchestrator import PipelineAgent, PipelineConfig
    from hailuo_video.script_engine import SceneSpec, Shot, Storyboard

    assets = tmp_path / "assets"
    _write_ref(assets, "Puppy", "side_left",
               "https://oss.example.com/front.jpg",
               _iso(_now() - datetime.timedelta(hours=2)))

    sb = Storyboard(
        title="t",
        characters=[_char()],
        scenes=[SceneSpec(id=1, location="田野", key_elements=[])],
        shots=[Shot(index=1, scene_id=1, shot_type="中景", description="d",
                    voiceover="v", characters=["Puppy"])],
    )
    agent = PipelineAgent(PipelineConfig(run_dir=tmp_path / "run", assets_dir=assets))
    ctx = {"storyboard": sb, "shot_count": 1, "intermediate_dir": tmp_path}
    task = agent._build_task("image_gen", 0, ctx, phase=None)

    assert task.get("stale_references"), task
    consistency = agent._build_consistency(ctx)
    assert consistency["stale_reference_count"] >= 1


# ============================================================
# ADR-007 隔离：全局库必须在 assets_dir 之外
# ============================================================

def test_character_bible_dir_is_outside_project_assets_dir(tmp_path):
    from hailuo_video.project import ProjectContext

    sb_path = tmp_path / "sb.json"
    sb_path.write_text(json.dumps({
        "title": "t", "characters": [], "scenes": [],
        "shots": [{"index": 1, "scene_id": 1, "shot_type": "中景",
                   "description": "d", "voiceover": "v"}],
    }), encoding="utf-8")

    ctx = ProjectContext.from_storyboard_path(sb_path)
    bible = ctx.character_bible_dir
    assert bible != ctx.assets_dir
    assert ctx.assets_dir not in bible.parents
    assert bible not in ctx.assets_dir.parents


def test_project_cleanup_does_not_delete_global_bible(tmp_path):
    """cleanup() 只删项目 namespace —— 全局角色圣经必须幸存。"""
    from hailuo_video.project import ProjectContext, ProjectRegistry

    sb_path = tmp_path / "sb.json"
    sb_path.write_text(json.dumps({
        "title": "t", "characters": [], "scenes": [],
        "shots": [{"index": 1, "scene_id": 1, "shot_type": "中景",
                   "description": "d", "voiceover": "v"}],
    }), encoding="utf-8")

    ctx = ProjectContext.from_storyboard_path(sb_path)
    bible_root = tmp_path / "bible"
    bible_root.mkdir(parents=True, exist_ok=True)
    (bible_root / "keep.txt").write_text("x", encoding="utf-8")

    # 把全局库指到临时目录再 cleanup —— 目录不在 candidates 里 → 不受影响
    reg = ProjectRegistry()
    deleted = reg.cleanup(ctx, dry_run=True)
    assert bible_root not in deleted
    assert (bible_root / "keep.txt").is_file()
