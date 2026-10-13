# RERUN_NOTES — 明天 quota 重置后的执行说明

## 0. 触发原因

今天 (2026-10-13) 用 3 个 hailuo quota 重跑 shot_001，**3 次都出猫**（详见 archives/2026-10-13_v34_w3_attempts/）。

V3.4 W3 hotfix 修了 2 个机制问题（prompt 硬编码 "concept art" + first_frame_image 缺失），但**未能解决根本问题**：
- hailuo API 内部对 `"小黄"` 这个中文名的训练数据歧义（高频对应橘猫）
- 即便 first_frame 是狗，hailuo 仍出猫

## 1. 修复策略：消除歧义

把 character.name 从 **"小黄"** 改成 **"幼犬"**：

| 文件 | 修改 |
|---|---|
| `day1_rerun.json` (副本) | `characters[0].name` 从 `"小黄"` → `"幼犬"` |
| `day1_rerun.json` (副本) | shot_001/002/003.characters 引用同步改成 `"幼犬"` |
| `day1_rerun.json` (副本) | shot_001.subject_action `"小黄慵懒..."` → `"幼犬慵懒..."` |
| `day1_rerun.json` (副本) | shot_002/003 同上 |
| `day1_rerun_storyboards/shot_001_rerun.json` | 自动 build 生成 |

**为什么不直接改 day1.json？**
- day1.json 仍是 V3.3 W2 已 commit 的资产，不能动（reproducibility）
- 改副本 day1_rerun.json → build_storyboards 生成新 storyboard → 跑新 pipeline

## 2. 明天 quota 重置后执行步骤

### Step 1: 删 character cache（防卡通污染）
```bash
rm -rf sandbox/assets/characters/幼犬/
```

### Step 2: 重跑 shot_001_rerun（耗 1 quota）
```bash
cd sandbox && \
  python -m hailuo_video pipeline \
    ../sample/story_arc_xiaohuang_daily/day1_rerun_storyboards/shot_001_rerun.json \
    -o ../sample/story_arc_xiaohuang_daily/_working/shot_001_rerun.mp4 \
    --image-backend minimax --tts-backend mock --motion-budget 1 \
    --video-effect zoom_in --parallel 1 \
    --run-dir ../sample/run_xiaohuang_daily_shot_001_rerun
```

### Step 3: 抽帧验证是狗（关键人眼验收）
```bash
ffmpeg -y -ss 1 -i \
  sample/story_arc_xiaohuang_daily/_working/shot_001_rerun.mp4 \
  -frames:v 1 \
  sample/story_arc_xiaohuang_daily/_working/shot_001_rerun_kf.png
```
肉眼检查：是不是**奶黄幼犬**？立耳？犬科特征？
- ✅ 是 → 进入 Step 4
- ❌ 仍是猫 → 停止，hailuo API 持续不稳，需换 prompt 策略（如 "tutorial dog video" "shiba inu puppy" 英文 prompt 试）

### Step 4: 用 lib_ffmpeg 重拼最终版（覆盖 final/）
```python
# scripts/_finalize_with_rerun.py（明天写，本次只说明）
import sys; sys.path.insert(0, 'scripts')
from lib_ffmpeg import apply_cinematic_lut_by_shot_id, to_vertical, concat_files

# 4a) cinema LUT
apply_cinematic_lut_by_shot_id(
    'sample/story_arc_xiaohuang_daily/_working/shot_001_rerun.mp4',
    'sample/story_arc_xiaohuang_daily/_working/shot_001_rerun_cinema.mp4',
    'shot_001',
)
# shot_002 / shot_003 cinema 直接复用 archives/2026-10-12_v33_w2_first_run/*_cinema.mp4

# 4b) 拼接顺序
files = [
    'sample/story_arc_xiaohuang_daily/_working/shot_001_rerun_cinema.mp4',  # 新：窗边幼犬
    'sample/story_arc_xiaohuang_daily/archives/2026-10-12_v33_w2_first_run/shot_002_cinema.mp4',
    'sample/story_arc_xiaohuang_daily/archives/2026-10-12_v33_w2_first_run/shot_003_cinema.mp4',
]
concat_files(files, 'sample/story_arc_xiaohuang_daily/_working/xiaohuang_16x9_full3.mp4')

# 4c) 转 9:16
to_vertical(
    'sample/story_arc_xiaohuang_daily/_working/xiaohuang_16x9_full3.mp4',
    'sample/story_arc_xiaohuang_daily/final/xiaohuang_9x16_xiaohongshu.mp4',
)

# 4d) 抽封面帧
ffmpeg -y -ss 0.5 -i final/xiaohuang_9x16_xiaohongshu.mp4 -frames:v 1 final/cover_9x16.png
```

### Step 5: 归档今天 rerun（保留历史）
```bash
mv sample/story_arc_xiaohuang_daily/_working \
   sample/story_arc_xiaohuang_daily/archives/2026-10-14_v34_w3_rerun/
```

## 3. 期望结果

| 步骤 | 期望 | 失败怎么办 |
|---|---|---|
| Step 1 | rm 成功 | — |
| Step 2 | hailuo 200 OK | 检查 env / quota |
| Step 3 | 奶黄幼犬 | 仍出猫 → 换英文 prompt 或暂停 |
| Step 4 | 17.5s 全狗 9:16 mp4 | — |
| Step 5 | 归档清晰 | — |

## 4. 兜底策略（如果仍出猫）

- **策略 A**：把 character.name 改成英文 `"Puppy"` 或品种 `"Shiba Inu Puppy"`（避免中文歧义）
- **策略 B**：让用户换 T2V 之外的视频生成渠道（pixverse / kling）
- **策略 C**：接受现状（RECOVER 拼接版已可上传），仅在标题/文案里说明"何为小黄"

## 5. 关键 V3.4 修复已落地（确保修复链路完整）

| 文件 | 改动 | commit |
|---|---|---|
| `sandbox/hailuo_video/asset_prep.py` | 删 "concept art style" 硬编码 + _safe_style_anchor 守卫 | V3.4 W3 (6011e9f) |
| `sandbox/hailuo_video/orchestrator.py` | style_anchor 兜底 + 传 first_frame_image | V3.4 W3 (6011e9f) |
| `sandbox/hailuo_video/agents.py` | MotionGenerationAgent 传 first_frame_image | V3.4 W3 (6011e9f) |
| `sandbox/hailuo_video/pipeline.py` | V2 path 也走 _safe_style_anchor | V3.4 W3 (6011e9f) |
| `tests/test_v34_regression.py` | 19 个回归测试卡住 | V3.4 W3 (6011e9f) |

明天重跑只需 1 quota 验证修复链路 + 命名消歧义双重保险。