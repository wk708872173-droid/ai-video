"""V3.8 W2 — 导演解耦（Director Decoupling）。

## 问题

V3.5 给 `Shot` 加了 12 个 director 字段（`lens` / `lighting` / `depth_of_field` /
`color_grade` / `screen_direction` / `pacing` / `transition_in|out` / `narrative_beat` /
`emotion_arc_point` / `bgm_cue` / `hook`），但它们**只服务人工审稿**：

- `orchestrator._build_task` 的 `image_gen` 分支只拼
  `style_anchor + description + shot_type + camera_motion + subject_action + mood`；
- `motion_gen` 分支只拼 `style_anchor + subject_action`。

→ **12 个导演字段一个都没进模型 prompt**（`STATUS_REPORT_2026-10-20.md:35`：
"12 个导演字段在生成端尚未产生实际影响"）。

## 文献依据

- **arXiv:2606.24107「DramaDirector」**（短剧专用）：planner 把每个 shot
  **解耦成 static visual 条件 + dynamic narrative 条件**；参考图同时指导首帧生成
  与 I2V。→ 本模块就是这个解耦：`static_visual` → image prompt（首帧），
  `dynamic_narrative` → motion prompt（I2V）。
- **arXiv:2509.26555「Stable Cinemetrics」**：把影视控制形式化为
  Setup / Event / Lighting / Camera 4 个正交分类。→ `CINEMETRICS_AXES` 把本项目的
  字段归进这 4 类，形成受控词表。
- **arXiv:2412.02259「VideoGen-of-Thought」**：shot 规格展开成多域并**自校验**。
  → `validate_director_brief()`。
- **arXiv:2603.11421「ShotVerse」**：Plan-then-Control（规划与执行分离）。

## 设计约束（红线）

- **不新增 LLM 往返**：brief 完全由已有 storyboard 数据（LLM 第 3 次调用产出的
  shot + director 字段）确定性推导，不额外调模型。
- 纯 Python 判定，**不调 LLM**（字段值域校验用项目已有的白名单）。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# ---------- Stable Cinemetrics：4 类正交控制分类 ----------

# 本项目的分镜字段 → Stable Cinemetrics 的 4 类正交控制分类。
# （"audio" 是本项目额外的第 5 类，Stable Cinemetrics 不含音频。）
CINEMETRICS_AXES: dict[str, tuple[str, ...]] = {
    "setup": ("shot_type", "lens", "depth_of_field"),
    "event": ("subject_action", "narrative_beat", "emotion_arc_point"),
    "lighting": ("lighting", "color_grade"),
    "camera": ("camera_motion", "screen_direction", "pacing",
               "transition_in", "transition_out"),
    "audio": ("bgm_cue", "hook"),
}

# 反查表：字段名 → 所属分类
FIELD_TO_AXIS: dict[str, str] = {
    name: axis for axis, names in CINEMETRICS_AXES.items() for name in names
}

# 12 个 director 字段（V3.5 Shot dataclass 上的那批）
DIRECTOR_FIELDS: tuple[str, ...] = (
    "hook", "pacing", "lens", "depth_of_field", "lighting", "screen_direction",
    "color_grade", "transition_in", "transition_out", "bgm_cue",
    "narrative_beat", "emotion_arc_point",
)

# 静态视觉条件（→ 首帧 / image prompt）用到的字段
STATIC_VISUAL_FIELDS: tuple[str, ...] = (
    "shot_type", "lens", "depth_of_field", "lighting", "color_grade",
)

# 动态叙事条件（→ I2V / motion prompt）用到的字段
DYNAMIC_NARRATIVE_FIELDS: tuple[str, ...] = (
    "subject_action", "camera_motion", "screen_direction", "pacing",
    "transition_in", "emotion_arc_point",
)

# 色彩锁定关键词 → 归一化色基调（用于跨镜连续性门禁 / 后期 LUT）
_COLOR_LOCK_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("teal-and-orange", "teal and orange", "青橙"), "teal-orange"),
    (("暖金", "金色", "暖调", "暖色", "golden", "warm"), "warm-gold"),
    (("冷蓝", "冷调", "冷色", "blue", "cool", "cold"), "cool-blue"),
    (("自然中性", "中性", "neutral"), "neutral"),
    (("高对比", "对比强", "high contrast"), "high-contrast"),
    (("低对比", "柔和", "low contrast", "soft"), "low-contrast"),
)


@dataclass(frozen=True)
class DirectorBrief:
    """一个 shot 的导演 brief —— **静态视觉**与**动态叙事**解耦。

    对应 DramaDirector（arXiv:2606.24107）的两个条件：
      - `static_visual`    → image_gen 的 prompt（首帧画面）
      - `dynamic_narrative`→ motion_gen 的 prompt（I2V 运动）
    """
    static_visual: str = ""
    dynamic_narrative: str = ""
    color_lock: str = ""
    lens: str = ""
    lighting: str = ""
    color_grade: str = ""
    pacing: str = ""
    screen_direction: str = ""
    narrative_beat: str = ""
    used_fields: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["used_fields"] = list(self.used_fields)
        return d

    @property
    def has_director_fields(self) -> bool:
        """是否真的用到了 director 字段（而不是只有基础字段推导）。"""
        return any(f in DIRECTOR_FIELDS for f in self.used_fields)


# ---------- 工具 ----------

def _get(obj: Any, name: str, default: Any = "") -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _text(obj: Any, name: str) -> str:
    return str(_get(obj, name, "") or "").strip()


def _find_scene(storyboard: Any, scene_id: Any) -> Any:
    scenes = list(_get(storyboard, "scenes", []) or [])
    for sc in scenes:
        if _get(sc, "id") == scene_id:
            return sc
    return None


def _character_appearances(storyboard: Any, names: list[str]) -> list[str]:
    """取在场角色的 appearance 文本（视觉锚定的核心）。

    依据 arXiv:2503.10391（CINEMA）：一致性要靠**视觉可判定属性**，
    不能只靠角色名 → 这里带 appearance，不带名字。
    """
    chars = {_get(c, "name", ""): c for c in list(_get(storyboard, "characters", []) or [])}
    out: list[str] = []
    for name in names:
        ch = chars.get(name)
        if ch is None:
            continue
        appearance = _text(ch, "appearance")
        palette = [str(p) for p in (_get(ch, "color_palette", []) or [])]
        parts = [appearance]
        if palette:
            parts.append("palette " + "/".join(palette))
        out.append("，".join(p for p in parts if p))
    return out


def derive_color_lock(*texts: str) -> str:
    """从若干文本里解析出**色彩锁定**标记（跨镜色基调）。

    用于跨镜连续性门禁（Batch 4）与后期 LUT 基准。
    """
    blob = " ".join(t for t in texts if t).lower()
    for keywords, lock in _COLOR_LOCK_RULES:
        for kw in keywords:
            if kw.lower() in blob:
                return lock
    return ""


# ---------- 规划 ----------

def plan_director_brief(
    shot: Any,
    storyboard: Any = None,
    *,
    style_anchor: str = "",
) -> DirectorBrief:
    """把一个 shot 规划成 DirectorBrief（静态视觉 / 动态叙事解耦）。

    **纯确定性推导**（不调 LLM）：输入是 storyboard 里已有的 shot + director 字段 +
    角色表 + 场景表，输出是两条可直接进 prompt 的文本。

    Args:
        shot: Shot 实例或等价 dict
        storyboard: Storyboard 实例（提供角色表 / 场景表 / style_anchor）；None 时退化为纯 shot 字段
        style_anchor: 显式画风锚点（优先于 storyboard.style_anchor）。
            **必须传已兜底过的值**（`_safe_style_anchor`）——
            否则空画风会让 image prompt 丢掉 ADR-006 的 anti-cartoon 守卫词。

    Returns:
        DirectorBrief
    """
    anchor = (style_anchor or _text(storyboard, "style_anchor") or "").strip()
    char_names = [str(c) for c in (_get(shot, "characters", []) or [])]
    appearances = _character_appearances(storyboard, char_names) if storyboard is not None else []
    scene = _find_scene(storyboard, _get(shot, "scene_id")) if storyboard is not None else None

    shot_type = _text(shot, "shot_type")
    lens = _text(shot, "lens")
    dof = _text(shot, "depth_of_field")
    lighting = _text(shot, "lighting")
    color_grade = _text(shot, "color_grade")
    description = _text(shot, "description")

    subject_action = _text(shot, "subject_action") or description
    camera_motion = _text(shot, "camera_motion")
    screen_direction = _text(shot, "screen_direction")
    pacing = _text(shot, "pacing")
    transition_in = _text(shot, "transition_in")
    narrative_beat = _text(shot, "narrative_beat")
    mood = [str(m) for m in (_get(shot, "mood", []) or [])]

    # ---------- 静态视觉条件（→ 首帧） ----------
    static_parts: list[str] = []
    if anchor:
        static_parts.append(f"[风格:{anchor}]")
    if appearances:
        static_parts.append(f"[角色:{'，'.join(appearances)}]")
    if scene is not None:
        loc = _text(scene, "location")
        atmo = _text(scene, "atmosphere")
        scene_bits = [b for b in (loc, atmo) if b]
        if scene_bits:
            static_parts.append(f"[场景:{'，'.join(scene_bits)}]")
    if description:
        static_parts.append(description)
    if shot_type:
        static_parts.append(f"[{shot_type}]")
    # 运镜同时描述"怎么构图"与"怎么动" → 静态与动态两侧都带（保持既有 image prompt 契约）
    if camera_motion:
        static_parts.append(f"[运镜:{camera_motion}]")
    # 主体动作进静态视觉条件：首帧要呈现动作起始姿态
    if subject_action and subject_action != description:
        static_parts.append(f"[动作:{subject_action}]")
    if mood:
        static_parts.append(f"[氛围:{', '.join(mood)}]")
    if lens:
        static_parts.append(f"[焦段:{lens}]")
    if dof:
        static_parts.append(f"[景深:{dof}]")
    if lighting:
        static_parts.append(f"[光线:{lighting}]")
    if color_grade:
        static_parts.append(f"[色彩:{color_grade}]")

    # ---------- 动态叙事条件（→ I2V） ----------
    dynamic_parts: list[str] = []
    if subject_action:
        dynamic_parts.append(subject_action)
    if camera_motion:
        dynamic_parts.append(f"[运镜:{camera_motion}]")
    if screen_direction:
        dynamic_parts.append(f"[方向:{screen_direction}]")
    if pacing:
        dynamic_parts.append(f"[节奏:{pacing}]")
    if mood:
        dynamic_parts.append(f"[氛围:{', '.join(mood)}]")
    if narrative_beat:
        dynamic_parts.append(f"[节拍:{narrative_beat}]")
    if transition_in:
        dynamic_parts.append(f"[转场:{transition_in}]")

    # ---------- 色彩锁定 ----------
    scene_atmo = _text(scene, "atmosphere") if scene is not None else ""
    color_lock = derive_color_lock(color_grade, lighting, scene_atmo, description)

    used: list[str] = []
    for name in DIRECTOR_FIELDS:
        if _get(shot, name, "") not in ("", 0, 0.0, None, [], {}):
            used.append(name)

    return DirectorBrief(
        static_visual=" ".join(p for p in static_parts if p).strip(),
        dynamic_narrative=" ".join(p for p in dynamic_parts if p).strip(),
        color_lock=color_lock,
        lens=lens,
        lighting=lighting,
        color_grade=color_grade,
        pacing=pacing,
        screen_direction=screen_direction,
        narrative_beat=narrative_beat,
        used_fields=tuple(used),
    )


# ---------- 自校验（VGoT） ----------

def validate_director_brief(
    brief: DirectorBrief,
    *,
    require_static: bool = True,
    require_dynamic: bool = True,
    require_color_lock: bool = False,
) -> list[str]:
    """自校验 brief（arXiv:2412.02259 VGoT 的 self-validation 环节）。

    返回问题列表（空 = 通过）。**只报告，不抛错** —— 上层决定是否阻断。
    """
    issues: list[str] = []
    if require_static and not brief.static_visual.strip():
        issues.append("static_visual 为空：首帧没有任何画面描述")
    if require_dynamic and not brief.dynamic_narrative.strip():
        issues.append("dynamic_narrative 为空：I2V 没有动作/运镜描述")
    if require_color_lock and not brief.color_lock.strip():
        issues.append("color_lock 为空：跨镜色基调未锁定（连续性门禁会退化）")
    if brief.lens and brief.lens not in ("wide", "35mm", "50mm", "85mm", "telephoto"):
        issues.append(f"lens={brief.lens!r} 不在受控词表内")
    if brief.pacing and brief.pacing not in ("快", "中", "慢"):
        issues.append(f"pacing={brief.pacing!r} 不在受控词表内")
    return issues


def brief_for_shot(
    shot: Any,
    storyboard: Any = None,
    *,
    style_anchor: str = "",
) -> DirectorBrief:
    """便捷入口：规划 + 自校验（有问题时把问题塞进 static_visual 前的告警位）。

    自校验结果不阻断，只通过 `DirectorBrief.used_fields` 之外的
    `validate_director_brief()` 暴露给调用方。
    """
    return plan_director_brief(shot, storyboard, style_anchor=style_anchor)


__all__ = [
    "CINEMETRICS_AXES",
    "DIRECTOR_FIELDS",
    "DYNAMIC_NARRATIVE_FIELDS",
    "FIELD_TO_AXIS",
    "STATIC_VISUAL_FIELDS",
    "DirectorBrief",
    "brief_for_shot",
    "derive_color_lock",
    "plan_director_brief",
    "validate_director_brief",
]
