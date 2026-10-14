"""V3.5 ADR-007：Project 隔离回归测试。

每个 V3.5 暴露的隔离 bug = 1 个 test 锁死，避免未来重复踩坑。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
for p in (SANDBOX,):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from hailuo_video.project import (  # noqa: E402
    ProjectContext,
    ProjectContextError,
    ProjectRegistry,
    _safe_filename,
    _safe_project_name,
)


# ---------- Fixtures ----------

@pytest.fixture
def in_tmp_cwd(tmp_path, monkeypatch):
    """所有 cwd-relative 路径都在 tmp_path 下，避免污染真实 sandbox/。

    用法：tmp_storyboard / second_storyboard 自动 in_tmp_cwd。
    """
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def tmp_storyboard(tmp_path):
    """建一个临时 storyboard.json，返回路径（用 in_tmp_cwd 隔离 cwd）。"""
    sb = tmp_path / "storyboard.json"
    sb.write_text(json.dumps({
        "title": "test story",
        "characters": [{"name": "test_char"}],
        "scenes": [],
        "shots": [],
        "style_anchor": "photorealistic cinematic",
    }), encoding="utf-8")
    return sb


@pytest.fixture
def second_storyboard(tmp_path):
    """建第二个 storyboard（不同内容）→ 不同 project_id。"""
    sb = tmp_path / "storyboard2.json"
    sb.write_text(json.dumps({
        "title": "different story",
        "characters": [{"name": "other_char"}],
        "scenes": [],
        "shots": [],
    }), encoding="utf-8")
    return sb


# ---------- 1. project_id 派生稳定 ----------

def test_project_id_derives_from_storyboard_hash(tmp_path):
    """ADR-007-A：相同 storyboard 内容 → 相同 project_id。"""
    sb = tmp_path / "sb.json"
    sb.write_text('{"title": "x", "z": 1}', encoding="utf-8")
    ctx1 = ProjectContext.from_storyboard_path(sb)
    ctx2 = ProjectContext.from_storyboard_path(sb)
    assert ctx1.project_id == ctx2.project_id
    assert len(ctx1.project_id) == 8  # sha256[:8]
    assert len(ctx1.storyboard_hash) == 64  # sha256 hex


def test_project_id_differs_per_storyboard(tmp_path, second_storyboard):
    """ADR-007-A：不同 storyboard 内容 → 不同 project_id（防撞库）。"""
    sb1 = tmp_path / "a.json"
    sb1.write_text('{"a": 1}', encoding="utf-8")
    ctx1 = ProjectContext.from_storyboard_path(sb1)
    ctx2 = ProjectContext.from_storyboard_path(second_storyboard)
    assert ctx1.project_id != ctx2.project_id, (
        f"不同 storyboard 必须派生不同 project_id："
        f"{ctx1.project_id} vs {ctx2.project_id}"
    )


def test_missing_storyboard_fails_fast(tmp_path):
    """ADR-007-E：未提供 storyboard 立刻硬失败"""
    with pytest.raises(ProjectContextError, match="storyboard 不存在"):
        ProjectContext.from_storyboard_path(tmp_path / "nonexistent.json")


# ---------- 2. character cache namespace 隔离 ----------

def test_character_cache_namespaced_by_project_id(tmp_storyboard, tmp_path):
    """ADR-007-A + G1：角色 cache 路径含 project_id namespace。

    即使两个项目都有 '主角' 角色，路径不同 → 不撞库。
    """
    ctx_a = ProjectContext.from_storyboard_path(tmp_storyboard)
    ctx_b = ProjectContext.from_storyboard_path(tmp_storyboard)
    # 强制不同 project_id
    ctx_b2 = ProjectContext(
        project_id="diff0001",
        project_name=ctx_b.project_name,
        storyboard_path=ctx_b.storyboard_path,
        storyboard_hash=ctx_b.storyboard_hash,
        assets_dir=tmp_path / "sandbox/assets/characters/diff0001",
        run_dir=tmp_path / "sample/run_diff0001",
        output_dir=tmp_path / "sample/test_proj",
        log_path=tmp_path / "logs/diff0001_session.jsonl",
    )
    path_a = ctx_a.character_angle_path("主角", "front")
    path_b = ctx_b2.character_angle_path("主角", "front")
    assert path_a != path_b, f"角色 cache 必须 namespace 隔离：{path_a} vs {path_b}"
    # 同样角色名"主角"在不同 project 下走不同路径


def test_safe_filename_preserves_chinese():
    """V3.5：保留中文（与 V3.3 缓存兼容）+ 去除真正非法字符。"""
    assert _safe_filename("shot_001") == "shot_001"
    assert _safe_filename("小黄") == "小黄"   # 保留
    assert _safe_filename("foo/bar") == "foo_bar"  # 去 /
    assert _safe_filename("a:b") == "a_b"  # 去 :
    assert _safe_filename('a"b') == "a_b"  # 去 "
    assert _safe_filename("") == "conversation_story"  # 兜底


# ---------- 3. env override 自动 restore ----------

def test_env_override_restores_after_context(tmp_storyboard):
    """ADR-007-C：apply_env 进入时覆盖、退出 restore（不污染后续调用）。
    """
    # 预设置一个外部 env
    original = "11:11"
    os.environ["MINIMAX_IMAGE_ASPECT_RATIO"] = original
    ctx = ProjectContext.from_storyboard_path(
        tmp_storyboard, aspect_ratio="9:16",
    )
    assert ctx.env_overrides["MINIMAX_IMAGE_ASPECT_RATIO"] == "9:16"
    with ctx.apply_env():
        assert os.environ["MINIMAX_IMAGE_ASPECT_RATIO"] == "9:16"  # overridden
        # HAILUO_VIDEO_PROJECT_ID 也是覆盖项
        assert os.environ["HAILUO_VIDEO_PROJECT_ID"] == ctx.project_id
    # 退出后 restore
    assert os.environ["MINIMAX_IMAGE_ASPECT_RATIO"] == original
    assert os.environ.get("HAILUO_VIDEO_PROJECT_ID") is None  # 原来没有


def test_env_override_does_not_leak_across_projects(tmp_path):
    """G3：项目 A 设 9:16，项目 B 跑时应该是 16:9（不被 A 污染）。
    """
    sb_a = tmp_path / "a.json"; sb_a.write_text('{"a": 1}', encoding="utf-8")
    sb_b = tmp_path / "b.json"; sb_b.write_text('{"b": 2}', encoding="utf-8")

    ctx_a = ProjectContext.from_storyboard_path(sb_a, aspect_ratio="9:16")
    ctx_b = ProjectContext.from_storyboard_path(sb_b, aspect_ratio="1:1")

    # 模拟连续跑两个项目
    with ctx_a.apply_env():
        a_env = os.environ["MINIMAX_IMAGE_ASPECT_RATIO"]
    assert a_env == "9:16"
    # A 退出后，B 跑前应该恢复
    with ctx_b.apply_env():
        b_env = os.environ["MINIMAX_IMAGE_ASPECT_RATIO"]
    assert b_env == "1:1"


# ---------- 4. state registry 隔离 ----------

def test_state_registry_isolated_per_project(tmp_path, monkeypatch):
    """ADR-007-D：state.json 按 project_id 命名空间 → 跨项目不互读。

    替换 ProjectRegistry.STATE_DIR 到 tmp_path（不污染真实 sandbox）。
    """
    monkeypatch.setattr(
        ProjectRegistry, "STATE_DIR", tmp_path / ".project_state",
    )
    reg = ProjectRegistry()

    # 项目 A 写 state
    reg.save("projA", {"key": "valueA", "shots_done": 1})
    # 项目 B 写 state
    reg.save("projB", {"key": "valueB", "shots_done": 2})

    # 读回：互不干扰
    state_a = reg.load("projA")
    state_b = reg.load("projB")
    assert state_a["key"] == "valueA"
    assert state_b["key"] == "valueB"
    # 文件系统也是分开的
    assert reg.state_path("projA").name == "projA.json"
    assert reg.state_path("projB").name == "projB.json"
    assert reg.state_path("projA") != reg.state_path("projB")


def test_registry_cleanup_removes_only_target_project(tmp_path, monkeypatch):
    """G1/G2：cleanup 仅清指定 project，不动其他 project。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        ProjectRegistry, "STATE_DIR", tmp_path / ".project_state",
    )
    reg = ProjectRegistry()

    # 模拟两个 project 的 cache + run_dir + state（用真 ProjectContext）
    sb_a = tmp_path / "story_a" / "day1.json"
    sb_a.parent.mkdir()
    sb_a.write_text(json.dumps({"title": "A", "shots": []}), encoding="utf-8")
    sb_b = tmp_path / "story_b" / "day1.json"
    sb_b.parent.mkdir()
    sb_b.write_text(json.dumps({"title": "B", "shots": []}), encoding="utf-8")

    ctx_a = ProjectContext.from_storyboard_path(sb_a)
    ctx_b = ProjectContext.from_storyboard_path(sb_b)
    # 在各自的 cache 路径建占位文件
    ctx_a.assets_dir.mkdir(parents=True)
    ctx_b.assets_dir.mkdir(parents=True)
    (ctx_a.run_dir / "shot_001").mkdir(parents=True)
    (ctx_b.run_dir / "shot_001").mkdir(parents=True)
    reg.save(ctx_a.project_id, {"x": 1})
    reg.save(ctx_b.project_id, {"x": 2})

    # 仅清 A
    deleted = reg.cleanup(ctx_a)
    assert any(ctx_a.project_id in str(p) or "run_" in str(p) for p in deleted)
    assert not ctx_a.assets_dir.exists()
    # B 完整保留
    assert ctx_b.assets_dir.exists()
    assert ctx_b.run_dir.is_dir()
    assert reg.state_path(ctx_b.project_id).is_file()


