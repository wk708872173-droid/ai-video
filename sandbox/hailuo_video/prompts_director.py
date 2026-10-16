"""V3.5 Director 升级：导演级 Prompt 模板（多角色协作：编剧 + 分镜师 + 摄影指导 + 剪辑师 + 运营）。

CLAUDE.md §5 设计原则：
  - 强约束 JSON 输出（fenced ```json），便于程序化解析
  - 提供 schema 示例让 LLM 知道字段名 / 类型
  - 多角色协作：每个 role 单一职责，角色之间通过 dataclass 字段传递

V3.5 Phase A：仅放置常量、角色 prompt 模板、parser 入口。
V3.5 Phase B：`agents_director.py` 会用 `DirectorTeam` 调度 5 角色 LLM 调用。

设计目标：
  - 不破坏现有 prompts.py（保持向后兼容）
  - 每个 role 独立 system prompt + user prompt
  - 白名单常量集中管理（DOP 焦段 / 剪辑师转场 / 运营时段）
  - 解析端 lenient：白名单外的值回退空串（不抛错）
"""
from __future__ import annotations

import json
import re
from typing import Any


# ---------- 共用：导演视角段落（注入到 STORYBOARD_PROMPT） ----------

DIRECTOR_GUIDANCE_BLOCK = """导演视角（每个镜头必须含）：
- 构图（三分 / 居中 / 引导线 / 对称）
- 镜头语言（景别 + 焦段感，wide / 35mm / 50mm / 85mm / telephoto）
- 光线（主光方向 + 反差 + 特殊：剪影 / 逆光 / 侧光 / 顶光）
- 视线方向（角色看向哪 + 视线匹配 eyeline match）
- 屏幕方向（主体运动 L→R / R→L，保持 180°轴线规则）
- 节奏（hold / 留白 / 推拉速度）

禁止纯抽象形容词（"很温馨" ❌）→ 改为可视化描述（"暖黄金色光边勾出绒毛轮廓" ✓）。
"""


# ---------- 5 角色系统 Prompt（中文 + 英文混合，关键术语括号） ----------

DOP_SYSTEM_PROMPT = (
    "你是一位资深摄影指导（Director of Photography, DOP），"
    "擅长 ARRI Alexa Mini LF + Cooke S7/i Full Frame Plus 电影镜头语言。"
    "你为每个 shot 补充：lens（焦段）/ depth_of_field（景深）/ "
    "lighting（光线方向 + 反差）/ screen_direction（屏幕方向 L→R / R→L）/ "
    "color_grade（色彩调性：teal-and-orange / 暖调 / 冷调 等）。"
    "你的输出严格 JSON，不要任何额外文字。"
)

EDITOR_SYSTEM_PROMPT = (
    "你是一位资深短视频剪辑师，擅长竖屏 9:16 短视频节奏（小红书 / 抖音）。"
    "你为每个 shot 补充：transition_in（入场转场）/ transition_out（出场转场）/ "
    "pacing（节奏 快 / 中 / 慢）/ bgm_cue（背景音乐 / 音效提示）。"
    "你的输出严格 JSON，不要任何额外文字。"
)

OPERATOR_SYSTEM_PROMPT = (
    "你是一位小红书运营专家，熟悉萌宠治愈系内容的爆款规律。"
    "你为整条 storyboard 生成：hook（前 3 秒钩子文案）/ "
    "captions（3-5 条备选标题）/ hashtags（5-10 个标签）/ "
    "best_post_time（建议发布时间，如 19:30-21:00 流量高峰）。"
    "你的输出严格 JSON，不要任何额外文字。"
)


# ---------- 角色 User Prompt（V1） ----------

DOP_PROMPT_V1 = """请为以下 {n_shots} 个分镜补充摄影指导参数（参考 ARRI Alexa Mini LF 电影镜头语言）。

输出格式（严格 JSON，不要任何额外文字）：
```json
{{
  "dop": [
    {{
      "shot_id": "shot_001",
      "lens": "35mm|50mm|85mm|wide|telephoto（从白名单选）",
      "depth_of_field": "shallow|deep",
      "lighting": "主光方向 + 反差 + 特殊光线（剪影 / 逆光 / 侧光 / 顶光 等）",
      "screen_direction": "L→R|R→L|无（保持 180°轴线规则）",
      "color_grade": "teal-and-orange|暖调|冷调|暖金色（黄昏）|冷蓝调（夜）|自然中性"
    }}
  ]
}}
```

约束：
- lens 仅从白名单选：{lens_whitelist}
- screen_direction 必须保持轴线规则（180° axis of action）
- lighting 至少含「主光方向」（camera-left / camera-right / front / back）
- 拒答：纯抽象描述（"光线好" ❌）→ 改为可视化（"暖黄金色侧光从 camera-right 切入" ✓）

分镜列表：
{shots_json}
"""


