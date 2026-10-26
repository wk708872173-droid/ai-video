# PRD — RMAP AI 漫剧产品

> **唯一真相源**。PM-Agent 维护。冲突时以本文件为准。
> 与 `sandbox/hailuo_video/prd.md` 的关系：本文是**顶层产品需求**（PM 视角），sandbox/prd.md 是**协议层变更详表**（V2.x 各版本的实现细节）。
>
> **当前状态**（2026-10-10）：DAG 为 8 phases（`prep → script → image_gen → motion_gen → video_synth → tts → editing → qa`）。8 个 Domain Agent 继承 `agents.Agent`。`editing/` 与 `quality/` 是包，不再有 `editing_engine.py` / `quality.py`。image_gen 的 `subject_references` 只传角度、表情、场景的 http(s) URL（最多 4 个），V2 `run_pipeline` 与 V3 orchestrator 共用 `asset_prep.resolve_shot_reference_urls`。REQ-301 / REQ-303 / REQ-304 / REQ-305 已完成；REQ-302 的 4 步代码已在 `motion_backend.py`；YouTube 真上传与真人脸识别仍是 stub。

---

## 产品定位

### 一句话

**RMAP = 用 AI 自动把短篇故事变成可发布的 AI 漫剧短片**，生成端用 MiniMax Hailuo-2.3 真 AI 视频，AI 负责分镜提示词与后期剪辑，人负责战略与验收。

### 目标用户

| 角色 | 用 RMAP 干什么 | 价值 |
| :--- | :--- | :--- |
| **个人内容创作者** | 每日自动产出 1-3 条 60 秒水墨风漫剧 | 不烧自己 GPU / 不写 ffmpeg / 不挑 prompt |
| **AI 漫剧工作室** | 批量跑分镜 → 自动剪辑 → 投 YouTube | 把"生成 + 后期"从 8h 压到 30min |
| **RMAP 开发者（自己）** | 验证多 Agent 架构的产品化潜力 | 产品即脚手架，跑通即落地 |

### 核心价值主张

| 维度 | RMAP（现状） | 替代方案 |
| :--- | :--- | :--- |
| **生成端** | MiniMax Hailuo-2.3 真 AI 视频帧间运动（每天 3 quota） | PixVerse V6 / Kling 3.0 / Sora——未跑通，重启成本高 |
| **一致性** | Master Reference + 固定 Seed + 4 阶段 picker | 单 prompt 多次跑——脸型 / 服饰必漂移 |
| **后期** | EditingAgent 色彩归一化 + 重叠帧叠化 + 可选 AI 剪辑 MCP | 裸 ffmpeg concat——色温跳变、动作断裂 |
| **调度** | daily_cron 每日 9:00 自动跑 + quota 硬保护 | 手敲 CLI——配额易超、状态不可追溯 |
| **成本** | 零自托管 GPU（Ken Burns 兜底）；Max 套餐 ≤ 3/日 | 全 GPU 跑——硬件门槛 / 电费 / 维护 |

### 当前能力快照（2026-09-29）

| 能力 | 状态 | 来源 |
| :--- | :--- | :--- |
| 文生图（MiniMax image） | ✅ P0 已交付 | REQ-002 |
| Edge-TTS 中文配音 | ✅ P0 已交付 | REQ-003 |
| Ken Burns 推拉镜头视频 | ✅ P0 已交付 | REQ-001 |
| ffmpeg 拼接 + 字幕烧入 | ✅ P0 已交付 | REQ-004 |
| RMAP 4 阶段 picker + 一致性保证 | ✅ P0 已交付 | REQ-005 |
| QA 校验 + picker fallback 警告 | ✅ P0 已交付 | REQ-006 |
| 8 Domain Agents + Orchestrator（DAG） | ✅ P1 已交付 | REQ-101~105 |
| Hailuo-2.3 真 AI 视频帧间运动 | ✅ P2 已交付（V3.1） | REQ-201 |
| 分镜含运动指令字段（`camera_motion` 等） | ✅ P2 已交付（V3.2） | REQ-301 |
| EditingAgent 色彩归一化 | ✅ P2 已交付（V3.2） | REQ-303.a |
| EditingAgent xfade 叠化 | ✅ P2 已交付（V3.2 自动开发冲刺） | REQ-303.b |
| EditingAgent 运动匹配剪切 | ✅ P2 已交付（V3.2 自动开发冲刺） | REQ-303.c |
| EditingAgent LUT 调色 | ✅ P2 已交付（V3.2 自动开发冲刺） | REQ-303.d |
| EditingAgent full pipeline（color → xfade → LUT） | ✅ P2 已交付（V3.2 自动开发冲刺） | REQ-303.full |
| **L1 合成微动（ffmpeg trick：手持呼吸 + 帧间柔化）** | ✅ P2 已交付（V3.2 自动开发冲刺 轮 11） | REQ-303.L1 |
| **L2 多帧定格动画（每镜 5 张动作阶段图 + xfade 拼接）** | ✅ P2 已交付（V3.2 自动开发冲刺 轮 12） | REQ-303.L2 |
| 每日自动调度 + 配额保护 | ✅ P2 已交付（V3.2 脚本） | REQ-305 |
| Windows Task Scheduler 集成脚本生成器 | ✅ P2 已交付（V3.2 自动开发冲刺） | REQ-305.b |
| PublishAgent ABC + Mock + Youtube stub | ✅ P2 已交付（V3.2 自动开发冲刺）；真 YouTube 上传待凭据 | REQ-401 (interface) |
| 70/30 提示法 + 三拍结构 + 16 种 camera_motion 白名单 | ✅ P2 已交付（V3.2 自动开发冲刺 轮 2） | REQ-301.b（提示词强化）|
| cli.py `editing` 子命令（5 个 mode + 47 种 xfade transition） | ✅ P2 已交付（V3.2 自动开发冲刺 轮 2） | REQ-303 CLI |
| MCP 客户端 ABC + Mock + CutStudio/SeeCut stub | ✅ P2 已交付（V3.2 自动开发冲刺 轮 2）；真 MCP 接入待服务部署 | REQ-303.e |
| `--resume-from` 实际跳过已完成 phase + 加载历史 registry | ✅ P2 已交付（V3.2 自动开发冲刺 轮 3）| REQ-103 |
| 面部相似度比对（QAAgent face mode，hash/null/face_recognition 3 个 backend） | ✅ P2 已交付（V3.2 自动开发冲刺 轮 3，零依赖轻量版） | REQ-402 (interface) |
| Hailuo 4 步完整流程（submit/poll/retrieve/download） | ✅ 代码已在 `motion_backend.py`；真 quota 端到端按当日额度复验 | REQ-302 |
| Orchestrator DAG 含 editing phase | ✅ 8 phases，`concat` 已改名为 `editing` | REQ-304 |
| EditingAgent AI 剪辑 MCP 集成（CutStudio / SeeCut） | ⏳ P2 未做 | REQ-303.e |
| YouTube 真上传（OAuth + resumable upload） | ⏳ P3 未做（stub 已就位） | REQ-401 (real) |
| 面部相似度自动比对 + 重试 | ⏳ P3 未做（hash 后端已有，`face_recognition` 仍是 stub） | REQ-402 |
| 角色语音一致性（per-shot voice override） | ✅ V3.9 已交付（按说话角色分配 + 探测式防撞车） | REQ-403 |
| 多 scene 跨场一致性 | ⏳ P3 未做 | REQ-404 |
| 实时预览 | ⏳ P3 未做 | REQ-405 |
| 运动参数知识库 | ⏳ P3 未做 | REQ-406 |
| 反馈闭环（播放量反哺提示词） | ⏳ P3 未做 | REQ-407 |

### 关键边界（**不做的事**）

- ❌ **不换生成模型**——不引入 PixVerse V6 / Kling 3.0 / Sora。Hailuo-2.3 + Max 套餐是已跑通的唯一路径
- ❌ **不上 GPU 自托管**——Max 套餐 + Ken Burns 兜底已能覆盖 3 条/日
- ❌ **不做实时预览**——REQ-405 暂搁置，等 3 条/日跑稳后再评估
- ❌ **不做 asyncio 全栈改造**——ThreadPoolExecutor 足够，V4 候选
- ❌ **不静默降级**——picker fallback / Hailuo 配额用尽 / EditingAgent 失败，全部显式 `[WARN]`，写日志；Secrets/PII/Auth 域硬失败

### 核心断言（与代码一致）

```
Hailuo 负责生成（视频帧间运动）
AI 负责提示词与剪辑（ScriptWriterAgent + EditingAgent）
人负责战略与验收（不参与微观搬砖）
```

---

## 终极目标：novel → YouTube 每日自动发布

### 定义

**"终极目标"** = `python daily_cron.py` 一行命令触发，端到端产出已发布到 YouTube 的 AI 漫剧短片，全程不需人介入（除了首次 OAuth 授权 + 每日验收 review）。

### 终态路径

```
[小说文本 / 梗概]
    ↓ daily_cron 每日 9:00 自动触发
[ScriptWriterAgent] ─→ 含运动指令的 Storyboard JSON
    ↓
[ImageGenAgent × N] + [MotionGenerationAgent × Top-N] + [TTSAgent × N] + [VideoSynthAgent fallback]
    ↓
[EditingAgent] ─→ 色彩归一化 + 运动匹配剪切 + 重叠帧叠化 + LUT 调色
    ↓ final.mp4
[QAAgent] ─→ 面部相似度 + 一致性检查（不合格自动重试 1 次）
    ↓ 通过
[PublishAgent] ─→ YouTube OAuth + upload（自动取标题/标签/章节）
    ↓
[YouTube 公开链接] + [logs/daily_report.md 日报]
```

### 验收标准（终极目标）

| 项 | 验收 | 当前状态 |
| :--- | :--- | :--- |
| **端到端跑通** | `python scripts/daily_cron.py --once` → YouTube 上线一条 60s 漫剧 | ⏳ 待 YouTube OAuth 凭据 |
| **每日自动** | Windows Task Scheduler 配置后，每日 9:00 自动触发，无需手动 | 🟡 脚本就绪；Task Scheduler 集成脚本待生成（`scripts/setup_daily_cron.ps1`）|
| **配额保护** | Hailuo 调用 ≤ 3/日；超额 → Ken Burns 兜底 + `[WARN]` | ✅ `daily_cron.py` 已实装 |
| **质量门禁** | QAAgent 面部相似度 < 阈值 → 自动重试 1 次 | ⏳ REQ-402 待实装 |
| **失败不静默** | 任何环节失败 → `[FAIL]` 写日报，pipeline exit ≠ 0 | ✅ 已实装 |
| **可恢复** | 任何 phase 失败 → `--resume-from <phase>` 续跑 | ✅ `PipelineAgent` 按 phase 名跳过此前阶段 |
| **0-quota 出片**（L2 多帧定格） | 30 image-01 调用产 36s 6 镜定格动画 mp4（无需 Hailuo quota） | ✅ **轮 12 已达**——`sample/story_arc_orange_cat_4_seasons_L2_FINAL.mp4` |

