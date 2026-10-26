# Knowledge — 海螺视频 (Hailuo) 项目

> 本目录知识条目仅针对 `sandbox/hailuo_video/` 项目相关决策与教训。
> 完整的 ai-os 知识库在 `ai_agents_dev_tool/knowledge/`，本目录为本地精简版。

## D-009 沙盒旁路项目：sandbox/<name>/ + 自带 prd.md + 不入主队列
- 决策日期：2026-09-16
- 提议人：王总（下令"再开一个好玩的视频项目"）
- 状态：**ACCEPTED**
- 上下文：D-007 已定"同时只激活一个项目 + 新客户走旁路"。落地 V0.1 时再问一层：旁路到底长什么样？放 src/ 会污染客户包（D-007 第 3 条），放 scripts/ 会被审计成内核改动。
- 选项：
  - A. 放 `src/<新客户>/` —— 污染 D-007 边界
  - B. 放 `scripts/<新工具>/` —— 与 dev_evidence.py / gate_check.py 等工厂内核平级，混淆
  - C. 放 `sandbox/<name>/`，自带 1 页 prd.md + runtime/ 子目录（不写 agentorg.runtime），不入主 TASK_QUEUE.json
- 决策：**C**
- 理由：sandbox/ 在仓根，与 src/agentorg/、src/hr_analytics/、dash-agent-plus/ 三个已知区域全部隔离；自带 prd.md 满足宪法"PRD 引用强制令"；runtime 子目录独立写日志，不污染 runtime/ 全局
- 后续动作：
  1. `sandbox/hailuo_video/` 已落地（PRD-HVL V0.1，async 客户端 + CLI + 25 单测全绿）
  2. 门禁：pytest 全绿 + dev_evidence.py record 留痕；豁免完整 PRD 门 / 队列 lint / HITL / audit（"轻量门禁"档位）
  3. 端点未核实（HAILUO_VIDEO_SUBMIT_URL / POLL_URL）必须 source=misconfigured 硬失败，不得 LLM_DRAFT 假装成功

## D-010 接口未公开核实 = 必走 misconfigured 硬失败
- 决策日期：2026-09-16
- 提议人：王总（执行海螺 CLI 时）
- 状态：**ACCEPTED**
- 上下文：海螺视频 API 端点、字段、状态枚举在 WebFetch 被企业网关挡、WebSearch 也无公开页面，靠"猜 base URL"违反宪法"禁止编造 API 字段"
- 选项：
  - A. 猜端点 + 占位 URL，跑通就声称 done —— 违反 [Operating Rule 1] FLOOR
  - B. 把端点做成环境变量必填项；缺则 source=misconfigured 硬失败；CLI ping 报告 TODO 清单
- 决策：**B**
- 理由：把"未知 API"翻译为"配置缺项"，调用方必须主动补 .env 才能跑通；pytest 用 mock 验证 I/O 契约，不依赖真实 endpoint；calls.jsonl 落盘但绝不写 Key
- 后续动作：
  1. `HAILUO_API_KEY` / `HAILUO_VIDEO_SUBMIT_URL` / `HAILUO_VIDEO_POLL_URL` 三件齐 = is_configured()
  2. 任一缺 → source=misconfigured + stderr 含 "⬅️ TODO 你从 tokenplan 控制台查"
  3. 单测 `test_*_misconfigured_*` 覆盖三类缺项

## D-011 MiniMax Plus token plan 无视频 API 权限 → 切本地 diffusers 推理
- 决策日期：2026-09-18
- 提议人：王总（实测 MiniMax Plus 套餐无 Hailuo 视频 API 额度后）
- 状态：**ACCEPTED**
- 上下文：D-010 立的"环境变量必填 + 缺则 misconfigured 硬失败"在 MiniMax Plus 场景下永远硬失败——Plus 套餐根本不开放视频 API 端点。原先调研的 Agnes AI / InferencePort (free-multimodal-proxy) 因 [UNVERIFIED] 端点 + deep-research 子代理 structured output 故障被否决（D-010 / L-008 不能猜 base URL）。
- 选项：
  - A. 升级 MiniMax 套餐到 Max/Ultra ($50/$120 每月) —— 引入持续支出，PRD §7 非目标里"按量/订阅免费"的原意相违
  - B. 接未核实第三方免费 API（Agnes / InferencePort）—— 违反 D-010；上轮 deep-research 已挂，无 verified endpoint
  - C. 切本地 diffusers CogVideoX 推理 —— 零外部网络，diffusers 生态成熟，CPU 可跑（慢）
- 决策：**C**
- 理由：唯一既符合宪法"禁止编造 API 字段"又零成本的方案。R1 已知 CPU 推理慢（5s 视频 30min+），用户已接受；R2~R5 在 prd.md §11 列为 UNVERIFIED，需真实权重烟测验证。
- 后续动作：
  1. 删除 `sandbox/hailuo_video/hailuo_client.py`（V0.x HTTP 客户端）+ 旧 `.state.json`（含旧字段名）
  2. 新增 `sandbox/hailuo_video/local_client.py`（diffusers CogVideoX 包装，注入覆盖同款风格）
  3. CLI `generate / batch / status` 参数集从远端 submit-poll 改本地推理参数（num_frames/steps/guidance/seed）；`status` 不再 poll 远端，只读 state.json
  4. `runs.jsonl` 替代 `calls.jsonl`（op=inference 单类型）；state 字段从 `last_task_id` 改 `last_run_id` + 新增 model/device/num_frames
  5. doctor 检查从远端 endpoint 可达性改 torch/diffusers/模型缓存/磁盘空间
  6. .env.example 全 `LOCAL_VIDEO_*`，HAILUO_* 整段删除；**redact 源 .env.example 里泄漏的真实 MINIMAX_API_KEY**（Rule 4 违反，本轮已在本仓修复，源仓待用户处理）
  7. 真实权重烟测：装 torch/diffusers + 下 CogVideoX-2b 权重后跑 `generate --steps 5 --num-frames 9`

## D-012 MiniMax chat API 接入脚本引擎：按 D-010 模式实现 + MockBackend 单测隔离
- 决策日期：2026-09-18
- 提议人：王总（看完红果漫剧文章 + 想用 AI 写剧本后）
- 状态：**ACCEPTED**
- 上下文：V2-Local 只解决视频生成；漫剧流水线还需 novel → script → characters → storyboard 三步 LLM 辅助。复用现有 `MINIMAX_*` env 配置（`api.minimax.io/v1/chat/completions` + `MiniMax-M3`），不引入新第三方依赖。
- 选项：
  - A. 接 Ollama 本地 LLM —— CPU 跑 7B 模型 ~3 tokens/sec；用户已有 MiniMax 配置可复用，避免再加一套本地 LLM 基础设施
  - B. 抽象 LLMBackend，多 backend 共存 —— 过度设计，V2.1 只一种 backend 足够
  - C. 只写 schema 不接 LLM —— 流程断在第一步，无法端到端跑通
