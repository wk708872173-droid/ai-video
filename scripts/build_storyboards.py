"""V3.2 自动开发冲刺（轮 8）—— story_arc.json → N 个独立 storyboard.json。

用法：
    python scripts/build_storyboards.py <story_arc.json> <output_dir>

例：
    python scripts/build_storyboards.py \
        sample/story_arc_orange_cat_4_seasons/day1.json \
        sample/story_arc_orange_cat_4_seasons/day1_storyboards

每个 shot 拆为 1 个独立 storyboard.json，可被 pipeline 单独跑（消耗 1 Hailuo quota）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SANDBOX = REPO / "sandbox"
sys.path.insert(0, str(SANDBOX))

from hailuo_video.script_engine import StoryArc  # noqa: E402


def build_storyboards(arc_path: Path, output_dir: Path) -> list[Path]:
    """把 story_arc.json 拆为 N 个 storyboard.json。

    每个 storyboard 顶层结构与单 shot pipeline 输入一致：
      - title: "{arc.title} · {shot.shot_id}"
      - style_anchor: 全局继承
      - characters / scenes: 全局继承
      - shots: [单 shot]（pipeline 只看 shots[0]）

    输出文件命名：{output_dir}/{shot.shot_id}.json
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    arc = StoryArc.from_json(arc_path)
    written: list[Path] = []
    for sb in arc.to_storyboards():
        # shot_id 保证存在（to_storyboards 已用 shot.shot_id 拼 title）
        out_name = f"{sb.shots[0].shot_id}.json"
        out_path = output_dir / out_name
        out_path.write_text(
            json.dumps(sb.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written.append(out_path)
    return written


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("Usage: python build_storyboards.py <story_arc.json> <output_dir>", file=sys.stderr)
        return 2
    arc_path = Path(argv[1])
    output_dir = Path(argv[2])
    if not arc_path.is_file():
        print(f"[FAIL] story_arc.json not found: {arc_path}", file=sys.stderr)
        return 2
    written = build_storyboards(arc_path, output_dir)
    print(f"[OK] {arc_path.name} → {len(written)} storyboards in {output_dir}/:")
    for p in written:
        print(f"  - {p.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
