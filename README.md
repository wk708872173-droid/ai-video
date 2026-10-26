# AI Video Agent — 本地视频生成工作台

> **一句话**：把"小说片段"端到端变成"配音 + 字幕 + 画面"的漫剧 mp4。本地优先、零成本优先、免费 API 兜底。

## 📐 框架：RMAP（Reference-Matrix Asset-First Pipeline）

完整方法论见 **[`FRAMEWORK.md`](FRAMEWORK.md)**。核心：物料先行 + i2i 锚定。

- 每个角色 × 4 角度 × 3 表情 = 12 reference sheets
- 每个场景 × 3 时间变体 = 3 reference sheets
- 每 shot 自动选最佳 3 个 URLs 作 i2i 锚定
- **3 步工作流**：`prep`（生成物料）→ `pipeline`（端到端）
- **典型 sample**：见 `sample/mvp_v7_full_matrix.mp4`（V7，5 shots / 19s / 1.24 MB）

复制 [`storyboard_template.json`](storyboard_template.json) 启动新项目。

---
![alt text](image-1.png)
## 🎬 这个项目能做什么

输入一段小说文本，输出一个完整的漫剧短片 mp4，包括
- **画面**：每镜头 5s 视频片段（Ken Burns + MiniMax i2i 锚定）
- **配音**：Edge-TTS 旁白/对白 mp3
- **字幕**：.vtt 时间轴（可烧到视频里）

支持：
- ✅ 完整漫剧（`script` + `prep` + `pipeline` 三步走，按 FRAMEWORK.md）
- ✅ 单镜头快速测试（`image "a cat" -o cat.jpg --backend mock`）
- ✅ 批量（`batch prompts.txt -o ./out/`）
- ❌ 不做视频后期 / 转码 / 拼接以外的剪辑

---

## 💰 成本一览（核心卖点）

| 环节 | 工具 | 成本 |
|---|---|---|
| 剧本 / 角色 / 分镜 | MiniMax chat API | **约 0**（Plus 套餐免费额度内） |
| 配音 / 字幕 | Edge-TTS | **0**（微软免费接口） |![alt text](image.png)
| 文生图 | MiniMax image API | **约 0**（Plus 套餐免费额度内） |
| 文生视频 | 本地 diffusers / CogVideoX | **0**（但 CPU 极慢） |
| 拼接 | ffmpeg via imageio-ffmpeg | **0** |

**几乎全程零成本**。完整漫剧 MiniMax Token Plan 配额足够。

---

## 🚀 快速上手（RMAP 三步法，详见 FRAMEWORK.md）

```bash
# 1. 安装本项目（可编辑模式，一次性）
#    ⚠️ 包实际位于 sandbox/ 下，必须装一下；否则下面的
#       `python -m hailuo_video ...` 会报 "No module named hailuo_video"
pip install -e .
#    依赖随装（pillow / imageio-ffmpeg / edge-tts）。
#    还要跑本地视频推理（generate / batch 子命令）则加：
#    pip install -e ".[local-video]"

# 2. 填 .env（MiniMax API Key，剧本+图像需要；Plus 套餐已有）
echo "MINIMAX_API_KEY=sk-cp-你的key" > .env
echo "MINIMAX_BASE_URL=https://api.minimaxi.com/v1" >> .env

# 3. 复制 storyboard 模板，改 characters/scenes/shots
cp storyboard_template.json my_storyboard.json

# 4. 跑 Phase 0：生成 reference 物料（一劳永逸，可缓存重跑）
python -m hailuo_video prep my_storyboard.json --assets-dir ./assets

# 5. 跑 Phase 1+2：端到端生成 mp4
python -m hailuo_video pipeline my_storyboard.json -o final.mp4 \
  --image-backend minimax --tts-backend edge \
  --voice zh-CN-XiaoxiaoNeural --video-seconds 5

# 不想花钱先试通流程？三个子命令都支持 mock 后端（不调任何真 API）：
#   python -m hailuo_video script novel.txt -o sb.json --backend mock
#   python -m hailuo_video image "一只猫" -o shot.jpg --backend mock
#   python -m hailuo_video tts "你好" -o out.mp3 --backend mock
```

---

## 📁 文件结构

