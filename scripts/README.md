# scripts/ — 产品化脚本索引

8 个脚本，按用途分组。所有脚本都遵循 CLAUDE.md 规范（不静默降级、Secrets 域硬失败、shell-quote 安全）。

---

## 一、故事化生产（核心 — 用户 6 镜 / 2 天 quota 流程）

### 1. [build_storyboards.py](build_storyboards.py)
**StoryArc → N 个独立 storyboard JSON**

```bash
python scripts/build_storyboards.py sample/story.json sample/output_dir
```

- 读 `sample/story.json`（含 1 个 story_arc + 多个 shots）
- 输出 N 个独立 storyboard.json（每个可被 pipeline 单独跑，消耗 1 quota/张）
- StoryArc → Storyboard 拆解保留全局 style_anchor + character 锁定

### 2. [run_story.sh](run_story.sh) + [.bat](run_story.bat)
**批量跑 N 个 storyboard**

```bash
bash scripts/run_story.sh sample/story_arc_orange_cat_4_seasons/day1_storyboards
```

- 自动检测 storyboard.json 是否已生成（没有就自动调 build_storyboards.py）
- 逐个跑 pipeline，每个 1 个 quota
- 自动从 `intermediate/` 救回产物（绕开 cmd_pipeline 输出路径 bug）
- 自动匹配每镜 Ken Burns effect（shot_001→zoom_in 等）

### 3. [stitch_story.py](stitch_story.py)
**N 个 mp4 拼接为完整故事**

```bash
python scripts/stitch_story.py output.mp4 input_*.mp4 --transition fade --overlap 0.5
```

- 自动检测有无音轨 → 无音轨用 `concat demuxer`（硬切）
- 有音轨尝试 `editing.crossfade_clips`（带转场）
- 失败降级用 `concat filter`（保留 audio，硬切）

---

## 二、电影质感升级（无 Hailuo quota）

### 4. [cinematic_upgrade.py](cinematic_upgrade.py) → **T1 cinema 版**
**单图 Ken Burns + 抛物线缓动 + 色调 LUT + 程序化环境音 + 胶片颗粒**

```bash
python scripts/cinematic_upgrade.py sample/story_dir --suffix _cinema
python scripts/stitch_story.py output.mp4 sample/*_cinema.mp4
```

- 镜头不再机械（抛物线 ease-in-out 替代线性）
- yuv420p + bt709（专业色彩空间，替代默认的 yuvj420p + bt470bg）
- 每镜独立 ambient audio（pink/brown noise + 滤波：晨鸟/虫鸣/梦境呼吸）
- 色调 LUT（Day 1 暖 / Day 2 冷）
- tblend 帧间柔化 + noise 胶片颗粒
- **0 Hailuo quota**

### 5. [multi_frame_shot.py](multi_frame_shot.py) → **L2 定格动画**
**每镜生成 5 张"动作阶段"图 + ffmpeg xfade 拼成定格动画**

```bash
python scripts/multi_frame_shot.py shot_001 --arc sample/story.json
python scripts/multi_frame_shot.py shot_002 shot_003 --arc sample/story.json   # 批量
```

- 每个 shot 5 帧（动作阶段）：蜷缩→睁眼→伸爪→弓背→望窗（shot_001 示例）
- ffmpeg xfade 拼成 6s（1.2s/帧 + 0.3s crossfade）
- 6 镜 × 5 帧 = 30 次 image-01 调用
- **0 Hailuo quota + 角色真动作阶段**——肉眼能看到"猫真的在醒来"

### 6. [finalize_l2_story.py](finalize_l2_story.py) → **L2 终版**
**给 L2 6 个 shot 加 audio + 胶片颗粒 + 拼接为完整 mp4**

```bash
python scripts/finalize_l2_story.py
```

- 在 multi_frame_shot.py 输出基础上加 ambient audio + noise 颗粒
- 用 editing.crossfade_clips 尝试 crossfade 拼接
- 失败降级 concat filter 保留 audio
- 输出 `sample/story_..._L2_FINAL.mp4`（36s / 带音频）

---

## 三、批量与运维

### 7. [daily_cron_setup.py](daily_cron_setup.py)
**生成 Windows Task Scheduler 集成脚本（PS1 + BAT + uninstall）**

```bash
python scripts/daily_cron_setup.py
```

- 生成 4 个脚本：setup_daily_cron.ps1 / setup_daily_cron.bat / uninstall_daily_cron.ps1 / uninstall_daily_cron.bat
- 用法：在 Windows 上右键 setup_daily_cron.ps1 → "以管理员身份运行"
- 纯字符串生成可单测（27 个测试覆盖 HH:MM 校验 / task name / 输出 4 文件）

### 8. [regen_3_videos.sh](regen_3_videos.sh) + [.bat](regen_3_videos.bat)
**假期回来后一键重生 3 条视频**

```bash
bash scripts/regen_3_videos.sh
```

- 重跑 sample/storyboard_golden_retriever.json / orange_cat.json / fawn_forest.json
- 把 motion mp4 拷贝成最终 mp4（绕开 cmd_pipeline 输出路径 bug）
- 用 3 个 Hailuo quota

---

## 四、辅助工具

### 9. [explain_story.py](explain_story.py)
**打印 StoryArc 的脚本 + 实际喂给 image_gen / motion_gen 的最终 prompt**

```bash
python scripts/explain_story.py sample/story.json
```

- 输出每个 shot 的：description / image_gen prompt（含 `[风格:...]` 基线）/ Ken Burns effect / motion_complexity
- 用于"对照最终视频看输入什么"

### 10. (后续可加) `finalize_l1_story.py`
**L1 cinema 终版（同 finalize_l2_story.py 但针对 _cinema.mp4）** — 待需要时再写

---

## 五、脚本使用流程图

```
1. 写 1 个 story.json（含 N 个 shots）
     ↓
2. build_storyboards.py（拆 N 个独立 storyboard）
     ↓
3. 选路线：
     A. run_story.sh（3 quota → T2 真视频）        ← 明天 quota 后跑
     B. multi_frame_shot.py + finalize_l2_story.py（30 image-01 → L2 定格）  ← 现在能跑
     C. cinematic_upgrade.py（0 quota → T1 cinema）  ← 现在能跑
     D. 直接用 Ken Burns mp4（0 quota → V0 原版）    ← 历史基线
     ↓
4. stitch_story.py（拼 N 个为完整故事）
     ↓
5. 发小红书 / YouTube
```

---

## 六、成本矩阵（明日 quota 重置后）

| 脚本组合 | Hailuo | image-01 | 时间 | 视觉 |
| :--- | :--- | :--- | :--- | :--- |
| `build_storyboards` + `run_story.sh` + `stitch_story.py` | 3 | 6 | ~3 分钟 | ⭐ 真电影 |
| `build_storyboards` + `multi_frame_shot` + `finalize_l2_story` | **0** | **30** | ~5 分钟 | ⚠️ 定格动画 |
| `build_storyboards` + `cinematic_upgrade` + `stitch_story.py` | **0** | 6 | ~2 分钟 | ❌ 静态 PPT（带滤镜）|
| `run_story.sh` + `cinematic_upgrade`（混合）| 0 | 6 | ~3 分钟 | ⚠️ 部分静态 + 部分真动作 |