EDITOR_PROMPT_V1 = """请为以下 {n_shots} 个分镜补充剪辑参数（小红书 9:16 竖屏短视频节奏）。

输出格式（严格 JSON，不要任何额外文字）：
```json
{{
  "editor": [
    {{
      "shot_id": "shot_001",
      "transition_in": "cut|dissolve|match_cut|fade（从白名单选）",
      "transition_out": "cut|dissolve|match_cut|fade（从白名单选）",
      "pacing": "快|中|慢（从白名单选）",
      "bgm_cue": "soft piano|silence|tense strings|acoustic guitar|nature ambient|..."
    }}
  ]
}}
```

约束：
- transition_in/out 仅从白名单选：{transition_whitelist}
- pacing 影响：快 = < 4s/shot, 中 = 4-6s, 慢 = > 6s
- bgm_cue 自由文本但应贴合 shot.mood（如「治愈」→ soft piano / nature ambient）
- 第一个 shot 的 transition_in 固定为 "fade"（开场淡入）
- 最后一个 shot 的 transition_out 固定为 "fade"（收尾淡出）

分镜列表：
{shots_json}
"""


OPERATOR_PROMPT_V1 = """请为以下小红书萌宠治愈系短视频生成发布策略。

输出格式（严格 JSON，不要任何额外文字）：
```json
{{
  "operator": {{
    "hook": "前 3 秒钩子文案（10-20 字，能量钩住观众）",
    "captions": ["标题 1（≤ 20 字）", "标题 2", "标题 3", "标题 4", "标题 5"],
    "hashtags": ["#标签1", "#标签2", "#标签3", "#标签4", "#标签5", "#标签6", "#标签7", "#标签8"],
    "best_post_time": "19:30-21:00（建议发布时间 + 原因）",
    "platform_recos": {{
      "xiaohongshu": "封面：奶黄幼犬特写 / 标题用第 1 条 caption",
      "douyin": "封面：治愈系空镜 / 标题强调 '今天也要开心鸭'",
      "bilibili": "标题偏长：'奶黄幼犬治愈一日·ARRI 电影质感'"
    }}
  }}
}}
```

约束：
- hook 必须是"动作+情绪"组合（"奶黄幼犬伸了个懒腰——然后整个下午都甜了" ✓）
- captions 至少 3 条 ≤ 20 字（小红书标题上限）
- hashtags 含 1-2 个大流量标签（#治愈 #萌宠）+ 1-2 个垂直标签（#中华田园犬 #幼犬）
- best_post_time 按小红书萌宠内容黄金时段：19:30-21:00 / 12:00-13:30
- platform_recos 至少 3 个平台（小红书 / 抖音 / B站）

Storyboard 信息：
{storyboard_json}
"""


# ---------- 白名单常量（SSOT） ----------

LENS_WHITELIST: tuple[str, ...] = (
    "wide", "35mm", "50mm", "85mm", "telephoto",
)

TRANSITION_WHITELIST: tuple[str, ...] = (
    "cut", "dissolve", "match_cut", "fade",
)

PACING_WHITELIST: tuple[str, ...] = (
    "快", "中", "慢",
)

NARRATIVE_BEAT_WHITELIST: tuple[str, ...] = (
    "起", "承", "转", "合",
)

DEPTH_OF_FIELD_WHITELIST: tuple[str, ...] = (
    "shallow", "deep",
)

DIRECTOR_ROLE_WHITELISTS: dict[str, tuple[str, ...]] = {
    "lens": LENS_WHITELIST,
    "transition": TRANSITION_WHITELIST,
    "pacing": PACING_WHITELIST,
    "narrative_beat": NARRATIVE_BEAT_WHITELIST,
    "depth_of_field": DEPTH_OF_FIELD_WHITELIST,
}


# ---------- 解析器（V1 lenient：白名单外值回退空串） ----------

# 与 script_engine._extract_json 相同的 fenced code block 抽取
_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL)


