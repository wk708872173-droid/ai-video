"""V5.0 Batch 1（P0）— 真实 LLM 路径的四个叠加缺陷。

## 背景

2026-11-03 用户更新了 `.env` 的 API key，验收时发现**真 LLM 路径从未跑通过**。
PRD 里那条 `R1 [UNVERIFIED]`（端点/模型/响应结构未核实）是准确的 ——
项目一直在用 mock 跑测试，真路径从来没被端到端验证过。

实测（`MiniMax-M3`，真 API）暴露了 **4 个叠加**的缺陷，
任意一个单独存在都足以让真路径失败：

| # | 缺陷 | 症状 | 修法 |
|---|---|---|---|
| 1 | **prompt 与 schema 自相矛盾** | `NOVEL_TO_SCRIPT_PROMPT` 要求"改编为一段 **30 镜头**的漫剧剧本"，但输出 schema **没有 shots 字段**（分镜是第 3 步才生成）。模型为自洽疯狂推理 | prompt 改成"只写场景级剧本"，并显式说明"不要写分镜表" |
| 2 | **请求体没有 `response_format`** | 提示词里写"输出严格 JSON"只是祈使句，不是约束。实测 `response_format={"type":"json_object"}` **被 API 支持** | 请求体加该字段 |
| 3 | **推理模型内联 `<think>` 块** | `MiniMax-M3` 的 content 形如 `<think>...推理...</think>{json}` → JSON 解析必然失败 | 解析前 `strip_reasoning()` |
| 4 | **`max_tokens` / `timeout` 太小** | 推理 token 与输出**共享**预算：4096 被推理吃光 → `finish_reason=length` → content 为空 → "LLM 返回为空"；60s 超时把 95s 的成功请求打成 TimeoutError | `MAX_TOKENS` 4096→8192、`REQUEST_TIMEOUT` 60→180 |

外加一个渲染层缺陷：

| # | 缺陷 | 症状 | 修法 |
|---|---|---|---|
| 5 | **`{{` / `}}` 转义没还原** | 模板的双花括号是 `.format()` 时代写法，渲染器改成 `.replace()` 后**没人还原** → 模型看到 `{{ ... }}` 这种非法 JSON 语法 | 渲染器先反转义再替换 |

R1：全程 **不调用真 API**（计费端点留给用户手工烟测）。
本文件锁的是**请求体形状**与**解析容错**，这些用注入的 http_post 即可验证。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SANDBOX = REPO_ROOT / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from hailuo_video import prompts as pr  # noqa: E402
from hailuo_video import script_engine as se  # noqa: E402


# ---------- 缺陷 5：{{ }} 转义还原 ----------

def test_format_prompt_unescapes_double_braces():
    """渲染后的模板里不该再有 `{{` / `}}`（模型看到的是非法 JSON 语法）。"""
    out = se._format_prompt('输出 JSON：\n{{\n  "a": {v}\n}}\n', v="1")
    assert "{{" not in out, f"双花括号泄漏到 prompt：{out!r}"
    assert "}}" not in out
    assert '"a": 1' in out


def test_safe_fill_unescapes_double_braces():
    out = pr._safe_fill('```json\n{{\n  "x": {x}\n}}\n```', x="7")
    assert "{{" not in out
    assert "}}" not in out
    assert '"x": 7' in out


def test_unescape_does_not_corrupt_inserted_values():
    """反转义只处理**双**花括号；插入值里的单花括号（json.dumps）必须完好。"""
    payload = json.dumps({"a": 1, "b": [2, 3]}, ensure_ascii=False)
    out = se._format_prompt("{{" + "pre" + "}}\n{novel}\n{{post}}", novel=payload)
    assert payload in out, "插入值被反转义破坏了"
    assert out.startswith("{pre}")


def test_rendered_storyboard_prompt_has_no_double_braces():
    """`render_storyboard_prompt` 是生产真实使用的渲染器。"""
    out = pr.render_storyboard_prompt(
        n_shots=3, script_json="{}", characters_json="[]",
    )
    assert "{{" not in out, "STORYBOARD_PROMPT 渲染后仍含双花括号"
    assert "}}" not in out


# ---------- 缺陷 3：推理块剥离 ----------

def test_strip_reasoning_removes_closed_think_block():
    raw = '<think>We need answer strict JSON only.</think>\n{"title": "t"}'
    out = se.strip_reasoning(raw)
    assert "<think>" not in out
    assert json.loads(out)["title"] == "t"


def test_strip_reasoning_removes_unclosed_think_block():
    """推理耗尽 token 时 think 块不闭合 —— 从 <think> 到文末都要丢。"""
    raw = '<think>We need answer strict JSON only... {"partial": '
    out = se.strip_reasoning(raw)
    assert "<think>" not in out
    assert out.strip() == ""


def test_strip_reasoning_is_idempotent_and_noop():
    raw = '{"title": "t"}'
    assert se.strip_reasoning(raw) == raw
    once = se.strip_reasoning('<think>x</think>{"a":1}')
    assert se.strip_reasoning(once) == once


def test_extract_json_parses_think_prefixed_json():
    """端到端：带 think 前缀的输出能被解析（这是 MiniMax-M3 的真实形态）。"""
    raw = '<think>reasoning here</think>{"title": "橘猫望天", "style_anchor": "写实"}'
    data = se._extract_json(raw)
    assert data["title"] == "橘猫望天"


def test_extract_json_rejects_reasoning_only_output():
    with pytest.raises(se.LLMError) as exc:
        se._extract_json("<think>only reasoning, no answer</think>")
    assert "推理" in str(exc.value)


# ---------- 缺陷 2：请求体带 response_format ----------

def _capture_body(monkeypatch, response: dict) -> dict:
    """用注入的 http_post 抓一次真实请求体。"""
    captured: dict = {}

    def _fake_post(url, body, headers):
        captured["url"] = url
        captured["body"] = body
        captured["headers"] = headers
        return response

    monkeypatch.setattr(se, "_load_dotenv", lambda: None)
    backend = se.MiniMaxChatBackend(
        api_key_value="k" * 32, base_url_value="https://example.invalid/v1",
        model_value="test-model", http_post=_fake_post,
    )
    backend.chat(system="s", user="u")
    return captured


def test_request_body_has_json_object_response_format(monkeypatch):
    """提示词说"输出严格 JSON"不够 —— 请求体必须带 response_format。"""
    resp = {"choices": [{"message": {"content": "{}"}}], "usage": {}}
    cap = _capture_body(monkeypatch, resp)
    assert cap["body"].get("response_format") == {"type": "json_object"}


def test_request_body_max_tokens_has_headroom(monkeypatch):
    """推理 token 与输出共享预算 —— max_tokens 太小会 finish_reason=length。"""
    resp = {"choices": [{"message": {"content": "{}"}}], "usage": {}}
    cap = _capture_body(monkeypatch, resp)
    assert cap["body"]["max_tokens"] >= 8192, (
        f"max_tokens={cap['body']['max_tokens']} 太小，推理会吃光预算"
    )


def test_request_timeout_is_large_enough_for_reasoning_models():
    """实测真实请求 95s（第一步入场推理重）—— 60s 上限会把成功请求打成超时。"""
    assert se.REQUEST_TIMEOUT >= 180, (
        f"REQUEST_TIMEOUT={se.REQUEST_TIMEOUT} < 180 —— 实测真实请求耗时 95s"
    )


# ---------- 空 content + reasoning_content：明确报错 ----------

def test_empty_content_with_reasoning_reports_token_exhaustion(monkeypatch):
    """content 空但有 reasoning_content → 报"推理吃光预算"，不是"返回为空"。"""
    resp = {
        "choices": [{
            "message": {"content": "", "reasoning_content": "long thinking..."},
            "finish_reason": "length",
        }],
        "usage": {},
    }
    monkeypatch.setattr(se, "_load_dotenv", lambda: None)
    backend = se.MiniMaxChatBackend(
        api_key_value="k" * 32, base_url_value="https://example.invalid/v1",
        model_value="test-model", http_post=lambda u, b, h: resp,
    )
    with pytest.raises(se.LLMError) as exc:
        backend.chat(system="s", user="u")
    msg = str(exc.value)
    assert "max_tokens" in msg or "推理" in msg, (
        f"报错没有指出真正原因（推理吃光 token 预算）：{msg}"
    )


# ---------- 缺陷 1：prompt 与 schema 不再矛盾 ----------

def test_novel_to_script_prompt_does_not_demand_shots():
    """第 1 步只产出场景级剧本 —— prompt 不能再要求"30 镜头"。

    schema 里没有 shots 字段（分镜是第 3 步 `script_to_storyboard` 的活），
    prompt 却要求"30 镜头" → 模型为自洽疯狂推理 → 推理吃光 token 预算。
    """
    assert "30 镜头" not in pr.NOVEL_TO_SCRIPT_PROMPT, (
        "prompt 仍要求 30 镜头，但输出 schema 没有 shots 字段 —— 自相矛盾"
    )
    assert "不要写分镜表" in pr.NOVEL_TO_SCRIPT_PROMPT, (
        "prompt 应显式声明本步不写分镜表"
    )


def test_novel_to_script_prompt_scene_count_matches_schema():
    """场景数 5-8 与 schema 的 scenes 数组一致，且字数与之匹配。"""
    assert "5-8 个" in pr.NOVEL_TO_SCRIPT_PROMPT
    # 500-800 字配 30 镜头 = 每镜 20 字，明显不合理；改后 400-700 配 5-8 场
    assert "500-800 字" not in pr.NOVEL_TO_SCRIPT_PROMPT
