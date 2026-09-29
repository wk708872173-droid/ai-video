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
            raise LLMError(f"MiniMax HTTP {exc.code}：{detail}") from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"MiniMax URLError：{exc.reason}") from exc
        except (OSError, TimeoutError) as exc:
            raise LLMError(f"MiniMax 网络错：{type(exc).__name__}: {exc}") from exc

        if not isinstance(data, dict):
            raise LLMError(f"MiniMax 返回非 dict：{data!r}")
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"MiniMax 响应结构不符：{data!r}") from exc
        if not isinstance(content, str):
            raise LLMError(f"MiniMax 返回 content 非 str：{type(content)}")
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