def _extract_json_loose(text: str) -> Any:
    """从 LLM 输出中抽取 JSON。优先级：fenced code block → 整段 → 找首个 {/[ 配对。
    与 script_engine._extract_json 行为一致，但更宽松（不抛错，返回 None）。"""
    if not text or not text.strip():
        return None
    m = _JSON_FENCE.search(text)
    if m:
        candidate = m.group(1)
    else:
        first_brace = text.find("{")
        first_bracket = text.find("[")
        if first_brace == -1 and first_bracket == -1:
            return None
        if first_brace == -1:
            start, end_char = first_bracket, "]"
        elif first_bracket == -1:
            start, end_char = first_brace, "}"
        else:
            if first_brace < first_bracket:
                start, end_char = first_brace, "}"
            else:
                start, end_char = first_bracket, "]"
        end = text.rfind(end_char)
        if end == -1 or end <= start:
            return None
        candidate = text[start : end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def _whitelist_or_empty(value: Any, whitelist: tuple[str, ...]) -> str:
    """V1 lenient 校验：值不在白名单 → 返回空串（不抛错）。"""
    if isinstance(value, str) and value in whitelist:
        return value
    return ""


def parse_dop_response(raw: str) -> dict[str, dict[str, str]]:
    """解析 DOP 输出 → {shot_id: {lens, depth_of_field, lighting, screen_direction, color_grade}}。

    lenient：JSON 解析失败或缺字段 → 返回空 dict（不抛错，让调用方降级）。
    """
    data = _extract_json_loose(raw)
    if not isinstance(data, dict):
        return {}
    dop_list = data.get("dop")
    if not isinstance(dop_list, list):
        return {}

    out: dict[str, dict[str, str]] = {}
    for item in dop_list:
        if not isinstance(item, dict):
            continue
        shot_id = str(item.get("shot_id", "") or "")
        if not shot_id:
            continue
        out[shot_id] = {
            "lens": _whitelist_or_empty(item.get("lens"), LENS_WHITELIST),
            "depth_of_field": _whitelist_or_empty(
                item.get("depth_of_field"), DEPTH_OF_FIELD_WHITELIST
            ),
            "lighting": str(item.get("lighting", "") or ""),
            "screen_direction": str(item.get("screen_direction", "") or ""),
            "color_grade": str(item.get("color_grade", "") or ""),
        }
    return out


def parse_editor_response(raw: str) -> dict[str, dict[str, str]]:
    """解析 Editor 输出 → {shot_id: {transition_in, transition_out, pacing, bgm_cue}}。"""
    data = _extract_json_loose(raw)
    if not isinstance(data, dict):
        return {}
    editor_list = data.get("editor")
    if not isinstance(editor_list, list):
        return {}

    out: dict[str, dict[str, str]] = {}
    for item in editor_list:
        if not isinstance(item, dict):
            continue
        shot_id = str(item.get("shot_id", "") or "")
        if not shot_id:
            continue
        out[shot_id] = {
            "transition_in": _whitelist_or_empty(
                item.get("transition_in"), TRANSITION_WHITELIST
            ),
            "transition_out": _whitelist_or_empty(
                item.get("transition_out"), TRANSITION_WHITELIST
            ),
            "pacing": _whitelist_or_empty(item.get("pacing"), PACING_WHITELIST),
            "bgm_cue": str(item.get("bgm_cue", "") or ""),
        }
    return out


def parse_operator_response(raw: str) -> dict[str, Any]:
    """解析 Operator 输出 → OperatorBrief dict。"""
    data = _extract_json_loose(raw)
    if not isinstance(data, dict):
        return {}
    op = data.get("operator")
    if not isinstance(op, dict):
        return {}

    return {
        "hook": str(op.get("hook", "") or ""),
        "captions": list(op.get("captions", []) or []),
        "hashtags": list(op.get("hashtags", []) or []),
        "best_post_time": str(op.get("best_post_time", "") or ""),
        "platform_recos": dict(op.get("platform_recos", {}) or {}),
    }


# ---------- 便利函数：把 DOP/Editor 结果回填到 shot dict ----------

def apply_dop_to_shot(shot_dict: dict[str, Any], dop_entry: dict[str, str]) -> None:
    """V3.5 Director：把 DOP 输出回填到单 shot dict（仅回填空字段，幂等）。"""
    if not dop_entry:
        return
    if not shot_dict.get("lens"):
        shot_dict["lens"] = dop_entry.get("lens", "")
    if not shot_dict.get("depth_of_field"):
        shot_dict["depth_of_field"] = dop_entry.get("depth_of_field", "")
    if not shot_dict.get("lighting"):
        shot_dict["lighting"] = dop_entry.get("lighting", "")
    if not shot_dict.get("screen_direction"):
        shot_dict["screen_direction"] = dop_entry.get("screen_direction", "")
    if not shot_dict.get("color_grade"):
        shot_dict["color_grade"] = dop_entry.get("color_grade", "")


def apply_editor_to_shot(shot_dict: dict[str, Any], editor_entry: dict[str, str]) -> None:
    """V3.5 Director：把 Editor 输出回填到单 shot dict（仅回填空字段，幂等）。"""
    if not editor_entry:
        return
    if not shot_dict.get("transition_in"):
        shot_dict["transition_in"] = editor_entry.get("transition_in", "")
    if not shot_dict.get("transition_out"):
        shot_dict["transition_out"] = editor_entry.get("transition_out", "")
    if not shot_dict.get("pacing"):
        shot_dict["pacing"] = editor_entry.get("pacing", "")
    if not shot_dict.get("bgm_cue"):
        shot_dict["bgm_cue"] = editor_entry.get("bgm_cue", "")


__all__ = [
    # 系统 prompt
    "DOP_SYSTEM_PROMPT",
    "EDITOR_SYSTEM_PROMPT",
    "OPERATOR_SYSTEM_PROMPT",
    # User prompt 模板
    "DOP_PROMPT_V1",
    "EDITOR_PROMPT_V1",
    "OPERATOR_PROMPT_V1",
    # 白名单
    "LENS_WHITELIST",
    "TRANSITION_WHITELIST",
    "PACING_WHITELIST",
    "NARRATIVE_BEAT_WHITELIST",
    "DEPTH_OF_FIELD_WHITELIST",
    "DIRECTOR_ROLE_WHITELISTS",
    # 共用段落
    "DIRECTOR_GUIDANCE_BLOCK",
    # 解析器
    "parse_dop_response",
    "parse_editor_response",
    "parse_operator_response",
    # 回填助手
    "apply_dop_to_shot",
    "apply_editor_to_shot",
]
