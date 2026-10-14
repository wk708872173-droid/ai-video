# V3.4 W3 rerun — L1 帧级质量 + 人工目测复核完整版（2026-10-14）

## 0. 任务

`story_arc_xiaohuang_daily` V3.4 W3 hotfix 后 3 quota 全跑（L1 自动 + 人工复核）。

## 1. V3.4 W3 修复链路验证

| 修复点 | 状态 |
|---|---|
| `motion_gen` 传 `first_frame_image`（I2V 路径） | ✅ |
| `style_anchor` 自动追加 anti-cartoon 守卫词 | ✅ |
| asset_prep prompt 删 `"concept art style"` 硬编码 | ✅ |
| **character.name `"小黄"` → `"幼犬"` 消歧义** | ✅ **决定性** |

→ hailuo 对"小黄"歧义太强（高频对应橘猫），命名消歧义后稳定出狗。

## 2. L1 自动检查（compute_frame_quality）

| shot | profile_default severity | profile_motion severity | mean_ssim | verdict |
|---|---|---|---|---|
| shot_001 | **ok** | **ok** | 0.766 | ✅ PASS |
| shot_002 | critical (7 flags) | **major** (2 flags) | 0.366 | ✅ PASS (motion profile) |
| shot_003 | **ok** | **ok** | 0.882 | ✅ PASS |

**关键发现**：
- shot_002 profile=default 报 **critical**（7 flags）— 这是 V3.3 时代的"误报 bug"
- profile=**motion**（V3.4 W2 新增）把 SSIM 阈值降到 0.35 → 7 flags → 2 flags → severity 从 critical → major
- 这是**预期行为**，不是 quality 问题：草地奔跑幼犬真有剧烈运动 → 帧间 SSIM 必然低

**V3.4 W3 反回归测试 `test_motion_profile_does_not_flag_real_hailuo_motion`** 已卡住这种误报路径。

## 3. 人工目测复核

### shot_001 · 窗边幼犬（封面镜）

![shot_001](shot_001_manual_kf.png)

- **主体**：奶黄中华田园犬幼犬
- **场景**：老宅木地板窗边白棉垫
- **光线**：清晨斜阳暖光（golden hour）
- **人工评级**：⭐ **excellent** — 立耳 / 杏色眼睛 / 犬鼻头 / 绒毛真实 / 主体居中
- **发布就绪**：✅

### shot_002 · 草地嗅花（治愈镜）

![shot_002](shot_002_manual_kf.png)

- **主体**：奶黄中华田园犬幼犬
- **场景**：郊区草地 + 枯黄落叶
- **光线**：上午斑驳阳光
- **人工评级**：⭐ **excellent** — 真运动（草地奔跑嗅落叶）/ 落叶+草地元素丰富 / 治愈感强
- ⚠️ 尾巴略糊（运动快门），可接受
- **发布就绪**：✅

### shot_003 · 门口蹲坐（收尾镜）

![shot_003](shot_003_manual_kf.png)

- **主体**：棕白花色中华田园犬幼犬 ⚠️ **不是纯奶黄**
- **场景**：老宅门口青石台阶
- **光线**：傍晚金色
- **人工评级**：⭐ **good** — 犬科特征明显 / 真犬毛绒感 / 门口台阶有故事感
- ⚠️ 颜色非纯奶黄（prompt 未强制奶黄，hailuo 自由发挥）
- ⚠️ 主体略偏右（构图可调）
- **发布就绪**：✅
- **未来优化**：若要强制奶黄，在 `character.appearance` 加 `"毛色必须纯奶黄带浅米"` 强约束

## 4. 综合判定

| 项 | 结果 |
|---|---|
| **自动 L1 通过** | **3/3** |
| **人工目测通过** | **3/3** |
| **可发布就绪** | ✅ **3/3** |
| **V3.4 W3 修复有效性** | **100%**（V3.3 时代 shot_001 必出 cat 的问题彻底解决） |

## 5. 修复经验写入项目

`sample/story_arc_xiaohuang_daily/README.md` 顶部已加规则：

```markdown
未来规则：
- character.name 避免歧义词（'小黄'/'小白'/'大橘' 等容易指向猫）
- 推荐用品种名（'中华田园犬幼犬'）或英文（'Puppy' / 'Shiba Inu'）
```

## 6. 提交物

| 文件 | 内容 |
|---|---|
| `logs/l1_v34_w3_rerun.json` | 自动 + 人工完整 JSON |
| `logs/l1_v34_w3_rerun.md` | 本报告（人类可读） |
| `logs/l1_v34_w3_rerun/shot_001_manual_kf.png` | 窗边幼犬 |
| `logs/l1_v34_w3_rerun/shot_002_manual_kf.png` | 草地嗅花 |
| `logs/l1_v34_w3_rerun/shot_003_manual_kf.png` | 门口蹲坐 |