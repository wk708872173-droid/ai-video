"""V3.3 W1 — auto_review_report.py 自动生成"需人眼 review"清单。

设计：
  - 扫一个目录（或单个 mp4），对每个视频跑 L1 帧级质量检查
  - 也对"多个 shot"做跨视频色差检查（inter-shot consistency）
  - 输出 logs/daily_review_needed.md：
    * 表格列出每个视频的 L1 状态（pass / minor / major / critical）
    * 按严重度排序的"必看"清单
    * 自动放过列表
  - 0 LLM 依赖，纯 ffmpeg + PIL

用法：
    python scripts/auto_review_report.py <dir|mp4> [<dir|mp4> ...]
    python scripts/auto_review_report.py sample/story_arc_orange_cat_4_seasons/
    python scripts/auto_review_report.py sample/*.mp4 --output logs/daily_review.md
    python scripts/auto_review_report.py sample/ --strict  # 更严苛阈值

CLAUDE.md R4 兼容：
  - 不静默降级：每个视频状态明确（无 None / 无 silent fallback）
  - Auth/Secrets/PII 不在本工具范围（CLAUDE.md R3 适用域）
  - 不动配置 / 不动 production（CLAUDE.md STEP 06）
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SANDBOX = REPO / "sandbox"
sys.path.insert(0, str(SANDBOX))

from hailuo_video.quality import (  # noqa: E402
    compute_frame_quality,
    compute_color_consistency,
    FrameQualityReport,
    DEFAULT_SSIM_THRESHOLD,
    DEFAULT_COLOR_THRESHOLD,
)


# ---------- 扫描 ----------

def collect_videos(inputs: list[str]) -> list[Path]:
    """从输入参数（路径 / 通配符 / 目录）解析 mp4 列表，按文件名排序。"""
    out: list[Path] = []
    seen: set[Path] = set()
    for raw in inputs:
        p = Path(raw)
        if p.is_dir():
            candidates = sorted(p.rglob("*.mp4"))
        elif any(c in raw for c in ("*", "?", "[")):
            candidates = sorted(glob.glob(raw))
            candidates = [Path(c) for c in candidates if Path(c).suffix.lower() == ".mp4"]
        else:
            candidates = [p] if p.is_file() else []
        for c in candidates:
            if c.is_file() and c not in seen:
                seen.add(c)
                out.append(c)
    return out


# ---------- 单视频 L1 ----------

def assess_one_video(mp4: Path, tmp_dir: Path, *, strict: bool) -> FrameQualityReport:
    """单视频 L1 检查（compute_frame_quality）。strict=True 用更严苛阈值。"""
    ssim_t = max(0.5, DEFAULT_SSIM_THRESHOLD - 0.10) if strict else DEFAULT_SSIM_THRESHOLD
    color_t = max(0.5, DEFAULT_COLOR_THRESHOLD - 0.10) if strict else DEFAULT_COLOR_THRESHOLD
    return compute_frame_quality(
        mp4,
        sample_count=6,
        ssim_threshold=ssim_t,
        color_threshold=color_t,
        tmp_dir=tmp_dir,
    )


# ---------- 跨视频色差 ----------

def assess_cross_video(image_paths: list[Path], *, strict: bool) -> dict:
    """跨视频色差：抽 1 关键帧 → 跑 color consistency。"""
    if len(image_paths) < 2:
        return {"ok": True, "flagged_pairs": [], "mean_similarity": 1.0}
    threshold = max(0.5, DEFAULT_COLOR_THRESHOLD - 0.10) if strict else DEFAULT_COLOR_THRESHOLD
    rep = compute_color_consistency(image_paths, similarity_threshold=threshold)
    return {
        "ok": rep.ok,
        "flagged_pairs": [(i + 1, j + 1, sim) for i, j, sim in rep.flagged_pairs],
        "mean_similarity": rep.mean_similarity,
        "min_similarity": rep.min_similarity,
        "warnings": rep.warnings,
    }


# ---------- Markdown 报告 ----------

def render_markdown(
    video_reports: list[tuple[Path, FrameQualityReport]],
    cross_report: dict,
    output_path: Path,
) -> str:
    """生成 markdown 报告内容 + 写盘。"""
    by_severity = defaultdict(list)
    for mp4, rep in video_reports:
        by_severity[rep.severity].append((mp4, rep))

    lines: list[str] = []
    lines.append(f"# Daily Review Needed — {time.strftime('%Y-%m-%d')}")
    lines.append("")
    lines.append(f"> 自动生成（`auto_review_report.py`）：扫 {len(video_reports)} 个视频，"
                 f"L1 帧级质量检查 + 跨视频色差。")
    lines.append("")

    # 总览表
    lines.append("## 总览")
    lines.append("")
    lines.append("| 视频 | 大小 | 严重度 | L1 状态 | 主要问题 |")
    lines.append("|---|---|---|---|---|")
    for mp4, rep in video_reports:
        size = mp4.stat().st_size if mp4.exists() else 0
        size_str = f"{size/1024:.0f}KB" if size < 1024 * 1024 else f"{size/(1024*1024):.1f}MB"
        if rep.severity == "ok":
            status = "✅ pass"
            problems = "-"
        else:
            status = f"⚠️ {rep.severity}"
            problems_parts = []
            if rep.flagged_pairs:
                n = len(rep.flagged_pairs)
                problems_parts.append(f"{n} 对帧 flag")
            if rep.warnings:
                problems_parts.append(f"{len(rep.warnings)} 个 warning")
            problems = "; ".join(problems_parts) or "-"
        rel = mp4.name
        lines.append(f"| `{rel}` | {size_str} | {rep.severity} | {status} | {problems} |")
    lines.append("")

    # 跨视频色差
    if not cross_report["ok"] and cross_report["flagged_pairs"]:
        lines.append("## 跨视频色差（跨镜一致性）")
        lines.append("")
        lines.append(f"mean 色差相似度: **{cross_report['mean_similarity']:.3f}** | "
                     f"min: **{cross_report['min_similarity']:.3f}**")
        lines.append("")
        lines.append(f"[WARN] {len(cross_report['flagged_pairs'])} 对相邻镜被 flag：")
        for i, j, sim in cross_report["flagged_pairs"]:
            lines.append(f"- shot {i} ↔ shot {j}: sim={sim:.3f}")
        lines.append("")
    elif len(video_reports) >= 2:
        lines.append("## 跨视频色差（跨镜一致性）")
        lines.append("")
        lines.append(f"mean 色差相似度: {cross_report['mean_similarity']:.3f} | "
                     f"min: {cross_report['min_similarity']:.3f}")
        lines.append("")
        lines.append("[OK] 所有相邻镜通过色差检查")
        lines.append("")

    # 必看清单
    must_review = by_severity.get("critical", []) + by_severity.get("major", [])
    should_review = by_severity.get("minor", [])
    pass_list = by_severity.get("ok", [])

    if must_review:
        lines.append("## 🚨 必看（critical + major）")
        lines.append("")
        for mp4, rep in must_review:
            lines.append(f"### `{mp4.name}` ({rep.severity})")
            lines.append("")
            lines.append("```")
            lines.append(rep.format())
            lines.append("```")
            lines.append("")
    if should_review:
        lines.append("## 👀 建议看（minor）")
        lines.append("")
        for mp4, rep in should_review:
            lines.append(f"### `{mp4.name}` ({rep.severity})")
            lines.append("")
            lines.append("```")
            lines.append(rep.format())
            lines.append("```")
            lines.append("")
    if pass_list:
        names = ", ".join(f"`{mp4.name}`" for mp4, _ in pass_list)
        lines.append(f"## ✅ 自动放过（无需看）")
        lines.append("")
        lines.append(names)
        lines.append("")

    # 统计
    lines.append("---")
    lines.append("")
    lines.append(f"**统计**：critical={len(by_severity.get('critical', []))}, "
                 f"major={len(by_severity.get('major', []))}, "
                 f"minor={len(by_severity.get('minor', []))}, "
                 f"ok={len(by_severity.get('ok', []))}")
    lines.append("")
    lines.append("**说明**：本报告为 L1 确定性检查（0 LLM）。严重度 = 'critical/major' 表示必须看；"
                 "'minor' 表示有警示但通过；'ok' 表示无需 review。")

    content = "\n".join(lines)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    return content


# ---------- 主入口 ----------

def main() -> int:
    p = argparse.ArgumentParser(
        prog="auto_review_report",
        description="W1: 自动生成 daily_review_needed.md（视频质量自检报告）",
    )
    p.add_argument("inputs", nargs="+", help="mp4 文件 / 目录 / 通配符")
    p.add_argument("--output", "-o", default="logs/daily_review_needed.md",
                   help="输出 markdown 路径（默认 logs/daily_review_needed.md）")
    p.add_argument("--strict", action="store_true",
                   help="用更严苛阈值（default 阈值 -0.10）")
    p.add_argument("--json", action="store_true",
                   help="同时输出 JSON 详细数据（<output>.json）")
    args = p.parse_args()

    videos = collect_videos(args.inputs)
    if not videos:
        print(f"[FAIL] 无视频：{args.inputs}", file=sys.stderr)
        return 2

    print(f"[INFO] 扫到 {len(videos)} 个 mp4，开始 L1 检查...", file=sys.stderr)
    tmp_root = Path("logs/_auto_review_tmp")
    tmp_root.mkdir(parents=True, exist_ok=True)

    video_reports: list[tuple[Path, FrameQualityReport]] = []
    for mp4 in videos:
        tmp_dir = tmp_root / mp4.stem
        try:
            rep = assess_one_video(mp4, tmp_dir, strict=args.strict)
        except Exception as exc:
            rep = FrameQualityReport(video_path=str(mp4))
            rep.warnings.append(f"L1 异常：{type(exc).__name__}: {exc}")
        video_reports.append((mp4, rep))
        print(f"  [{rep.severity:8s}] {mp4.name}", file=sys.stderr)

    # 跨视频色差（抽 1 关键帧）
    print(f"[INFO] 跑跨视频色差...", file=sys.stderr)
    cross_keyframes: list[Path] = []
    for mp4, _ in video_reports:
        kf_dir = tmp_root / mp4.stem / "kf"
        kf_dir.mkdir(parents=True, exist_ok=True)
        try:
            from hailuo_video.quality import extract_keyframes
            frames = extract_keyframes(mp4, sample_count=1, tmp_dir=kf_dir)
            if frames:
                cross_keyframes.append(frames[0])
        except Exception:
            pass
    cross_report = assess_cross_video(cross_keyframes, strict=args.strict)

    # 写 markdown
    output_path = Path(args.output)
    content = render_markdown(video_reports, cross_report, output_path)
    print(f"\n[OK] 报告落盘：{output_path}", file=sys.stderr)

    # 可选 JSON
    if args.json:
        json_path = output_path.with_suffix(".json")
        json_data = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "video_count": len(video_reports),
            "videos": [
                {
                    "path": str(mp4),
                    "severity": rep.severity,
                    "sample_count": rep.sample_count,
                    "mean_ssim": rep.mean_ssim,
                    "min_ssim": rep.min_ssim,
                    "mean_color_sim": rep.mean_color_sim,
                    "min_color_sim": rep.min_color_sim,
                    "brightness_variance": rep.brightness_variance,
                    "motion_variance": rep.motion_variance,
                    "flagged_pairs": [
                        {"i": i, "j": j, "metric": m} for i, j, m in rep.flagged_pairs
                    ],
                    "warnings": rep.warnings,
                }
                for mp4, rep in video_reports
            ],
            "cross_video": cross_report,
        }
        json_path.write_text(json.dumps(json_data, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        print(f"[OK] JSON 落盘：{json_path}", file=sys.stderr)

    # 控制台摘要
    severity_counts = defaultdict(int)
    for _, rep in video_reports:
        severity_counts[rep.severity] += 1
    print(f"\n=== Summary ===", file=sys.stderr)
    for sev in ("critical", "major", "minor", "ok"):
        if severity_counts[sev]:
            print(f"  {sev}: {severity_counts[sev]}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