- 决策：**A + B 的折中**：默认 MiniMaxChatBackend；LLMBackend 抽象允许单测用 MockBackend + 未来加 OllamaBackend 占位
- 理由：D-010 / L-008 要求"未核实 API 不猜端点"。`MiniMax-M3` 端点 [UNVERIFIED] → 严格按"必填 Key + 缺失硬失败 + 单测 mock 验证 I/O 契约"三件套实现，跟 V0.x 海螺客户端同模式。
- 后续动作：
  1. 新增 `sandbox/hailuo_video/script_engine.py`（LLMBackend ABC + MiniMaxChatBackend + MockBackend + 数据类 + 4 个 pipeline 函数）
  2. 新增 `sandbox/hailuo_video/prompts.py`（3 个中文 prompt 模板 + JSON schema 示例）
  3. CLI 加 `script novel.txt -o storyboard.json [--shots 30] [--backend minimax|mock]`
  4. 单测 `tests/test_script_engine.py` 全部 MockBackend，不依赖真 LLM
  5. **首次真实 minimax 烟测必须由用户做**——R1 [UNVERIFIED] 端点未公开文档；填好 Key 后用 `--shots 5` 小负载跑一次验证响应结构；若不符改 `MiniMaxChatBackend.chat()` 的响应解析路径
  6. 不污染 D-011 的零外部网络底线：`script_engine` 是独立模块，与 `local_client.py`（视频）解耦；env var 独立（MINIMAX_* 不影响 LOCAL_VIDEO_*）

## D-013 文生图走 MiniMax image API（不复用 fal.ai）：用户核实端点后接入
- 决策日期：2026-09-18
- 提议人：王总（看过红果漫剧文章 + 知道 Plus 套餐可能含图像额度后）
- 状态：**ACCEPTED**
- 上下文：先前 Tier 1 推荐走 fal.ai FLUX 做文生图。改为 MiniMax 自家 image API 原因：
  1. **零额外账户/Key**：复用现有 `MINIMAX_API_KEY`（Plus 套餐已有），免去 fal.ai 注册 + 充 credits
  2. **官方端点，用户核实**：王总查证后提供端点（满足 D-010 验证步骤）；不再有"未核实"风险
  3. **不引第三方依赖**：保持"只依赖 MiniMax"原则
- 选项：
  - A. fal.ai FLUX（先前推荐）—— 需注册 / 充 credits；额外 30 元/100 张；价格波动
  - B. 本地 FLUX / SD（自托管）—— 需 GPU，CPU 完全不可行
  - C. MiniMax image API（image-01，CN 域 api.minimaxi.com）—— 用户核实端点，零额外成本
- 决策：**C**
- 理由：成本最低 / 验证已完成 / 与 D-012 同后端抽象风格（`MiniMaxImageBackend` + `MockImageBackend`）。
- 后续动作：
  1. 新增 `sandbox/hailuo_video/image_engine.py`（ImageBackend ABC + MiniMaxImageBackend + MockImageBackend + ImageResult + write_image）
  2. CLI 加 `image "prompt" -o out.jpg [--n 1] [--aspect-ratio 16:9] [--response-format base64|url] [--backend minimax|mock]`
  3. .env.example 加 `MINIMAX_IMAGE_MODEL=image-01` + `MINIMAX_IMAGE_RESPONSE_FORMAT` + `MINIMAX_IMAGE_ASPECT_RATIO` + `MINIMAX_IMAGE_N`（全部可选，有默认）
  4. **修正 .env.example / script_engine 的 `MINIMAX_BASE_URL` 默认值**为 `https://api.minimaxi.com/v1`（D-013 用户指出之前 `api.minimax.io` 是错的）
  5. 单测 `tests/test_image_engine.py` 全部 `MockImageBackend`，覆盖 base64 / url / 嵌套层级兼容
  6. **R1 首次真实烟测必须由用户做**——响应 schema 嵌套层级（`data.image_base64` vs `data.data.image_base64`）按用户提供的 Python 示例实现；若实际不符 `_parse_response` 抛"响应结构不符" + 打印 raw 给用户改

## D-014 TTS 走 edge-tts（pip 包）：零 Key 零成本，CPU 友好
- 决策日期：2026-09-18
- 提议人：王总（"Edge-TTS 你来给我安装"）
- 状态：**ACCEPTED**
- 上下文：V2.2 已能文生图；漫剧还需要配音 / 旁白 / 对白。候选对比：
  - **edge-tts**：Microsoft Edge read-aloud API；pip 包（7.2.8）；免 Key / 免账号 / 零成本；中文 voices 14 种；CPU 友好；不依赖任何 GPU
  - CosyVoice / GPT-SoVITS / FishSpeech（开源本地）：需 GPU（≥2GB 模型），CPU 极慢或不可用
  - ElevenLabs / Replicate：付费（5-30 元/段），引入持续支出
- 选项：
  - A. edge-tts —— 零成本 / 免账号 / pip 包稳定 / CPU 友好
  - B. 本地开源 TTS —— 需 GPU，CPU 不可行
  - C. 付费云 TTS —— 引入持续支出
- 决策：**A**
- 理由：零成本 + 零依赖 + 漫剧体量（30 段 × 5s 旁白 ≈ 30 段 mp3）零压力；用户明示"你来安装"。
- 后续动作：
  1. `pip install edge-tts` 装好（7.2.8 已装，烟测通过：mp3 26 KB）
  2. 新增 `sandbox/hailuo_video/tts_engine.py`（TTSBackend ABC + EdgeTTSBackend + MockTTSBackend + TTSResult + synth/list_voices/synth_batch）
  3. CLI 加 `tts "..." -o out.mp3 [--voice ...] [--rate ...] [--volume ...] [--pitch ...] [--subtitle out.vtt] [--list-voices]`
  4. 单测 `tests/test_tts_engine.py` 全部 `MockTTSBackend`，不依赖网络 / 不依赖 edge-tts import
  5. **R1 [UNVERIFIED]**：edge-tts 闭源 web 服务，端点不固定；按 pip 包 API 走；升级到首次烟测确认
  7. **不破坏 D-011 视频零网络底线**：edge-tts 仅在用户显式调 `tts` 时联网；视频仍 100% 本地

## D-016 MVP 走 Ken Burns 视频（不接真 AI 视频生成）
- 决策日期：2026-09-18
- 提议人：王总（"做一个最小的 MVP，生成一个小样"）
- 状态：**ACCEPTED**
- 上下文：V2.0 立的本地 CogVideoX 在 CPU 上太慢（5s 视频 ≈ 30min），30 镜头 = 15h+；用户授权"今天一直工作到明天早上 6 点"，单晚要交付可见的 sample.mp4
- 选项：
  - A. 升级 MiniMax 套餐拿真视频 API —— 用户已明确 Plus 不带视频（V2.0 立此路不通）
  - B. 接未核实第三方免费 API（Agnes / InferencePort）—— V2.0 已否决（D-010 / L-008）
  - C. 升级 GPU（自购显卡）—— 出 MVP 时间窗不允许等硬件
  - D. **Ken Burns 视频**（业界漫剧通用方案）：每镜头只生 1 张关键帧图 → ffmpeg zoompan 推拉镜头 → 配音 → 字幕 → 拼接
- 决策：**D**
- 理由：唯一能在 CPU 上 1-2 分钟跑出 19s 完整漫剧（5 镜头带配音字幕）的方案；用户接受"动画化静帧"作为 MVP 妥协
- 后续动作：
  1. 新增 `sandbox/hailuo_video/video_engine.py`（KenBurnsBackend + VideoResult + zoompan 表达式）
  2. 新增 `sandbox/hailuo_video/pipeline.py`（run_pipeline 端到端编排 4 阶段：image → video → tts → concat）
  3. CLI 加 `pipeline storyboard.json -o final.mp4 [--image-backend minimax|mock] [--tts-backend edge|mock]`
  4. **已交付 5 个 sample mp4**（存于 `ai_video/sample/`）：守株待兔女声/男声/井底之蛙/自相矛盾/守株待兔 10-shot 长版；时长 19-30s，大小 920KB-2MB
  5. V2.5 实测发现 3 个 bug 已修：zoompan 表达式 / edge-tts 空字符串校验 / MiniMax image base_resp 嵌套
  6. 不破坏 D-011 视频零外部网络底线：Ken Burns 是本地 ffmpeg 操作

