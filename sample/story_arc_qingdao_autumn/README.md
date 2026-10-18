# Story Arc: 青岛·十月中旬·秋日自然人文 — 索引

> **主题**：青岛 10 月中旬秋日自然 + 人文风光
> **形式**：小红书 9:16 竖屏·纯秋日风光（0 角色，3 镜风景纪录）
> **风格**：写实电影感·ARRI Alexa Mini LF + Cooke S7/i 镜头语言
> **执行日期**：2026-10-19
> **Pipeline 版本**：V3.5 Director (Phase A)

## ⭐ 当前推荐上传版本（2026-10-19 完成）

| 文件 | 时长 | 分辨率 | 大小 | 说明 |
|---|---|---|---|---|
| `final/qingdao_autumn_day1_9x16_xiaohongshu.mp4` | **18s** | **1080x1920** | **7.1 MB** | **小红书主推·竖屏** |
| `final/qingdao_autumn_day1_16x9.mp4` | 18s | 1920x1080 | 7.5 MB | 横屏备用 |
| `final/cover_day1_9x16.png` | — | 1080x1920 | 2.1 MB | 封面（栈桥+海鸥+凉亭·暖金） |

## 📐 故事弧（一日三时段·自然人文闭环）

```
Day 1：晨 → 上午 → 黄昏（青岛一日·自然人文全景）

shot_001  栈桥·晨曦·海鸥          远景  simple  辽阔 / 宁静 / 晨光
shot_002  八大关·秋日小径·公主楼  中景  simple  温暖 / 治愈 / 秋意
shot_003  老城天际线·红瓦·崂山夕阳 远景  simple  壮阔 / 温暖 / 怀旧

起（栈桥地标）→ 承（八大关秋色）→ 合（老城全景）
```

## 🎨 风格锚定（style_anchor）

- **摄影机**：ARRI Alexa Mini LF + Cooke S7/i Full Frame Plus 镜头
- **帧率**：24 fps
- **光线**：自然光金时刻 + 大气薄雾
- **调色**：暖秋色（深琥珀橙 + 青蓝阴影）·经典旅行纪录片质感
- **景深**：浅景深 + 奶油虚化背景
- **胶片颗粒**：细微
- **构图**：9:16 竖屏·xiaohongshu 优化

## 🎬 Cinema LUT 后处理

- 公式：`eq=brightness=0.04:saturation=1.15:gamma=0.98,curves=preset=cross_process`
- 效果：暖色微提（+4% 亮度 / +15% 饱和度）+ 交叉处理曲线（增加阴影青色调）
- shot_001 (栈桥): 暖金晨光 + 海鸥 · ✅ 9:16 主体居中
- shot_002 (八大关): 秋日金黄 + 落叶 · ✅ 9:16 黄绿调（被 LUT 推得稍黄，可接受）
- shot_003 (崂山): 远山 + 落日 + 金空 · ✅ 9:16 极美

## 📂 目录结构

```
story_arc_qingdao_autumn/
├── README.md                                # 本文件（总索引）
├── day1.json                                # 3 镜 story_arc（合并）
├── day1_storyboards/                        # 3 个 shot 独立 storyboards
│   ├── shot_001.json                        栈桥·晨曦·海鸥
│   ├── shot_002.json                        八大关·秋日小径
│   └── shot_003.json                        老城·黄昏·崂山
├── final/                                   # ⭐ 当前推荐上传
│   ├── qingdao_autumn_day1_9x16_xiaohongshu.mp4    7.1 MB
│   ├── qingdao_autumn_day1_16x9.mp4                7.5 MB
│   └── cover_day1_9x16.png                        2.1 MB
├── archives/                                # 历史产物（按执行日期）
│   └── 2026-10-19_v35_qingdao_autumn/       ⭐ 今天的成功版
│       ├── shot_001/002/003.mp4               3 镜 hailuo 原始 (2.2/1.4/2.1 MB)
│       ├── shot_001/002/003_cinema.mp4        + 暖秋色 LUT (3.3/1.9/2.7 MB)
│       ├── shot_001/002/003_image_000.jpg     3 张 first_frame
│       ├── qingdao_autumn_day1_16x9.mp4     3 镜 16:9 拼接 (7.7 MB)
│       ├── qingdao_autumn_day1_9x16_xiaohongshu.mp4  3 镜 9:16 主推 (7.3 MB)
│       └── cover_day1_9x16.png              9:16 封面 (2.2 MB)
```