### 距终极目标的差距清单

| # | 缺口 | 影响 | 行动 |
| :--- | :--- | :--- | :--- |
| 1 | REQ-303.b/c/d 后期子功能 | 色温 / 叠化 / LUT | ✅ 已在 `editing/` |
| 2 | Task Scheduler 集成脚本 | 每日自动 | ✅ `scripts/daily_cron_setup.py` 可生成；本机任务仍需用户安装 |
| 3 | YouTube OAuth 凭据缺失 | 无法真上传 | 用户后续提供 `client_id` + `client_secret` |
| 4 | REQ-401 PublishAgent 未实装 | 接口 + Mock 已就位 | 待凭据后接入真 API |
| 5 | REQ-402 面部相似度比对未实装 | 质量门禁缺一环 | 待 `face_recognition` 库接入 |
| 6 | REQ-302 真 quota 复验 | 代码已有 submit/poll/retrieve/download | 有当日额度时再跑一条，确认 `task_id → file_id → mp4` |

### 关键边界（终极目标专属）

- ❌ **全自动 ≠ 无人值守**：YouTube 首次 OAuth 必须人介入；后续 access_token 自动刷新
- ❌ **不引入视频上传替代方案**：不用 Vimeo / Bilibili / 自建 Web—— YouTube 是目标渠道（YouTube 反馈快、用户基数大）
- ❌ **不发 YouTube Shorts**：本 PRD 默认 ≤ 60s 横屏视频；Shorts 竖屏适配不在范围内
- ❌ **不做内容审核自动化**：YouTube 自带 Community Guidelines；本系统不引入 NSFW 检测 / 重复检测——那是 YouTube 自己的责任
- ❌ **不在外网暴露 daily_cron 接口**：脚本只跑本地 / 跑用户的 Windows，不开 HTTP 端点

### 一句话终极目标

> **每日 9:00，novel 进，YouTube 链接出——人只负责首次 OAuth 与最终 review。**

---

## 需求（按优先级）

### P0 已交付（V2.0–V2.6）

#### REQ-001 · 本地视频推理（CogVideoX）
- **用户故事**：作为内容创作者，我想在本地机器上跑 prompt → mp4，不烧 API 配额
- **验收**：
  - 机器：`pytest tests/test_hailuo_video.py -q` 全绿（CI 不装 torch）
  - 人工：`python -m hailuo_video generate "a cat" --steps 5 --num-frames 9 -o /tmp/out.mp4` 落 mp4 + runs.jsonl + state.json
- **优先级**：P0
- **实现位置**：`sandbox/hailuo_video/local_client.py`
- **关联决策**：D-011（MiniMax Plus 无视频 API → 切本地 diffusers）

#### REQ-002 · 文生图（MiniMax image API）
- **用户故事**：作为创作者，我想把文本 prompt 转成 16:9 水墨风格 JPG
- **验收**：
  - 机器：image CLI 能调 MiniMax image API 或 mock backend
  - 人工：`python -m hailuo_video image "测试" -o /tmp/x.jpg --backend mock` 生成有效 jpg
- **优先级**：P0
- **实现位置**：`sandbox/hailuo_video/image_engine.py`
- **关联决策**：D-013（用户核实 CN 域 `api.minimaxi.com/v1/image_generation`，模型 `image-01`）

#### REQ-003 · TTS（Edge-TTS）
- **用户故事**：作为创作者，我想要免费中文配音，零 Key 零账号
- **验收**：
  - 机器：`pytest tests/test_tts_engine.py -q` 全绿
  - 人工：`python -m hailuo_video tts "你好" -o /tmp/x.mp3` 落 mp3 + 可选 .vtt
- **优先级**：P0
- **实现位置**：`sandbox/hailuo_video/tts_engine.py`
- **关联决策**：D-014（Edge-TTS pip 包；零成本）

#### REQ-004 · ffmpeg 拼接
- **用户故事**：把 per-shot mp4+mp3+vtt 拼成一条完整 mp4
- **验收**：
  - 机器：`pytest tests/test_video_engine.py -q` 全绿（拼接逻辑在 video_engine.py / concat_engine.py）
  - 人工：`python -m hailuo_video concat manifest.json -o out.mp4` 落带音频+字幕 mp4
- **优先级**：P0
- **实现位置**：`sandbox/hailuo_video/concat_engine.py`
- **依赖**：`imageio-ffmpeg==0.6.0`（自带 ffmpeg 二进制）
- **注**：原 PRD 引用 `tests/test_concat_engine.py`，仓库无此文件（`ls tests/`）；现有覆盖在 `test_video_engine.py`（[UNVERIFIED] 命名一致性）

#### REQ-005 · RMAP 4 阶段一致性 picker
- **用户故事**：作为创作者，我希望同角色 5 张镜头看起来脸型/服饰一致（而非 5 张不同脸）
- **验收**：
  - 人工：`sample/mvp_v7_full_matrix.mp4` — `[UNVERIFIED]` 2026-09-29 `ls sample/` 为空，**该文件不存在**，需先生成
  - 机器：style_anchor + 角色外貌注入测试 — `[UNVERIFIED]` 用例数待数（`test_asset_prep.py` 覆盖中）
- **优先级**：P0
- **实现位置**：`sandbox/hailuo_video/pipeline.py` + `asset_prep.py`
- **关联决策**：D-016（物料先行 + i2i 锚定）

#### REQ-006 · QA 校验 + picker fallback 警告
- **用户故事**：作为创作者，我想 pipeline 跑完后自动告诉我哪些 shot 走了 fallback（避免静默降级）
- **验收**：
  - 人工：`python -m hailuo_video validate storyboard.json` 抓到 unknown picker 字段
  - 机器：consistency check 测出相邻 shot 色差过大
- **优先级**：P0
- **实现位置**：`sandbox/hailuo_video/quality/`
- **验收状态**：✅ 已交付 V2.6（`test_quality.py` 覆盖）

---

### P1 已交付（V3.0 多 Agent）

#### REQ-101 · 7 个 domain agents + orchestrator
- **用户故事**：作为开发者，我希望每阶段（脚本/物料/图像/视频/TTS/拼接/QA）可独立测试、可独立替换
- **验收**：
  - 机器：`pytest tests/ -q` — 当前 **1209 passed, 12 skipped**（2026-10-26）；正式报告需跑 `--no-header`
  - 人工：每个 Agent 可单独 mock 后跑 pipeline 端到端
- **优先级**：P1
- **实现位置**：`sandbox/hailuo_video/{agents,orchestrator,artifact,retry,log}.py`
- **验收状态**：✅ 已交付 V3.0（`test_agents.py` + `test_orchestrator.py` 覆盖）

#### REQ-102 · 阶段间并发（ThreadPoolExecutor max_workers=4）
- **用户故事**：作为开发者，我希望 image_gen/video_synth/tts per-shot 并发跑，不串行
- **验收**：
  - 机器：`parallel=2` 测试 3 shots 全部并发
  - 人工：`pipeline --parallel 4` 比 `--parallel 1` 快 1.5×（I/O bound 场景）
- **优先级**：P1
- **实现位置**：`sandbox/hailuo_video/orchestrator.py:run_phase`
- **风险**：MiniMax Plus quota 触限 → max_workers 限 4（D-014）

#### REQ-103 · 断点续跑（--from / --resume）
- **用户故事**：作为开发者，pipeline 中途 Ctrl-C 后能从失败 phase 续跑（不重新跑 image_gen）
- **验收**：
  - 机器：`registry.json` 落盘 + `Registry.load_json_or_empty`
  - 人工：`pipeline --resume-from concat` 跳过前面 phase（前提：registry.json 已有前面 phase 产出）
- **优先级**：P1
- **实现位置**：`sandbox/hailuo_video/cli.py:cmd_pipeline` + `orchestrator.py`
- **验收状态**：🟡 部分完成（--resume-from 已接收，registry 跳过 phase 的逻辑待补）

#### REQ-104 · Artifact registry + 原子写
- **用户故事**：作为开发者，我希望 pipeline 中途失败时已产出 artifact 不丢
- **验收**：
  - 机器：`Registry.save_json` 用 tmp + os.replace 原子写
  - 手工：`pytest tests/test_artifact.py -q` 全 23 passed
- **优先级**：P1
- **实现位置**：`sandbox/hailuo_video/artifact.py`
- **验收状态**：✅ 已交付

#### REQ-105 · 重试策略（指数退避，仅网络错）
- **用户故事**：作为开发者，我希望 transient 网络错（URLError/TimeoutError）自动重试，但 API-key / schema 错不重试（这些是 bug）
- **验收**：
  - 机器：`with_retry` 仅 retry_on 列出的异常
  - 手工：timeout 模拟 → 2 次后成功
- **优先级**：P1
- **实现位置**：`sandbox/hailuo_video/retry.py`
- **验收状态**：✅ 已交付

---

### P2 V3.1 已交付（真 AI 视频生成）

#### REQ-201 · 真 AI 视频帧间运动（Hailuo-2.3）
- **用户故事**：作为创作者，我希望 mp4 帧与帧之间有连贯运动（不是 Ken Burns 推拉镜头）
- **验收**：
  - 机器：`tests/test_motion.py` 覆盖 30 cases：submit / poll / file_retrieve / download / 失败降级 / 条件路由 / budget 限制 —— ✅ **V3.1 交付**
  - 人工：`python scripts/test_hailuo_real.py` 端到端跑通
  - 真人：实测已跑通 3.28MB / 5.88s / 1080P H.264 mp4 — `[UNVERIFIED] task_id=446861242024333`（需用户提供截图或日志复核）
- **优先级**：P2 → V3.1 已交付（用户在 MiniMax Max 套餐，每天 3 次 hailuo-2.3）
- **依赖**：MiniMax Max 套餐（每天 3 次 hailuo-2.3）
- **实现位置**：`motion_backend.py` + `agents.py:MotionGenerationAgent`
- **验收状态**：✅ **V3.1 已交付**；4 步流程完整（submit → poll → file_retrieve → download），原 BUG-001（`_submit` 返回 dict）已于 2026-09-29 复核撤回（`motion_backend.py:287-297` 实测返回 task_id 字符串正确，`test_minimax_backend_success_response` 通过）

---

### P2 V3.2 已交付 / 部分交付（运动指令层 + EditingAgent + 调度）

> 以下 REQ-301 ~ REQ-305 当前状态：301 ✅ 已完成 / 302 🟡 部分完成 / 303 🟡 色彩归一化已完成，子功能待续 / 304 🟡 DAG 结构已更新，端到端跑通待 editing 子功能完成 / 305 ✅ 脚本已完成，Task Scheduler 集成待用户配

