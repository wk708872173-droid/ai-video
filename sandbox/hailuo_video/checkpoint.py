r"""V5.0 Batch 3 — 人机协同节点（Human-in-the-loop Checkpoint）。

## 问题

全仓 `grep "input(\|confirm\|--yes\|approve\|interactive"` → **零命中**。
整个 pipeline 是 **fire-and-forget**：一条命令跑到底，人只能在最后看结果。

这与客户的实际工作方式冲突：

- **image-01 按张计费**（角色 sheet 7 张/角色）—— 参考图长相对不对，
  应该**人看一眼再往下跑**，而不是烧完整条链路才发现"小黄又变猫了"；
- 客户的第 1 优先级是「无人值守生产」，第 3 优先级是「画面质量」——
  全自动保住了前者，但**质量问题的反馈点太晚**。

## 设计

**checkpoint = 跑到某个 phase 结束后干净停下**，产出一个人能读的确认报告，
人看完决定继续还是中止。

关键设计：**复用现有的 resume 机制**，不引入新机制。

- 停下时把 checkpoint 记进 `run_dir/checkpoints.jsonl`；
- 报告里写明**下一步该跑哪个 phase**（`resume_from`）；
- 人执行 `pipeline ... --resume-from <下一个 phase>` 即从断点续跑 ——
  走的是已有的 `registry.json` 续跑路径（`orchestrator._resolve_resume_index`）。

## 为什么默认只开 `prep`

`prep`（角色/场景参考图生成）是**性价比最高的确认点**：
它是整条链路里**最贵**的一步（每角色 7 张图），也是**最容易出错**的一步
（"小黄变猫"事故就发生在这里）。在它之后停一次，
比在成片之后才发现问题省下整条下游链路的钱。

其他 phase（`editing` 成片确认等）可以显式打开：
`--checkpoint prep,editing`。

## 契约变更

`PipelineResult.source` 新增 `"checkpoint"` 值。
`ok` property 从 `source == "ok"` 改为 `source in ("ok", "checkpoint")` ——
**checkpoint 是"成功暂停"，不是失败**。这是有意扩展，必须有测试锁定。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# 默认开启确认的 phase
DEFAULT_CHECKPOINT_PHASES: tuple[str, ...] = ("prep",)


@dataclass(frozen=True)
class Checkpoint:
    """一次"停下来等人确认"的记录。"""

    phase: str                       # 停在哪个 phase 之后
    question: str = ""               # 问人的那个问题
    report_path: str = ""            # 确认报告（markdown）
    resume_from: str = ""            # 人确认后，从哪个 phase 继续
    approved: bool | None = None     # None = 待确认
    created_at: str = ""
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Checkpoint":
        return cls(
            phase=str(data.get("phase", "")),
            question=str(data.get("question", "")),
            report_path=str(data.get("report_path", "")),
            resume_from=str(data.get("resume_from", "")),
            approved=data.get("approved"),
            created_at=str(data.get("created_at", "")),
            summary=dict(data.get("summary") or {}),
        )


class CheckpointStore:
    """checkpoint 记录（append-only jsonl）。

    存在 `run_dir/checkpoints.jsonl` —— **不随 project 隔离**？
    不对：run_dir 本身**就是** project 隔离的（ADR-007：`run_{project_id}`），
    所以 checkpoint 天然按项目隔离。
    """

    FILENAME = "checkpoints.jsonl"

    def __init__(self, run_dir: Path | str) -> None:
        self.run_dir = Path(run_dir)
        self.path = self.run_dir / self.FILENAME

    def append(self, checkpoint: Checkpoint) -> Checkpoint:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(checkpoint.to_dict(), ensure_ascii=False) + "\n")
            f.flush()
        return checkpoint

    def read_all(self) -> list[Checkpoint]:
        if not self.path.is_file():
            return []
        out: list[Checkpoint] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(Checkpoint.from_dict(json.loads(line)))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
        return out

    def latest(self, phase: str = "") -> Checkpoint | None:
        """最后一次 checkpoint（可按 phase 过滤）。"""
        items = self.read_all()
        if phase:
            items = [c for c in items if c.phase == phase]
        return items[-1] if items else None

    def pending(self) -> list[Checkpoint]:
        """还没被确认（approved is None）的 checkpoint。"""
        return [c for c in self.read_all() if c.approved is None]


def parse_checkpoint_phases(value: str | list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    """解析 `--checkpoint` 参数。

    - None / "" → 用默认值（只开 prep）
    - "none" / "off" → **全关**（纯自动跑到底）
    - "prep,editing" → 这两个 phase
    """
    if value is None:
        return DEFAULT_CHECKPOINT_PHASES
    if isinstance(value, (list, tuple)):
        items = [str(v).strip() for v in value]
    else:
        items = [v.strip() for v in str(value).split(",")]
    items = [v for v in items if v]
    if not items:
        return DEFAULT_CHECKPOINT_PHASES
    if len(items) == 1 and items[0].lower() in ("none", "off", "false", "0"):
        return ()
    return tuple(items)


def next_phase_after(phases: tuple[Any, ...] | list[Any], phase_name: str) -> str:
    """给定 phase，返回**下一个** phase 的名字（续跑起点）。"""
    names = [str(getattr(p, "name", "")) for p in phases]
    if phase_name in names:
        idx = names.index(phase_name)
        if idx + 1 < len(names):
            return names[idx + 1]
    return ""


# ---------- 确认报告（人读） ----------

def build_report(
    phase: str,
    storyboard: Any,
    registry: Any = None,
    *,
    question: str = "",
) -> tuple[str, dict[str, Any]]:
    """生成确认报告（markdown + 结构化摘要）。

    `prep` 阶段的报告回答的是客户最痛的那个问题：
    **「角色长相对不对？」**

    内容：
      - 角色清单（名字 / 品种 / 外观摘要 / 配色 / 参考图数量）
      - **名字歧义告警**（"小黄" 这类中文色词 → 已知"小黄变猫"事故）
      - 场景参考图数量
      - 缺失/失败的参考图（如果有）

    返回 `(markdown, summary_dict)`。
    """
    chars = list(getattr(storyboard, "characters", []) or [])
    scenes = list(getattr(storyboard, "scenes", []) or [])

    char_refs: dict[str, int] = {}
    scene_refs = 0
    if registry is not None:
        try:
            for art in registry.by_kind_iter("character_ref"):
                label = str(art.metadata.get("kind_sub", "") or "")
                name = str(art.artifact_id)
                # artifact_id 形如 char_小黄_front / expr_小黄_happy
                for ch in chars:
                    cname = str(getattr(ch, "name", "") or "")
                    if cname and cname in name:
                        key = f"{cname}:{label}" if label else cname
                        char_refs[key] = char_refs.get(key, 0) + 1
            scene_refs = len(registry.by_kind_iter("scene_ref"))
        except Exception:
            char_refs = {}
            scene_refs = 0

    ambiguity: list[str] = []
    try:
        from hailuo_video.character_bible import check_name_ambiguity
        for ch in chars:
            ambiguity.extend(check_name_ambiguity(ch))
    except Exception:
        ambiguity = []

    lines: list[str] = []
    lines.append(f"# 确认报告 — {phase} 阶段完成后")
    lines.append("")
    lines.append(f"**问题**：{question or '确认以上产出是否符合预期？'}")
    lines.append("")
    lines.append("## 角色")
    if chars:
        lines.append("| 名字 | 品种 | 外观（摘要） | 配色 | 参考图 |")
        lines.append("|---|---|---|---|---|")
        for ch in chars:
            name = str(getattr(ch, "name", "") or "")
            species = str(getattr(ch, "species", "") or "")
            appearance = str(getattr(ch, "appearance", "") or "")
            palette = "、".join(str(p) for p in (getattr(ch, "color_palette", []) or []))
            n = sum(v for k, v in char_refs.items() if k.startswith(f"{name}:") or k == name)
            lines.append(
                f"| {name} | {species or '—'} | {appearance[:40]}{'…' if len(appearance) > 40 else ''} "
                f"| {palette or '—'} | {n} |"
            )
    else:
        lines.append("_（无角色）_")
    lines.append("")
    lines.append(f"## 场景参考图：{scene_refs} 张（共 {len(scenes)} 个场景）")
    lines.append("")

    if ambiguity:
        lines.append("## ⚠️ 名字歧义告警")
        lines.append("")
        for w in ambiguity:
            lines.append(f"- {w}")
        lines.append("")
        lines.append(
            "> 这些是**已知会出问题**的名字（2026-10-13「小黄变猫」事故）。"
            "改用品种名或英文即可，不必重跑整条链路。"
        )
        lines.append("")
    else:
        lines.append("## ✅ 名字歧义检查：通过")
        lines.append("")

    summary = {
        "phase": phase,
        "character_count": len(chars),
        "scene_count": len(scenes),
        "scene_ref_count": scene_refs,
        "ambiguity_warnings": ambiguity,
    }
    return "\n".join(lines), summary


__all__ = [
    "DEFAULT_CHECKPOINT_PHASES",
    "Checkpoint",
    "CheckpointStore",
    "build_report",
    "next_phase_after",
    "parse_checkpoint_phases",
]
