# PRD — RMAP AI 漫剧产品

> **唯一真相源**。PM-Agent 维护。冲突时以本文件为准。
> 与 `sandbox/hailuo_video/prd.md` 的关系：本文是**顶层产品需求**（PM 视角），sandbox/prd.md 是**协议层变更详表**（V2.x 各版本的实现细节）。
>
> **V3.2 状态**：修正版（2026-09-29）。REQ-301~305、REQ-401~407 标为 `[V3.2 future]` / `[P3 future]`，均未实装。
> **校验日期**：2026-09-29；测试收集 368（目标 400+，待 [UNVERIFIED]）。

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
- **实现位置**：`sandbox/hailuo_video/quality.py`
- **验收状态**：✅ 已交付 V2.6（`test_quality.py` 覆盖）

---

### P1 已交付（V3.0 多 Agent）

#### REQ-101 · 7 个 domain agents + orchestrator
- **用户故事**：作为开发者，我希望每阶段（脚本/物料/图像/视频/TTS/拼接/QA）可独立测试、可独立替换
- **验收**：
  - 机器：`pytest tests/ -q` — `[UNVERIFIED]` 当前 collect 368 tests；正式报告需跑 `--no-header`
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
  - 机器：`tests/test_motion.py` — `[UNVERIFIED]` 用例数（collect-only 看不到分组）
  - 人工：`python scripts/test_hailuo_real.py` 端到端跑通
  - 真人：实测已跑通 3.28MB / 5.88s / 1080P H.264 mp4 — `[UNVERIFIED] task_id=446861242024333`（需用户提供截图或日志复核）
- **优先级**：P2 → V3.1 已交付（用户在 MiniMax Max 套餐，每天 3 次 hailuo-2.3）
- **依赖**：MiniMax Max 套餐（每天 3 次 hailuo-2.3）
- **实现位置**：`motion_backend.py` + `agents.py:MotionGenerationAgent`
- **验收状态**：🟡 **4 步流程代码已就绪但 `_submit` 有 bug**（见下方"已知问题"）

---

### P2 V3.2 即将实施（运动指令层 + EditingAgent + 调度）

> 以下 REQ-301 ~ REQ-305 **全部为 `[V3.2 future]`，文件尚未创建**。

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
  - 机器：`tests/test_motion.py` 覆盖 poll 超时（600s）/ file_retrieve 解析 / download 落盘
  - 人工：`--motion-budget 3` 跑通，输出 3 个 `intermediate/shot_NNN_motion.mp4`
- **优先级**：P2（V3.2）
- **实现位置**：`motion_backend.py:_default_execute`（已就绪，待实测校准）
- **技术要点**：
  - timeout 600s，interval 10s
  - `file_id` → `GET /v1/files/retrieve?file_id=...` → `download_url`
  - 失败降级 → Ken Burns + `[WARN]`，pipeline 不 halt

#### REQ-303 · EditingAgent（新增）
- **用户故事**：作为创作者，我希望 pipeline 自动完成色彩归一化、运动匹配剪切、重叠帧叠化，避免多镜头拼接出现色温跳变和动作断裂
- **验收**：
  - 机器：`tests/test_editing.py` 覆盖 19 cases：compute_target_stats / eq_filter / normalize_clip / pipeline / EditingAgent 集成 —— ✅ **2026-09-29 完成**（色彩归一化子功能）
  - 人工：跑通 3 镜头 Pipeline，输出 `final.mp4` 无明显色温跳变（**待真 ffmpeg 端到端验证**）
- **优先级**：P2（V3.2）
- **实现位置**：`editing_engine.py`（新建）+ `agents.py:EditingAgent`
- **验收状态**：🟢 **2026-09-29 完成色彩归一化子功能**（19 测试通过；全测试 391 passed, 2 skipped）
- **子功能完成度**：
  | 步骤 | 状态 | 说明 |
  | :--- | :--- | :--- |
  | 色彩归一化 | ✅ 完成 | ffmpeg `eq` filter (brightness / saturation / gamma)，从中间帧采样算均值 |
  | 运动匹配剪切 | ⏳ 未做 | 帧差分分析（待 REQ-303.x） |
  | 重叠帧叠化 | ⏳ 未做 | ffmpeg `xfade` filter（待 REQ-303.x） |
  | 统一调色 | ⏳ 未做 | ffmpeg `lut3d` filter（待 REQ-303.x） |
  | AI剪辑集成 | ⏳ 未做 | CutStudio / SeeCut MCP 端点（P3） |
- **技术要点**：
  - `editing_engine.color_normalize_pipeline(video_paths, output_dir)`：analyze → compute_target（多段均值） → normalize_clip 串行
  - `set_runner()` 注入 mock，单测不依赖真 ffmpeg
  - 失败 → EditingError，硬失败不静默（CLAUDE.md R4）

#### REQ-304 · Orchestrator DAG 增加 editing phase `[V3.2 future]`
- **用户故事**：作为开发者，我希望 DAG 在 concat 之后、qa 之前增加一个 editing phase，串行执行
- **验收**：
  - 机器：`tests/test_orchestrator.py` 覆盖 DAG 含 8 phases（原 7 + editing）
  - 人工：`pipeline --dry-run` 输出 DAG 中可见 `editing` phase