```
ai_video/
├── .env.example                         # 配置模板（真实 Key 放 .env）
├── README.md                            # ← 你现在看的
├── sandbox/hailuo_video/
│   ├── __init__.py                      # 包入口
│   ├── __main__.py                      # Windows UTF-8 shim + sys.path
│   ├── cli.py                           # argparse 子命令入口
│   ├── local_client.py                  # V2.0  本地视频推理（diffusers / CogVideoX）
│   ├── script_engine.py                 # V2.1  LLM 辅助剧本（MiniMax chat / Mock）
│   ├── prompts.py                       # V2.1  3 个中文 prompt 模板
│   ├── image_engine.py                  # V2.2  文生图（MiniMax image API / Mock）
│   ├── tts_engine.py                    # V2.3  TTS（edge-tts / Mock）
│   ├── concat_engine.py                 # V2.4  ffmpeg 拼接（imageio-ffmpeg）
│   ├── state.py                         # 最近一次 run 的状态（原子写 .state.json）
│   ├── calllog.py                       # runs.jsonl 解析
│   ├── batch.py                         # 批量编排（顺序）
│   ├── manifest.py                      # batch CSV 报告
│   ├── doctor.py                        # 环境健康检查（torch / 磁盘 / 缓存）
│   └── prd.md                           # 完整设计文档
├── tests/
│   ├── test_hailuo_video.py             # V2.0 单测（66）
│   ├── test_script_engine.py            # V2.1 单测（47）
│   ├── test_image_engine.py             # V2.2 单测（43）
│   └── test_tts_engine.py               # V2.3 单测（23）
└── knowledge/                           # 项目本地决策库（D-009 ~ D-014）
    ├── decisions.md
    ├── lessons.md
    └── index.json
```

---

## 🛠️ CLI 命令速查

```bash
python -m hailuo_video --help
```

| 命令 | 作用 | 示例 |
|---|---|---|
| `generate` | 本地推理 → mp4 | `generate "a cat" -o out.mp4 --steps 5` |
| `ping` | 打印当前配置 + 环境状态 | `ping` |
| `status` | 读 state.json 看最近一次 generate | `status --last` |
| `batch` | 批量跑 prompts.txt | `batch prompts.txt -o ./out/` |
| `log` | 看 runs.jsonl 历史 | `log --tail 10` |
| `doctor` | 环境健康检查 | `doctor` |
| `script` | novel → JSON storyboard | `script novel.txt -o sb.json --backend mock` |
| `image` | prompt → jpg/png | `image "a cat" -o cat.jpg --backend mock` |
| `tts` | text → mp3 | `tts "你好" -o hi.mp3 --backend mock` |
| `concat` | manifest.json → final.mp4 | `concat manifest.json -o final.mp4` |
| `pipeline` | storyboard.json → final.mp4（8 phase 端到端） | `pipeline sb.json -o final.mp4 --aspect-ratio 9:16` |
| `prep` | 只生成角色/场景参考图（Phase 0） | `prep sb.json --assets-dir ./assets` |
| `editing` | 后期处理（5 种 mode） | `editing --mode full --input a.mp4 --output b.mp4` |
| `validate` / `check-assets` / `consistency-check` | schema 校验 / 缺料清单 / 色差度量 | `validate sb.json` |
| `review` | VLM 画面评审（**默认 mock，零网络调用**） | `review shot_001.jpg --output report.json` |

每个命令都支持 `--backend mock`（单测假数据，不调真 API）。

### V3.8 新增的关键能力

