"""REQ-SCRIPT 脚本引擎单测。全部 MockBackend，不依赖真 LLM。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from hailuo_video import pipeline as pl  # noqa: E402
from hailuo_video import script_engine as se  # noqa: E402


# ---------- MockBackend ----------

def test_mock_backend_records_calls_and_returns_response():
    backend = se.MockBackend()
    backend.on("漫剧编剧", '{"title": "x", "scenes": []}')
    out = backend.chat(system="你是漫剧编剧", user="hi")
    assert out == '{"title": "x", "scenes": []}'
    assert len(backend.calls) == 1


def test_mock_backend_raises_when_no_match():
    backend = se.MockBackend()
    with pytest.raises(AssertionError):
        backend.chat(system="?", user="?")


# ---------- _extract_json ----------

def test_extract_json_from_fenced_block():
    raw = "废话 ```json\n{\"a\": 1}\n``` 尾部"
    assert se._extract_json(raw) == {"a": 1}


def test_extract_json_from_bare_braces():
    raw = '前缀 {\"a\":1} 后面'
    assert se._extract_json(raw) == {"a": 1}


def test_extract_json_from_list():
    raw = "```json\n[1,2,3]\n```"
    assert se._extract_json(raw) == [1, 2, 3]


def test_extract_json_empty_raises():
    with pytest.raises(se.LLMError, match="为空"):
        se._extract_json("")


def test_extract_json_no_json_raises():
    with pytest.raises(se.LLMError, match="未含 JSON"):
        se._extract_json("just plain text no braces")


def test_extract_json_malformed_raises():
    with pytest.raises(se.LLMError, match="解析失败"):
        se._extract_json('```json\n{not valid}\n```')


# ---------- Data class round-trip ----------

def test_script_from_dict_valid():
    data = {
        "title": "测试剧",
        "scenes": [
            {"id": 1, "time": "白天", "location": "街道",
             "characters": ["A"], "summary": "开场", "dialogue": "你好"},
        ],
    }
    s = se.Script.from_dict(data)
    assert s.title == "测试剧"
    assert len(s.scenes) == 1
    assert s.scenes[0].time == "白天"


def test_script_from_dict_missing_fields_defaults():
    data = {"title": "x", "scenes": [{"id": 1}]}
    s = se.Script.from_dict(data)
    assert s.scenes[0].location == ""
    assert s.scenes[0].characters == []


def test_script_from_dict_non_list_scenes_raises():
    with pytest.raises(se.LLMError):
        se.Script.from_dict({"title": "x", "scenes": "not a list"})


def test_character_payload_dict_with_characters_key():
    raw = json.dumps({"characters": [{"name": "A", "appearance": "高", "personality": ["刚"], "color_palette": ["红"]}]})
    chars = se._parse_characters_payload(raw)
    assert len(chars) == 1
    assert chars[0].name == "A"


def test_character_payload_bare_list():
    raw = json.dumps([{"name": "A", "appearance": "高", "personality": [], "color_palette": []}])
    chars = se._parse_characters_payload(raw)
    assert len(chars) == 1


def test_shots_payload_dict_with_shots_key():
    shots_data = [{"index": i, "scene_id": 1, "shot_type": "中景", "description": f"d{i}",
                   "characters": [], "dialogue": "", "voiceover": "", "mood": [], "camera_motion": ""}
                  for i in range(1, 4)]
    raw = json.dumps({"shots": shots_data})
    out = se._parse_shots_payload(raw, n_shots=3)
    assert len(out) == 3
    assert out[0].shot_type == "中景"


def test_shots_payload_wrong_count_raises():
    raw = json.dumps({"shots": [{"index": 1, "scene_id": 1, "shot_type": "中景", "description": "x"}]})
    with pytest.raises(se.LLMError, match="分镜数量不符"):
        se._parse_shots_payload(raw, n_shots=5)  # 调用方传 5 但 LLM 只返 1


# ---------- REQ-301 V3.2：运动指令字段 ----------

def test_shots_payload_v32_motion_fields_parsed():
    """V3.2：每个 shot 必含 subject_action / motion_complexity / duration。"""
    shots_data = [
        {"index": 1, "scene_id": 1, "shot_type": "中景", "description": "x",
         "characters": [], "dialogue": "", "voiceover": "", "mood": [],
         "camera_motion": "推近",
         "subject_action": "转身看向窗外",
         "motion_complexity": "simple",
         "duration": 6},
        {"index": 2, "scene_id": 1, "shot_type": "近景", "description": "y",
         "characters": [], "dialogue": "", "voiceover": "", "mood": [],
         "camera_motion": "固定",
         "subject_action": "点头微笑",
         "motion_complexity": "static",
         "duration": 4},
    ]
    raw = json.dumps({"shots": shots_data})
    out = se._parse_shots_payload(raw, n_shots=2)
    assert len(out) == 2
    assert out[0].subject_action == "转身看向窗外"
    assert out[0].motion_complexity == "simple"
    assert out[0].expected_duration == 6.0
    assert out[1].subject_action == "点头微笑"
    assert out[1].motion_complexity == "static"
    assert out[1].expected_duration == 4.0


def test_shots_payload_v32_expected_duration_backward_compat():
    """V3.2：旧字段 expected_duration 仍兼容。"""
    shots_data = [{"index": i, "scene_id": 1, "shot_type": "中景", "description": f"d{i}",
                   "characters": [], "dialogue": "", "voiceover": "", "mood": [],
                   "camera_motion": "", "subject_action": "x", "motion_complexity": "static",
                   "expected_duration": 5.5} for i in range(1, 4)]
    raw = json.dumps({"shots": shots_data})
    out = se._parse_shots_payload(raw, n_shots=3)
    assert out[0].expected_duration == 5.5
    assert out[0].motion_complexity == "static"


def test_shots_payload_v32_default_complexity_static_when_missing():
    """V3.2：缺省 motion_complexity → 'static'（安全 fallback，Ken Burns）。"""
    shots_data = [{"index": 1, "scene_id": 1, "shot_type": "中景", "description": "x",
                   "characters": [], "dialogue": "", "voiceover": "", "mood": [],
                   "camera_motion": ""}]  # 缺 subject_action/motion_complexity/duration
    raw = json.dumps({"shots": shots_data})
    out = se._parse_shots_payload(raw, n_shots=1)
    assert out[0].motion_complexity == "static"
    assert out[0].subject_action == ""
    assert out[0].expected_duration == 5.0


def test_shots_payload_v32_invalid_complexity_raises():
    """V3.2：motion_complexity 取值不在 {static, simple, complex} → LLMError。"""
    shots_data = [{"index": 1, "scene_id": 1, "shot_type": "中景", "description": "x",
                   "characters": [], "dialogue": "", "voiceover": "", "mood": [],
                   "camera_motion": "", "subject_action": "x",
                   "motion_complexity": "turbo", "duration": 5}]
    raw = json.dumps({"shots": shots_data})
    with pytest.raises(se.LLMError, match="motion_complexity 不合法"):
        se._parse_shots_payload(raw, n_shots=1)


def test_shots_payload_v32_invalid_duration_raises():
    """V3.2：duration 非数字 / 越界 → LLMError。"""
    # 非数字
    shots_data = [{"index": 1, "scene_id": 1, "shot_type": "中景", "description": "x",
                   "characters": [], "dialogue": "", "voiceover": "", "mood": [],
                   "camera_motion": "", "subject_action": "x",
                   "motion_complexity": "static", "duration": "abc"}]
    raw = json.dumps({"shots": shots_data})
    with pytest.raises(se.LLMError, match="duration 不合法"):
        se._parse_shots_payload(raw, n_shots=1)

    # 越界
    shots_data2 = [{"index": 1, "scene_id": 1, "shot_type": "中景", "description": "x",
                    "characters": [], "dialogue": "", "voiceover": "", "mood": [],
                    "camera_motion": "", "subject_action": "x",
                    "motion_complexity": "static", "duration": 60}]
    raw2 = json.dumps({"shots": shots_data2})
    with pytest.raises(se.LLMError, match="duration 越界"):
        se._parse_shots_payload(raw2, n_shots=1)


def test_shots_payload_v32_complex_motion_parsed():
    """V3.2：complex 类型也能解析（pipeline 决定后续 fallback）。"""
    shots_data = [{"index": 1, "scene_id": 1, "shot_type": "中景", "description": "x",
                   "characters": [], "dialogue": "", "voiceover": "", "mood": [],
                   "camera_motion": "横移", "subject_action": "打斗动作",
                   "motion_complexity": "complex", "duration": 8}]
    raw = json.dumps({"shots": shots_data})
    out = se._parse_shots_payload(raw, n_shots=1)
    assert out[0].motion_complexity == "complex"
    assert out[0].subject_action == "打斗动作"


# ---------- Pipeline（端到端 mock） ----------

def _script_raw() -> str:
    return json.dumps({
        "title": "测试剧",
        "scenes": [
            {"id": 1, "time": "白天", "location": "街道",
             "characters": ["A", "B"], "summary": "开场", "dialogue": "你好"},
            {"id": 2, "time": "夜晚", "location": "小巷",
             "characters": ["A"], "summary": "追逐", "dialogue": "站住"},
        ],
    })


def _characters_raw() -> str:
    return json.dumps({
        "characters": [
            {"name": "A", "appearance": "高个黑衣", "personality": ["冷"], "color_palette": ["黑"]},
            {"name": "B", "appearance": "矮个白衣", "personality": ["暖"], "color_palette": ["白"]},
        ],
    })


def _shots_raw(n: int = 30) -> str:
    shots = [{
        "index": i,
        "scene_id": 1 if i <= 15 else 2,
        "shot_type": "中景",
        "description": f"镜头{i}描述",
        "characters": ["A"] if i % 2 else ["B"],
        "dialogue": "",
        "voiceover": "",
        "mood": ["紧张"],
        "camera_motion": "推近",
    } for i in range(1, n + 1)]
    return json.dumps({"shots": shots})


def test_novel_to_script_returns_script():
    backend = se.MockBackend()
    backend.on("漫剧编剧", _script_raw())
    script = se.novel_to_script("某小说片段...", backend)
    assert script.title == "测试剧"
    assert len(script.scenes) == 2
    assert script.scenes[0].time == "白天"


def test_novel_to_script_empty_raises():
    backend = se.MockBackend()
    with pytest.raises(se.LLMError, match="novel 为空"):
        se.novel_to_script("", backend)


def test_novel_to_script_whitespace_only_raises():
    backend = se.MockBackend()
    with pytest.raises(se.LLMError):
        se.novel_to_script("   \n\t  ", backend)


def test_script_to_characters():
    script = se.Script(title="t", scenes=[
        se.Scene(id=1, time="白天", location="街道", characters=["A"]),
    ])
    backend = se.MockBackend()
    backend.on("角色设计师", _characters_raw())
    chars = se.script_to_characters(script, backend)
    assert len(chars) == 2
    assert chars[0].name == "A"


def test_script_to_storyboard_correct_count():
    script = se.Script(title="t", scenes=[
        se.Scene(id=1, time="白天", location="街道", characters=["A", "B"]),
        se.Scene(id=2, time="夜晚", location="小巷", characters=["A"]),
    ])
    chars = [se.Character(name="A", appearance="x", personality=[], color_palette=[]),
             se.Character(name="B", appearance="y", personality=[], color_palette=[])]
    backend = se.MockBackend()
    backend.on("分镜师", _shots_raw(30))
    shots = se.script_to_storyboard(script, chars, backend, n_shots=30)
    assert len(shots) == 30
    assert shots[0].shot_type == "中景"


def test_script_to_storyboard_wrong_count_raises():
    script = se.Script(title="t", scenes=[se.Scene(id=1, time="白天", location="x")])
    chars: list[se.Character] = []
    backend = se.MockBackend()
    backend.on("分镜师", _shots_raw(10))  # LLM 只返 10
    with pytest.raises(se.LLMError, match="分镜数量不符"):
        se.script_to_storyboard(script, chars, backend, n_shots=30)


def test_novel_to_storyboard_end_to_end():
    backend = se.MockBackend()
    backend.on("漫剧编剧", _script_raw())
    backend.on("角色设计师", _characters_raw())
    backend.on("分镜师", _shots_raw(5))  # 5 镜够测
    sb = se.novel_to_storyboard("某小说片段", backend, n_shots=5)
    assert sb.title == "测试剧"
    assert len(sb.characters) == 2
    assert len(sb.shots) == 5


def test_novel_to_storyboard_propagates_parse_error():
    backend = se.MockBackend()
    backend.on("漫剧编剧", "garbled output")
    with pytest.raises(se.LLMError):
        se.novel_to_storyboard("novel", backend, n_shots=5)


# ---------- MiniMaxChatBackend ----------

@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """env 干净；默认无 Key。"""
    for k in se._DOTENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    yield


def test_minimax_backend_no_key_raises():
    with pytest.raises(se.LLMError, match="MINIMAX_API_KEY 未设置"):
        se.MiniMaxChatBackend()


def test_minimax_backend_short_key_raises():
    with pytest.raises(se.LLMError, match="长度.*< 16"):
        se.MiniMaxChatBackend(api_key_value="short")


def test_minimax_backend_uses_default_base_url():
    backend = se.MiniMaxChatBackend(api_key_value="x" * 20)
    assert backend._base_url == se.DEFAULT_BASE_URL.rstrip("/")
    assert backend._model == se.DEFAULT_MODEL


def test_minimax_backend_custom_args():
    backend = se.MiniMaxChatBackend(
        api_key_value="x" * 20,
        base_url_value="https://other.example.com/v1/",
        model_value="Custom-Model",
    )
    assert backend._base_url == "https://other.example.com/v1"
    assert backend._model == "Custom-Model"


def test_minimax_backend_chat_with_injected_post():
    """注入 fake http_post → 验证响应解析路径。"""
    backend = se.MiniMaxChatBackend(
        api_key_value="x" * 20,
        http_post=lambda url, body, headers: {
            "choices": [{"message": {"content": "hello"}}]
        },
    )
    out = backend.chat(system="sys", user="usr")
    assert out == "hello"


def test_minimax_backend_chat_bad_structure_raises():
    backend = se.MiniMaxChatBackend(
        api_key_value="x" * 20,
        http_post=lambda url, body, headers: {"choices": []},
    )
    with pytest.raises(se.LLMError, match="响应结构不符"):
        backend.chat(system="s", user="u")


def test_minimax_backend_chat_http_error_raises():
    import urllib.error

    def boom(url, body, headers):
        raise urllib.error.HTTPError(url, 415, "Unsupported Media Type", {}, None)

    backend = se.MiniMaxChatBackend(api_key_value="x" * 20, http_post=boom)
    with pytest.raises(se.LLMError, match="HTTP 415"):
        backend.chat(system="s", user="u")


def test_minimax_backend_chat_url_error_raises():
    import urllib.error

    def boom(url, body, headers):
        raise urllib.error.URLError("Connection refused")

    backend = se.MiniMaxChatBackend(api_key_value="x" * 20, http_post=boom)
    with pytest.raises(se.LLMError, match="URLError"):
        backend.chat(system="s", user="u")


def test_minimax_backend_chat_non_dict_response_raises():
    """http_post 返回非 dict（字符串/None 等）→ 硬失败。"""
    backend = se.MiniMaxChatBackend(
        api_key_value="x" * 20,
        http_post=lambda url, body, headers: "<html>not json</html>",
    )
    with pytest.raises(se.LLMError, match="非 dict"):
        backend.chat(system="s", user="u")


def test_minimax_backend_chat_dict_with_invalid_json_response_raises():
    """http_post 返回 dict 但 choices 结构不对。"""
    backend = se.MiniMaxChatBackend(
        api_key_value="x" * 20,
        http_post=lambda url, body, headers: {"choices": []},
    )
    with pytest.raises(se.LLMError, match="响应结构不符"):
        backend.chat(system="s", user="u")


def test_minimax_backend_does_not_leak_api_key_in_logs(caplog):
    """确认 chat() 路径不打印 API Key 字面值。"""
    backend = se.MiniMaxChatBackend(
        api_key_value="secret-key-DO-NOT-LEAK-1234567890",
        http_post=lambda url, body, headers: {
            "choices": [{"message": {"content": "ok"}}]
        },
    )
    with caplog.at_level("DEBUG"):
        backend.chat(system="s", user="u")
    assert "secret-key" not in caplog.text


# ---------- 配置 ----------

def test_is_llm_configured_requires_key():
    assert se.is_llm_configured() is False


def test_is_llm_configured_requires_min_key_length(monkeypatch):
    import os
    monkeypatch.setenv('MINIMAX_API_KEY', "short")
    assert se.is_llm_configured() is False
    monkeypatch.setenv('MINIMAX_API_KEY', "x" * 20)
    assert se.is_llm_configured() is True


def test_configuration_warnings_empty_when_clean(monkeypatch):
    """未设 Key 时有警告。"""
    import os
    monkeypatch.setenv('MINIMAX_API_KEY', "x" * 20)
    warns = se.configuration_warnings()
    # Key OK，但 base_url 是默认 → 警告有
    assert any("端点未公开核实" in w for w in warns)


def test_configuration_warnings_short_key(monkeypatch):
    import os
    monkeypatch.setenv('MINIMAX_API_KEY', "abc")
    warns = se.configuration_warnings()
    assert any("长度" in w for w in warns)


# ---------- 文件 IO ----------

def test_write_and_read_storyboard_round_trip(tmp_path):
    sb = se.Storyboard(
        title="t",
        characters=[se.Character(name="A", appearance="x")],
        shots=[se.Shot(index=1, scene_id=1, shot_type="中景", description="d")],
    )
    path = se.write_storyboard(sb, tmp_path / "sb.json")
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["title"] == "t"
    assert data["characters"][0]["name"] == "A"
    assert data["shots"][0]["shot_type"] == "中景"


def test_read_novel_strips_whitespace(tmp_path):
    p = tmp_path / "n.txt"
    p.write_text("  小说内容  \n\n", encoding="utf-8")
    assert se.read_novel(p) == "小说内容"


# ---------- CLI ----------

from hailuo_video.cli import main as cli_main


def test_cli_script_missing_file(capsys, tmp_path):
    rc = cli_main(["script", str(tmp_path / "nope.txt"), "-o", str(tmp_path / "out.json")])
    err = capsys.readouterr().err
    assert rc == 2
    assert "不存在" in err


def test_cli_script_empty_file(capsys, tmp_path):
    p = tmp_path / "empty.txt"
    p.write_text("   \n  \t", encoding="utf-8")
    rc = cli_main(["script", str(p), "-o", str(tmp_path / "out.json")])
    err = capsys.readouterr().err
    assert rc == 2
    assert "为空" in err


def test_cli_script_mock_backend_now_succeeds(monkeypatch, tmp_path, capsys):
    """`--backend mock` 现在真能跑通（走 CliMockBackend 的 canned 响应）。

    ⚠️ 本测试**替换**了原 `test_cli_script_mock_backend_without_preset_fails`
    —— 后者断言 rc==1 + "MockBackend 无预设响应"，即把 D1 的缺陷当成了预期行为。
    那条断言在修复前并不算「写错」：当时 cmd_script 确实走 se.make_backend("mock")
    → 严格 MockBackend → 必然 AssertionError。但那是缺陷不是契约，故改为断言修复后
    的正确行为，而非删掉断言换绿。严格语义本身未动，护栏见
    tests/test_cli_mock_script.py::test_mock_backend_still_raises_on_unregistered_prompt。
    提案：docs/drafts/script_mock_backend_draft.md
    """
    novel = tmp_path / "novel.txt"
    novel.write_text("从前有个少年...", encoding="utf-8")
    out = tmp_path / "sb.json"
    rc = cli_main([
        "script", str(novel),
        "-o", str(out),
        "--backend", "mock",
        "--shots", "5",
    ])
    err = capsys.readouterr().err
    assert rc == 0, err
    assert out.exists()
    assert len(pl.load_storyboard(out).shots) == 5


def test_cli_script_mock_with_preset(monkeypatch, tmp_path, capsys):
    """MockBackend 预设响应 → 完整跑通。"""
    backend = se.MockBackend()
    backend.on("漫剧编剧", _script_raw())
    backend.on("角色设计师", _characters_raw())
    backend.on("分镜师", _shots_raw(5))

    # 替换 make_backend 返回预设好的 mock
    monkeypatch.setattr(se, "make_backend", lambda prefer="auto": backend)

    novel = tmp_path / "novel.txt"
    novel.write_text("从前有个少年...", encoding="utf-8")
    out = tmp_path / "sb.json"
    rc = cli_main([
        "script", str(novel),
        "-o", str(out),
        "--backend", "mock",
        "--shots", "5",
    ])
    err = capsys.readouterr().err
    assert rc == 0
    assert "[OK]" in err
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["title"] == "测试剧"
    assert len(data["shots"]) == 5


def test_cli_script_minimax_without_key(capsys, tmp_path):
    """未设 Key → minimax backend 硬失败。"""
    novel = tmp_path / "novel.txt"
    novel.write_text("从前有个少年...", encoding="utf-8")
    out = tmp_path / "sb.json"
    rc = cli_main([
        "script", str(novel),
        "-o", str(out),
        "--backend", "minimax",
        "--shots", "5",
    ])
    err = capsys.readouterr().err
    assert rc == 2
    assert "MINIMAX_API_KEY 未设置" in err or "长度不足" in err


def test_cli_script_unknown_backend(capsys, tmp_path):
    novel = tmp_path / "novel.txt"
    novel.write_text("从前有个少年...", encoding="utf-8")
    # argparse choices 校验会 SystemExit(2)，py test 自带 pytest.raises 可接
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["script", str(novel), "-o", "/tmp/out.json", "--backend", "nonsense"])
    assert exc_info.value.code == 2


def test_cli_script_help_renders_chinese(capsys):
    """L-009 UTF-8 烟测：argparse --help 输出到 stdout 后 SystemExit(0)。"""
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["script", "--help"])
    assert exc_info.value.code == 0
    out = capsys.readouterr()
    # argparse --help 输出到 stdout
    assert "novel_file" in out.out
    assert "storyboard" in out.out or "剧本" in out.out or "JSON" in out.out
