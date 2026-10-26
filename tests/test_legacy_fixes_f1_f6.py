"""F1~F6 回归护栏（2026-10-22 功能测试发现的 6 个既有问题）。

逐项锁住，避免"修好了但没人守"的退化：

  F1  novel_to_storyboard 此前不填 Storyboard.scenes / style_anchor
      → V2.5 scene sheet 无 reference、validate 告警、场景变体永远 fallback
  F2  _parse_shots_payload 从不读 shot_id（prompt 却把它列为必填）
  F3  MINIMAX_IMAGE_* 不在真正生效的 .env 加载白名单 → 从 .env 配了也不生效
  F4  pipeline 无条件读 MiniMax 的比例 env，选 gemini 时撞支持集校验
  F6  step_lint 只扫 sandbox/，tests/ 与 scripts/ 不在语法检查范围

（F5 = __version__ 对齐，护栏在 test_packaging.py）
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SANDBOX = REPO_ROOT / "sandbox"
SCRIPTS = REPO_ROOT / "scripts"
for p in (SANDBOX, SCRIPTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import auto_dev  # noqa: E402
from hailuo_video import image_engine as ie  # noqa: E402
from hailuo_video import script_engine as se  # noqa: E402
from hailuo_video.quality import validation as val  # noqa: E402


# ================= F1 — Storyboard 装配完整 =================

def _mock_sb(n_shots: int = 2) -> se.Storyboard:
    return se.novel_to_storyboard("从前有座山", se.CliMockBackend(), n_shots=n_shots)


def test_f1_storyboard_has_scenes():
    """F1：Storyboard.scenes 此前恒为空 → scene sheet 无从生成。"""
    sb = _mock_sb()
    assert sb.scenes, "novel_to_storyboard 未填充 scenes"
    spec = sb.scenes[0]
    assert spec.id == 1
    assert spec.location
    assert spec.atmosphere
    assert spec.key_elements


def test_f1_storyboard_has_style_anchor():
    """F1：style_anchor 此前恒为空 → V2.5 Layer 1 缺失（V3.2 轮 7 的老问题复发）。"""
    sb = _mock_sb()
    assert sb.style_anchor, "novel_to_storyboard 未填充 style_anchor"


def test_f1_shot_scene_ids_resolve_to_scenes():
    """每个 shot 的 scene_id 都能在 scenes[] 里找到（scene_undefined 的根因）。"""
    sb = _mock_sb()
    ids = {s.id for s in sb.scenes}
    for shot in sb.shots:
        assert shot.scene_id in ids, f"shot.scene_id={shot.scene_id} 无对应 scene"


def test_f1_mock_storyboard_validates_with_zero_issues():
    """端到端目标：mock 产出的 storyboard 干净通过 validate（0 error 0 warning）。"""
    rep = val.validate_storyboard(_mock_sb())
    assert not rep.issues, [f"{i.level}/{i.code}" for i in rep.issues]


def test_f1_scene_to_scene_spec_maps_fields():
    scene = se.Scene(
        id=7, time="夜", location="老宅",
        characters=["农夫"], summary="s", dialogue="d",
        atmosphere="烛光摇曳", key_elements=["油灯", "木桌"],
    )
    spec = scene.to_scene_spec()
    assert spec.id == 7
    assert spec.location == "老宅"
    assert spec.atmosphere == "烛光摇曳"
    assert spec.key_elements == ["油灯", "木桌"]
    # time 不进 SceneSpec：场景变体由 shot 侧 picker 决定
    assert not hasattr(spec, "time")


def test_f1_style_anchor_round_trips_through_script_dict():
    script = se.Script.from_dict({
        "title": "t",
        "style_anchor": "kodak portra 800, golden hour",
        "scenes": [],
    })
    assert script.style_anchor == "kodak portra 800, golden hour"
    assert script.to_dict()["style_anchor"] == "kodak portra 800, golden hour"


def test_f1_old_payload_without_new_fields_still_parses():
    """向后兼容：LLM 不返回新字段时必须取默认，不能炸。"""
    script = se.Script.from_dict({
        "title": "旧格式",
        "scenes": [{"id": 1, "time": "白天", "location": "山", "summary": "s"}],
    })
    assert script.style_anchor == ""
    assert script.scenes[0].atmosphere == ""
    assert script.scenes[0].key_elements == []


def test_f1_no_extra_llm_roundtrip_for_style_anchor():
    """F1 设计约束：style_anchor 随剧本同一次调用产出，不新增第 4 次 LLM 往返。"""
    backend = se.CliMockBackend()
    se.novel_to_storyboard("x", backend, n_shots=1)
    # 编剧 / 角色设计师 / 分镜师 —— 只有 3 个角色
    systems = [c["system"] for c in backend.calls]
    assert len(systems) == 3, f"预期 3 次 LLM 调用，实际 {len(systems)}"


# ================= F2 — shot_id 解析 =================

def test_f2_parse_shots_payload_reads_shot_id():
    """F2：prompt 把 shot_id 列为必填，但解析层从不读它 → 恒为空。"""
    raw = '{"shots": [{"shot_id": "shot_001", "index": 1, "scene_id": 1}]}'
    shots = se._parse_shots_payload(raw, 1)
    assert shots[0].shot_id == "shot_001"


def test_f2_shot_id_missing_defaults_to_empty():
    shots = se._parse_shots_payload('{"shots": [{"index": 1}]}', 1)
    assert shots[0].shot_id == ""


def test_f2_mock_storyboard_shots_have_ids():
    ids = [s.shot_id for s in _mock_sb(3).shots]
    assert ids == ["shot_001", "shot_002", "shot_003"]


# ================= F3 — .env 白名单 =================

@pytest.mark.parametrize("key", [
    "MINIMAX_IMAGE_MODEL",
    "MINIMAX_IMAGE_RESPONSE_FORMAT",
    "MINIMAX_IMAGE_ASPECT_RATIO",
    "MINIMAX_IMAGE_N",
    "GEMINI_IMAGE_MODEL",
    "GEMINI_IMAGE_ASPECT_RATIO",
    "GEMINI_IMAGE_N",
])
def test_f3_image_keys_are_dotenv_loadable(key):
    """F3：image_engine._DOTENV_KEYS 只是测试夹具用的；真正生效的是 script_engine 这份。

    两者曾经不一致 → 用户在 .env 里配了图像参数却完全不生效。
    """
    assert key in se._DOTENV_KEYS, f"{key} 不在 _load_dotenv 的白名单里，.env 配置无效"
    assert key in ie._DOTENV_KEYS, f"{key} 不在 image_engine 的键表里，访问器读不到"


def test_f3_dotenv_loader_actually_sets_image_key(tmp_path, monkeypatch):
    """端到端：写一个 .env，确认图像参数真的进了 os.environ。"""
    monkeypatch.chdir(tmp_path)
    for k in ie._DOTENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    (tmp_path / ".env").write_text(
        "MINIMAX_IMAGE_ASPECT_RATIO=9:16\nGEMINI_IMAGE_MODEL=gemini-2.5-flash-image\n",
        encoding="utf-8",
    )
    se._load_dotenv()
    import os
    assert os.environ["MINIMAX_IMAGE_ASPECT_RATIO"] == "9:16"
    assert os.environ["GEMINI_IMAGE_MODEL"] == "gemini-2.5-flash-image"


# ================= F4 — 各 backend 读自己的比例 env =================

def test_f4_minimax_reads_minimax_env(monkeypatch):
    monkeypatch.setenv("MINIMAX_IMAGE_ASPECT_RATIO", "9:16")
    monkeypatch.setenv("GEMINI_IMAGE_ASPECT_RATIO", "21:9")
    b = ie.MiniMaxImageBackend(api_key_value="x" * 20)
    assert b.default_aspect_ratio() == "9:16"


def test_f4_gemini_reads_gemini_env(monkeypatch):
    """F4 核心：选 gemini 时不再拿 MINIMAX_IMAGE_ASPECT_RATIO 去撞 Gemini 支持集。"""
    monkeypatch.setenv("MINIMAX_IMAGE_ASPECT_RATIO", "9:16")
    monkeypatch.setenv("GEMINI_IMAGE_ASPECT_RATIO", "21:9")
    b = ie.GeminiImageBackend(api_key_value="AIzaX")
    assert b.default_aspect_ratio() == "21:9"


def test_f4_gemini_default_aspect_is_accepted_by_its_own_validator(monkeypatch):
    """默认比例必须落在 Gemini 自己的支持集内，否则 pipeline 首镜就炸。"""
    monkeypatch.delenv("GEMINI_IMAGE_ASPECT_RATIO", raising=False)
    monkeypatch.delenv("MINIMAX_IMAGE_ASPECT_RATIO", raising=False)
    b = ie.GeminiImageBackend(api_key_value="AIzaX")
    assert b.default_aspect_ratio() in ie.GEMINI_VALID_ASPECT_RATIOS


def test_f4_base_class_defaults_to_minimax_env(monkeypatch):
    monkeypatch.setenv("MINIMAX_IMAGE_ASPECT_RATIO", "4:3")
    assert ie.MockImageBackend().default_aspect_ratio() == "4:3"


# ================= F6 — step_lint 覆盖范围 =================

def test_f6_step_lint_scans_tests_and_scripts(monkeypatch, capsys):
    """F6：报告里那句 'All N Python files compile clean' 应涵盖 tests/ 与 scripts/。"""
    monkeypatch.setattr(auto_dev, "run", lambda *a, **k: (0, "", ""))
    assert auto_dev.step_lint(None) is True
    out = capsys.readouterr().out
    assert "sandbox/ + tests/ + scripts/" in out


def test_f6_step_lint_file_count_covers_all_three_dirs(monkeypatch, capsys):
    monkeypatch.setattr(auto_dev, "run", lambda *a, **k: (0, "", ""))
    auto_dev.step_lint(None)
    import re
    m = re.search(r"All (\d+) Python files", capsys.readouterr().out)
    assert m
    n = int(m.group(1))
    expected = sum(
        1 for root in (SANDBOX, REPO_ROOT / "tests", SCRIPTS)
        for p in root.rglob("*.py")
        if "__pycache__" not in str(p) and "MODULE" not in str(p)
    )
    assert n == expected
    # 单扫 sandbox/ 会明显更少（tests 有几十个文件）
    only_sandbox = sum(
        1 for p in SANDBOX.rglob("*.py")
        if "__pycache__" not in str(p) and "MODULE" not in str(p)
    )
    assert n > only_sandbox


def test_f6_step_lint_reports_syntax_error(tmp_path, monkeypatch, capsys):
    """真坏文件必须被抓住（否则扩范围只是走过场）。"""
    bad = REPO_ROOT / "tests" / "_tmp_broken_for_lint.py"
    bad.write_text("def broken(:\n", encoding="utf-8")
    try:
        monkeypatch.setattr(auto_dev, "run", lambda *a, **k: (0, "", ""))
        # 用真实的 py_compile 结果
        import py_compile
        def fake_run(cmd, **k):
            try:
                py_compile.compile(str(cmd[-1]), cfile=str(bad) + "c", doraise=True)
                return (0, "", "")
            except py_compile.PyCompileError as exc:
                return (1, "", str(exc))
        monkeypatch.setattr(auto_dev, "run", fake_run)
        assert auto_dev.step_lint(None) is False
        assert "failed syntax check" in capsys.readouterr().out
    finally:
        bad.unlink(missing_ok=True)
        (bad.parent / "_tmp_broken_for_lint.pyc").unlink(missing_ok=True)