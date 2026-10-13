"""V3.4 W3 hotfix 归档：清理 sample/story_arc_xiaohuang_daily/ 混乱。

策略：
  - final/         ← 当前推荐上传（RECOVER 改名去 _RECOVER 后缀）
  - archives/<日期>/ ← 所有过去产物按日期归档（含抽帧临时目录）
  - day1_rerun.*   ← 明天 quota 重置后的目标 storyboard（小黄 → 幼犬）

执行前需要：
  - 今天的 V3.4 W3 hotfix 代码已 commit（git status clean）
"""
import shutil
from pathlib import Path

ROOT = Path("sample/story_arc_xiaohuang_daily")
ARCH = ROOT / "archives"
FINAL = ROOT / "final"


def move(src_rel: str, dest_dir: Path) -> None:
    """移文件到归档。保留文件名。"""
    src = ROOT / src_rel
    if not src.exists():
        print(f"[skip] {src_rel} 不存在")
        return
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if dest.exists():
        # 避免覆盖：加序号后缀
        i = 1
        while dest.exists():
            dest = dest_dir / f"{src.stem}_{i}{src.suffix}"
            i += 1
    shutil.move(str(src), str(dest))
    print(f"[move] {src_rel} → {dest.relative_to(ROOT)}")


def main():
    if not ROOT.is_dir():
        print(f"[FAIL] {ROOT} 不存在")
        return 1

    # 1) 归档今天 V3.4 W3 的 3 次失败尝试（含 cat shot_001 + keyframes）
    w3_dir = ARCH / "2026-10-13_v34_w3_attempts"
    for rel in [
        "shot_001.mp4",                  # 第 3 次 fresh run_dir 后产物
        "_v34run_shot_001_FRESH.png",    # 第 3 次抽帧（浅色猫）
        "_kf_v34/shot_001_v34.png",      # 第 2 次抽帧（V3.4 W3 fix 验证）
        "_kf_v34/shot_001_W3.png",       # 第 3 次（stale cache 时抽）
        "_kf_v34/shot_001_FRESH.png",    # 第 3 次抽帧
    ]:
        move(rel, w3_dir)
    # 删空 _kf_v34
    shutil.rmtree(ROOT / "_kf_v34", ignore_errors=True)
    print("[cleanup] _kf_v34/ 已删")

    # 2) 归档昨天 V3.3 W2 的产物（cat shot_001 + dog shot_002/003 + 旧 _frames/_kf）
    v33_dir = ARCH / "2026-10-12_v33_w2_first_run"
    for rel in [
        "shot_001_cinema.mp4",   # 昨天第一次 cat
        "shot_002.mp4",          # 昨天狗（之后 RECOVER 用这个）
        "shot_003.mp4",          # 昨天狗（之后 RECOVER 用这个）
        "xiaohuang_9x16_xiaohongshu.mp4",    # 昨天第一次 3 镜 concat（含猫）
        "xiaohuang_16x9_concat.mp4",         # 昨天第一次 16:9
        "cover_9x16.png",         # 昨天第一次封面（cat）
        "_frames/",               # 抽帧临时
        "_kf/",                   # 抽帧临时
        "_concat_list.txt",       # 拼接中间临时
    ]:
        move(rel, v33_dir)

    # 3) 归档 RECOVER → final/（去掉 _RECOVER 后缀作为"当前推荐"）
    FINAL.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "xiaohuang_9x16_xiaohongshu_RECOVER.mp4", FINAL / "xiaohuang_9x16_xiaohongshu.mp4")
    shutil.copy2(ROOT / "xiaohuang_16x9_recover.mp4", FINAL / "xiaohuang_16x9.mp4")
    # cover: 优先用 cover_v34.png（草地狗·小黄），如果没有用 RECOVER 第一帧
    if (ROOT / "cover_v34.png").is_file():
        shutil.copy2(ROOT / "cover_v34.png", FINAL / "cover_9x16.png")
    print("[copy] final/xiaohuang_9x16_xiaohongshu.mp4 + cover_9x16.png")

    # RECOVER 副本留 archive 备查
    rec_dir = ARCH / "2026-10-13_RECOVER_final"
    rec_dir.mkdir(parents=True, exist_ok=True)
    for f in ("xiaohuang_9x16_xiaohongshu_RECOVER.mp4", "xiaohuang_16x9_recover.mp4", "cover_v34.png"):
        if (ROOT / f).is_file():
            shutil.move(str(ROOT / f), str(rec_dir / f))
            print(f"[move] {f} → archives/2026-10-13_RECOVER_final/")

    print()
    print("=== 归档完成 ===")
    print(f"目录结构：")
    for p in sorted(ROOT.iterdir()):
        if p.is_dir():
            files = sorted(p.iterdir())
            print(f"  {p.name}/")
            for f in files[:6]:
                print(f"    {f.name}{'  +...' if len(files) > 6 and f == files[5] else ''}")
            if len(files) > 6:
                print(f"    ... ({len(files) - 5} more)")
        else:
            print(f"  {p.name}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())