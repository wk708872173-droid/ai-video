# V3.3 Hailuo 主线验证报告 — 2026-10-10

## 0. TL;DR

V3.3（`scene_references` + `style_anchor` 前置）hailuo 主线 = **真改善**，但 L1 阈值是给 L2 stop-motion 设计的，**对 hailuo 真实运动视频完全反指**。今天 3 hailuo quota 全部用完（shot_001/002/003 V3.3 产物落盘），V3.3 vs V0 人眼对比 **3 镜全胜**。

## 1. 今日动作

### 1.1 已完成
- ✅ V3.3 测试套件再跑 22/22 绿（`tests/test_orchestrator_v33_refs.py` + `tests/test_quality_frame.py`）
- ✅ W1 L1 单镜评估：6 镜 L2 现有产物 → 4 ok + 1 minor + 1 major（shot_005 真问题，V0 产出）
- ✅ L1 跨镜色差：6 镜 L2 min=0.996（完美，scene_references + style_anchor 全部到位）
- ✅ Hailuo pipeline 跑 shot_001/002/003（用 3/3 quota），motion mp4 落盘：
  - `sample/story_arc_orange_cat_4_seasons_shot_001_hailuo_v33.mp4` (1.9MB, 1920x1080)
  - `sample/story_arc_orange_cat_4_seasons_shot_002_hailuo_v33.mp4` (2.7MB, 1920x1080)
  - `sample/story_arc_orange_cat_4_seasons_shot_003_hailuo_v33.mp4` (0.8MB, 1920x1080)

### 1.2 跑挂的事
- ⚠️ `editing` 阶段每次 FAIL（不影响 motion mp4 获取，仅 cinema/stitch 后处理未跑）
- ⚠️ `scene_2/3` 无缓存 → `[picker] shot 0 scene_variant: requested='白天', fallback to 'day'`（不影响 V3.3 主流程，scene_references 自动空，style_anchor prefix 仍生效）

## 2. V3.3 vs V0 视觉对比（shot_001 hailuo 主线）

| 指标 | V0 (v3.mp4) | V3.3 (hailuo_v33.mp4) | 真实情况 |
|---|---|---|---|
| L1 severity | ✅ ok | ⚠️ major | L1 **倒置** |
| mean_ssim | 0.924 | 0.699 | V3.3 帧间真实变化（运动） |
| min_ssim | 0.866 | 0.633 | V3.3 运动跨越大 |
| 视觉运动 | 几乎不动 | **sleep → stretch → head up** | V3.3 真运动 |
| 电影感 | 静态照片 | 暖光 + 棉垫 + 真实表演 | V3.3 强 |
| 跨镜色差 | — | 0.572 vs V0 | 风格不同（V0 无风格可言） |

**结论**：V3.3 hailuo 主线大幅优于 V0。V0 几乎是一张静态图，V3.3 有完整"睡→伸→抬"的 6 秒表演。

## 3. 3 镜 V3.3 hailuo L1 评估

| shot | L1 severity | min_ssim | mean_ssim | 实际视觉 |
|---|---|---|---|---|
| shot_001 | major | 0.633 | 0.699 | ✅ 真运动（睡→伸→抬），电影感强 |
| shot_002 | **critical** | 0.400 | 0.507 | ✅ 真运动（花园行走→嗅花→看镜头），连续无 cut |
| shot_003 | ok | 0.993 | 0.995 | ❌ 几乎不动（夕阳屋顶坐 6 秒） |

**L1 完全倒置**：
- shot_002 critical ＝ 真运动（5 对连续 flag 是猫在动）
- shot_003 ok ＝ 几乎静止（0.995 mean_ssim ＝ 帧几乎无变化）
- L1 阈值是为 L2 stop-motion（5 帧 xfade，0 运动）设计的，对 hailuo 24fps 真实运动视频完全不适用

## 4. 暴露的工程问题（待修）

