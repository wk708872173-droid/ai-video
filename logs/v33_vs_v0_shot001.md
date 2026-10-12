# Daily Review Needed — 2026-10-10

> 自动生成（`auto_review_report.py`）：扫 2 个视频，L1 帧级质量检查 + 跨视频色差。

## 总览

| 视频 | 大小 | 严重度 | L1 状态 | 主要问题 |
|---|---|---|---|---|
| `story_arc_orange_cat_4_seasons_shot_001_v3.mp4` | 1.3MB | ok | ✅ pass | - |
| `story_arc_orange_cat_4_seasons_shot_001_l2.mp4` | 310KB | ok | ✅ pass | - |

## 跨视频色差（跨镜一致性）

mean 色差相似度: **0.043** | min: **0.043**

[WARN] 1 对相邻镜被 flag：
- shot 1 ↔ shot 2: sim=0.043

## ✅ 自动放过（无需看）

`story_arc_orange_cat_4_seasons_shot_001_v3.mp4`, `story_arc_orange_cat_4_seasons_shot_001_l2.mp4`

---

**统计**：critical=0, major=0, minor=0, ok=2

**说明**：本报告为 L1 确定性检查（0 LLM）。严重度 = 'critical/major' 表示必须看；'minor' 表示有警示但通过；'ok' 表示无需 review。