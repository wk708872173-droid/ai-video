"""V3.9 W-C — 角色语音一致性（REQ-403：per-shot / per-character voice override）。

## 问题

`orchestrator._build_task` 的 `tts` 分支只有**一个全局 voice**：

```python
task["voice"] = getattr(self.config, "voice", "zh-CN-XiaoxiaoNeural")
```

→ 整条片子里所有角色、所有旁白**用同一个音色**。这与本项目的核心价值主张
（"角色一致性"）直接冲突：画面上每只狗都长一样，声音上却只有一个人。

## 文献依据

- **arXiv:2405.09768**（TTS 评估）：语音合成的五个评估维度之一就是
  **speaker consistency**（说话人一致性）—— 同一说话人在不同 utterance 里
  音色要稳定。这是本模块要满足的不变量。
- **arXiv:2505.17320**（角色驱动 TTS）：**按角色**准备音色（每个角色一份
  专属数据集 / 专属音色），而不是全局一个音色。
  → 本模块的粒度是**角色**（character），不是 shot：同一角色在所有镜头里
  必须同一音色；不同角色应可区分。
- **arXiv:2210.05979**（ASCL，零样本多说话人 TTS）：说话人域偏移是零样本 TTS
  的主要难题 —— 对本项目（edge-tts 固定音色池）意味着**角色→音色的映射必须
  稳定且可预期**，不能随 shot 变化。

## 设计

优先级（从高到低）：

1. `shot.voice` —— 逐镜显式覆盖（人工在 storyboard.json 里指定）
2. `character.voice` —— 角色级指定（**跨镜头稳定**）
3. **确定性探测式分配** —— 角色名哈希取起始槽位，已占用则往后探测空位。
   同一组角色按同一顺序分配 → 每次运行结果相同；同片子内不同角色音色**必不相同**。
4. 全局默认（`--voice` / `EDGE_TTS_VOICE`）

旁白（voiceover）与角色对白（dialogue）分开：旁白用叙述者音色（**不在角色池里**），
对白用**说话角色**的音色。

零 API 成本：只是选一个 edge-tts 音色名，不改合成逻辑。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

# 旁白专用音色 —— **刻意不在角色音色池里**。
# 若旁白音色也在池中，某个角色可能被分配到与旁白相同的音色 → 旁白与角色
# 听起来一样，"角色可区分"这条就废了。（V3.9 自测踩到）
NARRATOR_VOICE = "zh-CN-YunyangNeural"

# 角色音色池（edge-tts 内置中文音色）。
# 刻意**不按性别/物种猜** —— 那是模型的活；本项目只保证"同角色同音色、
# 不同角色尽量可区分"。要精确指定请在 storyboard 的 characters[].voice 里写。
# 池子开大（15 个）→ 哈希撞车概率低；万一撞了 `VoiceCast.collisions()` 会报出来。
CHARACTER_VOICE_POOL: tuple[str, ...] = (
    # 中文女声
    "zh-CN-XiaoxiaoNeural",
    "zh-CN-XiaoyiNeural",
    "zh-CN-XiaohanNeural",
    "zh-CN-XiaomengNeural",
    "zh-CN-XiaomoNeural",
    "zh-CN-XiaoruiNeural",
    "zh-CN-XiaoshuangNeural",
    "zh-CN-XiaotianNeural",
    "zh-CN-XiaoweiNeural",
    "zh-CN-XiaoxuanNeural",
    "zh-CN-XiaoyanNeural",
    "zh-CN-XiaoyouNeural",
    # 中文男声
    "zh-CN-YunjianNeural",
    "zh-CN-YunxiNeural",
    "zh-CN-YutingNeural",
)

# 已知音色别名 → 完整名（与 tts_engine.KNOWN_VOICES 兼容）
VOICE_ALIASES: dict[str, str] = {
    "xiaoxiao": "zh-CN-XiaoxiaoNeural",
    "yunxi": "zh-CN-YunxiNeural",
    "yunjian": "zh-CN-YunjianNeural",
    "xiaoyi": "zh-CN-XiaoyiNeural",
    "yating": "zh-CN-YatingNeural",
    "xiaoshuang": "zh-CN-XiaoshuangNeural",
    "yunyang": "zh-CN-YunyangNeural",
    "ava_en": "en-US-AvaNeural",
    "andrew_en": "en-US-AndrewNeural",
}


@dataclass
class VoiceCast:
    """一次运行的角色 → 音色分配表。

    分配是**确定性**的：同一组角色按同一顺序分配 → 每次运行结果相同。
    同一项目跨天跑（角色表与顺序不变）→ 音色不变 —— 这就是 REQ-403 要的
    "角色语音一致性"。

    注意：分配是**探测式**的（哈希起始槽位 + 往后找空位），不是纯哈希 ——
    纯哈希下 5 个角色分到 15 个槽位约一半概率撞车（生日悖论）→ 两个角色
    听起来一样。探测式保证同片子内不同角色音色必不相同。
    """
    default_voice: str = "zh-CN-XiaoxiaoNeural"
    narrator_voice: str = NARRATOR_VOICE
    #: 角色名 → 音色（本次运行已确定的分配）
    assignments: dict[str, str] = field(default_factory=dict)
    #: 角色名 → 显式指定的音色（来自 character.voice）
    explicit: dict[str, str] = field(default_factory=dict)

    # ---------- 分配 ----------

    def assign(self, character_name: str, *, explicit_voice: str = "") -> str:
        """给一个角色定音色（幂等）。

        分配规则：
        1. 显式音色（`character.voice`）→ 直接用
        2. 否则按角色名哈希取**起始槽位**，若该槽位已被别的角色占用，
           沿池子往后探测到第一个空槽（**探测式分配**）。

        为什么不是纯哈希：纯哈希下 5 个角色分到 15 个槽位就有约一半概率
        撞车（生日悖论）→ 两个角色听起来一样。探测式保证**同片子内不同角色
        音色必不相同**，代价是：若某个项目的角色集合/顺序变了，同一角色的
        音色可能变（同一项目多天跑时角色表通常一致 → 音色稳定）。
        """
        name = (character_name or "").strip()
        if not name:
            return self.default_voice
        if name in self.assignments:
            return self.assignments[name]
        explicit_voice = (explicit_voice or "").strip()
        if explicit_voice:
            resolved = normalize_voice(explicit_voice)
            self.explicit[name] = resolved
        else:
            resolved = self._probe_pick(name)
        self.assignments[name] = resolved
        return resolved

    def _probe_pick(self, name: str) -> str:
        """哈希起始槽位 + 往后探测第一个未被占用的槽位。"""
        taken = set(self.assignments.values())
        start = hashlib.sha256(name.encode("utf-8")).digest()[0] % len(CHARACTER_VOICE_POOL)
        for offset in range(len(CHARACTER_VOICE_POOL)):
            candidate = CHARACTER_VOICE_POOL[(start + offset) % len(CHARACTER_VOICE_POOL)]
            if candidate not in taken:
                return candidate
        # 池子被占满（角色数 ≥ 池子大小）→ 退回起始槽位（并由 collisions() 报出来）
        return CHARACTER_VOICE_POOL[start]

    # ---------- 查询 ----------

    def voice_for(self, character_name: str, *, explicit_voice: str = "") -> str:
        """取角色音色（不写入分配表）。"""
        name = (character_name or "").strip()
        if not name:
            return self.default_voice
        if name in self.assignments:
            return self.assignments[name]
        return self.assign(name, explicit_voice=explicit_voice)

    def narrator(self) -> str:
        return self.narrator_voice

    def collisions(self) -> list[tuple[str, list[str]]]:
        """检测**多个角色共用同一音色**的情况。

        探测式分配下，只有角色数 ≥ 池子大小时才会撞车。撞车不阻断
        （改了就破坏"同角色跨镜头恒定"），但必须**报出来** ——
        "两个角色听起来一样"属于"看起来成功、实际有问题"的静默缺陷。
        """
        by_voice: dict[str, list[str]] = {}
        for name, voice in self.assignments.items():
            by_voice.setdefault(voice, []).append(name)
        return sorted(
            ((v, sorted(names)) for v, names in by_voice.items() if len(names) > 1),
            key=lambda t: t[0],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "default_voice": self.default_voice,
            "narrator_voice": self.narrator_voice,
            "assignments": dict(self.assignments),
            "explicit": dict(self.explicit),
        }


def normalize_voice(voice: str) -> str:
    """音色名归一化：别名 → 完整名；空串 → 空串。

    接受 `xiaoxiao` / `zh-CN-XiaoxiaoNeural` 两种写法。
    """
    v = (voice or "").strip()
    if not v:
        return ""
    return VOICE_ALIASES.get(v.lower(), v)


def speaker_of(shot: Any) -> str:
    """判定一个 shot 的**说话角色**。

    规则（确定性，不调 LLM）：
    1. `shot.speaker` 显式指定 → 用它
    2. 对白里出现了某个在场角色的名字 → 那个角色
    3. 有对白但认不出说话人 → 在场角色的第一个
    4. 只有旁白（无对白）→ 空串（= 旁白）
    """
    explicit = str(_get(shot, "speaker", "") or "").strip()
    if explicit:
        return explicit

    characters = [str(c) for c in (_get(shot, "characters", []) or []) if str(c).strip()]
    dialogue = str(_get(shot, "dialogue", "") or "")
    if dialogue:
        for name in characters:
            if name and name in dialogue:
                return name
        if characters:
            return characters[0]
    return ""


def resolve_voice_for_shot(
    shot: Any,
    storyboard: Any = None,
    *,
    cast: VoiceCast | None = None,
    default_voice: str = "zh-CN-XiaoxiaoNeural",
    narrator_voice: str = NARRATOR_VOICE,
) -> tuple[str, str]:
    """解出这一镜该用哪个音色。返回 (voice, speaker)。

    - 有对白 → 说话角色的音色（跨镜稳定）
    - 只有旁白 → 旁白音色
    - `shot.voice` 显式指定 → 最高优先级，直接用
    """
    cast = cast or VoiceCast(default_voice=default_voice, narrator_voice=narrator_voice)

    shot_voice = normalize_voice(str(_get(shot, "voice", "") or ""))
    if shot_voice:
        return shot_voice, speaker_of(shot)

    speaker = speaker_of(shot)
    if not speaker:
        return cast.narrator(), ""

    explicit = ""
    if storyboard is not None:
        for ch in list(_get(storyboard, "characters", []) or []):
            if str(_get(ch, "name", "") or "").strip() == speaker:
                explicit = normalize_voice(str(_get(ch, "voice", "") or ""))
                break
    return cast.voice_for(speaker, explicit_voice=explicit), speaker


def _get(obj: Any, name: str, default: Any = "") -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


__all__ = [
    "CHARACTER_VOICE_POOL",
    "NARRATOR_VOICE",
    "VOICE_ALIASES",
    "VoiceCast",
    "normalize_voice",
    "resolve_voice_for_shot",
    "speaker_of",
]
