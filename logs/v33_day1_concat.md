# Daily Review Needed — 2026-10-10

> 自动生成（`auto_review_report.py`）：扫 1 个视频，L1 帧级质量检查 + 跨视频色差。

## 总览

| 视频 | 大小 | 严重度 | L1 状态 | 主要问题 |
|---|---|---|---|---|
| `story_arc_orange_cat_4_seasons_day1_hailuo_v33_concat.mp4` | 5.3MB | critical | ⚠️ critical | 5 对帧 flag |

## 🚨 必看（critical + major）

### `story_arc_orange_cat_4_seasons_day1_hailuo_v33_concat.mp4` (critical)

```
[video] sample\story_arc_orange_cat_4_seasons_day1_hailuo_v33_concat.mp4 (6 帧采样)
  mean lite-SSIM: 0.390  min: -0.127（阈值 0.65）
  mean 色差 cosine: 0.829  min: 0.526（阈值 0.65）
  亮度方差: 0.0087（阈值 0.15）
  运动方差: 0.0041（阈值 0.04；越高越卡顿）
  [WARN] 5 对帧被 flag：
    - 帧 1 ↔ 帧 2: metric=0.552
    - 帧 3 ↔ 帧 4: metric=-0.127
    - 帧 4 ↔ 帧 5: metric=0.418
    - 帧 5 ↔ 帧 6: metric=0.383
    - 帧 3 ↔ 帧 4: metric=0.526
```

---

**统计**：critical=1, major=0, minor=0, ok=0

**说明**：本报告为 L1 确定性检查（0 LLM）。严重度 = 'critical/major' 表示必须看；'minor' 表示有警示但通过；'ok' 表示无需 review。