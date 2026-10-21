"""V3.2 自动开发冲刺（轮 9）—— 解释 StoryArc：打印每个 shot 的脚本 + 拼装后的最终 prompt。

用法：
    python scripts/explain_story.py <story_arc.json> [story_arc.json ...]

输出（每个 shot 一段）：
  - shot_id / title / duration / motion_complexity
  - Storyboard 顶层字段（characters / scenes / style_anchor）
  - **Orchestrator 实际拼装的 image_gen prompt**（70/30 + style + description + tags）
  - **Orchestrator 实际拼装的 motion_gen subject_action**（含 style_anchor）
  - **Orchestrator 实际使用的 video_effect（Ken Burns）**

这是"视频生成脚本与提示词的全貌"——用户可以拿着这份输出对照最终视频，
看到"输入什么 + 怎么组装 + 喂给什么 API"。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SANDBOX = REPO / "sandbox"
sys.path.insert(0, str(SANDBOX))

from hailuo_video.script_engine import StoryArc  # noqa: E402
from hailuo_video.orchestrator import PipelineAgent, PipelineConfig  # noqa: E402


def _assemble_image_prompt(shot, style_anchor: str) -> str:
    """复制 orchestrator._build_task 中 image_gen 的 prompt 拼装逻辑（V3.2 轮 7）。"""
    description = getattr(shot, "description", "") or ""
    camera_motion = getattr(shot, "camera_motion", "") or ""
    subject_action = getattr(shot, "subject_action", "") or ""
    mood = list(getattr(shot, "mood", []) or [])
    shot_type = getattr(shot, "shot_type", "") or ""

    parts = []
    if style_anchor:
        parts.append(f"[风格:{style_anchor}]")
    parts.append(description)
    if shot_type:
        parts.append(f"[{shot_type}]")
    if camera_motion:
        parts.append(f"[运镜:{camera_motion}]")
    if subject_action:
        parts.append(f"[动作:{subject_action}]")
    if mood:
        parts.append(f"[氛围:{', '.join(mood)}]")
    return " ".join(parts)


def _assemble_motion_subject_action(shot, style_anchor: str) -> str:
    """复制 orchestrator._build_task 中 motion_gen 的 subject_action 拼装。"""
    base = getattr(shot, "subject_action", "") or getattr(shot, "description", "")
    if style_anchor:
        return f"{base}, 风格:{style_anchor}"
    return base


# Ken Burns 镜头效果默认映射（与 run_story.sh 一致）
EFFECT_MAP = {
    "shot_001": "zoom_in",
    "shot_002": "pan_right",
    "shot_003": "zoom_out",
    "shot_004": "zoom_in",
    "shot_005": "zoom_in",
    "shot_006": "zoom_in",
}


def explain_arc(arc_path: Path) -> None:
    arc = StoryArc.from_json(arc_path)
    print("=" * 80)
    print(f"故事：{arc.title}")
    print(f"来源：{arc_path}")
    print(f"主角：{arc.characters[0].name if arc.characters else '(无)'}")
    style_anchor = arc.style_anchor

    print(f"\n[STYLE ANCHOR]（全局风格基线 — 注入到每镜 prompt + subject_action）")
    print(f"  {style_anchor}")

    if arc.characters:
        char = arc.characters[0]
        print(f"\n[CHARACTER APPEARANCE]（角色外观 — 锁脸用）")
        print(f"  {char.appearance}")

    print("\n[CONTINUITY]（跨镜衔接 — 提示下一镜我是谁我从哪来）")
    for i, shot in enumerate(arc.shots):
        cont = getattr(shot, "continuity_from_prev", "") or ""
        if cont:
            print(f"  {shot.shot_id}: {cont}")

    print("\n" + "=" * 80)
    print(f"[SHOTS]（共 {len(arc.shots)} 镜）")
    print("=" * 80)

    for i, shot in enumerate(arc.shots):
        print(f"\n── {shot.shot_id}（{i+1}/{len(arc.shots)}）──")
        print(f"  title：       {arc.title} · {shot.shot_id}")
        print(f"  shot_type：   {shot.shot_type}")
        print(f"  duration：    {shot.expected_duration}s")
        print(f"  motion_complexity：{shot.motion_complexity}")
        print(f"  characters：  {list(shot.characters)}")
        print(f"  mood：        {list(shot.mood)}")
        print(f"  camera_motion：  {shot.camera_motion or '(无)'}")
        print(f"  subject_action：  {shot.subject_action}")
        print()
        print(f"  [1] description（shot.description — 用户写的视觉描述）:")
        print(f"      {shot.description}")
        print()
        print(f"  [2] → image_gen prompt（MiniMax image-01 收到的真实 prompt）:")
        prompt = _assemble_image_prompt(shot, style_anchor)
        for line in prompt.split(". "):
            print(f"      {line.strip()}.")
        print()
        if shot.motion_complexity != "static":
            print(f"  [3] → motion_gen subject_action（Hailuo-2.3 收到的真实 prompt）:")
            sa = _assemble_motion_subject_action(shot, style_anchor)
            print(f"      {sa}")
        else:
            effect = EFFECT_MAP.get(shot.shot_id, "zoom_in")
            print(f"  [3] → video_synth effect（Ken Burns 镜头 — ffmpeg zoompan）: {effect}")
            print(f"      （描述：zoom_in / zoom_out / pan_left/right/up/down / static — 详见 video_engine.VALID_EFFECTS）")
        print()
        if i < len(arc.shots) - 1:
            print()


def main() -> int:
    p = argparse.ArgumentParser(
        prog="explain_story",
        description="打印 StoryArc 的脚本 + 实际喂给 image_gen / motion_gen / Ken Burns 的最终 prompt",
    )
    p.add_argument("arcs", nargs="+", help="story_arc.json 文件路径（可多个）")
    args = p.parse_args()

    for arc_path in args.arcs:
        p_obj = Path(arc_path)
        if not p_obj.is_file():
            print(f"[FAIL] {arc_path} 不存在", file=sys.stderr)
            return 2
        explain_arc(p_obj)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