#### REQ-301 · ScriptWriterAgent 增加运动指令字段
- **用户故事**：作为创作者，我希望分镜 JSON 中除了 `description`，还包含 `camera_motion`、`subject_action`、`duration`，让 Hailuo 生成有导演意图的画面
- **验收**：
  - 机器：`tests/test_script_engine.py` 覆盖 5 个新 case（motion 字段解析 / duration 别名 / 默认 static / 非法值拒收 / duration 越界拒收）—— ✅ **2026-09-29 完成**
  - 人工：`python -m hailuo_video script novel.txt -o storyboard.json` 输出的每个 shot 含运动指令
- **优先级**：P2（V3.2）
- **实现位置**：`script_engine.py:_parse_shots_payload` + `prompts.py:STORYBOARD_PROMPT`
- **验收状态**：🟢 **已完成 2026-09-29**（parser 提取 + 校验 + 5 测试通过；全测试 372 passed, 2 skipped）
- **字段规范**：
  ```json
  {
    "shot_id": "shot_001",
    "description": "角色A在雨中奔跑",
    "camera_motion": "[推进, 跟随]",
    "subject_action": "奔跑，然后停下回头看",
    "duration": 6,
    "motion_complexity": "complex",
    "dialogue": "…"
  }
  ```
- **技术要点**：
  - 用 LLM 按"三拍结构 + 70/30 提示法"生成；`camera_motion` 仅允许 Hailuo 支持的 15 种运镜指令
  - `motion_complexity` 严格校验 {static, simple, complex}，非法值硬失败（不静默）
  - `duration` 与旧字段 `expected_duration` 双名兼容（LLM 返 `duration`，dataclass 仍存 `expected_duration`）
  - `duration` 范围 (0, 30] 秒

#### REQ-302 · MotionGenerationAgent 补全轮询与下载
- **用户故事**：作为开发者，我希望 MotionGenerationAgent 完整实现 submit → poll → file_retrieve → download 四步，而不是只 submit
- **验收**：
  - 机器：`tests/test_motion.py` 覆盖 poll 超时（600s）/ file_retrieve 解析 / download 落盘 / 失败降级 —— 🟡 **2026-09-29 部分完成**（4 步代码已实现，单元测试覆盖 `_submit` 与 4 步链路；端到端真 API 跑通依赖用户当日 quota）
  - 人工：`--motion-budget 3` 跑通，输出 3 个 `intermediate/shot_NNN_motion.mp4`（**待用户 curl 实测校准 `download_url` 字段名**）
- **优先级**：P2（V3.2）
- **实现位置**：`motion_backend.py:_default_execute`（4 步已就绪；`_submit` 返回 task_id 字符串已实测正确，原 BUG-001 撤回）
- **技术要点**：
  - timeout 600s，interval 10s
  - `file_id` → `GET /v1/files/retrieve?file_id=...` → `download_url`
  - 失败降级 → Ken Burns + `[WARN]`，pipeline 不 halt
- **UNVERIFIED**：
  - `[UNVERIFIED]` 真 API 返回字段中 `download_url` 是否带签名 / 过期时间——若 24h 过期，daily_cron 跨日跑可能拿到失效 URL，需在 `motion_backend.py` 加一次重试 + 重新 submit
  - `[UNVERIFIED]` `MINIMAX_VIDEO_ENDPOINT=https://api.minimax.io/v1/video_generation` 是否为生产域名（用户提供 schema，未实测）

#### REQ-303 · EditingAgent（新增）
- **用户故事**：作为创作者，我希望 pipeline 自动完成色彩归一化、运动匹配剪切、重叠帧叠化，避免多镜头拼接出现色温跳变和动作断裂
- **验收**：
  - 机器：`tests/test_editing.py` 覆盖 19 cases：compute_target_stats / eq_filter / normalize_clip / pipeline / EditingAgent 集成 —— ✅ **2026-09-29 完成**（色彩归一化子功能）
  - 人工：跑通 3 镜头 Pipeline，输出 `final.mp4` 无明显色温跳变（**待真 ffmpeg 端到端验证**）
- **优先级**：P2（V3.2）
- **实现位置**：`editing/` + `agents.py:EditingAgent`
- **验收状态**：🟢 色彩归一化、运动匹配、xfade、LUT、full pipeline 已在 `editing/`
- **子功能完成度**：
  | 步骤 | 状态 | 说明 |
  | :--- | :--- | :--- |
  | 色彩归一化 | ✅ 完成 | `editing/color.py` |
  | 运动匹配剪切 | ✅ 完成 | `editing/motion.py` |
  | 重叠帧叠化 | ✅ 完成 | `editing/crossfade.py` |
  | 统一调色 | ✅ 完成 | `editing/lut.py` |
  | AI剪辑集成 | ⏳ stub | CutStudio / SeeCut 在 `mcp_client.py`，未接真服务 |
- **技术要点**：
  - `editing.color_normalize_pipeline(video_paths, output_dir)`：analyze → compute_target → normalize_clip
  - `set_runner()` 注入 mock，单测不依赖真 ffmpeg
  - 失败 → EditingError，硬失败不静默（CLAUDE.md R4）

#### REQ-304 · Orchestrator DAG 新增 `script` + `editing` phases
- **用户故事**：作为开发者，我希望 DAG 在 `prep` 之后增加 `script` phase（生成含运动指令的 Storyboard），在原 `concat` 位置替换为 `editing` phase（色彩归一化 + 叠化 + 调色）
- **验收**：
  - 机器：`tests/test_orchestrator.py` 覆盖 DAG 含 8 phases（prep / script / image_gen / motion_gen / video_synth / tts / editing / qa）—— ✅ DAG 与 `--resume-from` 跳过已落地
  - 人工：`pipeline --dry-run` 输出 DAG 中可见 `script` + `editing` phase
- **优先级**：P2（V3.2）
- **实现位置**：`orchestrator.py:DEFAULT_DAG` + `cli.py` `--resume-from editing`
- **验收状态**：🟡 **2026-09-29 DAG 已更新为 8 phases**；端到端跑通待 editing 子功能（REQ-303.b/c/d）完成后实测
- **V3.2 修正后 DAG**（与 `模型架构.md` §Orchestrator DAG 一致）：
  | Phase | Agents | Parallel | Halt-on-error |
  | :--- | :--- | :--- | :--- |
  | prep | asset_prep | ❌ | ✅ |
  | **script** | **script_writer** | **❌** | **✅** |
  | image_gen | image_gen | ✅ | ❌ |
  | motion_gen | motion_gen（条件） | ✅ | ❌ |
  | video_synth | video_synth | ✅ | ❌ |
  | tts | tts | ✅ | ❌ |
  | **editing** | **editing** | **❌** | **✅** |
  | qa | qa | ❌ | ❌ |
- **变更说明**：
  - 2026-09-29 V3.2 复核：原"修正版 PRD"曾判定 `script` 不是 DAG phase（仅 `cli.py:cmd_script` 子命令）。V3.2 架构修正后 **改为 `script` 也是 DAG phase**——`ScriptWriterAgent` 输出是后续 `image_gen` / `motion_gen` 的输入，必须串行且失败 HALT
  - `editing` phase 取代原 `concat` phase 位置；`ConcatAgent` 拆解后由 `EditingAgent` 接管（色彩归一化 + 叠化 + 调色 + AI 剪辑 MCP 可选）

#### REQ-305 · 每日调度层（daily_cron）
- **用户故事**：作为创作者，我希望每天 9:00 自动跑 `pipeline --motion-budget 3`，无需手动触发
- **验收**：
  - 机器：`tests/test_daily_cron.py` 覆盖 14 cases：quota 加载 / save / 跨日重置 / 配额用尽跳过 / pipeline 成功 +1 / pipeline 失败不计数 / CLI main 两条路径 —— ✅ **2026-09-29 完成**
  - 人工：第二天早上 9:30 检查 `output/` 有 3 条 mp4（**待 Windows Task Scheduler 实装**）
- **优先级**：P2（V3.2）
- **实现位置**：`scripts/daily_cron.py`（新建）
- **验收状态**：🟢 **2026-09-29 完成脚本 + 14 测试**；Task Scheduler 集成待后续（不在本 REQ 范围内）
- **技术要点**：
  - 硬编码每日最多 3 次 Hailuo 调用（用 `run/quota.json` 记录；`DAILY_MOTION_BUDGET` env 可覆盖）
  - 超额 → 自动跳过 + `[WARN]`，不静默
  - 跨日 → quota.json 自动重置（计数归 0，budget 沿用上次值）
  - pipeline 失败 → 不计数（防止 quota 被错扣）
  - 写 `logs/daily_report.md`（追加，不覆盖）
  - 注入式 `_runner`（单测不依赖真 subprocess）
  - **注**：因 Python 3.14 argparse `store_true + action=append` 回归 bug（`expected one argument`），CLI 设计为：单 flag `--pipeline-cmd "<space-separated>"`，脚本恒为单次跑模式（保留 `--loop` 扩展空间）

---

### P3 未来（Roadmap）

> 以下 REQ-401 ~ REQ-407 **全部为 `[P3 future]`，文件尚未创建**。

#### REQ-401 · 发布链路（YouTube + Farcaster） `[P3 future]`
- **用户故事**：作为创作者，我希望生成的视频自动发布到 YouTube 和 Farcaster
- **验收**：
  - 机器：`tests/test_publish_agent.py` 覆盖 OAuth 流程 / 上传成功 / 失败重试
  - 人工：视频出现在 YouTube 频道和 Farcaster cast 中
- **优先级**：P3
- **依赖**：Google Cloud OAuth 凭据 + Farcaster 签名密钥
- **实现位置**：`publish_agent.py`（新增） — `[UNVERIFIED] 文件不存在`

#### REQ-402 · 质量门禁（面部相似度自动比对） `[P3 future]`
- **用户故事**：作为创作者，我希望 QA 自动比对面部相似度，低于阈值自动重试
- **验收**：
  - 机器：`quality.compute_face_consistency()` 返回相似度分数
  - 人工：故意生成角色漂移的视频，QA 自动标记为不合格
- **优先级**：P3
- **依赖**：face_recognition 或 insightface 库
- **状态**：未做

#### REQ-403 · 角色语音一致性 ✅ V3.9 已交付
- **验收**：5 shots 同一角色的 Edge-TTS voice 一样
- **优先级**：P3
- **状态**：TTSAgent 已支持，但 shot 级 voice override 待加

#### REQ-404 · 多 scene 跨场一致性 `[P3 future]`
- **用户故事**：跨多个 scene（如城市场景 + 山地场景）的角色长相、画风跨场一致
- **验收**：跨 scene 的 mp4 5 shots 风格一致
- **优先级**：P3
- **状态**：scene_variant 已支持 day/sunset/night 3 变体；多 scene 待加

