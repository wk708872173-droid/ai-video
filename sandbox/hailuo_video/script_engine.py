"""剧本生成引擎：novel → script → characters → storyboard。

V2.1：因 D-011 立本地视频推理后，漫剧制作还需 LLM 辅助剧本编写 + 分镜拆解。
后端抽象：LLMBackend (ABC) → MiniMaxChatBackend (生产) / MockBackend (单测)。

D-010 应用：MiniMax chat 端点未公开核实，按"必填 + 缺失硬失败"模式实现。
R1 [UNVERIFIED]：MINIMAX_BASE_URL=https://api.minimax.io/v1 + MiniMax-M3 端点 / 字段需首次烟测验证。
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from hailuo_video.local_client import PACKAGE_ROOT
from hailuo_video.prompts import (
    CHARACTER_EXTRACTION_PROMPT,
    NOVEL_TO_SCRIPT_PROMPT,
    STORYBOARD_PROMPT,
)
# V3.5 Director 升级：白名单复用（prompts_director 是 leaf 模块，无循环 import 风险）
from hailuo_video.prompts_director import (
    DEPTH_OF_FIELD_WHITELIST,
    LENS_WHITELIST,
    NARRATIVE_BEAT_WHITELIST,
    PACING_WHITELIST,
    TRANSITION_WHITELIST,
)


SANDBOX_SCRIPT_DIR = PACKAGE_ROOT / "scripts_out"


# ---------- 配置 ----------

class LLMError(RuntimeError):
    """LLM 调用 / 解析失败。"""


_DOTENV_KEYS = (
    "MINIMAX_API_KEY",
    "MINIMAX_BASE_URL",
    "MINIMAX_MODEL",
)

DEFAULT_BASE_URL = "https://api.minimaxi.com/v1"  # D-013 用户核实：CN 域
DEFAULT_MODEL = "MiniMax-M3"
REQUEST_TIMEOUT = 60


def _load_dotenv() -> None:
    """读仓库根 .env（多路径 fallback）。沿用 V2-Local 的不覆盖已有模式。

    路径查找（DingTalk CLI / 跨平台）：
      1. 当前工作目录下的 .env（最优先 — CLI 用户通常 cd 到项目根）
      2. PACKAGE_ROOT.parent.parent/.env（package 邻接的 ai_video/ 根）
      3. ~/ai_video/.env（$HOME 兜底）
    """
    candidate_paths = [
        Path.cwd() / ".env",                                # CWD
        PACKAGE_ROOT.parent.parent / ".env",               # 项目根
        Path.home() / "ai_video" / ".env",                 # $HOME 兜底
    ]
    for path in candidate_paths:
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            if key not in _DOTENV_KEYS or key in os.environ:
                continue
            os.environ[key] = val.strip().strip('"').strip("'")
        return  # 找到就停止


_load_dotenv()


def _env_str(key: str, default: str) -> str:
    v = os.environ.get(key)
    return v if v else default


def api_key() -> str:
    return _env_str("MINIMAX_API_KEY", "")


def base_url() -> str:
    return _env_str("MINIMAX_BASE_URL", DEFAULT_BASE_URL)


def model_name() -> str:
    return _env_str("MINIMAX_MODEL", DEFAULT_MODEL)


def is_llm_configured() -> bool:
    """MiniMax API 端点 + Key 必填。Key 长度阈值与 V0.5 doctor 一致（≥16）。"""
    return bool(api_key()) and len(api_key()) >= 16 and bool(base_url())


def configuration_warnings() -> list[str]:
    msgs: list[str] = []
    if not api_key():
        msgs.append("MINIMAX_API_KEY 未设置")
    elif len(api_key()) < 16:
        msgs.append(f"MINIMAX_API_KEY 长度 {len(api_key())} < 16（可能不完整）")
    if base_url() == DEFAULT_BASE_URL:
        msgs.append(f"MINIMAX_BASE_URL={DEFAULT_BASE_URL}（默认值；端点未公开核实，需烟测）")
    return msgs


# ---------- 数据类 ----------

@dataclass
class Scene:
    id: int
    time: str
    location: str
    characters: list[str] = field(default_factory=list)
    summary: str = ""
    dialogue: str = ""
    # V3.5 Director 升级：per-scene 起承转合（来自 LLM 的 scene 级别 narrative_beat）
    narrative_beat: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Script:
    title: str
    scenes: list[Scene] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "scenes": [s.to_dict() for s in self.scenes]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Script":
        scenes_raw = data.get("scenes", [])
        if not isinstance(scenes_raw, list):
            raise LLMError(f"scenes 字段不是 list：{type(scenes_raw)}")
        scenes: list[Scene] = []
        for s in scenes_raw:
            if not isinstance(s, dict):
                continue
            try:
                scenes.append(Scene(
                    id=int(s.get("id", 0)),
                    time=str(s.get("time", "")),
                    location=str(s.get("location", "")),
                    characters=list(s.get("characters", []) or []),
                    summary=str(s.get("summary", "")),
                    dialogue=str(s.get("dialogue", "")),
                    # V3.5：narrative_beat 安全取（白名单外 → 空串）
                    narrative_beat=_whitelist_or_empty(
                        s.get("narrative_beat", ""), NARRATIVE_BEAT_WHITELIST
                    ),
                ))
            except (TypeError, ValueError) as exc:
                raise LLMError(f"scene 解析失败：{exc}") from exc
        return cls(title=str(data.get("title", "")), scenes=scenes)


@dataclass
class Character:
    name: str
    appearance: str
    personality: list[str] = field(default_factory=list)
    color_palette: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SceneSpec:
    """Scene 设计（用于 scene sheet 作 reference）。

    V2.5：scene sheet 是每个独立场景的关键元素 reference 图，
    shot 通过 scene_id 引用，让同场景所有 shot 共享同一背景基线。

    注意：和上面的 Scene（LLM 输出格式）字段不同——这是 reference 用。
    """
    id: int
    location: str
    atmosphere: str = ""
    key_elements: list[str] = field(default_factory=list)   # "远山", "稻田", "树桩"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Shot:
    index: int
    scene_id: int
    shot_type: str
    description: str
    shot_id: str = ""                     # V3.2 自动开发冲刺（轮 8）：story_id 形式如 "shot_001"
    characters: list[str] = field(default_factory=list)
    dialogue: str = ""
    voiceover: str = ""
    mood: list[str] = field(default_factory=list)
    camera_motion: str = ""
    time: str = ""                          # V2.5.2：白天 / 黄昏 / 夜（scene variant picker 用）
    # V3.1：升级路线整合（来自 ai_video/升级路线.md）
    subject_action: str = ""                # 角色动作："转身看向窗外"、"点头微笑"等
    expected_duration: float = 5.0          # 单 shot 视频时长（秒）
    motion_complexity: str = "static"       # "static" | "simple" | "complex"
                                                #   static  → Ken Burns 即可（已有 video_engine）
                                                #   simple   → 调用 MotionGenerationAgent（简单运动）
                                                #   complex  → 跳过（需 GPU + 真 AI 视频模型）
    # V3.2 自动开发冲刺（轮 8）：continuity_from_prev — 故事弧线里与前一镜的衔接（自由文本描述）
    continuity_from_prev: str = ""

    # ---------- V3.5 Director 升级：12 个 director 字段（追加；不重排） ----------
    # 默认空值/0 → 现有 Shot(index=1, scene_id=1, shot_type="...", description="...") 构造仍合法
    # 解析端用 .get() 安全取；白名单外值 → 空串（不抛错，向后兼容 day1-3 JSON）
    hook: str = ""                          # 前 3 秒钩子（通常仅 shot_001）
    pacing: str = ""                        # 快 / 中 / 慢（白名单 PACING_WHITELIST）
    lens: str = ""                          # wide / 35mm / 50mm / 85mm / telephoto
    depth_of_field: str = ""                # shallow / deep
    lighting: str = ""                      # 自由文本：主光方向 + 反差 + 特殊光线
    screen_direction: str = ""              # L→R / R→L / 无
    color_grade: str = ""                   # teal-and-orange / 暖调 / 冷调 / 自然中性
    transition_in: str = ""                 # cut / dissolve / match_cut / fade
    transition_out: str = ""                # cut / dissolve / match_cut / fade
    bgm_cue: str = ""                       # 自由文本：背景音乐 / 音效提示
    narrative_beat: str = ""                # 起 / 承 / 转 / 合（per-shot 标签）
    emotion_arc_point: float = 0.0          # 0..1 紧张度曲线（0=平, 1=高潮）

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Storyboard:
    title: str
    characters: list[Character] = field(default_factory=list)
    scenes: list[SceneSpec] = field(default_factory=list)     # V2.5：scene sheet reference
    shots: list[Shot] = field(default_factory=list)
    style_anchor: str = ""                # 全局画风关键词（prepend 到每个 shot prompt）
    char_sheet_anchor: str = ""           # 角色外观统一描述（可选覆盖）

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "characters": [c.to_dict() for c in self.characters],
            "scenes": [s.to_dict() for s in self.scenes],
            "shots": [s.to_dict() for s in self.shots],
            "style_anchor": self.style_anchor,
            "char_sheet_anchor": self.char_sheet_anchor,
        }


# ---------- StoryArc（V3.2 自动开发冲刺轮 8）----------

@dataclass
class StoryArc:
    """多镜故事弧线（V3.2 产品化能力）。

    设计意图：
      - 一个 StoryArc = N 个 shot 组成完整叙事
      - 每个 shot 可独立生成（每次烧 1 Hailuo quota）
      - 跨 shot 角色一致性（character + style_anchor 锁定）
      - 内置 continuity_from_prev 字段帮助下一镜理解前一镜

    用法：
      arc = StoryArc.from_json("story.json")            # 读 story.json
      storyboards = arc.to_storyboards()                  # 拆为 N 个 Storyboard
      # 每个 Storyboard 喂 pipeline 跑 1 次，产物拼接成完整故事
    """
    title: str
    style_anchor: str
    characters: list[Character] = field(default_factory=list)
    scenes: list[SceneSpec] = field(default_factory=list)
    shots: list[Shot] = field(default_factory=list)
    arc_metadata: dict[str, Any] = field(default_factory=dict)   # 弧线级元数据（duration_target / genre 等）

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StoryArc":
        """从 dict 构造（兼容 1 个 story.json 含全部 shots 的格式）。

        与 Storyboard 的 _parse_shots_payload（解析 LLM 输出）不同——这里直接
        接受已是 dict 列表的 shots，不重新走 JSON 抽取。
        """
        characters = [
            Character(
                name=str(c.get("name", "")),
                appearance=str(c.get("appearance", "")),
                personality=list(c.get("personality", []) or []),
                color_palette=list(c.get("color_palette", []) or []),
            )
            for c in (data.get("characters") or []) if isinstance(c, dict)
        ]
        scenes = [
            SceneSpec(
                id=int(s.get("id", i + 1)),
                location=str(s.get("location", "")),
                atmosphere=str(s.get("atmosphere", "")),
                key_elements=list(s.get("key_elements", []) or []),
            )
            for i, s in enumerate(data.get("scenes") or []) if isinstance(s, dict)
        ]
        shots = [_shot_from_dict(s, i) for i, s in enumerate(data.get("shots") or []) if isinstance(s, dict)]
        return cls(
            title=str(data.get("title", "story")),
            style_anchor=str(data.get("style_anchor", "")),
            characters=characters,
            scenes=scenes,
            shots=shots,
            arc_metadata=dict(data.get("arc_metadata") or {}),
        )


    @classmethod
    def from_json(cls, path: str | Path) -> "StoryArc":
        """读 story.json（顶层含全部 shots）。"""
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def to_storyboards(self) -> list["Storyboard"]:
        """把 StoryArc 拆为 N 个 Storyboard（每个 Storyboard = 1 shot = 1 pipeline run）。

        每个子 Storyboard 继承：
          - 全局 style_anchor（基线一致性）
          - 全局 characters / scenes
          - 但 shots 只含当前 shot

        返回 list[Storyboard]，顺序与原 StoryArc.shots 一致。
        """
        return [
            Storyboard(
                title=f"{self.title} · {shot.shot_id}",
                characters=list(self.characters),
                scenes=list(self.scenes),
                shots=[shot],
                style_anchor=self.style_anchor,
                char_sheet_anchor="",   # 角色外观已在 characters 里；不再额外覆盖
            )
            for shot in self.shots
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "style_anchor": self.style_anchor,
            "characters": [c.to_dict() for c in self.characters],
            "scenes": [s.to_dict() for s in self.scenes],
            "shots": [
                {
                    "index": i + 1,
                    "shot_id": s.shot_id,
                    "scene_id": s.scene_id,
                    "shot_type": s.shot_type,
                    "description": s.description,
                    "characters": s.characters,
                    "dialogue": s.dialogue,
                    "voiceover": s.voiceover,
                    "mood": s.mood,
                    "camera_motion": s.camera_motion,
                    "subject_action": s.subject_action,
                    "duration": s.expected_duration,
                    "motion_complexity": s.motion_complexity,
                    "continuity_from_prev": getattr(s, "continuity_from_prev", ""),
                }
                for i, s in enumerate(self.shots)
            ],
            "arc_metadata": dict(self.arc_metadata),
        }


# ---------- V3.5 Director 升级：白名单 helper ----------

def _whitelist_or_empty(value: Any, whitelist: tuple[str, ...]) -> str:
    """V3.5 lenient 校验：值不在白名单 → 返回空串（不抛错，向后兼容 day1-3 JSON）。

    与 prompts_director._whitelist_or_empty 行为一致；此处重复定义以避免
    script_engine → prompts_director 内部循环依赖（如未来 prompts_director
    需要引用 LLMError 的话）。
    """
    if isinstance(value, str) and value in whitelist:
        return value
    return ""


def _shot_from_dict(s: dict[str, Any], idx: int) -> Shot:
    """V3.2 自动开发冲刺（轮 8）：从 dict 直接构造 Shot（不走 LLM 输出解析）。

    与 _parse_shots_payload 不同：这里是 JSON 已解析好的 list[dict]，不再走
    JSON 抽取 / fenced code block 解析。校验比 LLM 输出更宽松。

    V3.5 Director 升级：12 个 director 字段用 .get() 安全取；白名单外值 → 空串。
    """
    motion_complexity_raw = str(s.get("motion_complexity", "static") or "static")
    if motion_complexity_raw not in ("static", "simple", "complex"):
        motion_complexity_raw = "static"  # storyboard.json 不强制校验（同 pipeline.load_storyboard）
    subject_action = str(s.get("subject_action", "") or "")
    duration_raw = s.get("duration", s.get("expected_duration", 5.0))
    try:
        duration = float(duration_raw) if duration_raw not in (None, "") else 5.0
    except (TypeError, ValueError):
        duration = 5.0
    # V3.5：emotion_arc_point 是 float，单独容错
    try:
        emotion_arc = float(s.get("emotion_arc_point", 0.0) or 0.0)
    except (TypeError, ValueError):
        emotion_arc = 0.0
    return Shot(
        index=int(s.get("index", idx + 1)),
        scene_id=int(s.get("scene_id", 0)),
        shot_type=str(s.get("shot_type", "中景")),
        description=str(s.get("description", "")),
        shot_id=str(s.get("shot_id", "") or ""),
        characters=list(s.get("characters", []) or []),
        dialogue=str(s.get("dialogue", "")),
        voiceover=str(s.get("voiceover", "")),
        mood=list(s.get("mood", []) or []),
        camera_motion=str(s.get("camera_motion", "")),
        subject_action=subject_action,
        expected_duration=duration,
        motion_complexity=motion_complexity_raw,
        continuity_from_prev=str(s.get("continuity_from_prev", "") or ""),
        # V3.5 Director 字段（白名单外 → 空串；自由文本直接 str()）
        hook=str(s.get("hook", "") or ""),
        pacing=_whitelist_or_empty(s.get("pacing", ""), PACING_WHITELIST),
        lens=_whitelist_or_empty(s.get("lens", ""), LENS_WHITELIST),
        depth_of_field=_whitelist_or_empty(
            s.get("depth_of_field", ""), DEPTH_OF_FIELD_WHITELIST
        ),
        lighting=str(s.get("lighting", "") or ""),
        screen_direction=str(s.get("screen_direction", "") or ""),
        color_grade=str(s.get("color_grade", "") or ""),
        transition_in=_whitelist_or_empty(
            s.get("transition_in", ""), TRANSITION_WHITELIST
        ),
        transition_out=_whitelist_or_empty(
            s.get("transition_out", ""), TRANSITION_WHITELIST
        ),
        bgm_cue=str(s.get("bgm_cue", "") or ""),
        narrative_beat=_whitelist_or_empty(
            s.get("narrative_beat", ""), NARRATIVE_BEAT_WHITELIST
        ),
        emotion_arc_point=emotion_arc,
    )


# ---------- JSON 解析 ----------

_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL)


def _extract_json(text: str) -> dict[str, Any] | list[Any]:
    """从 LLM 输出中抽取 JSON。优先级：fenced code block → 整段 → 找首个 {/[ 配对。"""
    if not text or not text.strip():
        raise LLMError("LLM 返回为空")
    m = _JSON_FENCE.search(text)
    if m:
        candidate = m.group(1)
    else:
        # 回退：取第一个 { 或 [ 到对应配对
        first_brace = text.find("{")
        first_bracket = text.find("[")
        if first_brace == -1 and first_bracket == -1:
            raise LLMError(f"LLM 输出未含 JSON：{text[:200]!r}")
        if first_brace == -1:
            start, end_char = first_bracket, "]"
        elif first_bracket == -1:
            start, end_char = first_brace, "}"
        else:
            # 哪个先就用哪个
            if first_brace < first_bracket:
                start, end_char = first_brace, "}"
            else:
                start, end_char = first_bracket, "]"
        end = text.rfind(end_char)
        if end == -1 or end <= start:
            raise LLMError(f"LLM 输出 JSON 未闭合：{text[:200]!r}")
        candidate = text[start : end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise LLMError(f"JSON 解析失败：{exc} | raw={candidate[:200]!r}") from exc


def _parse_script_payload(text: str) -> Script:
    data = _extract_json(text)
    if not isinstance(data, dict):
        raise LLMError(f"剧本 payload 不是 dict：{type(data)}")
    return Script.from_dict(data)


def _parse_characters_payload(text: str) -> list[Character]:
    data = _extract_json(text)
    if isinstance(data, list):
        # 允许裸 list
        raw_list = data
    elif isinstance(data, dict) and isinstance(data.get("characters"), list):
        raw_list = data["characters"]
    else:
        raise LLMError(f"角色 payload 结构不符：{data!r}")
    out: list[Character] = []
    for c in raw_list:
        if not isinstance(c, dict):
            continue
        out.append(Character(
            name=str(c.get("name", "")),
            appearance=str(c.get("appearance", "")),
            personality=list(c.get("personality", []) or []),
            color_palette=list(c.get("color_palette", []) or []),
        ))
    return out


def _parse_shots_payload(text: str, n_shots: int) -> list[Shot]:
    data = _extract_json(text)
    if isinstance(data, dict) and isinstance(data.get("shots"), list):
        raw_list = data["shots"]
    elif isinstance(data, list):
        raw_list = data
    else:
        raise LLMError(f"分镜 payload 结构不符：{data!r}")
    out: list[Shot] = []
    for i, s in enumerate(raw_list):
        if not isinstance(s, dict):
            continue
        # V3.2 REQ-301：subject_action / motion_complexity / duration 必填
        subject_action = str(s.get("subject_action", "") or "")
        motion_complexity_raw = str(s.get("motion_complexity", "static") or "static")
        if motion_complexity_raw not in ("static", "simple", "complex"):
            raise LLMError(
                f"shot {i+1} motion_complexity 不合法：{motion_complexity_raw!r} "
                "（必须 static / simple / complex）"
            )
        # duration 兼容两种命名：LLM 返 'duration'（V3.2 spec） / 'expected_duration'（旧）
        if "duration" in s:
            duration_raw = s["duration"]
        elif "expected_duration" in s:
            duration_raw = s["expected_duration"]
        else:
            duration_raw = 5.0
        try:
            duration = float(duration_raw)
        except (TypeError, ValueError) as exc:
            raise LLMError(f"shot {i+1} duration 不合法：{duration_raw!r}") from exc
        if duration <= 0 or duration > 30:
            raise LLMError(f"shot {i+1} duration 越界：{duration}（需 0 < d ≤ 30）")
        # V3.5：emotion_arc_point 是 float，单独容错
        try:
            emotion_arc = float(s.get("emotion_arc_point", 0.0) or 0.0)
        except (TypeError, ValueError):
            emotion_arc = 0.0

        out.append(Shot(
            index=int(s.get("index", i + 1)),
            scene_id=int(s.get("scene_id", 0)),
            shot_type=str(s.get("shot_type", "中景")),
            description=str(s.get("description", "")),
            characters=list(s.get("characters", []) or []),
            dialogue=str(s.get("dialogue", "")),
            voiceover=str(s.get("voiceover", "")),
            mood=list(s.get("mood", []) or []),
            camera_motion=str(s.get("camera_motion", "")),
            subject_action=subject_action,
            expected_duration=duration,
            motion_complexity=motion_complexity_raw,
            # V3.2 自动开发冲刺（轮 8）：故事弧线字段
            continuity_from_prev=str(s.get("continuity_from_prev", "") or ""),
            # V3.5 Director 字段（白名单外 → 空串；自由文本直接 str()）
            hook=str(s.get("hook", "") or ""),
            pacing=_whitelist_or_empty(s.get("pacing", ""), PACING_WHITELIST),
            lens=_whitelist_or_empty(s.get("lens", ""), LENS_WHITELIST),
            depth_of_field=_whitelist_or_empty(
                s.get("depth_of_field", ""), DEPTH_OF_FIELD_WHITELIST
            ),
            lighting=str(s.get("lighting", "") or ""),
            screen_direction=str(s.get("screen_direction", "") or ""),
            color_grade=str(s.get("color_grade", "") or ""),
            transition_in=_whitelist_or_empty(
                s.get("transition_in", ""), TRANSITION_WHITELIST
            ),
            transition_out=_whitelist_or_empty(
                s.get("transition_out", ""), TRANSITION_WHITELIST
            ),
            bgm_cue=str(s.get("bgm_cue", "") or ""),
            narrative_beat=_whitelist_or_empty(
                s.get("narrative_beat", ""), NARRATIVE_BEAT_WHITELIST
            ),
            emotion_arc_point=emotion_arc,
        ))
    if len(out) != n_shots:
        raise LLMError(
            f"分镜数量不符：要求 {n_shots}，LLM 返回 {len(out)}。"
            "请调整 prompt 或重试。"
        )
    return out


# ---------- LLM Backend 抽象 ----------

class LLMBackend(ABC):
    @abstractmethod
    def chat(self, system: str, user: str, **opts: Any) -> str:
        """返回 LLM 的文本响应。"""


class MockBackend(LLMBackend):
    """单测用的固定响应后端。每个 prompt 可以预设对应响应。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []
        self.responses: dict[str, str] = {}

    def on(self, key: str, response: str) -> None:
        self.responses[key] = response

    def chat(self, system: str, user: str, **opts: Any) -> str:
        self.calls.append({"system": system, "user": user})
        # 用 prompt 关键字匹配预设响应
        for k, v in self.responses.items():
            if k in system or k in user:
                return v
        # 默认回退：让单测明显失败
        raise AssertionError(f"MockBackend 无预设响应（system[:80]={system[:80]!r}）")


class MiniMaxChatBackend(LLMBackend):
    """MiniMax OpenAI 兼容 chat 接口。

    R1 [UNVERIFIED]：MINIMAX_BASE_URL/MINIMAX_MODEL 端点 / 字段未公开文档验证。
    首次使用前必须烟测：
        python -m hailuo_video doctor  # 检查配置
        python -m hailuo_video script test_novel.txt -o /tmp/out.json  # 真实跑一次
    """

    def __init__(
        self,
        *,
        api_key_value: str | None = None,
        base_url_value: str | None = None,
        model_value: str | None = None,
        timeout: int = REQUEST_TIMEOUT,
        # 单测注入
        http_post: Callable[[str, dict, dict], dict] | None = None,
    ) -> None:
        k = api_key_value if api_key_value is not None else api_key()
        if not k:
            raise LLMError("MINIMAX_API_KEY 未设置；请填 .env 或显式传入 api_key_value")
        if len(k) < 16:
            raise LLMError(f"MINIMAX_API_KEY 长度 {len(k)} < 16（可能不完整）")
        self._api_key = k
        self._base_url = (base_url_value or base_url()).rstrip("/")
        self._model = model_value or model_name()
        self._timeout = timeout
        self._http_post = http_post  # 注入覆盖；None = 走真网

    def chat(self, system: str, user: str, **opts: Any) -> str:
        url = f"{self._base_url}/chat/completions"
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": opts.get("temperature", 0.7),
            "max_tokens": opts.get("max_tokens", 4096),
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }

        # V3.5 P3：audit hook（CLAUDE.md §5：Request → LLM Payload → Response）
        # 仅在显式提供 project_id 时打 audit；默认不 audit 保持向后兼容
        # （现有 40 个 test_script_engine.py + 27 test_v35_audit.py 不依赖这个路径）
        project_id = str(opts.get("project_id", "") or "")
        endpoint = str(opts.get("endpoint", "minimax_chat") or "minimax_chat")
        log_dir = opts.get("log_dir")
        import time as _time
        t0 = _time.monotonic() if project_id else 0.0
        if project_id:
            from hailuo_video.audit_llm import audit_request, audit_response
            # payload 统一为 {system, user}（不含 model，避免 request/response hash 不一致）
            audit_request(
                project_id, endpoint,
                {"system": system, "user": user},
                log_dir=log_dir,
            )

        try:
            if self._http_post is not None:
                data = self._http_post(url, body, headers)
            else:
                data = self._real_post(url, body, headers)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                if hasattr(exc, "read") and exc.read is not None:
                    detail = exc.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            if project_id:
                audit_response(
                    project_id, endpoint,
                    {"system": system, "user": user},
                    {"error": detail},
                    duration_seconds=_time.monotonic() - t0,
                    ok=False, error_code=f"HTTP_{exc.code}",
                    log_dir=log_dir,
                )
            raise LLMError(f"MiniMax HTTP {exc.code}：{detail}") from exc
        except urllib.error.URLError as exc:
            if project_id:
                audit_response(
                    project_id, endpoint,
                    {"system": system, "user": user},
                    {"error": str(exc.reason)},
                    duration_seconds=_time.monotonic() - t0,
                    ok=False, error_code="URL_ERROR",
                    log_dir=log_dir,
                )
            raise LLMError(f"MiniMax URLError：{exc.reason}") from exc
        except (OSError, TimeoutError) as exc:
            if project_id:
                audit_response(
                    project_id, endpoint,
                    {"system": system, "user": user},
                    {"error": f"{type(exc).__name__}: {exc}"},
                    duration_seconds=_time.monotonic() - t0,
                    ok=False, error_code=type(exc).__name__,
                    log_dir=log_dir,
                )
            raise LLMError(f"MiniMax 网络错：{type(exc).__name__}: {exc}") from exc

        if not isinstance(data, dict):
            if project_id:
                audit_response(
                    project_id, endpoint,
                    {"system": system, "user": user},
                    {"error": f"non-dict response: {type(data).__name__}"},
                    duration_seconds=_time.monotonic() - t0,
                    ok=False, error_code="STRUCTURE_ERROR",
                    log_dir=log_dir,
                )
            raise LLMError(f"MiniMax 返回非 dict：{data!r}")
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            if project_id:
                audit_response(
                    project_id, endpoint,
                    {"system": system, "user": user},
                    {"error": f"structure: {exc}"},
                    duration_seconds=_time.monotonic() - t0,
                    ok=False, error_code="STRUCTURE_ERROR",
                    log_dir=log_dir,
                )
            raise LLMError(f"MiniMax 响应结构不符：{data!r}") from exc
        if not isinstance(content, str):
            if project_id:
                audit_response(
                    project_id, endpoint,
                    {"system": system, "user": user},
                    {"error": f"non-str content: {type(content).__name__}"},
                    duration_seconds=_time.monotonic() - t0,
                    ok=False, error_code="STRUCTURE_ERROR",
                    log_dir=log_dir,
                )
            raise LLMError(f"MiniMax 返回 content 非 str：{type(content)}")
        # 成功：audit response（截断 content 避免大文件）
        if project_id:
            # content 可能很长，截断到前 200 字符作为 response_summary
            summary = {
                "task_id": data.get("id", ""),
                "model": data.get("model", self._model),
                "usage": data.get("usage", {}),
                "content_preview": (content[:200] + "...") if len(content) > 200 else content,
            }
            audit_response(
                project_id, endpoint,
                {"system": system, "user": user},
                summary,
                duration_seconds=_time.monotonic() - t0,
                ok=True, log_dir=log_dir,
            )
        return content

    def _real_post(self, url: str, body: dict, headers: dict) -> dict:
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            # fp 可能为 None（测试或代理层丢错），安全地读 body
            detail = ""
            try:
                if hasattr(exc, "read") and exc.read is not None:
                    detail = exc.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            raise LLMError(f"MiniMax HTTP {exc.code}：{detail}") from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"MiniMax URLError：{exc.reason}") from exc
        except (OSError, TimeoutError) as exc:
            raise LLMError(f"MiniMax 网络错：{type(exc).__name__}: {exc}") from exc
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMError(f"MiniMax 返回非 JSON：{raw[:200]!r}") from exc