## D-017 文生图增接 Google Gemini "Nano Banana"，reference 契约从"URL 专用"扩为"URL 或本地路径"
- 决策日期：2026-10-22
- 提议人：王总（"我想增加 Google banana 文生图功能，是否可以实现"）
- 状态：**ACCEPTED**（代码已落地；真实 API 烟测待用户执行，见"后续动作 6"）
- 上下文：D-013 定的文生图只有 MiniMax `image-01` 一路。实测发现接入 Nano Banana 的**真正难点不在调用 API，而在 reference 契约**：
  1. MiniMax `image-01` 的 `subject_references` 收 **http(s) URL**；Gemini `inlineData` 收**裸字节**，两者不兼容
  2. `asset_prep.py` 4 处硬要求 `r.image_url[0]` 再 `urlopen` 下载；**Gemini 无 url 响应模式**，永远 inline base64 → 直接报错
  3. `_read_cached_url` 非 http 一律返回 `""`；`_reference_urls` 硬过滤非 http → 本地 ref 会被**静默丢弃**，角色一致性无声消失（违反 CLAUDE.md R4）
  4. Gemini **无 `n` 参数**（一次一张）；MiniMax 有
  5. Gemini 支持 10 个比例，MiniMax 只有 7 个（多 4:5 / 5:4 / 21:9）
- 选项：
  - A. 只做 t2i，不做 i2i —— 最小改动，但 pipeline 每镜传 `subject_reference`，只做 t2i 等于**放弃角色一致性**，等于废掉 RMAP 的核心方法论
  - B. 自建本地 HTTP 服务把字节转成 URL —— 引入本不该有的运行时组件与端口/生命周期问题
  - C. **把 reference 契约扩为"URL 或本地路径"，由 backend 各自解析** —— 采纳
  - D. 走第三方聚合平台 —— 字段名与响应结构完全不同，需两套 parse；且未确认用户网络可达
- 决策：**C**。理由：资产本来就已落盘在 `assets/characters/<name>/<angle>.jpg`，只是没有 URL 可持久化；让 reference 支持本地路径既保住角色一致性，又不引入任何新组件。
- 落地要点：
  1. `ImageBackend.supports_local_refs: bool = False` 类属性（非抽象方法，避免动 `MockImageBackend`）→ `GeminiImageBackend = True`
  2. `load_reference_bytes(ref) -> (mime, bytes)` 模块级原语：http(s) 下载 or 本地读，失败抛 `ImageError`
  3. `asset_prep.fetch_bytes(result)` 替掉 4 处手写 `urlopen`
  4. `_read_cached_url(path, image_path=None)` 加可选回退；`image_path=None` 时行为与 V2.5 完全一致（零回归）
  5. **`.url.txt` 必须继续写（Gemini 写空串）** —— `has_cached_character_angle` 等 5 个函数要求 `.jpg` 与 `.url.txt` 同时存在才算缓存命中，不写会导致每次 prep 重复计费（约 $0.039/张）
  6. **fail-closed 而非静默降级**：MiniMax 收到本地 ref 抛明确 `ImageError`（存量缓存全是 http URL，零影响）；Gemini 的 `finishReason` 非 OK（IMAGE_SAFETY 等）一律抛错，绝不返回 0 张；`thought:true` 草稿图跳过
- 关键 API 事实（官方文档，首次真机烟测前保留 `[UNVERIFIED]`）：
  - 端点 `{GEMINI_BASE_URL}/models/gemini-2.5-flash-image:generateContent`，认证 header `x-goog-api-key`
  - 响应 `candidates[0].content.parts[].inlineData.{data,mimeType}` —— REST **camelCase**；`inline_data`/`mime_type` 是 Python SDK 写法，混用会静默取不到图
  - `imageConfig.imageSize` 在本模型**会被拒**；`aspectRatio` 必须放 config，写进 prompt 无效
  - `gemini-2.5-flash-image-preview` 已废弃且会拒收 `imageConfig`（HTTP 400）—— 故用 GA id
- 后续动作：
  1. ✅ `GeminiImageBackend`（t2i + i2i + n>1 顺序循环）+ `load_reference_bytes` + 工厂分支
  2. ✅ `cli.py` 三处 `--image-backend choices` 加 `gemini`；`auto` 仍指向 minimax（不在用户脚下换默认 provider）
  3. ✅ `script_engine._DOTENV_KEYS` 加 `GEMINI_API_KEY` / `GEMINI_BASE_URL`
  4. ✅ `.env.example` 加 GEMINI 块（key 用 `***REDACTED***` 占位）
  5. ✅ 新增 `tests/test_gemini_image_backend.py`（56 项，含 payload 字段断言、camelCase、thought 跳过、finishReason、n>1 多次调用、key 不泄漏）
  6. ⏳ **真实 API 烟测必须由用户做**（约 $0.039/张，Claude 侧不代打计费端点）。需确认：① 请求体被接受 ② 响应确为 camelCase ③ 实际输出 mimeType 是 jpeg 还是 png（两者代码都已处理）
  7. ⏳ 代理：用户环境需 `HTTPS_PROXY`；`urllib` 自动读取，无需配置。`GEMINI_IMAGE_TIMEOUT=300` 应对 i2i 数 MB 请求体

## D-018 CLI mock 后端与单测 mock 后端分离（CliMockBackend vs MockBackend）
- 决策日期：2026-10-22
- 提议人：王总（功能测试发现 `script --backend mock` 结构性不可用）
- 状态：**ACCEPTED**（已实施）
- 上下文：`cli.py` 宣传 `--backend {minimax,mock}`「mock=单测假数据」，实测**必然失败**：`script_engine.MockBackend.chat()` 在无预设响应时故意抛 `AssertionError`（注释：「默认回退：让单测明显失败」），而响应只能靠 `.on()` 注入，**全仓生产代码 `.on()` 调用数为 0**。更糟的是 `cli.py` 的报错文案本身在推荐这条走不通的路（「或用 `--backend mock` 跑通流程」）。对照 D-013/D-014：`MockImageBackend` / `MockTTSBackend` 都真出结果，**只有 LLM 这条不一致**。
- 选项：
  - A. 给 `MockBackend` 加默认回退 —— ❌ **破坏被测试依赖的设计**：「忘了注册响应」的测试会静默变绿，违反 CLAUDE.md §2 证据阶梯
  - B. 诚实地从 CLI 摘掉 `script --backend mock` —— 零风险，但失去 `script` 的离线冒烟能力
  - C. **新增 `CliMockBackend`（内置 canned 响应），`MockBackend` 一行不动** —— 采纳
- 决策：**C**。理由：同时修好「不可用」与「报错误导」，并让三个后端的 mock 行为对齐。
- 落地要点：
  1. `CliMockBackend` 按 system prompt 角色关键字（编剧 / 角色设计师 / 分镜师）分派 canned JSON
  2. `make_backend` 加 `"cli-mock"`；`"mock"` 仍返回严格 `MockBackend`（单测护栏由 `test_mock_backend_still_raises_on_unregistered_prompt` 锁定）
  3. 镜头数从 prompt 正则读回（`_N_SHOTS_IN_PROMPT`）—— `_parse_shots_payload` 要求**精确等于**请求的 n_shots，构造参数只作兜底
  4. canned 取值全部落在 `quality/validation.py` 的已知字典内（shot_type / camera_motion / mood / time），picker 不会 fallback