#### REQ-405 · 实时预览 `[P3 future]`
- **用户故事**：pipeline 跑完前能看到中间帧（不用等整个 mp4 才能看）
- **验收**：pipeline 跑 image_gen phase 时立刻能看到第一张图
- **优先级**：P3
- **状态**：未做

#### REQ-406 · 运动参数知识库 `[P3 future]`
- **用户故事**：作为创作者，我希望系统记录每次成功生成的运动参数，形成推荐配置
- **验收**：`knowledge/motion_params.json` 累积 ≥100 条成功案例，含 camera_motion / seed / duration
- **优先级**：P3
- **状态**：未做（`knowledge/` 目录存在但 `motion_params.json` 不存在）

#### REQ-407 · 反馈闭环（数据驱动优化） `[P3 future]`
- **用户故事**：作为创作者，我希望根据播放量/互动数据反向优化提示词模板
- **验收**：`analytics.csv` 记录每条视频的播放量，每周生成优化建议
- **优先级**：P3
- **状态**：未做

---

## 已知问题（与 PRD 并行追踪）

> **2026-10-26 更新**：原表自 2026-09-29 起一直标"当前无未修复的已知问题"，但同期
> `sample/*/README.md`、`logs/_*/SUMMARY.md`、`docs/drafts/README.md` 里实际记录了 30+ 条
> 活 bug —— **空表本身就是一条文档缺陷**（让人以为没有坑）。下表按 V3.8 审计后的真实状态重填。

| ID | 严重度 | 描述 | 状态 / 计划修复 |
|---|---|---|---|
| BUG-101 | 🔴 高 | **9:16 在 V3 生产路径不可达**：`.env` 的 `MINIMAX_IMAGE_ASPECT_RATIO=9:16` 被 4 处硬编码遮蔽（`ProjectContext` 默认值 → `env_overrides` 覆盖 → `run_pipeline_v3` 无该形参 → `or` 短路）；CLI `pipeline` 子命令无 `--aspect-ratio` | ✅ **V3.8 已修**（D-027）。画幅解析改为"显式值 > backend env > 16:9"，CLI 补 `--aspect-ratio` |
| BUG-102 | 🔴 高 | **`motion_complexity="static"` 静默降级**：不进 motion_gen → 走 Ken Burns 兜底，却报 `[OK] ... (0 bytes, 19.1s)`，无 WARN | ✅ **V3.8 已修**（D-027）。记 event + 进 `PipelineResult.consistency` + CLI `[WARN]` |
| BUG-103 | 🔴 高 | **多镜 storyboard 全部镜头共用最后一张图**：`ImageGenAgent` 的 artifact_id 用结果下标而非 shot 序号，`Registry.add` 按 id 覆盖 | ✅ **V3.8 已修**（D-027）。artifact_id 与落盘文件名都按 shot 隔离 |
| BUG-104 | 🔴 高 | **12 个 director 字段到不了模型**：只服务人工审稿；`camera_motion` 连视频模型都没进过 | ✅ **V3.8 已修**（D-022）。static_visual → image prompt，dynamic_narrative → motion prompt |
| BUG-105 | 🟠 中 | **prompt 占位符字面泄漏**：`render_storyboard_prompt` 从未被调用 → `{three_beat_hint}` 等 4 个占位符以花括号进 LLM | ✅ **V3.8 已修**（D-022）。已接线 + 改用安全填充 |
| BUG-106 | 🟠 中 | **reference URL 24h 过期不校验**：跨天跑角色一致性锚定静默失效 | ✅ **V3.8 已修**（D-021）。过期即跳过 + WARN，不再静默使用 |
| BUG-107 | 🟠 中 | **角色跨天漂移**：character sheet 每项目/每天重生成 → Day1 与 Day2 不是同一套画 | ✅ **V3.8 已修**（D-021）。全局角色圣经按视觉身份跨项目复用 |
| BUG-108 | 🟠 中 | **跨镜连续性无门禁**：实测跨镜色差 mean=0.700 vs 理想 0.95，而旧门禁阈值 0.65 形同虚设 | ✅ **V3.8 已修**（D-023）。新增 `continuity.py`，阈值 0.90，默认只报警 |
| BUG-109 | 🟠 中 | **拼接层放大崩溃**：Hailuo 8fps 与 Ken Burns 24fps 混合，`-c copy` 不重采样 → 帧突跳跨段扩散 | ✅ **V3.8 已修**（D-023）。叠化前统一 `fps=24` |
| BUG-110 | 🟠 中 | **registry 落盘静默失败**（`except OSError: pass`）→ 续跑信息丢失无人知晓 | ✅ **V3.8 已修**（D-027）。记 event + 进 consistency（不 raise） |
| BUG-111 | 🟡 中 | **`concat_files` 的 `duration_seconds` 报的是墙钟耗时**（40.5s 的片子报 0.18s） | ✅ **V3.8 已修**（D-027）。改用 `probe_duration`，墙钟另存 `elapsed_seconds` |
| BUG-112 | 🟡 中 | **零 negative prompt 支持**：生成路径无"禁止出现什么"的表达 | ✅ **V3.8 已修**（D-025）。能力声明式 + 内联（不发未确认字段） |
| BUG-113 | 🟡 中 | **运动参数知识库不存在**（REQ-406）—— 各 sample 的实测经验只是散文，无法被程序消费 | ✅ **V3.8 已修**（D-024）。`knowledge/motion_params.json` + 关键词/关系边检索 |
| BUG-114 | 🟡 中 | **中文色词角色名歧义**："小黄" 在训练数据里高频指向橘猫（已知事故：first_frame 是狗仍出猫） | 🟡 **部分修**（D-021）。已加纯代码告警；根因（模型侧）需改用品种名/英文 |
| BUG-115 | 🟡 中 | **Hailuo 多角色 I2V 颜色合并**：双角色同框 + complex motion → 两角色被归一化成同色 | ⬜ **未修**。等模型侧；短期用 `motion_complexity=simple` + 单镜构图规避 |
| BUG-116 | 🟡 中 | **Hailuo 单段崩溃率 ~1/3**（样本估计，待更大样本确认） | 🟡 **部分修**（D-023）。跨镜门禁 + fps 归一化只能缓解拼接放大，单段根因未解 |
| BUG-117 | 🟡 中 | **MiniMax OSS URL 过期后无重新托管途径**（无上传端点）→ 过期只能重新生成（漂移风险） | ⬜ **未修**。Gemini 后端走本地字节无此问题；MiniMax 侧待用户提供托管方案 |
| BUG-118 | 🟢 低 | **`editing` phase 的 `_collect_video_paths_for_editing` 含一段与首段完全相同的死分支** | ✅ **V3.8 已修**（D-027）。死分支已删 |
| BUG-119 | 🟢 低 | **`last_frame_image` 死代码**：`_generate_i2v` 接受 last_frame 但 API 不支持（2013） | ✅ **V3.9 已修**。已删分支与 body 拼装；误传时告警并回退 T2V |
| BUG-120 | 🟢 低 | **PRD 声称 Task Scheduler 脚本待生成，但 `scripts/daily_cron_setup.py` 已存在** | ⬜ **未修**。文档漂移，待下次文档批处理 |
| BUG-127 | 🔴 高 | **多镜 storyboard 的产物互相覆盖**：`VideoSynthAgent`(`video_out`) / `TTSAgent`(`tts_audio`) 用固定 artifact_id，Registry 按 id 覆盖 → registry 里只剩最后一镜 | ✅ **V4.0 已修**（D-034）。artifact_id 按 shot 隔离 |
| BUG-128 | 🔴 高 | **成片无配音无字幕**：V3 editing 只处理画面，TTS 产物被丢在 registry 没人消费 → 实测 9 个已交付成片有 **8 个无音轨** | ✅ **V4.0 已修**（D-036）。每镜烧字幕 + 混旁白（真 ffmpeg 端到端验证）|
| BUG-129 | 🟠 中 | **editing 把同镜的 motion 与 Ken Burns 一起收进拼接列表** → 成片里该镜出现两次 | ✅ **V4.0 已修**（D-035）。按 shot 分组、motion 优先 |
| BUG-130 | 🟠 中 | **双角色镜头的场景参考图被挤掉**（4 个参考图上限；旧顺序 2×(角度+表情) 占满）→ 跨镜背景/光线锚定丢失，而连续性门禁正要衡量它 | ✅ **V4.0 已修**（D-037）。优先级改为 角度→场景→表情 |
| BUG-131 | 🟡 中 | **`--voice` 硬编码默认值遮蔽 `EDGE_TTS_VOICE`**（只有 pipeline 子命令有；tts 子命令是 default=None） | ✅ **V4.0 已修**（D-038）。默认 None = 未指定 → 按角色分配 |
| BUG-121 | 🔴 高 | **`.env` 白名单漂移**：代码共读 44 个 env key，能从 `.env` 加载的只有 24 个 → `MINIMAX_VIDEO_*`（Hailuo 视频端点）/ `EDGE_TTS_*`（音色）/ `YOUTUBE_*`（凭据）等 **20 个配了永不生效** | ✅ **V3.9 已修**（D-029）。统一 loader + 单一白名单 + 不变量测试（正则扫源码）|
| BUG-122 | 🟠 中 | **`auto_dev.py` 的 pytest 路径参数是字面量 "TESTS"** → 门禁跑测试时**不加载 `tests/conftest.py`**（conftest 按 args 目录链加载）| ✅ **V3.9 已修**（D-030）。改 `str(TESTS)` + 2 条回归测试 |
| BUG-123 | 🟠 中 | **secret scan 把凭据本身打进日志**（打的是 `m.group()[:30]` = 密钥前 30 字符），且 CI 上工作树干净时扫描**完全空转** | ✅ **V3.9 已修**。改为只打 `***REDACTED***``；diff 为空时退化为整仓扫描 |
| BUG-124 | 🟠 中 | **`dev` extra 缺 `pytest-cov`** —— 门禁覆盖率步骤用 `--cov`，缺它那步跑不起来 → CI 会因"解析失败"而红 | ✅ **V3.9 已修**。`dev = ["pytest>=7.0", "pytest-cov>=4.0"]` |
| BUG-125 | 🟡 中 | **全片所有角色与旁白共用一个音色**（`task["voice"]` 是全局值）→ 与"角色一致性"核心价值主张冲突 | ✅ **V3.9 已修**（REQ-403）。按说话角色分配，探测式保证同片子内不撞音色 |
| BUG-126 | 🟡 中 | **无 CI**：仓库从未有 GitHub Actions —— 门禁只在本地跑，回归靠人记 | ✅ **V3.9 已修**。`.github/workflows/ci.yml` 跑**同一个** `scripts/auto_dev.py`（py3.11/3.13）+ 可选 pre-commit hook |

> 新发现的疑似问题请直接加在此表，并在 §变更日志 留 entry。

## 验收标准模板

每条 REQ 必含：
- **用户故事**（作为 X，我想要 Y，以便 Z）
- **可验证验收**：
  - 机器：pytest case ID（如 `tests/test_X.py::test_y`）
  - 人工：CLI 命令或视频观看评分
- **边界**（什么算失败）
- **优先级** P0/P1/P2/P3
- **实现位置**（sandbox/hailuo_video/*.py 或 ai_video/*）

---

## 系统约束（Non-Negotiable）

来自 `C:\Users\10174306\.claude\CLAUDE.md` 全局准则：
- **R1 不编造 API 字段** —— MiniMax `subject_references` 实测 2026-09 才确认是复数
- **R4 不静默降级** —— picker fallback 必须显式 `[WARN]`，不静默退化
- **Plan-First**：超过 50 行改动先写 plan
- **禁 delete branch / skip**：永远不 xfail 修绿
- **Gate-Test**：commit 前 `pytest tests/ -q` 必须全绿
- **UNVERIFIED 标注**：所有未来 / 待验事实须前缀 `[UNVERIFIED]`，不假装已实施

---

## 跨文件功能复用清单

| 功能 | 复用位置 |
|---|---|
| `prep` CLI 子命令 | `cli.py:cmd_prep`（V2.5） |
| `pipeline` CLI 子命令（V3 多 Agent） | `cli.py:cmd_pipeline` |
| `validate` / `check-assets` / `consistency-check` | `cli.py:cmd_validate` / `cmd_check_assets` / `cmd_consistency_check`（V2.6） |
| `script` CLI 子命令（独立，非 DAG phase） | `cli.py:cmd_script`（V3.0） |
| ImageBackend ABC + Mock / MiniMax 实现 | `image_engine.py` |
| TTSBackend ABC + Mock / Edge 实现 | `tts_engine.py` |
| Ken Burns 视频生成 | `video_engine.py` |
| ffmpeg 拼接 | `concat_engine.py` |
| 色彩归一化 + 叠化 + 调色 | `editing/` + `EditingAgent` |
| Quality Assurance | `quality/` |
| 8 Domain Agents | `agents.py`（`Agent` 基类；`ConcatAgent` 不在 DAG 注册表） |
| PipelineAgent orchestrator + DAG | `orchestrator.py` |
| Artifact + Registry + 原子写 | `artifact.py` |
| RetryPolicy + with_retry | `retry.py` |
| StructuredLogger | `log.py` |
| MotionBackend ABC + MiniMax / Mock | `motion_backend.py` |
| **`每日调度 + 配额保护`** `[V3.2 已完成]` | **`scripts/daily_cron.py`（新建）** |
| **YouTube / Farcaster 发布** `[P3 future]` | **`publish_agent.py`（待新建）** |

---

## 验证端到端

```bash
# 1. 全测试通过
python -m pytest tests/ -q
# 期望：1209 passed, 12 skipped（2026-10-26）