| 能力 | 怎么用 | 说明 |
|---|---|---|
| **竖屏原生生成** | `pipeline sb.json -o out.mp4 --aspect-ratio 9:16` | 此前画幅被 4 处硬编码遮蔽，9:16 在 V3 生产路径完全不可达 |
| **全局角色圣经** | 自动（`CHARACTER_BIBLE_DISABLE=1` 可关） | 角色参考图按"视觉身份"跨项目复用 → 不再跨天漂移；reference URL 过期会被拒绝并告警 |
| **导演字段真正进模型** | 自动 | 12 个 director 字段拆成"静态视觉条件"（首帧）与"动态叙事条件"（I2V）；`camera_motion` 此前连视频模型都没进过 |
| **跨镜连续性门禁** | 自动（只报警不阻断） | 邻接镜色差 + 亮度跳变 + 色基调锁定，5 维可解释报告 |
| **负提示词** | 自动 | 生成路径首次具备"禁止出现什么"的表达（能力声明式，不发未确认字段） |
| **运动参数知识库** | `--knowledge-path knowledge/motion_params.json` | 检索历史配方与实测经验（默认**不写**，避免污染人工维护的种子数据） |
| **主体感知竖屏裁剪** | `to_vertical(..., subject_box=...)` | 偏心主体不再被中心裁掉（纯 PIL 显著性，零新依赖） |
| **角色语音一致性** | 自动（按说话角色分配） | 此前全片所有角色与旁白共用一个音色；现在同一角色跨镜头恒定同音色，探测式分配保证同片子内不撞音色 |
| **.env 配置真正生效** | 自动 | 此前 20 个键（Hailuo 视频端点 / TTS 音色 / YouTube 凭据…）配了永不生效；现在统一 loader + 单一白名单 + 漂移检测测试 |
| **成片带配音与字幕** | 自动 | 此前 V3 editing 只处理画面，TTS 产物被丢在 registry 没人消费 —— 实测 9 个已交付成片有 8 个无音轨；现在每镜烧字幕 + 混旁白 |
| **多镜产物隔离** | 自动 | 此前固定 artifact_id 让每镜产物互相覆盖（3 镜只剩 1 镜）；现在按 shot 隔离，editing 每镜只选一段（motion 优先） |
| **场景参考图不再被挤掉** | 自动 | 参考图优先级改为 角度→场景→表情（双角色镜头下场景锚定不再丢失） |

---

## 🏗️ 架构（模块依赖关系）

```
                            ┌─────────────────────┐
                            │  cli.py  (入口)      │
                            └──────────┬──────────┘
                                       │
        ┌──────────────┬───────────────┼───────────────┬──────────────┐
        ▼              ▼               ▼               ▼              ▼
  local_client.py  script_engine.py  image_engine.py  tts_engine.py  concat_engine.py
  (本地视频)       (LLM 剧本)        (MiniMax 图)    (Edge-TTS)    (ffmpeg 拼接)
        │              │               │               │              │
        └──────────────┴───────────────┴───────────────┴──────────────┘
                                       │
                                       ▼
                              state.py + calllog.py
                              (持久化：.state.json + runs.jsonl)
```

每个引擎模块结构一致：
```
XxxBackend (ABC)         # 抽象接口
  ├─ XxxXxxBackend        # 生产（调真 API / 本地推理 / ffmpeg）
  └─ MockXxxBackend       # 单测（注入假数据，不依赖外部）
```

---

## 🎯 一条漫剧的完整流水线

```
┌────────────────────────────────────────────────────────────┐
│  1. 剧本：novel.txt → storyboard.json                      │
│     $ python -m hailuo_video script novel.txt -o sb.json   │
│                                                            │
│     script:  title, scenes[]                              │
│     characters: name, appearance, color_palette           │
│     shots: scene_id, description, dialogue, voiceover     │
└────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────┐
│  2. 文生图：shot.description → shot.jpg                      │
│     $ python -m hailuo_video image "..." -o shot.jpg        │
└────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────┐
│  3. 文生视频：shot.description → shot.mp4                   │
│     $ python -m hailuo_video generate "..." -o shot.mp4     │
│     ⚠️ CPU 上 5s 视频 ≈ 30 分钟                             │
└────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────┐
│  4. 配音：shot.dialogue+voiceover → shot.mp3 + shot.vtt    │
│     $ python -m hailuo_video tts "..." -o shot.mp3 \        │
│           --subtitle shot.vtt                              │
└────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────┐
│  5. 拼接：写 manifest.json → final.mp4                       │
│     $ python -m hailuo_video concat manifest.json \        │
│           -o final.mp4                                     │
│                                                            │
│     manifest:                                              │
│     { "shots": [                                          │
│         {"video": "shot0.mp4", "audio": "shot0.mp3",      │
│          "subtitle": "shot0.vtt"},                       │
│         ...                                               │
│     ]}                                                   │
└────────────────────────────────────────────────────────────┘
```

---

## 🧪 测试与 CI

### 本地门禁（唯一权威命令）

```bash
python scripts/auto_dev.py            # 全门禁 + 自动 commit
python scripts/auto_dev.py --no-commit  # 只跑门禁，不提交
```

四步：pytest → secret scan → py_compile 语法检查 → 覆盖率门禁（≥ 80%）。

### CI（V3.9 新增）

`.github/workflows/ci.yml` 在 **push / PR** 上跑**同一个** `scripts/auto_dev.py`
（Python 3.11 / 3.13 两档）。CI 与本地门禁**同源** —— 不另起一套命令，否则
"CI 绿了"不代表"本地门禁也是绿的"。