# ---------- 5. ProjectContext 路径 namespace ----------

def test_run_dir_namespaced_by_project_id(tmp_storyboard):
    """ADR-007-B：run_dir 路径含 project_id（防止 ./run 默认值全局污染）。
    """
    ctx = ProjectContext.from_storyboard_path(tmp_storyboard)
    assert ctx.project_id in str(ctx.run_dir), (
        f"run_dir 应含 project_id namespace：{ctx.run_dir}"
    )
    assert "run_" in str(ctx.run_dir)
    # shot-level
    rd = ctx.run_dir_for_shot("shot_001")
    assert rd == ctx.run_dir / "shot_001"


def test_character_dir_inside_assets_dir(tmp_storyboard):
    """G1：角色 cache 必须在 project 自己 assets_dir 下（不与全局混合）。
    """
    ctx = ProjectContext.from_storyboard_path(tmp_storyboard)
    cd = ctx.character_dir("主角")
    # relative path
    rel = cd.relative_to(ctx.assets_dir)
    assert rel.parent == Path(".")  # 即 cd 是 assets_dir 下的直接子目录
    assert rel.name == "主角"  # 保留中文


# ---------- 6. 跨 storyboard 不同 project_id + 跨 storyboard 不共享 ----------

def test_cross_project_no_cache_collision(tmp_path):
    """端到端：两个 storyboard 各有同名角色 "主角"，但 cache 路径完全分离。
    """
    # 项目 A
    sb_a = tmp_path / "story_a" / "day1.json"
    sb_a.parent.mkdir()
    sb_a.write_text(json.dumps({
        "title": "project A",
        "characters": [{"name": "主角", "appearance": "girl"}],
        "shots": [],
    }), encoding="utf-8")

    # 项目 B（不同内容）
    sb_b = tmp_path / "story_b" / "day1.json"
    sb_b.parent.mkdir()
    sb_b.write_text(json.dumps({
        "title": "project B",
        "characters": [{"name": "主角", "appearance": "boy"}],
        "shots": [],
    }), encoding="utf-8")

    ctx_a = ProjectContext.from_storyboard_path(sb_a)
    ctx_b = ProjectContext.from_storyboard_path(sb_b)

    # 不同 project_id
    assert ctx_a.project_id != ctx_b.project_id
    # 同名"主角"角色走不同路径
    a_path = ctx_a.character_angle_path("主角", "front")
    b_path = ctx_b.character_angle_path("主角", "front")
    assert a_path != b_path, (
        f"跨项目同名角色必须不同路径：{a_path} vs {b_path}"
    )
    # 各自的 assets_dir 也不同
    assert ctx_a.assets_dir != ctx_b.assets_dir
    assert ctx_a.run_dir != ctx_b.run_dir
    assert ctx_a.output_dir != ctx_b.output_dir


# ---------- 7. 项目 metadata 反序列化 ----------

def test_project_context_serializable_to_dict(tmp_storyboard):
    """V3.5：ProjectContext.to_dict() 返回 JSON-safe dict（Path → str）。
    """
    ctx = ProjectContext.from_storyboard_path(tmp_storyboard)
    d = ctx.to_dict()
    # 必填字段都在
    assert d["project_id"] == ctx.project_id
    assert d["project_name"] == ctx.project_name
    assert d["storyboard_hash"] == ctx.storyboard_hash
    # Path 字段都是 str
    assert isinstance(d["storyboard_path"], str)
    assert isinstance(d["assets_dir"], str)
    assert isinstance(d["run_dir"], str)
    # JSON 序列化 OK
    serialized = json.dumps(d, ensure_ascii=False)
    parsed = json.loads(serialized)
    assert parsed["project_id"] == ctx.project_id