# 2. CLI help 全功能展示
python -m hailuo_video --help
python -m hailuo_video pipeline --help     # 含 --parallel / --dry-run / --resume-from / --motion-budget

# 3. 真实跑（消耗 MiniMax Max quota）
python -m hailuo_video prep storyboard.json --assets-dir ./assets
python -m hailuo_video pipeline storyboard.json -o final.mp4 \
  --image-backend minimax --tts-backend edge --motion-budget 3

# 4. Dry-run（验证 DAG 含 editing phase —— V3.2 后才有）
python -m hailuo_video pipeline storyboard.json -o /tmp/x.mp4 --dry-run

# 5. Registry 验证续跑
ls run/registry.json  # 应存在
python -c "from hailuo_video.artifact import Registry; print(Registry.load_json('./run/registry.json'))"

# 6. 每日调度（V3.2 实装后手动触发一次验证）
python scripts/daily_cron.py --once
```

---

## 变更日志

| 版本 | 日期 | 改了什么 | 谁改的 |
|---|---|---|---|
| V3.0 | 2026-09-28 | **首版顶层 PRD**。从 RMAP V2.5.2 + V2.6 + V3.0 多 Agent 架构抽取。需求 REQ-001 至 REQ-105 标记 P0/P1 已交付；REQ-201 至 REQ-204 标记 P2 future。 | PM-Agent |
| V3.1 | 2026-09-29 | **REQ-201 真 AI 视频生成已交付**。4 步流程（submit → poll → file_retrieve → download）实测跑通。CN 域端点确认为 `api.minimaxi.com`，Key 类型为 `sk-cp`。 | PM-Agent |
| **V3.2 (修正版)** | **2026-09-29** | **修正**：① 加 [UNVERIFIED] 标注所有未来 / 待验事实；② 修正 DAG 描述（`script` 是 CLI 子命令不是 DAG phase；当前 DAG 7 phases）；③ 修正测试数（368 vs 修正版声称 337）；④ 撤回 BUG-001（实测 `_submit` 正确返回 task_id 字符串）；⑥ `sample/mvp_v7_full_matrix.mp4` 标不存在；⑦ V3.2 / P3 全部 REQ 标 future。架构升级新增 REQ-301~305（V3.2）、REQ-401~407（P3）。 | PM-Agent |
| **V3.2 (复核)** | **2026-09-29** | **撤掉 BUG-001**：`motion_backend._submit` 经实测确认返回 task_id 字符串正确；`test_minimax_backend_success_response` 通过。V3.1 4 步流程标签维持有效。 | PM-Agent |
| **V3.2 REQ-301** | **2026-09-29** | **`script_engine._parse_shots_payload` 提取 V3.2 运动指令字段**：`subject_action` / `motion_complexity` / `duration`（兼容旧 `expected_duration`）。`motion_complexity` 严格校验 {static,simple,complex}；`duration` 范围 (0,30] 秒；非法值硬失败不静默。`prompts.STORYBOARD_PROMPT` 同步加 `duration`。新增 5 个测试，全测试 372 passed, 2 skipped。 | PM-Agent |
| **V3.2 REQ-303** | **2026-09-29** | **新建 `editing_engine.py` + `EditingAgent`**：色彩归一化子功能（ffmpeg `eq` filter）+ `_runner` 注入式 mock + 19 个新测试覆盖。Agents 注册从 8 → 9（EditingAgent）。全测试 391 passed, 2 skipped。运动匹配 / xfade / LUT 待后续 REQ。 | PM-Agent |
| **V3.2 REQ-305** | **2026-09-29** | **新建 `scripts/daily_cron.py`**：配额保护（`run/quota.json`）+ 跨日重置 + 日报 `logs/daily_report.md` + 注入式 `_runner` mock + 14 个新测试覆盖。Python 3.14 argparse `store_true + action=append` 回归 bug → 改为单 flag 空格分隔 + 默认单次跑模式。全测试 405 passed, 2 skipped。CLI 实测：`--pipeline-cmd "echo PIPELINE_RAN"` → rc=0, quota 计数 +1, status=ok。 | PM-Agent |
| **V3.2 架构修正** | **2026-09-29** | **PRD ↔ 模型架构.md 同步**：① 新增 §产品定位段（产品一句话 / 目标用户 / 价值主张 / 能力快照 / 关键边界）；② REQ-304 DAG 从 7 phases 扩为 8 phases（新增 `script` + `editing`），**翻转原"`script` 不是 DAG phase"判定**——`ScriptWriterAgent` 输出是后续 phase 输入，必须串行 HALT；③ REQ-302 状态从 "[V3.2 future]" 改 🟡 部分完成（4 步代码就绪，端到端待 quota 实测）；④ REQ-201 状态从"🟡 `_submit` 有 bug"改 ✅ V3.1 已交付（原 BUG-001 已撤回）；⑤ 当前能力快照表列出 8 ✅ + 2 🟡 + 6 ⏳。 | PM-Agent |
| **V3.2 自动开发冲刺（轮 1）** | **2026-09-29** | **§终极目标 实装冲刺**（用户授权 ≥3 轮迭代）：① PRD 新增 §终极目标（novel → YouTube 端到端路径 + 6 条验收标准 + 6 条差距清单 + 关键边界）；② `editing_engine.py` 实装 REQ-303.b/c/d（`crossfade_clips` ffmpeg xfade / `motion_match_cut_points` 帧差分 + `apply_lut` lut3d filter / `write_neutral_cube_lut` / `full_editing_pipeline` 完整链路）+ `full_editing_pipeline` 处理 N<2 跳过 crossfade；③ `EditingAgent` 扩展 `mode` 字段支持 `color_normalize` / `crossfade` / `motion_match` / `apply_lut` / `full` 5 个子模式；④ 新建 `scripts/daily_cron_setup.py`（PS1 + BAT + uninstall 脚本生成器，纯字符串生成可单测），Task Scheduler 集成凭此完成；⑤ 新建 `sandbox/hailuo_video/publish_agent.py`（ABC + Mock + Youtube stub；CLAUDE.md R4 Auth 域硬失败——缺 `YOUTUBE_*` 凭据即 `PublishAuthError`，不静默 fallback 到 Mock）；⑥ `test_editing.py` +28 新测试（51 通过）/ `test_daily_cron_setup.py` +27 / `test_publish_agent.py` +23；⑦ **全测试 421 passed, 2 skipped**（含旧套件向后兼容）。**接口就位待用户凭据**：YouTube OAuth + 真实端到端 Hailuo 4 步验证。 | PM-Agent |
| **V3.2 自动开发冲刺（轮 2）** | **2026-09-30** | **后期完善 + 接口扩面**（用户授权继续迭代）：① `prompts.STORYBOARD_PROMPT` 强化 — 70/30 提示法（70% 可视化 + 30% 氛围） + 三拍结构（起/承/转/合，Setup/Dev/Twist/Resolution 别名） + 16 种 `CAMERA_MOTION_WHITELIST` + subject_action 强制「动作动词 + 对象/结果」格式 + JSON schema 示例 shot；新增 `render_storyboard_prompt(include_hints=False)` 用于简化版本（单测 / 离线）；② `cli.py:cmd_editing` 实装 — `--mode {color_normalize\|crossfade\|motion_match\|apply_lut\|full}` + `--input`（append）+ `--transition`（47 种 xfade transition）+ `--overlap` + `--sample-count` + `--lut` + `--work-dir`；③ 新建 `sandbox/hailuo_video/mcp_client.py`（REQ-303.e AI 编辑 MCP 客户端）— ABC + Mock + `CutStudioMCPBackend` / `SeeCutSkillBackend` stub；**CLAUDE.md R4 Unavailable 域不静默**：stub 阶段 `is_available()=False` 且 `list_tools`/`call_tool` 抛 `NotImplementedError` 明确实装未到位；新增 `recommend_backend()` 自动选可用后端；④ `test_prompts.py` +16 / `test_mcp_client.py` +23 / `test_cmd_editing.py` +24 = **+63 新测试通过**；⑤ **全测试 484 passed, 2 skipped**（较轮 1 +63）。 | PM-Agent |
| **V3.2 自动开发冲刺（轮 3）** | **2026-09-30** | **续跑逻辑 + 质量门禁**（用户授权继续迭代）：① `PipelineAgent.__init__` 新增 `resume_from` + `load_registry` 参数（V3.2 REQ-103 真正续跑）：构造时若 `load_registry=True` 自动从 `run_dir/registry.json` 加载已有 artifacts；`run()` 在 `resume_from` 指定 phase 之前的所有 phase 直接跳过（不重跑 image_gen 等耗 quota 步骤）但 `registry.json` 仍落盘；新增 `_resolve_resume_index()` 解析 phase 名 → DEFAULT_DAG 索引；**CLAUDE.md R4 硬失败**：损坏的 `registry.json` 现在抛错（不再 silent fallback 到空 Registry）—— 之前 `load_json_or_empty` 吞掉 `JSONDecodeError` 是隐患。② `run_pipeline_v3` + `cli.py:cmd_pipeline` 都接通 `resume_from` 参数（`python -m hailuo_video pipeline storyboard.json --resume-from editing`）。③ 新建 `quality.py` 面部相似度模块（V3.2 REQ-402）：`FaceSimilarityBackend` ABC + `HashFaceSimilarityBackend`（PIL aHash + dHash，零依赖轻量代理）+ `NullFaceSimilarityBackend`（永远 1.0，单测用）+ `RealFaceRecognitionBackend`（stub，待 `pip install face_recognition`）；`compute_face_consistency()` 公开 API 默认阈值 0.85 + 注入式 backend。④ `QAAgent` 扩展 `mode` 字段支持 `color` / `face` / `combined`，新增 `face_backend` / `face_threshold` 输入；向后兼容：默认 `mode=color` 与 V2.6 一致。⑤ `test_orchestrator_resume.py` +15 / `test_face_consistency.py` +25 = **+40 新测试通过**；⑥ **全测试 524 passed, 2 skipped**（较轮 2 +40）。**注**：DEFAULT_DAG 当前仍是 7 phases（prep/image_gen/motion_gen/video_synth/tts/concat/qa）—— 与 [模型架构.md](模型架构.md) §V3.2 升级节声称的 8 phases（新增 `script` + `editing`）存在文档 vs 代码差距；待轮 4 把 `concat` 改名为 `editing` 并加 `script` phase。 | PM-Agent |
| **V3.2 自动开发冲刺（轮 4）** | **2026-09-30** | **DEFAULT_DAG 升级到 8 phases（闭合文档 vs 代码差距）**：① `orchestrator.py:DEFAULT_DAG` 从 7 phases 重构为 8 phases —— 在 `prep` 之后、`image_gen` 之前插入 `script` phase（serial + `halt_on_error=True`，依赖 `prep` Master Reference 产出，调用 `ScriptWriterAgent` 生成含运动指令的 Storyboard JSON）；`concat` phase 改名为 `editing`（依赖上游 4 个 parallel phase，调用 `EditingAgent` 做色彩归一化 + xfade + 运动匹配 + LUT）；`qa` phase 改为依赖 `editing`。② `PipelineAgent._agent_lookup` 同步：`concat` 替换为 `editing`（指向 `EditingAgent`）；保留 `ConcatAgent` 作为 fallback 兼容层；新增 `EditingAgent` / `MotionGenerationAgent` 显式 import。③ 测试更新：`test_orchestrator.py` 已有 7 个测试同步到 8 phases（`test_dag_has_eight_phases` / `test_dag_phase_dependencies` / `test_dag_halt_phases` / `test_dag_script_phase_is_serial_halt` / `test_dag_editing_phase_replaces_concat` / `test_dag_script_phase_runs_before_image_gen` / `_mock_all_agents` 改用 `editing` key）；`test_orchestrator_resume.py` 7 处 `resume_from="concat"` 改为 `editing`。④ **全测试 527 passed, 2 skipped**（较轮 3 +3 新测试；现有测试通过 import 路径全链路验证 8 phases 实装）。**差距闭合**：PRD §V3.2 状态行 / 模型架构.md / 升级路线.md 三处文档均声称 8 phases，代码现已对齐。 | PM-Agent |
| **V3.2 自动开发冲刺（轮 5）** | **2026-09-30** | **链式集成 + 公共模块重构**（用户授权继续迭代）：① `Orchestrator._build_task` 实装 shot → per-agent 字段映射（V3.2 链式集成 bug fix）：`image_gen` 任务自动拼接 `prompt` = description + [shot_type] + [运镜] + [动作] + [氛围]（70/30 提示法在 orchestrator 层落地）；`subject_references` = shot.characters；`motion_gen` 任务 `subject_action` / `duration`（从 `expected_duration`）/ `resolution`；`tts` 任务 `text` = shot.dialogue || shot.voiceover（对白优先）+ `write_subtitle=True`；`video_synth` 任务 `seconds` = shot.expected_duration；同时透传 PipelineConfig 的 `voice` / `aspect_ratio` / `parallel` 等配置到 task。**修复原有 bug**：`_build_task` 之前不映射 shot → 各 agent 拿到空 prompt/subject_references/tts.text 等，无法端到端跑通。② `ScriptWriterAgent` 修复 backend 缺失 bug：`_default_execute` 之前漏传 `backend` 参数给 `se_mod.novel_to_storyboard` → 改接受 `task["backend"]` 或 `task["backend_prefer"]`（默认 `auto`），用 `_se_mod.make_backend(...)` 注入。③ 公共模块抽取：`tests/_png_utils.py` 新建（`make_min_png` / `make_noise_png` / `make_pattern_png`），替换 `test_editing.py` / `test_face_consistency.py` 内重复的本地实现——单测 `sys.path` 注入 `tests/` 目录避免 import 失败。④ `test_chain_integration.py` 新建（14 个测试）：覆盖 ScriptWriterAgent 输出结构 / `_build_task` per-agent 映射 / 70/30 拼接 / motion_gen / tts 对白优先 / video_synth / PipelineConfig 透传 / 越界 shot 优雅降级 / 8+1 agent 注册清单 / **完整链路**：ScriptWriterAgent 输出 → Orchestrator `_build_task(image_gen)` 全字段贯通。⑤ **全测试 541 passed, 2 skipped**（较轮 4 +14 新测试）。 | PM-Agent |
| **V3.2 自动开发冲刺（轮 6）** | **2026-09-30** | **视频生成 + 4 个 pipeline bug 修复**（用户实测驱动的硬修复）：① `orchestrator.PipelineResult` 缺 `.ok` 属性（CLI 抛 AttributeError）——加 `@property def ok(self)`。② orchestrator 跑 `script` phase 即使 storyboard.json 已含完整 shots（触发 InputValidationError + halt）——加 `_script_already_loaded()` 检测，已加载则 skip。③ `ImageGenAgent` 把 `r.bytes` 当作 path 传给 registry（`compute_content_hash` 抛 TypeError）——新增 `_save_image_bytes()` 落盘到 intermediate_dir。④ `pipeline.load_storyboard` 只认 `expected_duration`，不认 V3.2 spec 的 `duration`（导致 Hailuo 收到 5s 而报"不支持"）——兼容两种字段名。⑤ `EditingAgent` 没收到 `video_paths` / `output_path` → 编辑 phase halt_on_error 失败——`_build_task` for editing 从 registry 收集 video_paths + 设 output_path。⑥ `VideoSynthAgent` 需要 `image_path` + `output_path`，原 `_build_task` 只注释"由 orchestrator 在调用前补"但从未补上——加 `_get_image_path_for_shot()` + 自动拼 `shot_NNN_kenburns.mp4`（这是 Ken Burns 一直没跑过的根因）。⑦ **用户验证结果**：3 条视频生成到 `sample/`（金毛犬 4MB / 橘猫 1.9MB / 梅花鹿 2.4MB），1080×1080 / 5.88s / 16:9（横屏）。**用户反馈问题**：所有 3 条画面"一眼 AI 生成"缺乏真实感，根因 = style_anchor 未注入 image_gen / motion_gen prompt。 | PM-Agent |
| **V3.2 自动开发冲刺（轮 7）** | **2026-09-30** | **写实风格 + style_anchor 注入修复**（用户反馈"画面缺乏真实感"）：① **核心 bug 修复**：`Orchestrator._build_task` 现在注入 `storyboard.style_anchor` 到 `image_gen.prompt` 最前（`[风格:...]` 标签，作为基线优先）和 `motion_gen.subject_action`（让 Hailuo 也知道视频风格）。② **3 个 storyboard style_anchor 重写为写实摄影参考**：金毛犬 = "documentary wildlife photography, Sony A1 + 200-600mm telephoto, golden hour lighting, Kodak Portra 800 film grain"；橘猫 = "studio pet photography, Fujifilm X-T5 + 56mm f/1.2, soft window light, creamy bokeh"；梅花鹿 = "BBC Planet Earth cinematography, RED V-Raptor + Cooke S7/i, golden god-rays"。③ **3 个 description 也强化**写实细节（金毛犬改为 trot 步态 / 4 足着地；猫头 / 鹿头微湿润等真实摄影细节；明确 no illustration / no CGI aesthetic）。④ 新增 5 个测试覆盖：style_anchor 在 image_gen prompt / motion_gen subject_action / 不泄漏到无关 agent / 无 style_anchor 时不报错。⑤ 新增 `scripts/regen_3_videos.sh` + `.bat`（用户假期后一键重生 3 条视频）。⑥ **全测试 546 passed, 2 skipped**（较轮 6 +5 新测试）。**待用户验证**：假期回来后跑 `bash scripts/regen_3_videos.sh`（消耗 3 Hailuo quota），新视频应有真实摄影质感而非 AI 默认风格。 | PM-Agent |
| **V3.2 自动开发冲刺（轮 8）** | **2026-09-30** | **故事化产品能力 — StoryArc + 6 镜剧情 + 跨天 batch + 自动拼接**（用户授权用 2 天 quota 做 6 条可拼接的故事视频）：① **新场景 `sample/story_arc_orange_cat_4_seasons/`**：6 镜剧情 "橘猫·四季·一日"（环形叙事 — Day 1 起 / Day 2 合），主角橘猫全程一致，shot_001 晨光苏醒 ↔ shot_006 黎明新生 首尾呼应；shot_003/004/005 走 Ken Burns（不耗 quota），shot_001/002/006 走 Hailuo-2.3。② **`Shot` dataclass 增 2 字段**：`shot_id`（trace ID）+ `continuity_from_prev`（与前一镜的衔接）。③ **`script_engine.StoryArc` 新类**（V3.2 产品化核心）：`from_json()` / `to_storyboards()` 拆 N 个 Storyboard，跨 shot 锁 character + style_anchor；`pipeline.load_storyboard` 同步读 shot_id + continuity_from_prev。④ **3 个新脚本**：`scripts/build_storyboards.py`（arc → N 个独立 storyboard JSON）/ `scripts/run_story.sh + .bat`（批量跑 storyboard，每个消耗 1 quota，自动从 intermediate/ 救回产物）/ `scripts/stitch_story.py`（crossfade 拼接 N mp4 为完整故事）。⑤ **13 个 story_arc 测试**：覆盖 StoryArc.from_json / to_storyboards / 角色一致性 / style_anchor 继承 / continuity 字段 / build_storyboards 端到端 / 真实 day1.json / day2.json 解析。⑥ **全测试 559 passed, 2 skipped**（较轮 7 +13 新测试）。⑦ **今日 Day 1 batch 部分成功**：shot_001 / 002 因 quota 已耗尽失败（2067 错误）；shot_003（static / Ken Burns）✅ 168KB 已落盘 sample/story_arc_orange_cat_4_seasons_shot_003.mp4。明日 quota 重置后用 `bash scripts/run_story.sh day1_storyboards` + `bash scripts/run_story.sh day2_storyboards` 重跑 + 用 `python scripts/stitch_story.py` 拼接为完整故事。 | PM-Agent |
| **V3.2 自动开发冲刺（轮 9）** | **2026-09-30** | **故事脚本与提示词全貌诊断工具**：用户要求看 `sample/story_orange_cat_4_seasons_final.mp4` 的脚本 + 提示词。① **诊断 + 抽取**：`scripts/explain_story.py` —— 一键 dump 每个 shot 的实际喂给 MiniMax image-01 的最终 prompt（含 `[风格:...]` 基线 + `[运镜:]` + `[动作:]` + `[氛围:]` 标签拼装）。② **164 行 PROMPTS.txt** 落盘 `sample/story_arc_orange_cat_4_seasons/PROMPTS.txt` —— 6 镜完整 final prompt 全记录（用户可对照最终视频看"输入什么 + 怎么组装"）。③ **README.md** 落盘 —— 含 6 镜环形叙事图 + 提示词拼装模板 + Ken Burns 镜头效果与叙事意图映射 + 给作者的设计备注（`style_anchor` 必须写真实摄影参考 / `appearance` 要含识别锚点 / `continuity_from_prev` 短文本就够 / 6 镜拼接时长 = 6 × Ns）。 | PM-Agent |
| **V3.2 自动开发冲刺（轮 10）** | **2026-09-30** | **电影质感升级 T1（无 quota）**：用户反馈最终视频"动态 PPT + 镜头机械感 + 一眼 AI"。三档方案（无 quota / 需 quota / 需专业后期）。T1 实装：`scripts/cinematic_upgrade.py` —— 每镜重渲染加 ① `eq` 色调 LUT（Day 1 暖 / Day 2 冷）② `tblend` 帧间柔化 8% ③ `noise` 胶片颗粒 ④ `yuv420p` + `bt709` 专业色彩空间 ⑤ **抛物线 ease-in-out Ken Burns**（替代机械线性）⑥ 程序化 `anoisesrc` 环境音（pink/brown noise + 滤波模拟晨鸟/虫鸣/梦境呼吸，每镜独立配方）。修复 `stitch_story.py` 加 `_any_has_audio()` 检测 + `concat filter` fallback（crossfade 在 tblend + noise 输入下崩 → 自动降级保留音频）。产物：[sample/story_arc_orange_cat_4_seasons_CINEMA_FINAL.mp4](sample/story_arc_orange_cat_4_seasons_CINEMA_FINAL.mp4) 36s / 1280×720 / 带音频 / 7.9 MB。**视觉提升**：镜头不再"死板推拉"，手持呼吸感 + 胶片颗粒 + 真环境音；**仍未做到**：角色真动作（仍是静态图 + 缓动 + 微动 = "高级 PPT"）。 | PM-Agent |
| **V3.2 自动开发冲刺（轮 11）** | **2026-09-30** | **L1 合成微动（ffmpeg trick，0 quota）**：`cinematic_upgrade.py` vf chain 加 `rotate='0.03*sin(2*PI*t/3-1)'`（手持镜头呼吸，每 3 秒微微摆 0.03°）+ `tblend=all_mode=average:all_opacity=0.05`（颜色 LUT 后置避免颜色被柔化）+ 保留胶片颗粒。**L2 定格动画设计**：用户问"中远近里面能不能也加细微动作调节的 3~5 张图片"，确认实施 L2 = 每镜 5 张不同动作阶段图 + ffmpeg xfade 拼接（定格动画 / stop motion 原理）。**产物**：[sample/story_arc_orange_cat_4_seasons_L1_FINAL.mp4](sample/story_arc_orange_cat_4_seasons_L1_FINAL.mp4) 36s / 带音频 / 7.9 MB —— 镜头有"活感"但角色仍静态；为 L2 多帧定格动画铺路。 | PM-Agent |
| **V3.2 自动开发冲刺（轮 12）** | **2026-09-30** | **L2 多帧定格动画实装（0 Hailuo quota + 30 image-01 调用）**：① **`scripts/multi_frame_shot.py`**：每镜生成 5 张"动作阶段"图（image-01），用 ffmpeg xfade 拼成 6 秒定格动画（每帧 1.2s + 0.3s crossfade）。② **6 镜 × 5 帧动作描述**（脚本内置硬编码）：shot_001（蜷缩入睡→睁眼→前爪伸→弓背打哈欠→转头望窗）/ shot_002（走入→左爪迈→右爪迈→接近花→鼻子贴花）/ shot_003（蹲伏→直身→转头→转回→尾巴尖摆）/ shot_004（走到坐垫→转圈→趴下→爪盖鼻→闭眼）/ shot_005（睁眼→半闭→闭合→全闭→呼吸起伏）/ shot_006（睡姿→微睁眼→打哈欠→坐起→看见小鸟）。③ **修复 xfade chain bug**：第二个 xfade 误用 `v_i` 作输入而不是前一个 xfade 的输出 `xf_i`；修复为 `prev_label = "v0" if i == 0 else f"xf{i}"`。④ **`scripts/finalize_l2_story.py`**：给 6 个 shot 加 ambient audio + 胶片颗粒，6 镜 concat 拼成完整 36s mp4。⑤ **产物**：[sample/story_arc_orange_cat_4_seasons_L2_FINAL.mp4](sample/story_arc_orange_cat_4_seasons_L2_FINAL.mp4) 36s / 1280×720 / 带音频 / 7.2 MB —— **角色真动作阶段 + 手持呼吸 + 胶片颗粒**；与 L1 相比，"猫真的在醒来 + 镜头手持呼吸 + 整体电影质感"。 | PM-Agent |
| **V3.2 自动开发冲刺（轮 13）** | **2026-09-30** | **完整归档**（用户授权"做 PRD changelog + 更新所有文件 + 存档"）：① PRD §变更日志记录轮 11/12 L1+L2 entry；② PRD §能力快照 + §终极目标反映 L2 已达"可发小红书"水平；③ 更新 `sample/story_arc_orange_cat_4_seasons/README.md` 含三档产物对照（T1 cinema / L1 微动 / L2 定格）；④ 重生成 `PROMPTS.txt` 包含 L2 5-frame prompts；⑤ 创建 `scripts/README.md` 索引 7 个产品脚本；⑥ 全测试 527 passed 通过（无回归）。**总归档产物 8 件**：[sample/story_orange_cat_4_seasons_final.mp4](sample/story_orange_cat_4_seasons_final.mp4)（原版 36s / 1.26 MB / 硬切）/ [story_arc_orange_cat_4_seasons_CINEMA_FINAL.mp4](sample/story_arc_orange_cat_4_seasons_CINEMA_FINAL.mp4)（T1 cinema / 36s / 7.9 MB）/ [story_arc_orange_cat_4_seasons_L1_FINAL.mp4](sample/story_arc_orange_cat_4_seasons_L1_FINAL.mp4)（L1 微动 / 36s / 7.9 MB）/ [story_arc_orange_cat_4_seasons_L2_FINAL.mp4](sample/story_orange_cat_4_seasons_L2_FINAL.mp4)（L2 定格动画 / 36s / 7.2 MB / **含真实角色动作阶段**）/ storyboard JSON × 2 / PROMPTS.txt / README.md。 | PM-Agent |

| **V3.8 文献驱动的全量优化（6 条工作流 + P0 止血批）** | **2026-10-26** | **用 arXiv 检索（16 组查询）把论文方法逐条映射到本项目已定位到 file:line 的缺陷**，形成 6 条工作流 + 1 个 P0 批，7 批分批落地，每批独立过 `scripts/auto_dev.py` 门禁。**P0 止血批（4 个静默失败）**：① 9:16 在 V3 生产路径不可达（4 处硬编码叠加遮蔽 env）→ 画幅解析改为「显式值 > backend env > 16:9」+ CLI `--aspect-ratio`；② `motion_complexity=static` 静默降级仍报 `[OK] (0 bytes)` → 记 event + 进 `PipelineResult.consistency` + CLI `[WARN]`；③ 多镜 storyboard 全部镜头共用最后一张图（artifact_id 未按 shot 隔离，`Registry.add` 按 id 覆盖）→ id 与落盘文件名都按 shot 隔离；④ registry 落盘 `except OSError: pass` 静默失败 → 记 event + 进 consistency（**不 raise**）。**W1 全局角色圣经**（`character_bible.py`）：角色参考图按「视觉身份」（appearance+palette+style_anchor 哈希，**不含名字**）跨项目复用 —— 修角色跨天漂移 + reference URL 过期静默失效 + 中文色词歧义（"小黄"→橘猫）纯代码告警。**W2 导演解耦**（`director.py`）：12 个 director 字段拆成 static_visual（→image prompt）+ dynamic_narrative（→motion prompt），**camera_motion 此前连视频模型都没进过**；顺带修 prompt 占位符字面泄漏（`render_storyboard_prompt` 从未被调用）与 `prompt_70_30` 复制粘贴缺陷。**W3 跨镜连续性门禁**（`continuity.py`）+ 叠化前 `fps=24` 归一化：实测跨镜色差 0.700 vs 理想 0.95，旧门禁 0.65 形同虚设 → 新阈值 0.90 + 5 维可解释报告。**W4 知识库 + 负提示词**：`knowledge/motion_params.json`（REQ-406，8 条种子来自实测复盘+ relation graph，纯 stdlib 检索不引入向量库）+ `negative_prompt.py`（能力声明式，不发未确认字段以免 4xx 且已计费）。**W5 竖屏**：`crop.py` 主体感知裁剪（纯 PIL 显著性，零新依赖）+ `to_vertical` 补 `crop_frac`。**W6 VLM 评审接口**（`vlm_review.py`）：评审器 ABC + 5 维 + 可解释报告 + CLI `review` 子命令，**默认 mock 零网络调用**，真 API 需 `--enable-real` + `GEMINI_API_KEY` + `GEMINI_VLM_MODEL` 三者齐备（缺一显式报错，不静默退回 mock）。**新增测试 197 个**（8 个新文件），**全测试 1209 passed, 12 skipped**（较 986 +223）；coverage 82.0%。**方法论教训**：「实现已存在但从未接线」是一类独立缺陷（`prompts_director.py` 的 parse/apply 与 `render_storyboard_prompt` 都有单测但生产零调用）。对应 **D-021 ~ D-028** + **L-011 ~ L-013**。⏳ **真实 API 效果待用户手工烟测**（10 项清单见 `knowledge/decisions.md` D-028；不代打计费端点）。 | PM-Agent |
| **V3.5 功能测试修复（2026-10-22）** | **2026-10-22** | **对 10-20~10-22 开发内容做功能测试后修 3 个缺陷（D1/D2/D3）**：① **D1 `script --backend mock` 结构性不可用** —— CLI 宣传该模式但 `MockBackend` 无预设响应即抛 `AssertionError`（生产代码 `.on()` 调用数为 0），且报错文案还在推荐这条走不通的路。新增 `CliMockBackend`（内置 canned 三角色 JSON）供 CLI 用，`MockBackend` 一行不动（「未匹配即抛」是被单测依赖的设计，加默认回退会让忘注册响应的测试静默变绿，违反 §2 证据阶梯）；镜头数从 prompt 正则读回（`_parse_shots_payload` 要求精确相等）。替换 1 项把 bug 写成契约的旧测试。② **D2 README 命令从仓库根跑不了** —— 新增 `pyproject.toml`（`package-dir={sandbox}`）走可编辑安装，README 快速上手第 1 步改为 `pip install -e .`；顺带补上项目首个依赖声明（pillow/imageio-ffmpeg/edge-tts，`torch`/`diffusers` 归可选 extra；**更正：`requests` 不是依赖**）。③ **D3 `auto_dev.py` 在 Windows cp932 控制台崩溃** —— emoji 触发 `UnicodeEncodeError`，且兜底异常处理器自己也抛，使门禁交出**不可信的退出码**；入口重配 stdout/stderr 为 utf-8 + `errors=replace`，兑现 **L-009**（教训早已记录，脚本一直没落实）。④ 提案与实施记录存 `docs/drafts/`（含对提案自身过强说法的更正）。**全测试 986 passed, 12 skipped**（较 968 +18）；`auto_dev.py` 在 cp932 且不设任何环境变量下 4 步全 PASS、EXIT=0。对应 **D-018 / D-019 / D-020**。 | PM-Agent |
| **V3.5 Gemini Nano Banana 图像后端（2026-10-22）** | **2026-10-22** | **增接 Google `gemini-2.5-flash-image`（Nano Banana），与 MiniMax `image-01` 平级**（`--image-backend gemini`，`image`/`pipeline`/`prep` 三处可选）。难点不在调 API 而在 **reference 契约**：MiniMax 收 http(s) URL、Gemini `inlineData` 收裸字节，且 Gemini **无 url 响应模式**（永远 inline base64）、**无 `n` 参数**（一次一张，n>1 顺序循环）。原实现下 `_read_cached_url` 非 http 一律返回 `""`、`_reference_urls` 硬过滤非 http → 本地 ref 被**静默丢弃**，角色一致性无声消失（违反 R4）。解法：把 reference 契约扩为「URL 或本地路径」，由 `ImageBackend.supports_local_refs` 声明各自能否消费 —— **不引入任何新组件**。`asset_prep` 加 `fetch_bytes` 替掉 4 处手写 `urlopen`；`.url.txt` **继续写空串**（`has_cached_*` 要求 `.jpg`+`.url.txt` 同时存在才算命中，不写会导致每次 prep 重复计费 ≈ $0.039/张）。fail-closed：MiniMax 收到本地 ref 抛明确错误（存量缓存全 http，零影响）、`finishReason` 非 OK 一律抛错不返回 0 张、`thought:true` 草稿图跳过。响应是 **camelCase `inlineData`**（`inline_data` 是 Python SDK 写法，混用会静默取不到图）；`imageConfig.imageSize` 本模型会拒收。**全测试 986 passed**。⏳ **真实 API 烟测待用户执行**（约 $0.039/张，不代打计费端点）。对应 **D-017**。 | PM-Agent |
---

## V3.2 自动开发冲刺（轮 14）—— T2 真电影版实装（2026-10-08）

**触发**：用户授权"跑 T2 真电影版"（6 个 Hailuo quota = 1 天 quota 全用完 + 隔天再 3 个）。

**流程**：
1. 探测 Hailuo quota → `task_id=450074561343845` 成功（已重置）
2. 修改 day1.json + day2.json：6 个 shot 的 `motion_complexity` 从 `"static"` → `"simple"`（Hailuo 路径）
3. 修 `scripts/run_story.sh`：`--motion-budget` 默认 1 → 3（让一天内 3 个 shot 全走 Hailuo）
4. 跑 Day 1（shot_001/002/003）→ 3 个 Hailuo 真视频，文件大小 224KB / 286KB / 166KB
5. 同一天 quota 仍可用 → 直接跑 Day 2（shot_004/005/006）→ 3 个 Hailuo 真视频
6. `cinematic_upgrade.py --suffix _t2cinema`：给 6 个 Hailuo mp4 加 ambient audio + 色调 LUT + 胶片颗粒
7. `stitch_story.py`：拼 6 个为完整 36s mp4

**产物**：[sample/story_orange_cat_4_seasons_T2_FINAL.mp4](sample/story_orange_cat_4_seasons_T2_FINAL.mp4) — **36 秒 / 1920×1080 / 带音频 / 7.75 MB / 真猫动作 + 色调 LUT + 胶片颗粒**

**5 版本对比（最终产物地图）**：

| 版本 | 文件 | 大小 | 角色动作 | 视觉定位 |
| :--- | :--- | :--- | :--- | :--- |
| V0 原版 | [story_orange_cat_4_seasons_final.mp4](sample/story_orange_cat_4_seasons_final.mp4) | 1.26 MB | 0% | "动态 PPT" |
| T1 cinema | [story_arc_orange_cat_4_seasons_CINEMA_FINAL.mp4](sample/story_arc_orange_cat_4_seasons_CINEMA_FINAL.mp4) | 7.68 MB | 0% | "高级 PPT" |
| L1 微动 | [story_arc_orange_cat_4_seasons_L1_FINAL.mp4](sample/story_arc_orange_cat_4_seasons_L1_FINAL.mp4) | 7.92 MB | 0% | "高级 PPT + 手持" |
| L2 定格 | [story_arc_orange_cat_4_seasons_L2_FINAL.mp4](sample/story_arc_orange_cat_4_seasons_L2_FINAL.mp4) | 7.21 MB | 100%（5 张动作阶段图）| "定格动画" |
| **⭐ T2 真电影** | [story_orange_cat_4_seasons_T2_FINAL.mp4](sample/story_orange_cat_4_seasons_T2_FINAL.mp4) | **7.75 MB** | **100%（Hailuo 真视频）** | **"真电影"** |

**总测试**：559 passed, 2 skipped（无回归）

---

## 待澄清问题（开放给 PM-Agent）

> ✅ = 已解决（保留作历史）；⬜ = 未解决

- [✅] **REQ-303 EditingAgent 实现优先级**（2026-09-29 解决）：色彩归一化 ✅ 先做；运动匹配 / 叠化 / LUT 后续 REQ-303.x
- [✅] **BUG-001 复核**（2026-09-29 撤回）：`motion_backend._submit` 实测返回 task_id 字符串正确（`motion_backend.py:287-297`），`test_minimax_backend_success_response` 通过。BUG-001 标记作废
- [✅] **`script` 是否 DAG phase**（2026-09-29 翻转）：是——`ScriptWriterAgent` 输出 Storyboard 是 `image_gen` / `motion_gen` 输入，串行 HALT。详见 REQ-304
- [✅] **REQ-103 --resume 完整续跑**（2026-10-10）：`PipelineAgent.run` 按 `resume_from` 跳过此前 phase，并从 `registry.json` 加载已有产物
- [⬜] **REQ-201 GPU 路径**：MiniMax Max/Ultra 套餐 vs 自部署 CogVideoX 哪个先做？需用户决策（当前选 Max）
- [⬜] **REQ-305 调度平台**：Windows Task Scheduler vs Linux Cron vs GitHub Actions？当前开发机是 Windows，建议先用 Task Scheduler（脚本已就绪，待用户手动配）
- [✅] **REQ-303.b/c/d**（2026-10-10）：运动匹配、xfade、LUT 已在 `editing/`
- [⬜] **REQ-401 发布优先级**：YouTube 先还是 Farcaster 先？建议先 YouTube（用户基数大，反馈快）
- [⬜] **REQ-406 知识库存储**：JSON vs SQLite vs 向量数据库？建议先用 JSON（简单，可读）
- [⬜] **REQ-405 实时预览**：web UI 还是 CLI tail？需 UX 设计

---

**与本 PRD 同源的文件**：
- `模型架构.md`（Product 多 Agent 架构文档）—— V3.0 实施细节
- `FRAMEWORK.md`（RMAP methodology）—— V2.5 picker 决策
- `sandbox/hailuo_video/prd.md`（V2.x 协议层变更详表）—— 实现细节
- `storyboard_template.json` —— 标准模板
- `sandbox/hailuo_video/` —— 源代码
- `tests/` —— **1209 passed, 12 skipped**（2026-10-26）

---

## V3.2 落地工程任务清单（与 PRD 同步推进）

按用户 2026-09-29 决策，按以下顺序推进：

1. ~~**BUG-001 修复**（前置依赖）~~ — **已撤回**（2026-09-29 实测 `_submit` 正确）
2. **REQ-301** — `script_engine.py` 增加 `camera_motion` / `subject_action` / `motion_complexity` / `duration` 字段。 — ✅ **2026-09-29 完成**
3. **REQ-303** — `editing/` + `EditingAgent`。 — ✅ 色彩归一化、运动匹配、xfade、LUT 已完成；MCP 真接入仍是 stub
4. **REQ-305** — 新建 `scripts/daily_cron.py` + Windows Task Scheduler 集成。 — ✅ **2026-09-29 完成脚本部分**（Task Scheduler 集成待用户手动配）

完成每项后必须：`pytest tests/ -q` 全绿；更新本 PRD 对应 REQ 的"验收状态"。