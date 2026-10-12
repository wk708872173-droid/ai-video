# Daily Review Needed — 2026-10-10

> 自动生成（`auto_review_report.py`）：扫 3 个视频，L1 帧级质量检查 + 跨视频色差。

## 总览

| 视频 | 大小 | 严重度 | L1 状态 | 主要问题 |
|---|---|---|---|---|
| `story_arc_orange_cat_4_seasons_shot_001_hailuo_v33.mp4` | 1.8MB | major | ⚠️ major | 2 对帧 flag |
| `story_arc_orange_cat_4_seasons_shot_002_hailuo_v33.mp4` | 2.6MB | critical | ⚠️ critical | 5 对帧 flag |
| `story_arc_orange_cat_4_seasons_shot_003_hailuo_v33.mp4` | 826KB | ok | ✅ pass | - |

## 跨视频色差（跨镜一致性）

mean 色差相似度: **0.592** | min: **0.548**

[WARN] 2 对相邻镜被 flag：
- shot 1 ↔ shot 2: sim=0.636
- shot 2 ↔ shot 3: sim=0.548

## 🚨 必看（critical + major）

### `story_arc_orange_cat_4_seasons_shot_002_hailuo_v33.mp4` (critical)

```
[video] sample\story_arc_orange_cat_4_seasons_shot_002_hailuo_v33.mp4 (6 帧采样)
  mean lite-SSIM: 0.507  min: 0.400（阈值 0.65）
  mean 色差 cosine: 0.969  min: 0.965（阈值 0.65）
  亮度方差: 0.0000（阈值 0.15）
  运动方差: 0.0000（阈值 0.04；越高越卡顿）
  [WARN] 5 对帧被 flag：
    - 帧 1 ↔ 帧 2: metric=0.497
    - 帧 2 ↔ 帧 3: metric=0.598
    - 帧 3 ↔ 帧 4: metric=0.418
    - 帧 4 ↔ 帧 5: metric=0.400
    - 帧 5 ↔ 帧 6: metric=0.623
```

### `story_arc_orange_cat_4_seasons_shot_001_hailuo_v33.mp4` (major)

```
[video] sample\story_arc_orange_cat_4_seasons_shot_001_hailuo_v33.mp4 (6 帧采样)
  mean lite-SSIM: 0.699  min: 0.633（阈值 0.65）
  mean 色差 cosine: 0.906  min: 0.840（阈值 0.65）
  亮度方差: 0.0009（阈值 0.15）
  运动方差: 0.0007（阈值 0.04；越高越卡顿）
  [WARN] 2 对帧被 flag：
    - 帧 1 ↔ 帧 2: metric=0.633
    - 帧 3 ↔ 帧 4: metric=0.637
```

## ✅ 自动放过（无需看）

`story_arc_orange_cat_4_seasons_shot_003_hailuo_v33.mp4`

---

**统计**：critical=1, major=1, minor=0, ok=1

**说明**：本报告为 L1 确定性检查（0 LLM）。严重度 = 'critical/major' 表示必须看；'minor' 表示有警示但通过；'ok' 表示无需 review。