## ⚠️ 关键发现 & 风险规避

| 风险 | 应对 | 实际效果 |
|---|---|---|
| hailuo 2.3 多角色颜色合并（Day 3 教训） | 0 角色纯秋日风光 | ✅ 完全规避 |
| `motion_complexity: "static"` 触发 pipeline 跳过 hailuo | shot_003 改成 `simple` | ✅ 第 3 次成功 |
| `MINIMAX_IMAGE_ASPECT_RATIO=9:16` env 不生效 | image 是 16:9，最终 9:16 在后期裁切 | ✅ 9:16 裁切完美 |
| hailuo I2V 简化远景元素（shot_003 屋顶→远山） | 主题"黄昏+落日+远山"对，仍 on-theme | ✅ 接受 |
| motion 视频色调偏冷（first frame 暖金 → motion 冷蓝） | cinema LUT 后期拉暖 | ✅ 拉回 |

## 🐛 Known Issue: motion_complexity 静默跳过

**症状**：shot_003 第一次跑（`motion_complexity: "static"`）时，pipeline 直接 `[OK] shot_003.mp4 (0 bytes, 19.1s)`，没有调用 hailuo API。Intermediate dir 只剩 image_000.jpg + kenburns.mp4，没有 motion.mp4。

**根因**：`motion_complexity: "static"` 触发 pipeline 优化：判定"static 镜头不需要 AI motion" → 直接走 kenburns fallback，不消耗 hailuo quota 但产出的是慢推/慢拉的伪运动视频。

**修复**：`motion_complexity: "static"` → `"simple"`（任何需要"镜头动 + 主体动"的镜头都不应该用 static）。如果真的想要完全静态，就明确用 `--motion-budget 0` 跳过 hailuo。

**为什么 [UNVERIFIED]**：pipeline 代码内部判断逻辑在 `orchestrator.py` 深处（不是 `motion_backend.py`），未深挖。

## ⏱ 执行时间线

```
07:17  shot_001 pipeline start
07:20  shot_001 motion 完成 (2.3 MB) → editing soft success
07:21  shot_002 pipeline start
07:26  shot_002 motion 完成 (1.4 MB) → editing soft success
07:26  shot_003 第 1 次跑（motion_complexity=static）→ 0 bytes 静默跳过
07:30  shot_003 第 2 次跑（motion_complexity=simple）→ 第 2 次尝试也失败
07:34  shot_003 第 3 次跑（重跑）→ motion 完成 (2.1 MB) ✅
07:35  3 镜 cinema LUT + 16:9 concat (7.5 MB) + 9:16 crop (7.1 MB) + cover (2.1 MB)
07:36  归档到 archives/2026-10-19_v35_qingdao_autumn/ (12 files, ~30 MB)
```

**总耗时**：约 20 分钟（含 2 次 shot_003 重试）
**Quota 消耗**：3/3（今日 quota 用完；shot_003 静默跳过那次不算 quota，2 次实际调用成功）

## 🚀 明天 quota 重置后（2026-10-20）可继续

- 跑 shot_004 / shot_005 / shot_006 扩展为"青岛一日 6 镜完整版"
- 候选场景：
  - 劈柴院·老字号·清晨烟火
  - 啤酒街·TSINGTAO 1903·午后
  - 崂山·茶园·秋茶采摘
  - 大学路·咖啡馆·红墙黄叶
  - 五四广场·奥帆中心·夜幕
- Day 2 故事方向：青岛"自然 vs 人文"对比（同 1 景不同季/同景不同时段）
