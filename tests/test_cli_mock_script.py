"""D1：CLI `--backend mock` 真能跑通（docs/drafts/script_mock_backend_draft.md）。

背景：修复前 `python -m hailuo_video script --backend mock` 必然抛
`AssertionError: MockBackend 无预设响应` —— CLI 宣传该模式，但 MockBackend 只接受
单测通过 `.on()` 注入的响应，生产代码零调用。

本文件锁住：
  1. CliMockBackend 能产出**通过 validate_storyboard 的 error 门槛**的 storyboard
  2. MockBackend 的严格语义（未注册即抛）**未被破坏** —— 这是单测依赖的设计
  3. CLI 端到端 rc=0，且报错文案不再推荐一条走不通的路
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))
from hailuo_video import pipeline as pl  # noqa: E402
from hailuo_video import script_engine as se  # noqa: E402
from hailuo_video.cli import main as cli_main  # noqa: E402
from hailuo_video.quality import validation as val  # noqa: E402


# ---------- CliMockBackend ----------

def test_cli_mock_produces_storyboard_with_n_shots():
    backend = se.CliMockBackend()
    sb = se.novel_to_storyboard("从前有座山", backend, n_shots=3)
    assert sb.title
    assert len(sb.shots) == 3
    assert [s.index for s in sb.shots] == [1, 2, 3]


def test_cli_mock_honors_requested_shot_count():
    """镜头数从 prompt 读回，而非构造参数 —— 构造参数只作兜底。"""
    sb = se.novel_to_storyboard("x", se.CliMockBackend(), n_shots=5)
    assert len(sb.shots) == 5
    # 构造参数给错了也不影响（prompt 优先）
    sb2 = se.novel_to_storyboard("x", se.CliMockBackend(n_shots=99), n_shots=2)
    assert len(sb2.shots) == 2


def test_cli_mock_passes_validation_without_errors():
    """canned 数据必须真能用 —— 产出不能通过 validate 的 mock 等于没修。"""
    sb = se.novel_to_storyboard("从前有座山", se.CliMockBackend(), n_shots=2)
    rep = val.validate_storyboard(sb)
    errors = [i for i in rep.issues if i.level == "error"]
    assert not errors, f"canned storyboard 有 error：{[i.code for i in errors]}"


def test_cli_mock_uses_whitelisted_vocabularies():
    """shot_type / camera_motion / mood 必须落在 picker 字典内，否则 picker 会 fallback。"""
    from hailuo_video import asset_prep as ap

    sb = se.novel_to_storyboard("x", se.CliMockBackend(n_shots=2))
    motions = set(ap.CAMERA_MOTION_TO_ANGLE)
    moods = set(ap.MOOD_TO_EXPRESSION)
    types = set(ap.SHOT_TYPE_TO_ANGLE)
    for s in sb.shots:
        assert s.shot_type in types, f"未知 shot_type: {s.shot_type}"
        assert s.camera_motion in motions, f"未知 camera_motion: {s.camera_motion}"
        assert all(m in moods for m in s.mood), f"未知 mood: {s.mood}"


def test_cli_mock_shots_reference_a_character():
    """shot.characters 必须指向已定义角色，否则角色参考图锚定会空转。"""
    sb = se.novel_to_storyboard("x", se.CliMockBackend(), n_shots=2)
    names = {c.name for c in sb.characters}
    assert names
    for s in sb.shots:
        assert set(s.characters) <= names


def test_cli_mock_unknown_role_raises_llm_error():
    """pipeline 新增 LLM 调用点而未补 canned 数据时必须显式报错，不返回垃圾。"""
    backend = se.CliMockBackend()
    with pytest.raises(se.LLMError, match="无对应 canned 响应"):
        backend.chat(system="你是一个全新的角色", user="x")


# ---------- MockBackend 严格语义未被破坏（回归护栏）----------

def test_mock_backend_still_raises_on_unregistered_prompt():
    """关键护栏：若给 MockBackend 加默认回退，「忘了注册响应」的测试会静默变绿。"""
    backend = se.MockBackend()
    with pytest.raises(AssertionError, match="无预设响应"):
        backend.chat(system="你是漫剧编剧", user="x")


def test_make_backend_mock_is_still_the_strict_one():
    assert isinstance(se.make_backend("mock"), se.MockBackend)
    assert not isinstance(se.make_backend("mock"), se.CliMockBackend)


def test_make_backend_cli_mock_is_separate():
    assert isinstance(se.make_backend("cli-mock"), se.CliMockBackend)


# ---------- CLI 端到端 ----------

def test_cli_script_mock_backend_succeeds(tmp_path, capsys):
    """修复的核心断言：这条命令以前必然 rc=1。"""
    novel = tmp_path / "novel.txt"
    novel.write_text("从前有座山。\n山里有座庙。\n", encoding="utf-8")
    out_json = tmp_path / "sb.json"
    rc = cli_main([
        "script", str(novel), "-o", str(out_json),
        "--shots", "2", "--backend", "mock",
    ])
    assert rc == 0, capsys.readouterr().err
    assert out_json.exists()
    sb = pl.load_storyboard(out_json)
    assert len(sb.shots) == 2


def test_cli_script_missing_key_message_no_longer_promises_a_broken_path(
    tmp_path, capsys, monkeypatch,
):
    """报错文案曾推荐 `--backend mock`，而那条路当时走不通 —— 现在两条都通，文案要说清。"""
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    novel = tmp_path / "novel.txt"
    novel.write_text("x", encoding="utf-8")
    rc = cli_main(["script", str(novel), "-o", str(tmp_path / "sb.json"), "--backend", "minimax"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "MINIMAX_API_KEY" in err
    # 文案应说明 mock 是可用路径，且提到「不调真 API」让人知道代价
    assert "mock" in err
    assert "不调真 API" in err