### pre-commit hook（可选）

```bash
git config core.hooksPath .githooks    # 启用一次即可
```

提交前自动跑测试 + 语法检查 + 覆盖率。紧急提交可 `SKIP_GATE=1 git commit ...` 跳过。

---

## 🧪 测试

```bash
python -m pytest tests/ -q
# 全部用 MockBackend，不依赖 torch / diffusers / Edge-TTS / ffmpeg / MiniMax Key
# CI 可跑，无需 GPU，无需网络，无需 Key
```

---

## ⚠️ 已知限制（Roadmap）

| 项 | 现状 | 怎么解决 |
|---|---|---|
| 视频生成慢（CPU） | 5s 视频 ≈ 30min | 装 GPU 后改 `LOCAL_VIDEO_DEVICE=cuda` `LOCAL_VIDEO_DTYPE=bfloat16` |
| MiniMax image 端点真实 schema | [UNVERIFIED] | 首次烟测后调整 `image_engine.py:_parse_response` |
| `script` JSON 解析 | `MockBackend` 100% 覆盖 | 真 MiniMax-M3 烟测 |
| `tts` 字幕格式 | .vtt 自动出，srt 待转 | ffmpeg 或脚本转换 |
| `concat` 字幕烧录 | Windows 反斜杠已处理 | 跨平台烟测 |
| `.env` 里的未知 key | 打 `[dotenv] [WARN]` 并忽略 | 检查是否拼错（只打 key 名，不打值） |
| Hailuo 多角色 I2V 颜色合并 | 双角色同框会被归一化成同色 | 模型侧限制；短期用 `motion_complexity=simple` + 单镜构图规避 |
| MiniMax OSS URL 过期后无法重新托管 | 过期只能重新生成（有漂移风险） | Gemini 后端走本地字节无此问题；MiniMax 侧需托管方案 |
| Hailuo 单段崩溃率 ~1/3 | 样本估计，待更大样本确认 | 跨镜门禁 + fps 归一化只能缓解拼接放大，单段根因未解 |
| VLM 评审只有 mock 后端 | 真 API 需 `--enable-real` + `GEMINI_VLM_MODEL` | 接口已就位，本轮刻意不接真 API（避免计费与速率限制） |

---

## 📚 进一步阅读

- **[`FRAMEWORK.md`](FRAMEWORK.md)** — RMAP 完整方法论（3 大一致性层 + 4 轴 picker + 缓存布局 + 失败模式表）
- **[`storyboard_template.json`](storyboard_template.json)** — 标准模板（带必填字段标注）
- [`sandbox/hailuo_video/prd.md`](sandbox/hailuo_video/prd.md) — 各版本（V2.0~V2.5.2）的详细设计文档
- [`sample/`](sample/) — V1~V7 mp4 samples（实物对比一致性强度）
- [`knowledge/decisions.md`](knowledge/decisions.md) — D-009 ~ D-028 项目决策史（为什么这样做）
- [`knowledge/lessons.md`](knowledge/lessons.md) — L-008 ~ L-013 教训（.env Key 泄漏 / Windows CJK / API 未核实 / 未接线的实现 / 硬编码遮蔽上游配置 / 静默降级要留痕）
- [`knowledge/motion_params.json`](knowledge/motion_params.json) — 运动参数知识库（REQ-406）：实测配方与经验 + relation graph，供检索增强提示词优化

---

## 🤝 贡献指南

1. 框架变更先读 [`FRAMEWORK.md`](FRAMEWORK.md)；新增 picker 维度同时改 3 处（schema + asset_prep + pipeline）
2. 单条改动先看 `prd.md` §V2.X 相应章节
3. 加新功能先写接口契约（dataclass + ABC），再写 MockBackend 单测，最后写真实 Backend
4. 涉及外部 API 必须按 D-010 模式实现：env 必填 + 缺失硬失败 + MockBackend + 单测
5. Windows 改动必须保留 `__main__.py` 的 UTF-8 shim（L-009）
6. 提交前 `python -m pytest tests/ -q` 全绿

---

**TL;DR for new devs**: 这是个"零成本本地 AI 漫剧工厂"。剧本用 MiniMax chat，图片用 MiniMax image API，视频本地 diffusers，配音互拼成片。CPU 能跑但慢；GPU 飞起。先读 `prd.md`，再读 `cli.py`，跑一次 `python -m hailuo_video --help`。