- **优先级**：P2（V3.2）
- **实现位置**：`orchestrator.py:DEFAULT_DAG` — 当前 7 phases（prep/image_gen/motion_gen/video_synth/tts/concat/qa），无 editing
- **未来 DAG 更新**：
  | Phase | Agents | Parallel | Halt-on-error |
  | :--- | :--- | :--- | :--- |
  | prep | asset_prep | ❌ | ✅ |
  | image_gen | image_gen | ✅ | ❌ |
  | motion_gen | motion_gen（条件） | ✅ | ❌ |
  | video_synth | video_synth | ✅ | ❌ |
  | tts | tts | ✅ | ❌ |
  | concat | concat | ❌ | ✅ |
  | **editing** | **editing** | **❌** | **✅** |
  | qa | qa | ❌ | ❌ |
- **注**：原修正版 PRD 把 `script` 列为 DAG phase — **`script` 不是 DAG phase**（`cli.py:cmd_script` 是独立 CLI 子命令；`orchestrator.py:DEFAULT_DAG` 无 script 条目）

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
  - 机器：`quality.py:check_face_consistency()` 返回相似度分数
  - 人工：故意生成角色漂移的视频，QA 自动标记为不合格
- **优先级**：P3
- **依赖**：face_recognition 或 insightface 库
- **状态**：未做

#### REQ-403 · 角色语音一致性 `[P3 future]`
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

| ID | 严重度 | 描述 | 计划修复 |
|---|---|---|---|
| （空） | — | 2026-09-29 复核：原 PRD 中标记的 "BUG-001 `_submit` 返回 dict 而非 task_id 字符串" **经实测确认不存在**——`_submit` 在 `motion_backend.py:287-297` 正确解析 payload 并返回 task_id 字符串；`tests/test_motion.py::test_minimax_backend_success_response` 通过验证（28 passed, 2 skipped）。原 BUG-001 标记作废。 | — |

---

## PRD 变更日志（修订追踪）

- **2026-09-29 V3.2 修正版复核**：
  - **撤掉 BUG-001**：`_submit` 实测返回 task_id 字符串（motion_backend.py:287-297），测试 `test_minimax_backend_success_response` 通过；初始判断基于误读源码，已修正。
  - 实际未触发的代码问题：**无**。V3.1 "4 步流程实测正确" 标签有效。

---

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
| **色彩归一化 + 叠化 + 调色** `[V3.2 future]` | **`editing_engine.py`（待新建）** |
| `Quality Assurance` | `quality.py` |
| 7 Domain Agents + MotionGenerationAgent | `agents.py` |
| **`editing` Agent + 色彩归一化引擎** `[V3.2 已完成]` | **`editing_engine.py`（新建）+ `agents.py:EditingAgent`** |
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
# 期望：405 passed, 2 skipped（V3.2 后；已达成 400+ 目标）

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

---

## 待澄清问题（开放给 PM-Agent）

- [ ] **REQ-103 --resume 完整续跑**：当前只接受 flag，Orchestrator 实际跳过 phase 的逻辑待补。下次跑 `pipeline --resume-from concat` 验证。
- [ ] **REQ-201 GPU 路径**：MiniMax Max/Ultra 套餐 vs 自部署 CogVideoX 哪个先做？需用户决策。
- [ ] **REQ-303 EditingAgent 实现优先级**：色彩归一化 / 运动匹配剪切 / 重叠帧叠化 三选一先做哪个？建议先做色彩归一化（ROI 最高）。
- [ ] **REQ-305 调度平台**：Windows Task Scheduler vs Linux Cron vs GitHub Actions？当前开发机是 Windows，建议先用 Task Scheduler。
- [ ] **REQ-401 发布优先级**：YouTube 先还是 Farcaster 先？建议先 YouTube（用户基数大，反馈快）。
- [ ] **REQ-406 知识库存储**：JSON vs SQLite vs 向量数据库？建议先用 JSON（简单，可读）。
- [ ] **REQ-204 实时预览**：web UI 还是 CLI tail？需 UX 设计。
- [ ] **BUG-001 复核（已撤）**：原 PRD 标记的 `_submit` 返回 dict 而非 task_id 字符串 — 2026-09-29 实测确认函数正确返回字符串（`motion_backend.py:287-297`），测试 `test_minimax_backend_success_response` 通过。原 BUG-001 标记作废。

---

**与本 PRD 同源的文件**：
- `模型架构.md`（Product 多 Agent 架构文档）—— V3.0 实施细节
- `FRAMEWORK.md`（RMAP methodology）—— V2.5 picker 决策
- `sandbox/hailuo_video/prd.md`（V2.x 协议层变更详表）—— 实现细节
- `storyboard_template.json` —— 标准模板
- `sandbox/hailuo_video/` —— 源代码
- `tests/` —— **405 测试覆盖**（V3.2 后；目标 400+，已达成）

---

## V3.2 落地工程任务清单（与 PRD 同步推进）

按用户 2026-09-29 决策，按以下顺序推进：

1. ~~**BUG-001 修复**（前置依赖）~~ — **已撤回**（2026-09-29 实测 `_submit` 正确）
2. **REQ-301** — `script_engine.py` 增加 `camera_motion` / `subject_action` / `motion_complexity` / `duration` 字段。 — ✅ **2026-09-29 完成**
3. **REQ-303** — 新建 `editing_engine.py` + `EditingAgent`；先做色彩归一化子功能。 — ✅ **2026-09-29 完成**（仅色彩归一化；运动匹配 / xfade / LUT 待后续 REQ）
4. **REQ-305** — 新建 `scripts/daily_cron.py` + Windows Task Scheduler 集成。 — ✅ **2026-09-29 完成脚本部分**（Task Scheduler 集成待用户手动配）

完成每项后必须：`pytest tests/ -q` 全绿；更新本 PRD 对应 REQ 的"验收状态"。