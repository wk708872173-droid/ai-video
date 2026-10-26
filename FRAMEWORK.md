# RMAP — Reference-Matrix Asset-First Pipeline

> 守株待兔 V1→V7 迭代沉淀的 AI 漫剧生产框架。**已验证可用，** 持续完善中。

---

## 🎯 核心理念（一句话）

**"物料先行 + i2i 锚定"** —— 别让 MiniMax 从零生图；先给每个角色、每个场景做好多维度 reference 图，每张分镜图都基于这些 reference 做 i2i 生成。

---

## 🏗️ 三大一致性层（按重要性递减）

```
Layer 1 (文本)  style_anchor     → 全局画风基线（prepend 到每个 shot prompt）
Layer 2 (图像)  character ref   → 角色长相 / 表情 / 角度 一致
Layer 3 (图像)  scene ref       → 场景背景 / 时间 一致
```

每一层独立可选、独立缓存。缺 Layer 1 = 画风漂移；缺 Layer 2 = 角色换脸；缺 Layer 3 = 场景换地。

---

## 🎯 4 轴 Picker 决策矩阵（每 shot 必跑）

| 轴 | 触发器 | 选项数 | picker 函数 | 缺它的代价 |
|---|---|---|---|---|
| **angle**（角度） | `shot_type` + `camera_motion` | 4 | `pick_angle_for_shot()` | 角色只看到正面 / 反面 |
| **expression**（表情） | `shot.mood[]` | 3 | `pick_expression_for_mood()` | 表情死板不随情绪 |
| **scene_variant**（场景时间） | `shot.time` | 3 | `pick_scene_variant_for_time()` | 黄昏与白天一样亮 |
| **character itself** | `shot.characters[]` | 1 | always | — |

每个 shot **自动发 3 个 subject_references**（char angle + char expression + scene variant）给 MiniMax API。

### Picker 决策示例

```yaml
# shot:
shot_type:    中景
camera_motion: pan_right
mood:         [急促, 慌张]
time:         清晨
characters:   [兔子]

# 决策:
angle:        side_left     # 中景 → 侧；pan_right → 看角色左
expression:   angry_sad     # 急促/慌张 → 怒/哀
scene_var:    day           # 清晨 → 白天

# 发出 3 个 URLs:
#   兔子/side_left + 兔子/expression_angry_sad + scene_1_day
```

---

## 🗂️ 资产缓存布局（V7 标准）

```
ai_video/
└── assets/
    ├── characters/<safe_name>/
    │   ├── front.jpg + .url.txt           # 4 angles × 24h 有效 URL
    │   ├── side_left.jpg + .url.txt
    │   ├── side_right.jpg + .url.txt
    │   ├── back.jpg + .url.txt
    │   ├── expression_neutral.jpg + .url.txt     # 3 expressions
    │   ├── expression_happy.jpg + .url.txt
    │   └── expression_angry_sad.jpg + .url.txt
    │   = 7 sheets per character
    └── scenes/
        ├── scene_0001_day.jpg + .url.txt          # 3 variants per scene
        ├── scene_0001_sunset.jpg + .url.txt
        └── scene_0001_night.jpg + .url.txt
        = 3 sheets per scene
```

**单 storyboard 标配**：N chars × 7 + M scenes × 3 sheets。守株待兔（2 char + 1 scene）= 14 + 3 = **17 sheets**。

---

## 🚀 工作流（3 步）

### Step 1 · 写剧本（LLM 辅助）
```bash
python -m hailuo_video script novel.txt -o storyboard.json \
    --backend minimax --shots 30
```
或手写 `storyboard.json`，必填字段：
```jsonc
{
  "title": "...",                         // 漫剧标题
  "style_anchor": "...",                  // ⭐ Layer 1：全局画风关键词
  "characters": [                         // ⭐ Layer 2 角色定义
    {
      "name": "...",
      "appearance": "...",               // ⭐ 必填详细（MiniMax 用 i2i 锁脸）
      "personality": [...],
      "color_palette": [...]              // ⭐ 配色（注入 prompt）
    }
  ],
  "scenes": [                             // ⭐ Layer 3 场景定义
    {
      "id": 1,
      "location": "...",
      "atmosphere": "...",
      "key_elements": [...]               // ⭐ 关键元素（"远山", "稻田", "树桩"）
    }
  ],
  "shots": [
    {
      "index": 1,
      "scene_id": 1,                     // ⭐ picker 用：选哪个 scene 的 variant
      "shot_type": "远景",                // ⭐ picker 用：选角度
      "camera_motion": "pan_left",       // ⭐ picker 用：pan 方向 → 角色哪侧
      "mood": ["平和", "写意"],          // ⭐ picker 用：选表情
      "time": "清晨",                     // ⭐ picker 用：选场景时间变体
      "characters": ["农夫"],
      "description": "...",               // shot 描述
      "voiceover": "...",                 // Edge-TTS 配音
      "dialogue": ""                      // 对白
    }
  ]
}
```

### Step 2 · 预生成物料（一劳永逸，可缓存）
```bash
python -m hailuo_video prep storyboard.json --assets-dir ./assets
# 产物：assets/characters/<name>/{4 angles + 3 expressions}.jpg × N
#       assets/scenes/scene_NNNN_{day,sunset,night}.jpg × M
# 全部 cache hit（重跑秒出，除非 --force）
```