- 连带修正：`tests/test_script_engine.py::test_cli_script_mock_backend_without_preset_fails`
  断言的正是本缺陷（`rc==1` + `AssertionError`）→ 已替换为 `test_cli_script_mock_backend_now_succeeds`。
  **这是行为变更，不是删断言换绿**；docstring 写明了替换理由。
- 后续动作：
  1. ✅ `CliMockBackend` + canned 数据 + `make_backend("cli-mock")` + 修 `cli.py` 报错文案
  2. ✅ 新增 `tests/test_cli_mock_script.py`（11 项，含严格语义护栏）
  3. ✅ `script --backend mock` 实测 rc=0，产出 storyboard，`validate` 无 error
  4. ⏳ 遗留：`novel_to_storyboard` 不填 `Storyboard.scenes` / `style_anchor`，mock 与真机路径都缺 V2.5 Layer 1（详见 D-021）

## D-019 补 pyproject.toml，走可编辑安装（包实际位于 sandbox/ 下）
- 决策日期：2026-10-22
- 提议人：王总（功能测试发现 README 命令从仓库根照抄即失败）
- 状态：**ACCEPTED**（已实施；`pip install -e .` 未代执行，见"后续动作 3"）
- 上下文：README 4+ 处写 `python -m hailuo_video ...`，但包在 `sandbox/hailuo_video/`，仓库**无任何 `pyproject.toml`/`setup.py`/`requirements.txt`**，故从仓库根直接 `No module named hailuo_video`（rc=1）。文档与现实不一致。
- 选项：
  - A. 加 `pyproject.toml` + `pip install -e .` —— 采纳。README 一字不改即正确，且顺带补上项目最大的结构性欠账之一（**至今无依赖声明**）
  - B. README 里全改成 `PYTHONPATH=sandbox python -m ...` —— 零代码风险，但命令变长、仍不便自动化，且下次新增依赖会再撞
  - C. 根目录放薄壳 `hailuo_video.py` —— 多一个影子入口，不推荐
- 决策：**A**。
- 落地要点：
  1. `package-dir = {"" = "sandbox"}`（非常规布局必须显式映射）
  2. `dynamic.version = {attr = "hailuo_video.__version__"}` —— **只写 `dynamic=["version"]` 不够**，缺这段时 wheel 会**静默**打出 `0.0.0` 且不报错
  3. 依赖清单**来自全仓真实 import 统计**，非凭印象：`pillow` / `imageio-ffmpeg` / `edge-tts`；`torch`/`diffusers`/`numpy` 归入可选 `local-video` extra（在 `doctor.py`/`local_client.py` 的 try 块内 import，缺失时走「环境未就绪」硬失败）
  4. **更正一处误判**：`requests` **不是**依赖 —— 全仓仅出现在 `mcp_client.py:169` 的说明文字里，从未被 import
  5. `runtime/` 只是数据目录（`runs.jsonl`），无 `__init__.py`，用 `exclude` 排除，wheel 实测 47 文件不含它与 `tests/`
- 风险处置（实施前标记为「最需要实测」）：安装后 `asset_prep.DEFAULT_ASSETS_DIR = Path("./assets")`
  是否会被重定向到包内？**实测未变**，仍相对 cwd 解析（venv 内中性目录验证）。故安装不改变任何运行时路径语义。
- 后续动作：
  1. ✅ `pyproject.toml` + README 快速上手第 1 步改为 `pip install -e .`（并说明为何必须装）
  2. ✅ 新增 `tests/test_packaging.py`（7 项，锁住包发现 / 版本解析 / 依赖与真实 import 对齐 / runtime 不误打包）
  3. ⏳ **`pip install -e .` 未代执行** —— 会写入用户全局 site-packages（非 venv 的 Python 3.14），留待用户决定；有效性已用一次性 venv 完整验证

## D-020 auto_dev 入口重配 stdout/stderr 为 UTF-8（兑现 L-009）
- 决策日期：2026-10-22
- 提议人：王总（功能测试发现门禁在 Windows 控制台崩溃）
- 状态：**ACCEPTED**（已实施）
- 上下文：`scripts/auto_dev.py` 是仓库唯一门禁命令，但报告大量使用 ✅/❌/⏭️/📋。Windows 控制台默认 cp932（GBK 系）编码不了这些码位 → 崩在 `print("✅ Tests PASSED")`；**更糟的是兜底的异常处理器 `print("❌ auto_dev 异常")` 自己也抛 `UnicodeEncodeError`**，使脚本以 traceback 结束而非返回可判读退出码 —— 门禁一旦触发 emoji 分支，**调用方拿不到可信 rc**。
- **注意：L-009 早已记录过同一教训**（「Windows 控制台 cp932/cp936 编码下 CJK help 文本触发 UnicodeEncodeError；pytest capsys 测不到。入口处必须 reconfigure stdout/stderr 为 UTF-8」），但 `auto_dev.py` 一直没落实。本条 ADR 即是 L-009 的补齐。
- 选项：
  - A. 入口处统一重配 stdout/stderr + `errors="replace"` —— 采纳。一处改动覆盖全部输出与异常处理器；`replace` 从机制上保证**门禁不会因编码问题丢失退出码**
  - B. emoji 换 ASCII 标记 —— 要改几十处且牺牲报告可读性（任何编码环境都能跑是附带好处）
  - C. 要求调用方设 `PYTHONIOENCODING` —— 把环境责任推给每个调用方；这正是「隐藏前置条件」病根，与 D-019 同类
- 决策：**A**。
- 落地要点：`sys` import 之后、`REPO` 定义之前加 6 行；比提案多 catch `AttributeError`（`capsys` 替换的流对象无 `reconfigure` 时不应中断脚本）。
- 验证：**cp932 控制台 + 不设任何环境变量**跑 `python scripts/auto_dev.py --no-commit` → 4 步全 PASS、`EXIT=0`。
- 附带收获：门禁第一次跑就抓到 D-018 的回归（那个把 bug 写成契约的测试变红）→ 说明门禁在生效，不是摆设。
- 后续动作：
  1. ✅ `auto_dev.py` 入口重配已落地并实测
  2. ⏳ 缺口知会：`step_lint` 只扫 `SANDBOX.rglob("*.py")`，`tests/` 与 `scripts/` 不在语法检查范围（故「All 43 Python files compile clean」不含新增测试文件）。实际风险为零（pytest 收集即编译），如需收口可另议

## D-021 全局角色圣经：角色参考图按"视觉身份"跨项目复用
- 决策日期：2026-10-26
- 状态：**ACCEPTED**（已实施）
- 上下文：character sheet 每个项目/每天重新生成 → Day1 的拉布拉多和 Day2 不是同一套画（`sample/story_arc_xiaohei_solo/README.md:259-262`）；叠加 reference URL 24h 过期且不校验（`:237-241`），跨天跑时角色一致性锚定**静默**失效。
- 文献依据：arXiv:2512.16954（视觉锚定是硬需求：去掉锚定后角色一致性从 7.99 崩到 0.55）；arXiv:2503.10391 CINEMA（不靠「参考图↔关键词」映射 —— 那正是"小黄变猫"的成因）；arXiv:2506.07848 PolyVivid（视觉身份 grounding）。
- 选项：
  - A. 全局角色圣经（身份键 = appearance+palette+style_anchor 的哈希，**不含名字**）+ 项目内 cache 双写 —— 采纳
  - B. 只在项目内缓存 —— 拒绝：解决不了跨天漂移（每个项目仍是独立身份）
  - C. 引入 CLIP/DINO 做相似度选图 —— 拒绝：违反依赖极简红线（`pyproject.toml:18-22` 只有 pillow/imageio-ffmpeg/edge-tts）