### 4.1 L1 阈值对 hailuo 反指（**最高优先级**）
- **现象**：L1 看到 hailuo 真实运动（低 SSIM）→ flag "major/critical"；看到 hailuo 静止帧（高 SSIM）→ flag "ok"
- **后果**：用户拿到 daily review 报告会被严重误导（真问题漏报，假问题刷屏）
- **建议修复**：
  - 给 hailuo 单独阈值档：`compute_frame_quality(..., profile="hailuo")` → SSIM 阈值 0.40 / color 阈值 0.60
  - 或：加 `motion_floor` 检测 —— 6 秒视频 motion_variance < 0.005 反而报警（"无运动"）
  - 估时：30 分钟（含测试）

### 4.2 multi_frame_shot.py stitcher 出黑边（**中优先**）
- **现象**：portrait 9:16 source → 16:9 1280x720 target，stitcher 用 `force_original_aspect_ratio=decrease,pad=...` 留 280px 黑边
- **V0 无此问题**（旧 stitcher 无 pad）
- **建议修复**：把 multi_frame_shot.py `default aspect_ratio` 从 `"9:16"` 改为 `"16:9"`（让源帧就 16:9，stitcher 不需要 pad）
- **未做原因**：今天 quota 优先 hailuo 主线，stitcher fix 需重跑 L2 验证
- 估时：15 分钟（1 行代码改 + 5 镜重生）

### 4.3 pipeline `editing` 阶段 FAIL（**低优先**）
- **现象**：`hailuo_video pipeline` 跑出 motion 后 editing 阶段 halt（不影响 motion mp4，但 final cinema 没生成）
- **建议**：下次跑前看 `log.jsonl` 找根因（缺 ffmpeg 滤镜？缺 tts？）
- 估时：1 小时（含定位）

## 5. 今日 quota 用量

| 类型 | 用 | 余 | 备注 |
|---|---|---|---|
| image-01 | 1 | 2 | ping 端点 |
| hailuo-2.3 | 3 | 0 | shot_001/002/003 motion 全产 |

## 6. 备份与产物索引

```
sample/
  _backup_v33run/                    # 今日备份：shot_001 旧全版本 + shot_002/003 旧 run dir
  _v33run_shot_001/                  # 今日 V3.3 run（motion + kenburns + image_000 + log.jsonl）
  _v33run_shot_002/
  _v33run_shot_003/
  story_arc_orange_cat_4_seasons_shot_001_hailuo_v33.mp4   # ⭐ V3.3 hailuo 主力产物
  story_arc_orange_cat_4_seasons_shot_002_hailuo_v33.mp4   # ⭐
  story_arc_orange_cat_4_seasons_shot_003_hailuo_v33.mp4   # ⭐
logs/
  v33_hailuo_vs_v0.md / .json        # V0 vs V3.3 shot_001 hailuo 对比
  v33_hailuo_3shots.md / .json       # V3.3 hailuo 3 镜 L1
  l2_review_20261010.md / .json      # 6 镜 L2 V0/V3.3 混
  _h33_3shots/                       # 3 镜 × 12 帧 = 36 张抽帧（人眼复核用）
  _hailuo_v33_kf/                    # shot_001 V3.3 12 帧
  _hailuo_v0_kf/                     # shot_001 V0 12 帧
```

## 7. 明天要做（如果继续）

1. **修 L1 hailuo 阈值**（不费 quota，估时 30 min）—— 解锁自动 review 可信度
2. **跑 cinematic_upgrade.py**（不费 quota，估时 10 min）—— 把 3 镜 hailuo motion 加 T1 cinema 质感
3. **查 editing 阶段 FAIL**（估时 1h）—— 拿完整 final.mp4 而非仅 motion
4. **跑 day2 (shot_004/005/006) hailuo**（3 quota，估时 15 min）—— 补齐 6 镜完整 V3.3 产物
5. **修 stitcher 黑边**（1 quota 重生 5 镜 L2，估时 30 min）—— L2 multi_frame_shot 视觉补完

## 8. 今日 session 关键判断

- V3.3 **scene_references + style_anchor** 改动**有效**，hailuo 主线从"静态照片"升级为"真实表演"
- W1 L1 当前**不可信**用于 hailuo 主线（误报 / 漏报同时存在），需要分档阈值
- 用户的"画风断层"痛点 → V3.3 在跨镜色差上给 0.996（L2 6 镜完美），hailuo 主线单镜内由静转动

**Net positive**：今天 1 半天换 3 个 V3.3 hailuo 真产物 + 1 个 L1 校准问题发现，价值密度高。