### Step 3 · 跑全流程（最终产物）
```bash
python -m hailuo_video pipeline storyboard.json -o final.mp4 \
    --image-backend minimax \
    --tts-backend edge \
    --voice zh-CN-XiaoxiaoNeural \
    --video-seconds 5 \
    --assets-dir ./assets \
    --keep-intermediate  # 调试用
```
产物：`final.mp4`（h264 1280x720 + aac 音频）+ `intermediate/` 中间产物

### 迭代
```bash
# 调 storyboard.json（改 description / mood / time）→ 重跑 pipeline（Phase 0 全部 cache hit）→ 几秒出新 mp4
```

---

## 🎯 MiniMax API 实战经验（守株待兔 V1→V7 沉淀）

| 失败模式 | 根因 | 解决方案 |
|---|---|---|
| **角色每次换脸** | MiniMax 文本→像素 非确定性，5 shot 5 张不同脸 | i2i subject_reference（V3+） |
| **画风漂移** | 每 shot prompt 独立写画风 | style_anchor 顶层字段（V3+） |
| **角色只正面** | 只生成 front reference | multi-angle picker（V5+） |
| **pan 方向无关** | side 不区分 left/right | camera_motion → side_left/right（V6+） |
| **表情死板** | 全用 front_neutral | mood → neutral/happy/angry_sad（V7+） |
| **场景不分昼夜** | 单 scene sheet | shot.time → day/sunset/night（V7+） |

### MiniMax API 字段名陷阱（实测修正）

```python
# ❌ 单数 + 缺下划线 → 2067 invalid params
body["subject_reference"] = [url]

# ✅ 复数 + 下划线 → 0 success
body["subject_references"] = [url1, url2, url3]  # 列表！实测支持最多 4 个
```

### Schema 实测（与官方文档略不同）

| 字段 | 官方/文档 | 实测 |
|---|---|---|
| URL 模式字段名 | `subject_reference` | **`subject_references`（复数）** |
| URL 模式字段值 | 单 URL | `list[str]`（实测支持多 URL） |
| URL 模式响应字段 | `data.image_url` | **`data.image_urls`（复数）** |
| URL 有效期 | 24h | **实测 Expires=1790... 签名** |
| prompt_optimizer | 默认 True | **必须 False（保 reference 锚定）** |

---

## 📊 守株待兔 sample 对比（按一致性强度）

| 版本 | mp4 | 一致性层 | refs/shot | 推荐 |
|---|---|---|---|---|
| V1 | 1.15 MB | 无 | 0 | ❌ |
| V2 | 1.07 MB | style + prompt | 0 | ❌ |
| V3 | 1.18 MB | + 1 角度 refs | 1-2 | ⚠️ 入门 |
| V4 | 1.23 MB | + char + scene | 2 | ⚠️ |
| V5 | 1.03 MB | + **3 角度** picker | 1-2 | ✅ MVP 起步 |
| V6 | 1.20 MB | + **4 角度** picker | 1-2 | ✅ |
| **V7** | **1.24 MB** | **+ 4 角度 × 3 表情 × 3 场景变体** | **3** | **✅ 推荐生产** |

存于 `ai_video/sample/` 7 个 mp4 文件可直接对比效果。

---

## 🎓 MiniMax Token Plan 实测配额（守株待兔全程）

| 操作 | 守株待兔全程消耗 | 配额占用 |
|---|---|---|
| Image API (17 sheets + 5 shots) | ~22 calls | ~22 quota |
| Script API (3 calls: novel→script→chars→shots) | 3 calls | ~3 quota |

Plus Token Plan 给的初始配额约数百次/月。**足够做几个完整漫剧**。若遇 `2067 token_plan quota exceeded`，等 24h 自动恢复，或升级到 Max/Ultra。

---

## 📁 项目文件结构（V7 后）

```
ai_video/
├── README.md                          # 项目总览（onboarding）
├── FRAMEWORK.md                       # ← 你在这（RMAP 方法论）
├── assets/                            # 物料缓存（git ignore）
│   ├── characters/<name>/
│   └── scenes/
├── sandbox/hailuo_video/
│   ├── cli.py                          # CLI 入口
│   ├── local_client.py                 # V2.0 本地视频（fallback / 备用）
│   ├── script_engine.py                 # V2.1 LLM 剧本
│   ├── image_engine.py                 # V2.2 MiniMax 图像 + subject_references
│   ├── tts_engine.py                   # V2.3 Edge-TTS
│   ├── concat_engine.py                # V2.4 ffmpeg 拼接
│   ├── video_engine.py                 # V2.5 Ken Burns
│   ├── asset_prep.py                   # V2.5.2 reference sheets 预生成
│   └── pipeline.py                     # V2.5.2 端到端编排
├── tests/                              # 244 passed
├── sample/                             # V1-V7 mp4 samples
└── knowledge/                          # D-009 ~ D-016 决策史
```

---

## 🚦 适用边界（坦白）

**RMAP 能解决**：
- ✅ 角色一致性（4 角度 + 3 表情）
- ✅ 场景一致性（3 时间变体 + key_elements）
- ✅ 画风一致性（style_anchor 文本层）
- ✅ 单角色 batch 跨 tool 一致（基于 cached reference）

**RMAP 不解决**（诚实标注）：
- ❌ 真 AI 视频帧间运动（Ken Burns 是"伪视频"）
- ❌ MiniMax Plus quota 超限时的生成（需等 24h 或升级套餐）
- ❌ 超长漫剧的 context window（每 shot 独立生成，10+ shots 时仍可能风格漂移）

需要真 AI 视频帧间运动 → 需 GPU + MiniMax Max/Ultra 套餐或自部署 CogVideoX。