- 决策：**A**。身份键**不含 `name`**（改名不断链），**含颜色词**（奶黄拉布拉多 ≠ 纯黑拉布拉多），**含 style_anchor**（不同画风是不同视觉身份）。
- 落地要点：
  1. 新增 `sandbox/hailuo_video/character_bible.py`；全局库在 `sandbox/assets/character_bible/`（**ADR-007 项目 namespace 之外** → `ProjectRegistry.cleanup` 不会误删）
  2. 缓存优先级：**项目内 cache > 全局圣经 > 重新生成**（项目内是"这个项目已备好的资产"，是权威来源）
  3. 新鲜度：本地路径（Gemini inlineData）永远新鲜；http(s) URL 用 `.expires.txt` sidecar → URL 自带 `Expires` → 图片 mtime 兜底；**能证明过期才判过期**（无法判定时照常使用，避免误杀还能用的 reference）
  4. 名字歧义守卫（纯代码）：`小黄`/`小白` 等中文色词 + 犬种描述 → WARN（不阻断）
- 边界：`CHARACTER_BIBLE_DISABLE=1` 可关掉退回纯项目隔离；`CHARACTER_BIBLE_DIR` 可指向共享盘。
- 验证：`tests/test_character_bible.py`（37 项，含"第二个项目 0 次 API 调用"、"过期 URL 被跳过"、"同名不同 appearance 身份键不同"、"全局库在 assets_dir 之外"）。
- 遗留：MiniMax 的 OSS URL 过期后**无重新托管途径**（无上传端点）→ 过期即需重新生成并告警；这在 Gemini 后端（本地字节）不存在。

## D-022 导演解耦：static visual / dynamic narrative 分进两个 prompt
- 决策日期：2026-10-26
- 状态：**ACCEPTED**（已实施）
- 上下文：V3.5 加了 12 个 director 字段，但生成端**一个都没消费** —— `orchestrator._build_task` 的 image_gen 只拼 style_anchor+description+shot_type+camera_motion+subject_action+mood；motion_gen 只拼 style_anchor+subject_action。**camera_motion 连视频模型都没进过**。字段只服务人工审稿（`STATUS_REPORT_2026-10-20.md:35`）。
- 文献依据：arXiv:2606.24107 DramaDirector（planner 把 shot **解耦成 static visual + dynamic narrative**，参考图同时指导首帧与 I2V）；arXiv:2509.26555 Stable Cinemetrics（Setup/Event/Lighting/Camera 4 类正交控制分类）；arXiv:2412.02259 VGoT（多域规格 + 自校验）；arXiv:2603.11421 ShotVerse（Plan-then-Control）。
- 选项：
  - A. DirectorBrief：静态视觉条件 → image prompt，动态叙事条件 → motion prompt —— 采纳
  - B. 12 个字段直接拼进 image prompt —— 拒绝：动/静不分，I2V 拿不到运镜与节奏
  - C. 实现完整多 agent 导演（DirectorTeam）—— 拒绝（本轮红线：不新增 LLM 往返、不做多 agent 编排）
- 决策：**A**，且**不新增 LLM 往返** —— brief 由已有 storyboard 数据**确定性推导**。
- 落地要点：
  1. 新增 `sandbox/hailuo_video/director.py`：`CINEMETRICS_AXES`（4 类分类覆盖 12 字段）、`DirectorBrief`、`plan_director_brief()`、`validate_director_brief()`（自校验）
  2. `Shot` 追加 3 字段（末尾、带默认值 → 旧 storyboard.json 与旧构造仍合法）：`static_visual` / `dynamic_narrative` / `color_lock`
  3. `prompts.py:render_storyboard_prompt` 改用安全填充（`_safe_fill`，不用 `.format()` —— LLM 产出的 JSON 含大量 `{}`），并**接线到 `script_to_storyboard`**（此前从未被调用 → `{three_beat_hint}` 等 4 个占位符以**字面花括号**进了 LLM prompt）
  4. 修 `prompts.py` 的复制粘贴缺陷：`prompt_70_30` 段此前直接复用了三拍结构提示
- 验证：`tests/test_director.py`（30 项，含"prompt 不含字面占位符"、"12 字段全进 prompt"、"camera_motion 进 motion prompt"、"旧 storyboard.json 仍解析"）。
- 连带修复：ADR-006 的 `_safe_style_anchor` 兜底必须传进 brief —— 否则空画风会让 image prompt 丢掉 anti-cartoon 守卫词（首版实现踩到，被既有测试抓出）。

## D-023 跨镜连续性门禁 + 拼接层帧率归一化
- 决策日期：2026-10-26
- 状态：**ACCEPTED**（已实施）
- 上下文：单镜 lite-SSIM 门禁有（`quality/frame_quality.py:90`，阈值 0.65），但**跨镜**维度无门禁：实测跨镜色差 mean=0.700 / min=0.587，理想基线 >0.95（`logs/_diagnose_3to4_jump_20261016/SUMMARY.md:36-39`）。旧门禁 0.65 太松 → 0.700 的片子"通过"。同时拼接层放大了崩溃：Hailuo 8fps 与 Ken Burns 24fps 混合，`-c copy` 不重采样 → 帧突跳跨段扩散（同文件 `:41-55`）。
- 文献依据：arXiv:2608.16717 PersonaShot（现有 benchmark 多只测单镜，不测**切点之后**的连贯性）；arXiv:2510.26412 LoCoT2V-Eval（5 维评估）；arXiv:2509.22799 VideoScore2（可解释报告）。
- 选项：
  - A. 新增 `continuity.py` 跨镜门禁（阈值 0.90）+ 叠化前 fps 归一化 —— 采纳
  - B. 调高既有 `compute_color_consistency` 的阈值 —— 拒绝：那是**帧间色差**语义，改它会波及既有调用方
  - C. 引入 CLIP / 深度特征做跨镜相似度 —— 拒绝（依赖极简红线）
- 决策：**A**。新增维度沿 LoCoT2V-Eval / PersonaShot 命名（perceptual / physical / cinematic / dynamic / herd）。
- 落地要点：
  1. `sandbox/hailuo_video/continuity.py`：复用 `quality/consistency.py:47 compute_color_consistency`（纯 PIL/numpy），阈值 **0.90**（介于实测 0.700 与理想 0.95 之间的保守中位）
  2. `editing/fps.py` + `crossfade.py`：每段在 scale 之后插 `fps=24`（`target_fps=None` 可关闭，保持旧行为）
  3. 门禁**默认只报警不阻断**（与既有 SSIM gate 一致）；结果进 `PipelineResult.consistency["continuity"]`
- 验证：`tests/test_continuity.py`（19 项，含"跨镜差异过大必须被 flag"、"色基调锁定冲突扣 HERD 分"、"fps 链每段都有 fps=24"）。
- 遗留：真实成片的跨镜 mean 是否能到 0.90 需实测（用户手工验证项 #10）。

