# Story Arc: 小黄·中华田园犬·治愈一日 — 索引

> 主线任务：小红书涨粉视频（9:16 竖屏）
> 主角：奶黄中华田园犬幼犬
> 用例：治愈系

## ⭐ 当前推荐上传版本（2026-10-14 更新：V3.4 W3 + "幼犬" 命名消歧义成功）

| 文件 | 时长 | 分辨率 | 大小 | 说明 |
|---|---|---|---|---|
| `final/xiaohuang_9x16_xiaohongshu.mp4` | **17.50s** | **1080x1920** | 10.7 MB | **小红书主推·竖屏（V3.4 W3 修复）** |
| `final/xiaohuang_16x9.mp4` | 17.50s | 1920x1080 | 10.8 MB | 16:9 横屏备用 |
| `final/cover_9x16.png` | — | 1080x1920 | 1.7 MB | 封面候选（奶黄幼犬趴窗边·特写） |

拼接顺序：shot_001（窗边·钩子）→ shot_002（草地嗅花·治愈）→ shot_003（门口蹲坐·收尾）
**全部 3 镜都是狗** ✅（V3.4 W3 + character.name "小黄" → "幼犬" 消歧义成功）

## 📅 版本历史

| 日期 | 标签 | shot_001 | shot_002 | shot_003 | 来源 |
|---|---|---|---|---|---|
| 2026-10-12 | V3.3 W2 第一次 | ❌ 橘色长毛猫（hailuo T2V） | ✅ 奶黄幼犬 | ✅ 奶黄幼犬 | [archives/2026-10-12_v33_w2_first_run/](archives/2026-10-12_v33_w2_first_run/) |
| 2026-10-13 | V3.4 W3 hotfix 尝试 | ❌ 浅色长毛猫（修复链路） | n/a | n/a | [archives/2026-10-13_v34_w3_attempts/](archives/2026-10-13_v34_w3_attempts/) |
| 2026-10-13 | RECOVER v1 | n/a | ✅ | ✅ | [archives/2026-10-13_RECOVER_final/](archives/2026-10-13_RECOVER_final/) |
| **2026-10-14** | **V3.4 W3 重跑（3 quota 全跑）** | **✅ 奶黄幼犬窗边** | **✅ 奶黄幼犬草地** | **✅ 棕白幼犬门口** | [archives/2026-10-14_v34_w3_rerun/](archives/2026-10-14_v34_w3_rerun/) |

## 🐛 "小黄变猫" 根因（V3.4 W3 + "幼犬" 命名已彻底修复）

V3.4 W3 hotfix 修了 3 个机制问题：
- ✅ asset_prep prompt 删 "concept art style" 硬编码
- ✅ motion_gen task 传 `first_frame_image`（hailuo 走 I2V 路径）
- ✅ style_anchor 自动追加 anti-cartoon 守卫词

**修复关键**：2026-10-14 把 character.name 从"小黄"改成"幼犬" — hailuo 对"小黄"训练数据歧义（高频对应橘猫）已彻底根除。

**未来规则**：所有中文角色名避免歧义词（"小黄" / "小白" 等容易指向橘猫/白猫）。用品种名（"中华田园犬幼犬"）或英文（"Puppy"）代替。

## 📂 目录结构

```
story_arc_xiaohuang_daily/
├── README.md                                # 本文件（总索引）
├── RERUN_NOTES.md                           # 明天 quota 重跑说明（已完成，2026-10-14 验证通过）
├── day1.json                                # 昨天 V3.3 W2 用的 story_arc（"小黄"命名）
├── day1_storyboards/                        # 3 个 shot storyboards
├── day1_rerun.json                          # 修复版 story_arc（"幼犬"命名）
├── day1_rerun_storyboards/                  # 3 个修复版 shot storyboards（2026-10-14 已用）
├── final/                                   # ⭐ 当前推荐上传
│   ├── xiaohuang_9x16_xiaohongshu.mp4     17.5s / 1080x1920 / 10.7MB
│   ├── xiaohuang_16x9.mp4                 17.5s / 1920x1080 / 10.8MB
│   └── cover_9x16.png                     封面（奶黄幼犬趴窗边）
├── archives/                                # 历史产物（按日期）
│   ├── 2026-10-12_v33_w2_first_run/        昨天第一次（cat shot_001 + dog 002/003）
│   ├── 2026-10-13_v34_w3_attempts/         V3.4 W3 hotfix 3 次失败尝试（V3.4 W3 fix）
│   ├── 2026-10-13_RECOVER_final/           RECOVER v1 拼接版
│   └── 2026-10-14_v34_w3_rerun/            ⭐ 今天的成功版（V3.4 W3 + 幼犬命名）
│       ├── shot_001/002/003_rerun.mp4        3 镜 hailuo 原始
│       ├── shot_001/002/003_rerun_cinema.mp4 加暖色 LUT 版
│       ├── shot_001/002/003_rerun_kf.png    3 张验证抽帧
│       └── xiaohuang_16x9.mp4             16:9 拼接版
└── run_xiaohuang_daily_shot_001_rerun/     # 今天 V3.4 W3 rerun run dir
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