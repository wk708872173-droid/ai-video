"""V3.2 重构：quality 子模块拆分 — 校验 + asset check。

原 quality.py:
  - L43-81 (Issue / ValidationReport dataclasses)
  - L84-195 (validate_storyboard)
  - L200-308 (AssetCheck / SceneCheck / AssetReport + check_assets)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hailuo_video import asset_prep as ap
from hailuo_video.script_engine import Character, SceneSpec, Shot, Storyboard


# ---------- 1. Issue 数据类 + ValidationReport ----------

@dataclass
class Issue:
    """校验 / 检查 / 度量中的一项问题。"""
    level: str                 # "error" | "warning" | "info"
    code: str                  # 短编码（机器可读）
    detail: str = ""            # 人类可读说明
    path: str = ""             # 关联字段路径（空表示全局）

    def format(self) -> str:
        marker = {"error": "✗", "warning": "⚠", "info": "✓"}.get(self.level, "·")
        prefix = f"{marker} [{self.code}]"
        if self.path:
            prefix += f" ({self.path})"
        return f"{prefix} {self.detail}".rstrip()


@dataclass
class ValidationReport:
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def format(self) -> str:
        lines = []
        for i in self.issues:
            lines.append(i.format())
        if not lines:
            lines.append("✓ [OK] storyboard.json schema valid（0 issue）")
        return "\n".join(lines)


def validate_storyboard(sb: Storyboard) -> ValidationReport:
    """通用 schema 校验：不依赖任何具体写作或角色。

    检查项：
      - 必填字段类型（title/characters/shots 存在且类型正确）
      - shots 非空 + 每 shot 必填字段
      - 引用一致性：shot.characters 引用存在的 character.name，shot.scene_id 引用存在的 scene.id
      - picker 已知字段警告（不在字典内 → fallback 提示）
    """
    rep = ValidationReport()

    # 顶层必填
    if not sb.title or not sb.title.strip():
        rep.issues.append(Issue("error", "title_empty", "storyboard.title 不能为空"))

    if not sb.characters:
        rep.issues.append(Issue("error", "no_characters", "storyboard.characters 为空；无法生成 character reference sheets"))

    if not sb.shots:
        rep.issues.append(Issue("error", "no_shots", "storyboard.shots 为空；没有要生成的镜头"))

    # style_anchor（warning level — 不是 error）
    if not getattr(sb, "style_anchor", "") or not sb.style_anchor.strip():
        rep.issues.append(Issue(
            "warning", "no_style_anchor",
            "storyboard.style_anchor 为空；V2.5 Layer 1 缺失 → 画风可能漂移"
        ))

    # 引用一致性
    char_names = {c.name for c in sb.characters}
    scene_ids = {s.id for s in sb.scenes}

    # picker 已知字段
    known_shot_types = set(ap.SHOT_TYPE_TO_ANGLE.keys())
    known_motions = set(ap.CAMERA_MOTION_TO_ANGLE.keys())
    known_moods = set(ap.MOOD_TO_EXPRESSION.keys())
    known_times = set(ap.TIME_TO_SCENE_VARIANT.keys())

    for i, shot in enumerate(sb.shots):
        path = f"shots[{i}]"

        # 必填
        if shot.index is None or shot.index <= 0:
            rep.issues.append(Issue("error", "shot_index_invalid",
                                    f"{path}.index 必须 > 0", path))
        if not shot.shot_type or not shot.shot_type.strip():
            rep.issues.append(Issue("error", "shot_type_empty",
                                    f"{path}.shot_type 不能为空（picker 必需）", path))
        if not shot.description or not shot.description.strip():
            rep.issues.append(Issue("warning", "shot_description_empty",
                                    f"{path}.description 为空；将生空白图", path))

        # shot_type 字典化
        if shot.shot_type and shot.shot_type not in known_shot_types:
            rep.issues.append(Issue(
                "warning", "shot_type_unknown",
                f"{path}.shot_type={shot.shot_type!r} 不在已知列表；picker 会 fallback 到 DEFAULT_ANGLES[0]",
                path,
            ))

        # camera_motion 字典化
        if shot.camera_motion and shot.camera_motion not in known_motions:
            rep.issues.append(Issue(
                "warning", "camera_motion_unknown",
                f"{path}.camera_motion={shot.camera_motion!r} 不在已知列表；pan 方向 picker 不会生效",
                path,
            ))

        # mood 字典化
        if shot.mood:
            unknown = [m for m in shot.mood if m not in known_moods]
            if unknown:
                rep.issues.append(Issue(
                    "warning", "mood_unknown",
                    f"{path}.mood 含未知 {unknown}；expression picker 会 fallback 到 neutral",
                    path,
                ))

        # time 字典化
        if shot.time and shot.time not in known_times:
            rep.issues.append(Issue(
                "warning", "time_unknown",
                f"{path}.time={shot.time!r} 不在已知列表；scene variant 会 fallback 到 day",
                path,
            ))

        # 引用一致性
        for cn in shot.characters or []:
            if cn not in char_names:
                rep.issues.append(Issue(
                    "error", "character_undefined",
                    f"{path}.characters 引用了未定义的 {cn!r}（在 characters[] 找不到）",
                    path,
                ))
        if shot.scene_id and shot.scene_id not in scene_ids:
            rep.issues.append(Issue(
                "warning", "scene_undefined",
                f"{path}.scene_id={shot.scene_id} 在 scenes[] 找不到；scene variant picker 用 default",
                path,
            ))

        # character appearance 太短（i2i 锁脸的关键）
        for cn in shot.characters or []:
            ch = next((c for c in sb.characters if c.name == cn), None)
            if ch and len(ch.appearance or "") < 20:
                rep.issues.append(Issue(
                    "warning", "appearance_too_short",
                    f"character[{cn}].appearance 太短（< 20 字）；i2i 锁脸可能失效",
                    path,
                ))

    return rep


# ---------- 2. check_assets ----------

@dataclass
class AssetCheck:
    character: str            # 角色名
    missing_angles: list[str] = field(default_factory=list)   # ["front", "side_left"]
    missing_expressions: list[str] = field(default_factory=list)

    @property
    def total_missing(self) -> int:
        return len(self.missing_angles) + len(self.missing_expressions)


@dataclass
class SceneCheck:
    scene_id: int
    missing_variants: list[str] = field(default_factory=list)


@dataclass
class AssetReport:
    characters: list[AssetCheck] = field(default_factory=list)
    scenes: list[SceneCheck] = field(default_factory=list)
    # picker 哪些 shot 会 fallback（key=shot.index）
    shot_fallbacks: dict[int, list[str]] = field(default_factory=dict)

    @property
    def all_missing(self) -> list[str]:
        msgs: list[str] = []
        for c in self.characters:
            for a in c.missing_angles:
                msgs.append(f"  - characters/{c.character}/{a}.jpg")
            for e in c.missing_expressions:
                msgs.append(f"  - characters/{c.character}/expression_{e}.jpg")
        for s in self.scenes:
            for v in s.missing_variants:
                msgs.append(f"  - scenes/scene_{s.scene_id:04d}_{v}.jpg")
        return msgs

    @property
    def ok(self) -> bool:
        return not self.all_missing

    def format(self) -> str:
        lines: list[str] = []
        if self.all_missing:
            lines.append(f"[FAIL] missing {len(self.all_missing)} reference sheet(s)：")
            lines.extend(self.all_missing)
        else:
            lines.append("[OK] 所有 reference sheet 都就绪")
        if self.shot_fallbacks:
            lines.append("")
            lines.append("[WARN] 以下 shot 会走 picker fallback（character/scene 引用不全）：")
            for idx, reasons in self.shot_fallbacks.items():
                lines.append(f"  - shot {idx}: {', '.join(reasons)}")
        return "\n".join(lines)


def check_assets(
    sb: Storyboard,
    assets_dir: Path,
) -> AssetReport:
    """列出 storyboard 所需但 assets_dir 还不存在的 reference sheets。

    用于 prep 之前的 pre-flight：知道哪些需要生成。
    """
    report = AssetReport()

    # characters
    for c in sb.characters:
        chk = AssetCheck(character=c.name)
        # angles
        for angle in ap.DEFAULT_ANGLES:
            if not ap.has_cached_character_angle(c.name, angle, assets_dir):
                chk.missing_angles.append(angle)
        # expressions
        for expr in ap.DEFAULT_EXPRESSIONS:
            if not ap.has_cached_character_expression(c.name, expr, assets_dir):
                chk.missing_expressions.append(expr)
        report.characters.append(chk)

    # scenes
    for s in sb.scenes:
        schk = SceneCheck(scene_id=s.id)
        for var in ap.DEFAULT_SCENE_VARIANTS:
            if not ap.has_cached_scene_variant(s.id, var, assets_dir):
                schk.missing_variants.append(var)
        report.scenes.append(schk)

    # picker fallback 检查
    for shot in sb.shots:
        reasons: list[str] = []
        # 在场角色引用完整性
        for cn in shot.characters or []:
            if cn not in {c.name for c in sb.characters}:
                reasons.append(f"character {cn!r} undefined")
        # scene 引用完整性
        if shot.scene_id and shot.scene_id not in {s.id for s in sb.scenes}:
            reasons.append(f"scene_id {shot.scene_id} undefined")
        # character 缺所有 sheets
        chk = next((c for c in report.characters if c.character in (shot.characters or [])), None)
        if chk and chk.total_missing == len(ap.DEFAULT_ANGLES) + len(ap.DEFAULT_EXPRESSIONS):
            reasons.append(f"character {chk.character} 全部 sheet 缺")
        # scene 缺所有 variants
        schk = next((s for s in report.scenes if s.scene_id == shot.scene_id), None)
        if schk and len(schk.missing_variants) == len(ap.DEFAULT_SCENE_VARIANTS):
            reasons.append(f"scene_{shot.scene_id} 全部 variant 缺")
        if reasons:
            report.shot_fallbacks[shot.index] = reasons

    return report