## D-024 运动参数知识库 + 检索增强提示词优化（REQ-406）
- 决策日期：2026-10-26
- 状态：**ACCEPTED**（已实施）
- 上下文：`docs/PRD.md:428` 记着"运动参数知识库（`knowledge/` 目录存在但 `motion_params.json` 不存在）"。各 `sample/*/README.md` 里沉淀了大量已验证经验（style_anchor 要写真实摄影参考、appearance ≥80 字含识别锚点、色锁必须放开头、中文色词歧义……），但只是散文，**无法被程序消费**。
- 文献依据：arXiv:2504.11739 RAPO / 2510.20206 RAPO++（检索增强的提示词优化：从 **relation graph** 检索修饰词）；arXiv:2608.17468 SAGE（自演化分镜技能：learn/attribute/evolve/**route** —— 知识不能全塞进上下文，要按需路由）。
- 选项：
  - A. 纯 stdlib 关键词 + relation graph 检索（JSON 库）—— 采纳
  - B. 向量库（chroma / milvus）—— 拒绝（依赖极简红线 + 本项目语料只有几十条）
  - C. 不做检索，只加一张手写提示词表 —— 拒绝：无法随产出积累（SAGE 的 attribute/evolve 缺失）
- 决策：**A**。CJK 用**字符二元组**切词（无分词器依赖），拉丁按词。
- 落地要点：
  1. 新增 `sandbox/hailuo_video/knowledge_store.py`（`MotionParamsStore` / `MotionParamRecord` / `search()` / `related()` / `record_result()`）
  2. 新建 `knowledge/motion_params.json`：8 条种子记录（全部来自项目实测复盘）+ relation graph
  3. **写库默认关闭**（`PipelineConfig.knowledge_path is None`；CLI `--knowledge-path` 显式开启）—— 写库是副作用，默认路径在仓库内，会污染人工维护的种子数据（首版实现踩到：测试跑一次就把空记录写进种子库）
- 验证：`tests/test_knowledge_store.py`（30 项，含"种子库可检索命中"、"库里不能混入空记录"、"写库默认关闭"）。

## D-025 负提示词：能力声明式 + 内联（不发未确认字段）
- 决策日期：2026-10-26
- 状态：**ACCEPTED**（已实施）
- 上下文：全仓**零** negative prompt 支持 —— `image_engine` 请求体只有 model/prompt/aspect_ratio/response_format/n/subject_references/prompt_optimizer。生成路径里没有任何"禁止出现什么"的表达，只能靠正向描述规避。
- 文献依据：arXiv:2610.15098 TIDE（把昂贵的迭代式提示词优化蒸馏成 rule-module skill：**单次 LLM 调用同时产出正/负提示词**，不做 test-time 视觉搜索）。**成本模型是关键**：本项目 Hailuo 每日只有 3 条 quota，"生成→评分→改写"的迭代搜索根本跑不动。
- 选项：
  - A. 能力声明式（`supports_native_negative_prompt`）：声明支持才发独立字段，否则**内联**到 prompt 末尾 —— 采纳
  - B. 直接发 `negative_prompt` 字段 —— 拒绝：`[UNVERIFIED]` 官方文档未列出该参数 → 会 4xx，而**请求已经计费**
  - C. 不做 —— 拒绝：TIDE 的核心价值（正+负一次产出）正是本项目缺的那块
- 决策：**A**。内联不是降级凑合 —— 项目**已经**在用同一手法且验证有效：`asset_prep._safe_style_anchor` 追加 `"no illustration no cartoon no anime no CGI"`（V3.4 ADR-003 防漂移守卫）。
- 落地要点：
  1. 新增 `sandbox/hailuo_video/negative_prompt.py`：`PROHIBITED_CONTENT` 为**唯一 SSOT**（此前仓库里根本没有这个白名单，只有 `image_engine.py:55` 的 Gemini 错误码）
  2. `ImageBackend.supports_native_negative_prompt = False`（默认）；`generate()` 签名加 `negative_prompt`
  3. `limit` 只作用在**基础集**上 —— 显式追加的 motion/character 项必须保留（首版实现把它们挤掉了，被自己的测试抓出）
- 验证：`tests/test_knowledge_store.py`（含"后端不发未确认字段"、"能力声明为 True 才发独立字段"、"mock 与真后端行为一致"）。

## D-026 VLM 评审接口：只建接口，不接真 API
- 决策日期：2026-10-26
- 状态：**ACCEPTED**（已实施，接口层）
- 上下文：REQ-407「反馈闭环（播放量反哺提示词）」与"提示词自动优化"此前完全没动。文献侧的闭环方法（VQQA / VideoScore2 / RAPO）都需要一个"能读图并输出可解释评分"的多模态评审器。
- 文献依据：arXiv:2603.12310 VQQA（VLM critique 当语义梯度，黑盒 NL 接口做闭环优化）；arXiv:2509.22799 VideoScore2（多维可解释 + CoT 理由）；arXiv:2604.28078 AesRM（可判定的层级 rubric）。
- 选项：
  - A. 本轮只建接口（评审器抽象 + 5 维 + 报告结构 + CLI + mock），真 API 默认关闭 —— 采纳（用户确认）
  - B. 本轮直接接 Gemini VLM —— 拒绝：会产生真实计费与速率限制，且会绑进每日 cron
  - C. 跳过这一轮 —— 拒绝：它是 W4 检索闭环与 REQ-407 的前置
- 决策：**A**。真后端需**三者齐备**：`--backend gemini` + `--enable-real` + `GEMINI_API_KEY` + `GEMINI_VLM_MODEL`；缺任一项**显式报错**（鉴权域硬失败 —— 静默退回 mock 会让用户误以为评审过了）。
- 落地要点：
  1. 新增 `sandbox/hailuo_video/vlm_review.py`：`VLMReviewer` ABC / `MockVLMReviewer`（零网络）/ `GeminiVLMReviewer`（`http_post` 注入点）/ `REVIEW_DIMENSIONS`（5 维）/ `build_review_prompt()` / `parse_review_response()`（非 JSON → 空报告，不抛错）
  2. 新增 CLI `review` 子命令（默认 `--backend mock`）
  3. `_DOTENV_KEYS` 加 `GEMINI_VLM_MODEL`；`.env.example` 加占位
- 验证：`tests/test_vlm_review.py`（28 项，含"mock 零网络调用"、"真 API 缺 enable-real 报错"、"非 JSON 响应不抛错"）。
- 边界：评审**只报告不阻断**；真实评审效果需用户手工烟测（验证项 #7）。

## D-027 P0 止血批：4 个"静默失败"缺陷（画幅 / static 降级 / registry 落盘 / 喂错图）
- 决策日期：2026-10-26
- 状态：**ACCEPTED**（已实施）
- 上下文：本轮代码审计确认 4 个**静默失败**缺陷 —— 它们让失败看起来像成功：
  1. **9:16 在 V3 生产路径不可达**（4 处叠加）：`project.py` 的 `aspect_ratio` 默认 `"16:9"` 会经 `env_overrides` 覆盖用户 `.env`；`run_pipeline_v3()` 没有该形参；`pipeline.py` 的 `cfg.aspect_ratio = cfg.aspect_ratio or project_context.aspect_ratio` 因 `"16:9"` truthy 永不触发且被错嵌在 `if sb_style:` 里；CLI 的 `pipeline` 子命令没有 `--aspect-ratio`（只有 `image` 子命令有）。→ 客户第一格式（小红书 9:16）根本出不来。
  2. **`motion_complexity:"static"` 静默降级**：被排除出 motion_gen → 走 Ken Burns 兜底，却报 `[OK] ... (0 bytes, 19.1s)`，无任何 WARN（`sample/story_arc_qingdao_autumn/README.md:81-89`）。
  3. **registry 落盘静默失败**：`except OSError: pass` → 续跑信息丢失无人知晓（违反项目 R4）。
  4. **多镜 storyboard 全部镜头共用最后一张图**：`ImageGenAgent` 造的是 `image_{idx}`（结果下标）而非按 shot 隔离，而 `Registry.add` 用 artifact_id 做 key → 后一个 shot 覆盖前一个；`_get_image_path_for_shot` 对非首镜永远落到"第一个可用图"兜底。
- 决策：一律**让失败可见**：画幅按"显式值 > backend env 默认 > 16:9"解析；static 降级记 event 并进 `PipelineResult.consistency`；registry 落盘失败记 event + 进 consistency（**不 raise**：不该炸掉已产出的成片）；artifact_id 与落盘文件名都按 shot 隔离。
- 连带修正：`concat_files` 的 `duration_seconds` 此前写的是**墙钟耗时**（拼接 40.5s 的片子报 0.18s）→ 改用 `probe_duration` 读回真实时长，墙钟另存 `elapsed_seconds`。
- 验证：`tests/test_p0_stopgap.py`（24 项）。
- 遗留：`editing` phase 的"重复跑 N 次"经核实**不成立** —— `Phase.is_parallel=False`，editing 本来就只跑一次；实际问题是 `_collect_video_paths_for_editing` 里有一段与首段**完全相同**的死分支，已删除。

## D-028 V3.8 六条工作流的文献映射（总览索引）
- 决策日期：2026-10-26
- 状态：**ACCEPTED**（已实施）
- 上下文：V3.8 的 6 条工作流不是凭经验设计的，而是逐条从 arXiv 检索（16 组查询）映射到本项目**已定位到 file:line 的具体缺陷**。下表是索引，细节见 D-021 ~ D-027。
- 映射表：

  | 工作流 | 修的缺陷 | 主要文献 |
  |---|---|---|
  | W1 全局角色圣经 | 角色跨天漂移 / reference 过期静默失效 / 中文色词歧义 | 2512.16954、2503.10391、2506.07848 |
  | W2 导演解耦 | 12 字段到不了模型 / prompt 占位符字面泄漏 | 2606.24107、2509.26555、2412.02259、2603.11421 |
  | W3 跨镜连续性 + fps | 跨镜色差无门禁 / 拼接层放大崩溃 | 2608.16717、2510.26412、2509.22799 |
  | W4 知识库 + 负提示词 | 零负提示词 / motion_params.json 不存在 | 2610.15098、2504.11739、2510.20206、2608.17468 |
  | W5 竖屏 + 主体感知裁剪 | 9:16 不可达 / 裁剪只有三档锚点 | 2307.16371、2101.04051 |
  | W6 VLM 评审接口 | REQ-407 前置 | 2603.12310、2509.22799、2604.28078 |

- 方法论教训（值得沉淀）：**"实现已存在但从未接线"是一类独立缺陷** —— `prompts_director.py` 的 `parse_dop_response` / `apply_dop_to_shot` 早就实现且有单测，生产代码零调用；`render_storyboard_prompt` 同理。检索文献前应先做**可达性审计**（"谁调用它"），而不是假设"没有这个功能"。

## D-029 统一 .env loader：白名单漂移已复发三次，本轮用**不变量测试**根治
- 决策日期：2026-10-26
- 状态：**ACCEPTED**（已实施）
- 上下文：同一类缺陷在仓库里复发了三次，每次都是"**白名单与代码/文档不同步**"：
  1. **D-018**：`image_engine` 有自己的 `_DOTENV_KEYS`，但真正生效的是 `script_engine` 那份 → `MINIMAX_IMAGE_*` 从未生效。
  2. **F3（2026-10-22）**：把 image 的 4 个键补进 `script_engine` 白名单 —— 但 video / tts / youtube 的键仍然漏着。
  3. **V3.9（本次）**：代码共读 **44** 个 env key，可加载的只有 **24** 个 → **20 个**在 `.env` 里配了永远不生效。
- V3.9 实测（`.env` 写入 4 个 key 后 import）：

  | key | V3.9 之前 | 后果 |
  |---|---|---|
  | `MINIMAX_VIDEO_ENDPOINT` | **未加载** | Hailuo 视频端点无法配置（切国际域只能改代码常量） |
  | `EDGE_TTS_VOICE` | **未加载** | 配音音色 / 语速配置失效 |
  | `YOUTUBE_CLIENT_ID` | **未加载** | 用户照 `.env.example` 填了凭据，代码仍报"凭据缺失" |
  | `LOCAL_VIDEO_DEVICE` | 已加载 | （唯一生效的 —— `local_client.py` 有第二套 loader） |
- 选项：
  - A. 统一 loader + 单一白名单 + **不变量测试**守住漂移 —— 采纳
  - B. 继续往两份白名单里补键 —— 拒绝：第三次的教训就是"补键"治不了漂移
  - C. 改成"全量加载 .env，不设白名单" —— 拒绝：会把拼错的 key 静默注入环境；且 `.env` 里可能有不该进 env 的东西
- 决策：**A**。新增 `sandbox/hailuo_video/dotenv.py`（唯一 loader，唯一白名单 `LOADABLE_ENV_KEYS`）；`script_engine` 与 `local_client` 的两份副本都改为薄封装并删除。
- 落地要点：
  1. **多路径合并**（旧行为"找到第一个 .env 就 return" → cwd 下的 .env 存在时仓库根的完全不看）
  2. **未知 key 告警**：白名单外的 key 打 `[dotenv] [WARN]`（**只打 key 名不打值**）—— 治拼错
  3. **不变量测试**（`tests/test_dotenv.py`）—— 这是本条决策的真正价值：
     - 代码里 `os.environ.get("X")` 的每个 X 都必须可加载（**正则扫源码**）
     - `.env.example` 里声明的每个 key 都必须可加载（或登记为 `EXTERNAL_ENV_KEYS`）
     - 白名单里的每个 key 都必须在 `.env.example` 里有说明（防"隐形 key"）
     新增 env key 忘了加白名单 → 测试立刻变红。
  4. `.env.example` 补齐此前**从未被文档化**的 13 个键（EDGE_TTS_* / YOUTUBE_* / MINIMAX_FILE_RETRIEVE_ENDPOINT / CHARACTER_BIBLE_* / DAILY_* / HAILUO_VIDEO_PROJECT_ID）
- 验证：`tests/test_dotenv.py`（18 项）；实测 4 个此前失效的键现已全部生效。
- 教训见 **L-014**（测试侧的另一条泄漏通道）。

## D-030 auto_dev.py 的 pytest 路径参数是字面量 "TESTS" —— 门禁一直在少跑 conftest
- 决策日期：2026-10-26
- 状态：**ACCEPTED**（已实施）
- 上下文：`scripts/auto_dev.py:80` 的 pytest 命令传的是**字面量字符串** `"TESTS"`（变量名漏进了命令行），而不是 `str(TESTS)`。
- 影响：pytest 收到一个**不存在的路径参数** → 回退到 `pyproject.toml` 的 `testpaths`（所以测试集是对的、门禁一直是绿的），**但 `tests/conftest.py` 不会被加载** —— conftest 是按 **args 的目录链**加载的，args 指向不存在的路径时整条链都取不到。
  - V3.9 新增 `tests/conftest.py`（`isolated_env` fixture + session 级 `CHARACTER_BIBLE_DISABLE`）在门禁里直接报 `fixture 'isolated_env' not found` → 门禁红了才暴露出来。
  - 更早的隐患：`tests/conftest.py` 之前不存在，所以这个 bug 一直没被触发。
- 决策：改成 `str(TESTS)`，并加两条回归测试锁住：
  1. pytest 的路径参数必须是 `str(TESTS)`（不能出现字面量 "TESTS"）
  2. pytest 的每个路径参数必须真实存在
- 验证：`tests/test_auto_dev.py` +2 项；门禁重跑 4 步全 PASS。
- 附带认知：**门禁绿 ≠ 门禁跑对了**。门禁自身的命令也要被测（本仓的 `tests/test_auto_dev.py` 用 `_FakeRun` 记录 subprocess 调用，是这个模式的好例子）。

## D-031 角色语音一致性：音色分配到**角色**，不是镜头（REQ-403）
- 决策日期：2026-10-26
- 状态：**ACCEPTED**（已实施）
- 上下文：`orchestrator._build_task` 的 tts 分支只有**一个全局 voice**
  （`task["voice"] = self.config.voice`）→ 整条片子所有角色、所有旁白**一个音色**。
  这与本项目"角色一致性"的核心价值主张直接冲突：画面上每只狗都长一样，声音上只有一个人。
  PRD 一直把 REQ-403 标为"⏳ P3 未做"。
- 文献依据：
  - **arXiv:2405.09768**（TTS 评估）：五个评估维度之一就是 **speaker consistency**
    （同一说话人在不同 utterance 里音色要稳定）→ 本模块要满足的不变量。
  - **arXiv:2505.17320**（角色驱动 TTS）：**按角色**准备音色（每角色专属音色），
    不是全局一个音色 → 分配粒度是 character，不是 shot。
  - **arXiv:2210.05979**（ASCL，零样本多说话人 TTS）：说话人域偏移是零样本 TTS 的
    主要难题 → 对本项目（edge-tts 固定音色池）意味着**角色→音色的映射必须稳定**。
- 决策：
  1. 新增 `sandbox/hailuo_video/voice_cast.py`：`VoiceCast`（角色→音色分配表）、
     `speaker_of()`（说话角色判定）、`resolve_voice_for_shot()`。
  2. 优先级：`shot.voice` > `character.voice` > **探测式分配** > 全局默认。
  3. 旁白（voiceover）与对白（dialogue）分开：旁白用叙述者音色，**该音色不在角色池里**。
  4. 角色撞音色 → 记 event 告警（不阻断，但必须可见）。
- 两个在实现中**被自己的测试抓出来**的设计错误（值得记）：
  1. **旁白音色原本也在角色池里**（都用 Yunxi）→ 某角色可能被分配到与旁白相同的音色
     → "角色可区分"这条就废了。修：旁白改用 `zh-CN-YunyangNeural`（不在池中），
     并加断言"旁白音色 ∉ 角色池"。
  2. **纯哈希分配会撞车**：15 个槽位分 5 个角色，按生日悖论约有一半概率至少撞一次
     （实测"兔子"与"海龟"都拿到 Xiaohan）。修：改成**探测式**（哈希取起始槽位，
     已占用则往后找空位）→ 同片子内不同角色音色**必不相同**。
     代价：若某项目的角色集合/顺序变了，同一角色的音色可能变
     （同一项目多天跑时角色表通常一致 → 稳定）。
- 落地要点：
  1. `Shot` 新增 `voice` / `speaker`；`Character` 新增 `voice`（均带默认值 → 旧 storyboard.json 仍可解析）
  2. `orchestrator` 挂 `self._voice_cast`（整个 run 内同一角色恒定同音色）+ `_voice_collision_seen`
  3. **零 API 成本**：只是选一个 edge-tts 音色名，不改合成逻辑
- 验证：`tests/test_voice_cast.py`（30 项）。
- 边界：**不按性别/物种猜音色** —— 那是模型的活。要精确指定请在 storyboard 的
  `characters[].voice` 里写（本仓的角色是犬/猫/鹿/农夫，猜错比不猜更糟）。

## D-032 清理 `last_frame_image` 死代码（BUG-119）
- 决策日期：2026-10-26
- 状态：**ACCEPTED**（已实施）
- 上下文：`logs/_verify_last_frame_image_20261016/SUMMARY.md`（2026-10-16 实测）已判定
  `last_frame_image` **被 API 拒绝（2013）**，且"已写代码但永远走不到"，并列出
  "必须做的清理"三项 —— 但那一轮**只做了验证没做清理**（本轮是 V3.8 红线的
  "不改 motion_backend.py"，留到 V3.9 单独做）。
- 决策：删除三处 —— ① `generate()` 里 `first_frame or last_frame` 的分支；
  ② `_generate_i2v()` 的 `last_frame_image` 形参；③ `_submit()` 的 body 拼装。
  误传 `last_frame_image` 时**告警并回退 T2V**（R4：不静默忽略），
  而不是一个必然 4xx 的请求。
- 连带：docstring 里的"双锚定"表述改为"不可行的优化路径，别再走"
  （DeepSeek Q5 的假设已被实测推翻 —— 留着会误导下一个人）。
- 验证：`tests/test_motion_last_frame_removed.py`（9 项，含"请求体里绝不能出现
  last_frame_image"的兜底断言）。
- 教训：V3.8 定了"不改 motion_backend.py"的红线，代价是这个已知死代码又留了一轮。
  **红线要写清"为什么"** —— 否则下一轮不知道该不该碰。

## D-033 CI 与本地门禁同源
- 决策日期：2026-10-26
- 状态：**ACCEPTED**（已实施）
- 上下文：仓库有 GitHub origin（github.com/wk708872173-droid/ai-video）但**从无 CI** ——
  门禁只在本地跑，回归靠人记；`docs/ARCHITECTURE.md:153` 的 "CI pre-commit hook"
  待办挂了很久。
- 选项：
  - A. CI 跑**同一个** `scripts/auto_dev.py` —— 采纳
  - B. CI 另起一套 `pytest` 命令 —— 拒绝："CI 绿了"不代表"本地门禁也是绿的"
    （D-030 的 auto_dev 路径参数 bug 正是"两套行为不一致"的实例）
  - C. 只加 pre-commit hook，不加 CI —— 拒绝：hook 只在有 commit 时跑，
    push 到 main 的路径不受保护
- 决策：**A**。`.github/workflows/ci.yml` 在 push/PR 上跑 `scripts/auto_dev.py --no-commit`
  （Python 3.11 / 3.13 两档）。另提供**可选**的 `.githooks/pre-commit`
  （`git config core.hooksPath .githooks` 启用）。
- 落地要点：
  1. `dev` extra 补 `pytest-cov` —— 门禁覆盖率步骤用 `--cov`，缺它那步跑不起来
  2. secret scan 两处修复：不再把凭据打进日志（只打 `***REDACTED***`）；
     diff 为空（CI 干净工作树）时退化为**整仓扫描**（排除 tests/ 的假凭据），
     否则 CI 上这一步永远是空转
- 验证：`tests/test_auto_dev.py` +5 项；门禁 4 步全 PASS。
