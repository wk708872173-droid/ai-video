# Story Arc: 小黄·中华田园犬·治愈一日 — 索引

> 主线任务：小红书涨粉视频（9:16 竖屏）
> 主角：奶黄中华田园犬幼犬
> 用例：治愈系

## ⭐ 当前推荐上传版本

| 文件 | 时长 | 分辨率 | 大小 | 说明 |
|---|---|---|---|---|
| `final/xiaohuang_9x16_xiaohongshu.mp4` | **17.50s** | **1080x1920** | 12.5 MB | **小红书主推·竖屏** |
| `final/xiaohuang_16x9.mp4` | 17.50s | 1920x1080 | 12.8 MB | 16:9 横屏备用 |
| `final/cover_9x16.png` | — | 1080x1920 | — | 封面候选（草地奶黄小狗） |

拼接顺序：shot_002（草地奔跑·钩子）→ shot_003（门口等待·治愈）→ shot_002（重复·收尾）
**全部 3 镜都是奶黄幼犬**（来自 2026-10-12 V3.3 W2 第一次跑的成功产物）。

## 📅 版本历史（含每次尝试）

| 日期 | 标签 | shot_001 | shot_002 | shot_003 | 来源 |
|---|---|---|---|---|---|
| 2026-10-12 | V3.3 W2 第一次 | ❌ 橘色长毛猫（hailuo T2V 自由发挥） | ✅ 奶黄幼犬 | ✅ 奶黄幼犬 | [archives/2026-10-12_v33_w2_first_run/](archives/2026-10-12_v33_w2_first_run/) |
| 2026-10-13 | V3.4 W3 hotfix | ❌ 浅色长毛猫（修了 first_frame_image + 删 concept art，但 hailuo 仍不接受） | n/a | n/a | [archives/2026-10-13_v34_w3_attempts/](archives/2026-10-13_v34_w3_attempts/) |
| 2026-10-13 | RECOVER v1 | n/a | ✅ | ✅ | [archives/2026-10-13_RECOVER_final/](archives/2026-10-13_RECOVER_final/) |

## 🐛 "小黄变猫" 根因（已修复一半）

V3.4 W3 修了 2 个机制问题：
- ✅ asset_prep prompt 删 "concept art style" 硬编码
- ✅ motion_gen task 传 `first_frame_image`（hailuo 走 I2V 路径）

**未修复**：hailuo API 对 "小黄" 这个中文名的训练数据歧义（高频对应橘猫），比 first_frame 更强。

**明天 quota 重置后的对策**：把 character.name 从"小黄"改成"幼犬"（消歧义），重跑 shot_001 1 quota 验证。详见 [RERUN_NOTES.md](RERUN_NOTES.md)。

## 📂 目录结构

```
story_arc_xiaohuang_daily/
├── README.md                                # 本文件（总索引）
├── RERUN_NOTES.md                           # 明天 quota 重跑说明
├── day1.json                                # 昨天 V3.3 W2 用的 story_arc
├── day1_storyboards/                        # 3 个 shot storyboards
│   ├── shot_001.json
│   ├── shot_002.json
│   └── shot_003.json
├── day1_rerun.json                          # 明天待跑 story_arc（"小黄" → "幼犬"）
├── day1_rerun_storyboards/
│   └── shot_001_rerun.json
├── final/                                   # 当前推荐上传（⭐ 见上表）
├── archives/                                # 历史产物（按日期）
│   ├── 2026-10-12_v33_w2_first_run/
│   ├── 2026-10-13_v34_w3_attempts/
│   └── 2026-10-13_RECOVER_final/
├── shot_002_cinema.mp4                      # 工作副本（带暖色 LUT，可重拼用）
└── shot_003_cinema.mp4
```

## 🚀 明天 quota 重置后立即执行

```bash
# 1) 跑 shot_001（验证"小黄"→"幼犬" 修复链路）
cd sandbox && \
  python -m hailuo_video pipeline \
    ../sample/story_arc_xiaohuang_daily/day1_rerun_storyboards/shot_001_rerun.json \
    -o ../sample/story_arc_xiaohuang_daily/_working/shot_001_rerun.mp4 \
    --image-backend minimax --tts-backend mock --motion-budget 1 \
    --video-effect zoom_in --parallel 1 \
    --run-dir ../sample/run_xiaohuang_daily_shot_001_rerun

# 2) 抽帧验证是狗（不是猫）
ffmpeg -y -ss 1 -i sample/story_arc_xiaohuang_daily/_working/shot_001_rerun.mp4 \
  -frames:v 1 sample/story_arc_xiaohuang_daily/_working/shot_001_rerun_kf.png

# 3) 如果是狗：re-concat → 替换 final/ 中的版本
python scripts/_recover_xiaohuang.py --with-rerun
```