# ---------- 工厂 ----------

def make_backend(prefer: str = "auto") -> LLMBackend:
    """按 prefer 选择后端：
      - "mock"      → MockBackend（单测）
      - "minimax"   → MiniMaxChatBackend（要求 API Key 已配）
      - "auto"      → 先 MiniMaxChatBackend，失败再回退（V2.1 不回退；硬失败）
    """
    if prefer == "mock":
        return MockBackend()
    if prefer in ("minimax", "auto"):
        return MiniMaxChatBackend()
    raise LLMError(f"未知 backend: {prefer}")


# ---------- Pipeline ----------

def _format_prompt(template: str, **kwargs: Any) -> str:
    """仅安全替换我们控制的变量（不调用 str.format 以免 LLM 输出含 { 报错）。"""
    out = template
    for k, v in kwargs.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def novel_to_script(novel: str, backend: LLMBackend) -> Script:
    if not novel or not novel.strip():
        raise LLMError("novel 为空")
    user = _format_prompt(NOVEL_TO_SCRIPT_PROMPT, novel=novel)
    raw = backend.chat(
        system="你是漫剧编剧，输出严格 JSON。",
        user=user,
    )
    return _parse_script_payload(raw)


def script_to_characters(script: Script, backend: LLMBackend) -> list[Character]:
    script_json = json.dumps(script.to_dict(), ensure_ascii=False, indent=2)
    user = _format_prompt(CHARACTER_EXTRACTION_PROMPT, script_json=script_json)
    raw = backend.chat(
        system="你是角色设计师，输出严格 JSON。",
        user=user,
    )
    return _parse_characters_payload(raw)


def script_to_storyboard(
    script: Script,
    characters: list[Character],
    backend: LLMBackend,
    *,
    n_shots: int = 30,
) -> list[Shot]:
    script_json = json.dumps(script.to_dict(), ensure_ascii=False, indent=2)
    chars_json = json.dumps([c.to_dict() for c in characters], ensure_ascii=False, indent=2)
    user = _format_prompt(
        STORYBOARD_PROMPT,
        n_shots=n_shots,
        script_json=script_json,
        characters_json=chars_json,
    )
    raw = backend.chat(
        system="你是漫剧分镜师，输出严格 JSON。",
        user=user,
    )
    return _parse_shots_payload(raw, n_shots=n_shots)


def novel_to_storyboard(
    novel: str,
    backend: LLMBackend,
    *,
    n_shots: int = 30,
) -> Storyboard:
    """端到端：novel → script → characters → storyboard。"""
    script = novel_to_script(novel, backend)
    characters = script_to_characters(script, backend)
    shots = script_to_storyboard(script, characters, backend, n_shots=n_shots)
    return Storyboard(title=script.title, characters=characters, shots=shots)


# ---------- 文件 IO ----------

def write_storyboard(sb: Storyboard, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(sb.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target


def read_novel(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8").strip()