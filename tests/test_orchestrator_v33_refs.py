"""V3.3 修复测试（2026-10-09）。

覆盖：
  - Change D：motion_gen subject_action 中 style_anchor 前置而非后置
  - Change A：image_gen task 含 scene_references 字段，从 assets/scenes 缓存读 URL
  - Change A 兜底：缓存缺失 → scene_references = []，不阻塞 pipeline
  - ImageGenAgent：scene_references 非空时优先用作 subject_reference（i2i 多 ref 锚定）
  - ImageGenAgent：scene_references 为空时兜底用 subject_references（向后兼容）
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from hailuo_video.orchestrator import PipelineAgent, PipelineConfig  # noqa: E402
from hailuo_video.agents import ImageGenAgent  # noqa: E402
from hailuo_video.script_engine import (  # noqa: E402
    Character,
    SceneSpec,
    Shot,
    Storyboard,
)


# ---------- Fixtures ----------

def _make_storyboard_with_scene(n: int = 2, scene_id: int = 1) -> Storyboard:
    """建一个含 scene_id/time 的最小 Storyboard。"""
    chars = [Character(name="橘猫", appearance="orange cat", personality=[], color_palette=["橙"])]
    scenes = [SceneSpec(id=scene_id, location="窗台", atmosphere="清晨斜阳", key_elements=["木窗", "棉垫"])]
    shots = []
    for i in range(n):
        shots.append(Shot(
            index=i,
            scene_id=scene_id,
            shot_type="中景",
            description=f"第 {i+1} 镜描述：橘猫在窗台上",
            shot_id=f"shot_{i+1:03d}",
            characters=["橘猫"],
            dialogue="",
            voiceover="",
            mood=["温柔", "慵懒"],
            camera_motion="缓慢推近",
            subject_action="橘猫伸懒腰",
            expected_duration=6.0,
            motion_complexity="simple",
            time="白天" if i == 0 else "黄昏",  # 第 1 镜 day，第 2 镜 sunset
        ))
    return Storyboard(title="测试", style_anchor="photorealistic cinematic", characters=chars, scenes=scenes, shots=shots)


# ---------- Change D 测试 ----------

def test_motion_gen_style_anchor_prefix():
    """V3.3 Change D：style_anchor 前置 + V3.4 ADR-006 自动追加守卫词。"""
    sb = _make_storyboard_with_scene(n=1)
    cfg = PipelineConfig(image_backend=None, run_dir=Path("/tmp/_test_v33"))
    agent = PipelineAgent(cfg, resume_from=None, load_registry=False)
    ctx = {"storyboard": sb, "shot_count": len(sb.shots), "intermediate_dir": Path("/tmp/_v33_inter")}

    task = agent._build_task("motion_gen", 0, ctx, type("P", (), {"name": "motion_gen"})())

    # V3.4 ADR-006：style_anchor 自动追加 'no illustration no cartoon no anime no CGI'
    sa = task.get("subject_action", "")
    assert sa.startswith("[风格:photorealistic cinematic,"), f"expected prefix, got: {sa[:80]}"
    assert "no illustration no cartoon no anime no CGI" in sa
    # 主体动作在后
    assert "橘猫伸懒腰" in sa


def test_motion_gen_no_style_anchor_uses_fallback():
    """V3.4 ADR-006：style_anchor 空时不再 '不变'，而是用 PHOTOREALISTIC_FALLBACK 兜底。

    V3.3 行为：style_anchor="" → subject_action 原文不带 [风格:...]
    V3.4 行为：style_anchor="" → _safe_style_anchor("") = PHOTOREALISTIC_FALLBACK
              → subject_action 仍加 [风格:PHOTOREALISTIC_FALLBACK] 前缀
    目的：空 style_anchor 也不再让 prompt 缺 anti-cartoon 守卫（"小黄变猫"事件根因）。
    """
    from hailuo_video.asset_prep import PHOTOREALISTIC_FALLBACK
    sb = _make_storyboard_with_scene(n=1)
    sb.style_anchor = ""
    cfg = PipelineConfig(image_backend=None, run_dir=Path("/tmp/_test_v33"))
    agent = PipelineAgent(cfg, resume_from=None, load_registry=False)
    ctx = {"storyboard": sb, "shot_count": len(sb.shots), "intermediate_dir": Path("/tmp/_v33_inter")}

    task = agent._build_task("motion_gen", 0, ctx, type("P", (), {"name": "motion_gen"})())

    # 现在 subject_action **必含** [风格:...] 前缀（不再是 verbatim）
    assert "[风格:" in task["subject_action"]
    assert PHOTOREALISTIC_FALLBACK in task["subject_action"]
    assert "橘猫伸懒腰" in task["subject_action"]


# ---------- Change A 测试 ----------

def test_image_gen_has_scene_references_field():
    """Change A：image_gen task 新增 scene_references 字段。"""
    sb = _make_storyboard_with_scene(n=1, scene_id=1)
    cfg = PipelineConfig(image_backend=None, assets_dir=Path("./assets"), run_dir=Path("/tmp/_test_v33"))
    agent = PipelineAgent(cfg, resume_from=None, load_registry=False)
    ctx = {"storyboard": sb, "shot_count": len(sb.shots), "intermediate_dir": Path("/tmp/_v33_inter")}

    task = agent._build_task("image_gen", 0, ctx, type("P", (), {"name": "image_gen"})())

    assert "scene_references" in task, "Change A 缺失：image_gen task 应含 scene_references 字段"
    assert isinstance(task["scene_references"], list)


def test_image_gen_scene_references_uses_cache_when_available(tmp_path):
    """Change A：缓存命中时 scene_references 含真 URL。

    准备一个 fake scene 缓存（assets/scenes/scene_0001_day.url.txt），
    验证 _resolve_scene_refs_for_shot 能读出来。
    """
    fake_url = "https://fake.example.com/scene_0001_day.jpg?Expires=9999999999&Signature=xyz"
    assets_dir = tmp_path / "assets"
    scenes_dir = assets_dir / "scenes"
    scenes_dir.mkdir(parents=True)
    (scenes_dir / "scene_0001_day.url.txt").write_text(fake_url, encoding="utf-8")

    sb = _make_storyboard_with_scene(n=1, scene_id=1)
    cfg = PipelineConfig(image_backend=None, assets_dir=assets_dir, run_dir=tmp_path / "run")
    agent = PipelineAgent(cfg, resume_from=None, load_registry=False)
    ctx = {"storyboard": sb, "shot_count": len(sb.shots), "intermediate_dir": tmp_path / "inter"}

    task = agent._build_task("image_gen", 0, ctx, type("P", (), {"name": "image_gen"})())

    assert task["scene_references"] == [fake_url]


def test_image_gen_scene_references_empty_when_no_cache(tmp_path):
    """Change A 兜底：缓存缺失时 scene_references = []，不阻塞 pipeline。"""
    assets_dir = tmp_path / "assets_empty"
    assets_dir.mkdir()

    sb = _make_storyboard_with_scene(n=1, scene_id=999)  # 不存在的 scene_id
    cfg = PipelineConfig(image_backend=None, assets_dir=assets_dir, run_dir=tmp_path / "run")
    agent = PipelineAgent(cfg, resume_from=None, load_registry=False)
    ctx = {"storyboard": sb, "shot_count": len(sb.shots), "intermediate_dir": tmp_path / "inter"}

    task = agent._build_task("image_gen", 0, ctx, type("P", (), {"name": "image_gen"})())

    assert task["scene_references"] == []


def test_image_gen_picker_time_for_variant():
    """Change A：shot.time 控制 picker（白天 → day / 黄昏 → sunset）。

    用同一 scene_id=1，但 shot_001 time='白天' 应取 day.url.txt；
    shot_002 time='黄昏' 应取 sunset.url.txt。
    """
    fake_day = "https://fake.example.com/day.jpg?sig=1"
    fake_sunset = "https://fake.example.com/sunset.jpg?sig=2"
    assets_dir = Path("assets")
    scenes_dir = assets_dir / "scenes"
    # 不修改真实 assets/，用 monkeypatch 改 _resolve_scene_refs_for_shot 不安全
    # 这里通过 tmp_path 注入缓存
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp_assets = Path(td) / "assets"
        (tmp_assets / "scenes").mkdir(parents=True)
        (tmp_assets / "scenes" / "scene_0001_day.url.txt").write_text(fake_day)
        (tmp_assets / "scenes" / "scene_0001_sunset.url.txt").write_text(fake_sunset)

        sb = _make_storyboard_with_scene(n=2, scene_id=1)
        cfg = PipelineConfig(image_backend=None, assets_dir=tmp_assets, run_dir=Path(td) / "run")
        agent = PipelineAgent(cfg, resume_from=None, load_registry=False)
        ctx = {"storyboard": sb, "shot_count": len(sb.shots), "intermediate_dir": Path(td) / "inter"}

        task_day = agent._build_task("image_gen", 0, ctx, type("P", (), {"name": "image_gen"})())
        task_sunset = agent._build_task("image_gen", 1, ctx, type("P", (), {"name": "image_gen"})())

        assert task_day["scene_references"] == [fake_day]
        assert task_sunset["scene_references"] == [fake_sunset]


def test_image_gen_prompt_still_contains_style_anchor():
    """回归：Change A 不应破坏 Change D 的 style_anchor 注入。"""
    sb = _make_storyboard_with_scene(n=1)
    cfg = PipelineConfig(image_backend=None, run_dir=Path("/tmp/_test_v33"))
    agent = PipelineAgent(cfg, resume_from=None, load_registry=False)
    ctx = {"storyboard": sb, "shot_count": len(sb.shots), "intermediate_dir": Path("/tmp/_v33_inter")}

    task = agent._build_task("image_gen", 0, ctx, type("P", (), {"name": "image_gen"})())

    # V3.4 ADR-006：style_anchor 自动追加守卫词；prefix 不再严格匹配 "[风格:photorealistic cinematic]"
    assert "[风格:photorealistic cinematic," in task["prompt"]
    assert "no illustration no cartoon no anime no CGI" in task["prompt"]


# ---------- ImageGenAgent 测试 ----------

class _FakeBackend:
    """Mock image backend，记录最近一次调用的 subject_reference。"""

    def __init__(self):
        self.calls: list[dict] = []

    def generate(self, prompt, **opts):
        self.calls.append({"prompt": prompt, **opts})
        from hailuo_video.image_engine import ImageResult
        # 返一个 dummy ImageResult（含 bytes 让 agent 落盘）
        return [ImageResult(prompt=prompt, bytes=b"\x89PNG\r\n\x1a\n" + b"\x00" * 100, mime_type="png", model="fake", duration_seconds=0.0)]


def test_image_gen_agent_prefers_scene_references():
    """V3.3：scene_references 非空时优先用作 subject_reference。"""
    backend = _FakeBackend()
    agent = ImageGenAgent()
    task = {
        "image_backend": backend,
        "prompt": "test",
        "subject_references": ["橘猫"],        # 旧：角色名（应被忽略）
        "scene_references": ["https://fake.example.com/scene.jpg"],  # 新：真 URL
        "aspect_ratio": "16:9",
        "intermediate_dir": "/tmp/_v33_inter",
    }
    result = agent.execute(task)
    assert result.ok, f"execute failed: {result.error}"
    # backend 收到的是 scene URL，不是角色名
    assert backend.calls[0]["subject_reference"] == ["https://fake.example.com/scene.jpg"]


def test_image_gen_agent_drops_character_names():
    """角色名不是参考图，不能传给 image backend。"""
    backend = _FakeBackend()
    agent = ImageGenAgent()
    task = {
        "image_backend": backend,
        "prompt": "test",
        "subject_references": ["橘猫"],
        "scene_references": [],
        "aspect_ratio": "16:9",
        "intermediate_dir": "/tmp/_v33_inter",
    }
    result = agent.execute(task)
    assert result.ok, f"execute failed: {result.error}"
    assert backend.calls[0]["subject_reference"] is None


def test_image_gen_agent_merges_character_and_scene_urls():
    """角度/表情 URL 与场景 URL 合并，重复的不追加，最多 4 个。"""
    backend = _FakeBackend()
    agent = ImageGenAgent()
    urls = [
        "https://fake.example.com/front.jpg",
        "https://fake.example.com/expr.jpg",
        "https://fake.example.com/extra1.jpg",
        "https://fake.example.com/extra2.jpg",
    ]
    task = {
        "image_backend": backend,
        "prompt": "test",
        "subject_references": ["橘猫", *urls],
        "scene_references": [
            "https://fake.example.com/expr.jpg",
            "https://fake.example.com/scene.jpg",
        ],
        "aspect_ratio": "16:9",
        "intermediate_dir": "/tmp/_v33_inter",
    }
    result = agent.execute(task)
    assert result.ok, f"execute failed: {result.error}"
    assert backend.calls[0]["subject_reference"] == urls


def test_image_gen_task_uses_character_and_scene_cache(tmp_path):
    """缓存齐全时 subject_references = 角度 + 表情 + 场景 URL。"""
    assets = tmp_path / "assets"
    char_dir = assets / "characters" / "橘猫"
    char_dir.mkdir(parents=True)
    scene_dir = assets / "scenes"
    scene_dir.mkdir()
    front = "https://fake.example.com/front.jpg"
    expr = "https://fake.example.com/neutral.jpg"
    scene = "https://fake.example.com/day.jpg"
    (char_dir / "front.url.txt").write_text(front, encoding="utf-8")
    (char_dir / "expression_neutral.url.txt").write_text(expr, encoding="utf-8")
    (scene_dir / "scene_0001_day.url.txt").write_text(scene, encoding="utf-8")

    sb = _make_storyboard_with_scene(n=1, scene_id=1)
    cfg = PipelineConfig(image_backend=None, assets_dir=assets, run_dir=tmp_path / "run")
    agent = PipelineAgent(cfg, resume_from=None, load_registry=False)
    ctx = {"storyboard": sb, "shot_count": 1, "intermediate_dir": tmp_path / "inter"}
    task = agent._build_task("image_gen", 0, ctx, type("P", (), {"name": "image_gen"})())

    assert task["subject_references"] == [front, expr, scene]
    assert task["scene_references"] == [scene]
    assert "橘猫" not in task["subject_references"]
