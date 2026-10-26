# PRD-LV — 本地视频推理 (Local Video / CogVideoX) sandbox PRD

> 旁路 sandbox 项目。不入主 `TASK_QUEUE.json`，不污染 `src/agentorg/`。
> 阶段：V2 (Local Backend)。门禁：pytest + 真实权重烟测。
> V2.1：新增脚本引擎（LLM 辅助剧本 + 角色 + 分镜）。
> V2.2：新增图像生成（MiniMax image API；用户核实端点）。
> V2.3：新增 TTS（edge-tts 包；零 Key 零成本）。
> V2.4：新增拼接（ffmpeg via imageio-ffmpeg 包；零系统依赖）。
> V2.5：**MVP 端到端 pipeline**（Ken Burns 视频 + MiniMax image + Edge-TTS + concat）。已交付 5 个 sample mp4。
> 状态：ACTIVE（V2-Local 替代 V0.x HTTP 客户端）。
> 上一版：V0.5 MiniMax 海螺 HTTP 客户端（V2 起删除）。

## 1. 背景
V0.x 走 MiniMax 海螺视频 API（`api.minimax.io/v1` 端点 + Bearer Key），实测发现 **MiniMax Plus token plan 不含视频 API 额度**（D-011）。V2-Local 切换到本地 diffusers CogVideoXPipeline，零外部网络依赖。用户当前环境为 **纯 CPU**（V2-Local 已知 R1：5s 视频可能 30min+）。

## 2. 用户与场景
工程师 / 内容创作者：在本地机器上跑 prompt → mp4，不烧 API 配额。

## 3. 痛点
- MiniMax Plus 没视频 API：V0.x 客户端永远 `source=misconfigured` 硬失败
- 第三方未核实 API 风险高（D-010 / L-008）：拒绝接 Agnes / free-multimodal-proxy
- 没有批量入口（即使有 API 也要手动一个个跑）

## 4. 输入
- prompt 文本（英文/中文）
- 输出路径
- 可选：`--num-frames` / `--height` / `--width` / `--steps` / `--guidance` / `--seed`（覆盖 env）

## 5. 输出
- mp4 文件落到指定路径
- 推理记录落 `sandbox/hailuo_video/runtime/runs.jsonl`（每跑一次一行）
- 最近一次 run 状态落 `sandbox/hailuo_video/.state.json`（原子写）

## 6. 成功标准
- `python -m hailuo_video doctor` 在装好 torch + diffusers 后 `configured=True`，且模型已缓存时 `model_cached` ✓
- `python -m hailuo_video ping` 打印当前 model / device / num_frames 等配置 + ready 状态
- `python -m hailuo_video generate "..." -o out.mp4 --steps 5 --num-frames 9` 同步返回 mp4（CPU 上 9 帧 + 5 步约 1-3 分钟）
- 失败（缺依赖 / dtype 不兼容 / OOM）非零退出且 stderr 有可机读错误码前缀
- `runs.jsonl` / `state.json` 不含 env var 值

## 7. 非目标
- 不接任何外部视频 API（MiniMax / Agnes / free-multimodal-proxy 全否）
- 不做 web UI
- 不入主 `TASK_QUEUE.json`
- 不改 `src/agentorg/`
- 不做并发 batch（V3+ 视 GPU 情况再加）
- 不做视频后期 / 拼接 / 转码
- **V2.1 脚本引擎**：只做 novel→storyboard 三步，不调外部视频 / 图像 / TTS（见 §V2.1）
- **V2.2 图像生成**：只做 prompt → image bytes，不调视频 / TTS
- **V2.3 TTS**：只做 text → mp3 bytes，不调视频 / 图像
- **V2.4 拼接**：只做 shots[] → final.mp4，不调任何上游 LLM/API

## 8. 验收条件
- `pytest tests/test_hailuo_video.py -q` 全绿（单测 mock `_override_pipeline_load/_call`，不依赖 torch/diffusers）
- `runs.jsonl` 不含 cache_dir 等 env var 值（仅写 model/device/num_frames/duration 等元信息）
- `LOCAL_VIDEO_DEVICE=cuda` 但 torch 无 CUDA → `doctor` 报 ✗ + exit 1
- `LOCAL_VIDEO_DTYPE=bfloat16` + `LOCAL_VIDEO_DEVICE=cpu` → `generate` 走 `source=disabled`，exit 1
- 真实权重烟测：装 torch / diffusers + 下载 CogVideoX-2b 权重后跑一次 `generate`，落 mp4

## 9. 接口契约（边界）
```python
local_client.generate(prompt, output_path, *, model, device, dtype, num_frames,
                      height, width, steps, guidance, seed, cache_dir) -> VideoResult
# VideoResult 含 ok/source/error/run_id/model/device/num_frames/duration/bytes
local_client.set_overrides(load=..., call=...)           # 单测注入
local_client.reset_overrides()
local_client.is_environment_ready() -> bool              # torch + diffusers 都装好
local_client.warnings() -> list[str]
local_client.config_dict() -> dict                       # 全部生效配置

state.State / load_state / save_state / update_last       # .state.json 原子读写
  字段：last_run_id / last_prompt / last_status / last_model / last_device / last_num_frames / last_duration_seconds
calllog.CallRecord / load_calls / tail_calls / format_calls  # runs.jsonl 解析（op=inference）
manifest.write_manifest(summary, path) -> Path            # BatchSummary → manifest.csv（含 model/device/num_frames）
doctor.diagnose() / format_diagnose()                     # torch/diffusers/device/dtype/model_cached/disk
batch.load_prompts(path) -> list[str]
batch.run_batch(prompts, output_dir, *, num_frames, ...) -> BatchSummary  # 顺序，无并发
cli.cmd: generate / ping / status / batch / log / doctor
```

## 10. 配置（环境变量）
| Key | 必填 | 默认 | 说明 |
| --- | --- | --- | --- |
| `LOCAL_VIDEO_MODEL` | — | `THUDM/CogVideoX-2b` | HuggingFace repo id |
| `LOCAL_VIDEO_DEVICE` | — | `cpu` | `cpu` / `cuda` |
| `LOCAL_VIDEO_DTYPE` | — | `float32` | CPU 必 float32；CUDA 可 bf16 |
| `LOCAL_VIDEO_NUM_FRAMES` | — | `49` | ~6s @ 8fps |
| `LOCAL_VIDEO_HEIGHT` | — | `480` | |
| `LOCAL_VIDEO_WIDTH` | — | `720` | |
| `LOCAL_VIDEO_STEPS` | — | `50` | smoke 测试可降到 5 |
| `LOCAL_VIDEO_GUIDANCE` | — | `6.0` | classifier-free guidance |
| `LOCAL_VIDEO_SEED` | — | `-1` | -1 = 随机 |
| `LOCAL_VIDEO_CACHE_DIR` | — | `~/.cache/huggingface` | 模型权重目录 |

## 11. 风险与开放问题
- **R1 [已确认]**：CPU 推理极慢（5s 视频 30min+）。建议 smoke 用 `--steps 5 --num-frames 9`。
- **R2 [UNVERIFIED]**：CogVideoX-2b 在 Windows + CPU + float32 上的实际首帧耗时——等真实权重烟测验证。
- **R3 [UNVERIFIED]**：HuggingFace 在国内网络可达性。必要时设 `HF_ENDPOINT=https://hf-mirror.com`。
- **R4 [UNVERIFIED]**：diffusers 版本兼容。建议 pin `diffusers==0.32.x`（V2 实施前 `pip show diffusers` 查实际）。
- **R5**：CogVideoX-2b 权重 ~10GB，需保证 `LOCAL_VIDEO_CACHE_DIR` 所在盘 ≥ 15GB 可用空间。

## 12. 变更日志
| 版本 | 日期 | 变更 |
| --- | --- | --- |
| V2.5 | 2026-09-18 | **MVP**：新增 `video_engine.py`（Ken Burns 视频引擎，ffmpeg zoompan filter，CPU 友好 5s mp4 ≈ 5s 渲染）+ `pipeline.py`（端到端编排：image → video → tts → concat）+ `pipeline` CLI 子命令。**关键妥协**：CPU 上 CogVideoX 5s 视频 ≈ 30min，Ken Burns 走"图片 + 推拉镜头"代替真 AI 视频生成；视觉上和漫剧平台差别不大，但坦白讲不是真 AI 视频。R1 [UNVERIFIED] ffmpeg 7.1 zoompan 表达式限制：`on(N,0,1)` 在某些版本触发 "Failed to configure output pad"，改用静态 + 帧计数自增 `on` 变量。已交付 5 个 sample mp4（守株待兔女声/男声/井底之蛙/自相矛盾/守株待兔 10-shot 长版），存于 `sample/` 目录。 |
| V2.4 | 2026-09-18 | **新增**：拼接引擎（`concat_engine.py`）+ `concat` 子命令。基于 `imageio-ffmpeg==0.6.0` 自带 ffmpeg 7.1 essentials 二进制（pip 包，零系统依赖）。`manifest.json → final.mp4`：每个 shot 走 `merge_clip (video+audio) → burn_subtitles (.vtt/.srt) → concat_videos (concat demuxer, -c copy)`。中间产物临时目录，跑完即删（`--keep-intermediate` 调试用）。`ConcatResult` 含 ffmpeg 日志供排查。单测 mock subprocess 不依赖 ffmpeg。 |
| V2.3 | 2026-09-18 | **新增**：TTS 引擎（`tts_engine.py`）+ `tts` 子命令。基于 `edge-tts==7.2.8`（pip 包）。text → mp3（可选同时生成 .vtt 字幕）。零 Key / 零账号 / 零成本；Microsoft Edge read-aloud API。中文默认 voice `zh-CN-XiaoxiaoNeural`（女）。提供 `--list-voices [--filter-locale zh-]` 查表。`MockTTSBackend` 单测全离线。R1：edge-tts 闭源 web 服务，端点不固定；D-010 模式（mock + 烟测 + 字段解析 schema 不固化）。**R2 [V2.5 实测发现]**：edge-tts 7.2 对空字符串 reject（regex 校验 `^[+-]\d+%$`），空 rate/volume/pitch 需 fallback 到 `+0%` / `+0Hz`，否则抛 ValueError。已在 V2.5 fix。 |
| V2.2 | 2026-09-18 | **新增**：图像生成引擎（`image_engine.py`）+ `image` 子命令。prompt → MiniMax image API → jpg/png 落盘。**端点 + schema 由用户核实（D-013）**：CN 域 `https://api.minimaxi.com/v1/image_generation`，模型 `image-01`，响应 `data.image_base64` (base64 模式) 或 `data.image_url` (url 模式)。默认 base64（推荐，无过期）；url 模式 24h 过期。`MINIMAX_API_KEY` 缺失或长度 < 16 → 硬失败 exit 2；`MockImageBackend` 单测覆盖 I/O 契约。**R1 [V2.5 实测修正]**：响应 schema 顶层是 `base_resp`（status_code + status_msg），失败时 `status_code=1004` 表示 auth fail；本类 `_parse_response` 已兼容嵌套剥 `base_resp` 后再 `data.image_base64` 路径。 |
| V2.1 | 2026-09-18 | **新增**：脚本引擎（`script_engine.py` + `prompts.py`）+ `script` 子命令。novel → script → characters → storyboard 全流程走 LLM（默认 MiniMax-M3，可切 mock）。JSON 结构化输出，可直接喂下游 fal.ai / Edge-TTS / ffmpeg 流水线。LLM 后端按 D-010 模式实现：`MINIMAX_API_KEY` 缺失或长度 < 16 → 硬失败；R1 [UNVERIFIED] 端点需首次烟测。单测全部走 `MockBackend`，不依赖真 LLM。 |
| V2.0 | 2026-09-18 | **大改**：从 MiniMax 海螺 HTTP 客户端切到本地 diffusers CogVideoX 后端。删除 `hailuo_client.py`（V0.x HTTP 客户端）；新增 `local_client.py`（diffusers 包装）。CLI 子命令保留 `generate / ping / status / batch / log / doctor`，参数集从远端异步 submit-poll 改为本地推理参数（num_frames/steps/guidance/seed）。`status` 不再 poll 远端任务（无任务），只读 state.json。`batch` 不再支持 `--parallel`（V3+ 视 GPU 情况再加）。`runs.jsonl` 替代 `calls.jsonl`，单条记录是单次推理。state 字段从 `last_task_id` 改为 `last_run_id`，新增 `last_model / last_device / last_num_frames`。Manifest CSV 去掉 `task_id` 列，加 `model / device / num_frames` 列。doctor 检查从远端 endpoint 可达性改为 torch / diffusers / 模型缓存 / 磁盘空间。 |
| V0.5 | 2026-09-17 | （废弃）+doctor.py + HEAD 不带 auth 验端点。 |
| V0.4 | 2026-09-17 | （废弃）+并发 batch + manifest.py。 |
| V0.3 | 2026-09-16 | （废弃）+state.py + calllog.py。 |
| V0.2 | 2026-09-16 | （废弃）+Batch 模式。 |
| V0.1 | 2026-09-16 | （废弃）初稿（CLI + async 客户端 + 单测 mock 全覆盖）。 |

---

## §V2.1 脚本引擎（LLM 辅助 novel → storyboard）

### 背景
V2-Local 只解决"视频生成"，漫剧制作还需要"剧本 + 角色 + 分镜"三步。靠人工写文案慢；用 LLM 辅助可把这三步从"小时"压到"分钟"。

### 接口契约
```python
script_engine.LLMBackend           # ABC，chat(system, user, **opts) -> str
  ├─ MiniMaxChatBackend            # 生产（env MINIMAX_API_KEY + MINIMAX_BASE_URL + MINIMAX_MODEL）
  └─ MockBackend                   # 单测 / 离线演示

script_engine.novel_to_script(novel, backend) -> Script
script_engine.script_to_characters(script, backend) -> list[Character]
script_engine.script_to_storyboard(script, characters, backend, *, n_shots=30) -> list[Shot]
script_engine.novel_to_storyboard(novel, backend, *, n_shots=30) -> Storyboard

script_engine.write_storyboard(sb, path) -> Path        # 落 JSON
script_engine.read_novel(path) -> str

# 数据类（dataclass）
Script(title, scenes: list[Scene])
Scene(id, time, location, characters, summary, dialogue)
Character(name, appearance, personality, color_palette)
Shot(index, scene_id, shot_type, description, characters, dialogue, voiceover, mood, camera_motion)
Storyboard(title, characters, shots)
```

### 输出 JSON Schema（喂下游流水线用）
```json
{
  "title": "剧本标题",
  "characters": [
    {"name": "角色名", "appearance": "外貌", "personality": ["关键词"], "color_palette": ["色"]}
  ],
  "shots": [
    {
      "index": 1,
      "scene_id": 1,
      "shot_type": "中景",
      "description": "镜头视觉描述（80-150 字）",
      "characters": ["角色A"],
      "dialogue": "",
      "voiceover": "",
      "mood": ["紧张", "快速"],
      "camera_motion": "缓慢推近"
    }
  ]
}
```

### CLI
```bash
python -m hailuo_video script novel.txt -o storyboard.json [--shots 30] [--backend minimax|mock]
```

### D-010 应用
- `MINIMAX_API_KEY` 缺失或 < 16 字符 → 硬失败 exit 2，stderr 提示补 .env
- 端点未公开核实 → 仅在用户显式调用 minimax backend 时才发网；mock backend 全离线
- `MockBackend` 单测覆盖 I/O 契约，与 V0.x 测试模式一致

### 风险与未验证
- **R1 [UNVERIFIED]**：`https://api.minimaxi.com/v1/chat/completions` 端点 / `MiniMax-M3` 模型字段 / 响应结构未公开核实。首次使用前必须：
  1. `python -m hailuo_video doctor`（V2.0 现有的）不覆盖 LLM 端点；本轮新增 doctor LLM check（待补）
  2. 填好 `MINIMAX_API_KEY` 后跑 `python -m hailuo_video script test_novel.txt -o /tmp/sb.json --backend minimax --shots 5`（shots 越小越省 token）
  3. 检查 stderr + JSON 是否合理；若 LLM 返回非 OpenAI 标准格式，调整 `MiniMaxChatBackend.chat()` 的响应解析
- **R2 [UNVERIFIED]**：脚本 / 分镜 JSON 的质量取决于 LLM 输出稳定性。若 LLM 返回非 fenced JSON，靠回退分支 `text.find('{')` 抓，但鲁棒性差。
- **R3**：novel 输入若超过 LLM context window（MiniMax-M3 推测 8k-32k），需先切片（V2.1 不实现，留 TODO）。

---

## §V2.2 图像生成（MiniMax image API）

### 背景
V2-Local（视频）+ V2.1（剧本）已就绪；下一步是"分镜 → 静态图"。本地 FLUX/SD 跑不动（CPU），按 D-013 决策复用现有 MiniMax API Key 调官方 image 端点。

### 接口契约（D-013 用户核实）
- **端点**：`POST {MINIMAX_BASE_URL}/image_generation`
  - 默认 CN 域 `https://api.minimaxi.com/v1/image_generation`（海外切 `api.minimax.io/v1` 也可）
- **请求字段**：
  - `model`: `image-01`（env `MINIMAX_IMAGE_MODEL`）
  - `prompt`: str
  - `aspect_ratio`: `1:1` / `16:9` / `4:3` / `3:4` / `9:16` / `2:3` / `3:2`（env `MINIMAX_IMAGE_ASPECT_RATIO`，默认 `16:9`）
  - `response_format`: `base64`（推荐，无过期） / `url`（24h 过期）（env `MINIMAX_IMAGE_RESPONSE_FORMAT`）
  - `n`: 1-9（env `MINIMAX_IMAGE_N`，默认 1）
  - 可选：`prompt_optimizer: bool`、`subject_reference: str`（URL，图生图）
- **响应 schema**（R1 [UNVERIFIED]，按用户提供的 Python 示例）：
  - base64 模式：`{"data": {"image_base64": ["..."]}}` 或嵌套 `{"data": {"data": {"image_base64": ["..."]}}}`
  - url 模式：`{"data": {"image_url": ["..."]}}`（类比）

### 代码契约
```python
image_engine.ImageBackend          # ABC, generate(prompt, **opts) -> list[ImageResult]
  ├─ MiniMaxImageBackend           # 生产
  └─ MockImageBackend              # 单测

image_engine.ImageResult            # bytes + mime + prompt + model + aspect_ratio + duration + ...
image_engine.write_image(result, path) -> Path
image_engine.image_extension(mime) -> str
image_engine.make_backend("minimax" | "mock") -> ImageBackend
```

### CLI
```bash
python -m hailuo_video image "a cat on the moon" -o out.jpg \
  [--backend minimax|mock] [--n 1] [--aspect-ratio 16:9] [--response-format base64|url]
```
- `-o` 是文件路径（单张 `n=1`）或目录（`n>1` 自动命名 `000.jpg / 001.jpg`）

### D-010 应用
- `MINIMAX_API_KEY` 缺失或 < 16 字符 → 硬失败 exit 2
- `response_format` / `aspect_ratio` 不在白名单 → 启动时 stderr 警告
- 单测 `MockImageBackend` 全离线，验证 I/O 契约

### R1 [UNVERIFIED] — 首次真实烟测必做
- 响应 schema 嵌套层级：用户提供的示例是 `data["data"]["image_base64"]`，但实际可能是 `data["data"]["data"]["image_base64"]` 或纯 `data.image_base64`。本类 `_parse_response` 已兼容 dict-of-dict 与 list-of-str 两种结构；若都不匹配会抛 `ImageError("响应结构不符")` + 打印完整 raw，让用户调整。
- 首次跑：
  ```bash
  python -m hailuo_video image "测试 prompt" -o /tmp/test.jpg --response-format base64
  ```
- 若报错"响应结构不符"，把 stderr 的 raw 贴回来，调整 `image_engine.py:_parse_response` 路径即可。

### 与 D-011 / D-012 共存
- `script_engine`（chat）和 `image_engine`（image）共享 `MINIMAX_API_KEY` + `MINIMAX_BASE_URL`
- 不破坏 D-011 立下的"零外部网络"底线（视频生成仍 100% 本地 CogVideoX）
- `image_engine` 在用户显式调用 `--backend minimax` 时才发网；mock 模式离线

---

## §V2.3 TTS（edge-tts）

### 背景
V2.2 已能文生图，但漫剧还需要旁白 / 对白配音。免费 TTS 候选对比：
- **edge-tts**（微软 Edge read-aloud API）：免 Key 免账号、零成本、中文 voices 14 种、稳定（pip 包 7.2.8）
- CosyVoice / GPT-SoVITS / FishSpeech（开源本地）：需 GPU + 装大模型（≥2GB），CPU 极慢或不可用
- Replicate / ElevenLabs：付费（5-30 元/段）

### 决策（详见 D-014）
**用 edge-tts**：零成本 + 中文支持好 + 不依赖 GPU。

### 接口契约
```python
tts_engine.TTSBackend (ABC)
  ├─ EdgeTTSBackend                  # 生产：pip 装 edge-tts 即用
  └─ MockTTSBackend                  # 单测：注入 fake bytes

tts_engine.synth(text, output_path, *, voice, rate, volume, pitch, write_subtitle) -> TTSResult
tts_engine.list_voices(filter_locale="") -> list[dict]
tts_engine.synth_batch([(text, path, opts), ...], backend=...) -> list[TTSResult]
tts_engine.write_tts(result, path) -> Path

tts_engine.TTSResult(bytes, mime_type="audio/mpeg", text, voice, rate, volume, pitch,
                      duration_seconds, sub_file, source="ok"|"error"|"disabled", error, extra)
```

### CLI
```bash
python -m hailuo_video tts "你好，世界" -o out.mp3
python -m hailuo_video tts "hello" -o out.mp3 --voice en-US-AvaNeural --rate "+10%" --subtitle out.vtt
python -m hailuo_video tts --list-voices [--filter-locale zh-]    # 列 voices
python -m hailuo_video tts "..." -o out.mp3 --backend mock         # 单测模式
```

### env var
| Key | 默认 | 说明 |
| --- | --- | --- |
| `EDGE_TTS_VOICE` | `zh-CN-XiaoxiaoNeural` | 默认 voice 名 |

### R1 [UNVERIFIED]
- edge-tts 闭源 web 服务，端点 / 协议可能变。本类按官方 `edge-tts` 7.2.8 pip 包接口写，**不**直接拼 HTTP 请求
- 首次烟测：`python -m hailuo_video tts "你好世界" -o /tmp/t.mp3`；若 1.5 KB 内有 mp3 字节 → OK
- 若 edge-tts 升级导致 `Communicate` API 改字段，需调整 `EdgeTTSBackend.synth` 的 stream 解析

### 与 D-011 / D-012 / D-013 共存
- edge-tts 不依赖 `MINIMAX_*` env var，与 script/image 引擎完全独立
- 不破坏 D-011 视频零网络底线：edge-tts 仅在用户显式调 `tts` 时联网
- 单测 `MockTTSBackend` 全离线

---

## §V2.4 拼接（ffmpeg via imageio-ffmpeg）

### 背景
漫剧最终一步：把每镜头的视频 + 配音 + 字幕拼成一条完整 mp4。
- 选用 **imageio-ffmpeg pip 包**（自带 ffmpeg 7.1 essentials 二进制），不依赖系统 ffmpeg 安装
- 与 diffusers 共用同一套 ffmpeg 来源；社区标配

### 接口契约
```python
concat_engine.is_available() -> bool               # ffmpeg 在 PATH / imageio-ffmpeg 装好
concat_engine.ffmpeg_version() -> str
concat_engine.merge_clip(video, audio, output, **opts) -> Path
concat_engine.burn_subtitles(video, subtitle, output, **opts) -> Path
concat_engine.concat_videos([video_a, video_b, ...], output, **opts) -> Path
concat_engine.concat_pipeline(shots, output, **opts) -> ConcatResult

# 数据类
ShotItem(index, video, audio, subtitle, duration_seconds)
ConcatResult(output, shot_count, duration_seconds, bytes, source, error, ffmpeg_log, extra)

# Manifest IO
concat_engine.make_concat_result_from_manifest(path) -> list[ShotItem]
concat_engine.write_concat_manifest(shots, path) -> Path
```

### Manifest 格式
```json
{
  "shots": [
    {"video": "out/0000_xxx.mp4", "audio": "audio/0000.mp3", "subtitle": "audio/0000.vtt"},
    {"video": "out/0001_xxx.mp4", "audio": "audio/0001.mp3", "subtitle": "audio/0001.vtt"}
  ]
}
```

### CLI
```bash
python -m hailuo_video concat manifest.json -o final.mp4 [--no-burn-subs] [--keep-intermediate]
```

### R1 [UNVERIFIED]
- ffmpeg 7.1 已装（imageio-ffmpeg 0.6.0），但 **首次烟测必跑一次**——本机 path / codec / Windows 反斜杠转义等可能微调；`burn_subtitles` 的 `vf=subtitles=...` 转义按 Windows 反斜杠 `\`→`/` + `:`→`\:`，已实测兼容，但边角可能需调
- 默认 `-c copy`（无损快速拼接），要求所有 clip codec/分辨率一致；若想混 codec，可传 `--extra-args "-c:v libx264 -preset fast"`

### 与 V2.0 / V2.1 / V2.2 / V2.3 共存
- 不调任何上游 API；纯本地 ffmpeg 拼接
- 输出 mp4 是整个漫剧流水线的最终产物

---

## §V2.5 MVP 端到端 pipeline（Ken Burns 视频）

### 背景
- V2.0 CogVideoX 在 CPU 上太慢（5s 视频 ≈ 30min），30 镜头 = 15h+，一个晚上跑不完 MVP sample
- 业界 AI 漫剧通用做法：每镜头只生 1 张关键帧图 → 用 Ken Burns 推拉镜头效果 + 配音 + 字幕拼成 mp4
- 视觉上和漫剧平台差距不大；坦白讲不是真 AI 视频生成

### 端到端流水线
```
storyboard.json ─┐
                  ├─→ pipeline.py ─→ final.mp4
                  │
                  ├─ image (MiniMax)
                  ├─ video (Ken Burns)
                  ├─ tts (Edge-TTS)
                  └─ concat (ffmpeg)
```

### 已交付 sample
存于 `ai_video/sample/`：
| 文件 | 时长 | 大小 | 故事 | voice |
|---|---|---|---|---|
| `mvp_shou_zhu_dai_tu.mp4` | 19.0s | 1.1 MB | 守株待兔 5 镜头 | 女声（Xiaoxiao） |
| `mvp_shou_zhu_dai_tu_male.mp4` | 20.7s | 920 KB | 守株待兔 5 镜头 | 男声（Yunxi） |
| `mvp_jing_di_zhi_wa.mp4` | 21.8s | 1.4 MB | 井底之蛙 5 镜头 | 女声 |
| `mvp_zi_xiang_mao_dun.mp4` | 20.7s | 1.1 MB | 自相矛盾 5 镜头 | 女声 |
| `mvp_shou_zhu_dai_tu_long.mp4` | 29.4s | 2.0 MB | 守株待兔 10 镜头完整版 | 女声 |

### 接口契约
```python
video_engine.KenBurnsBackend         # ffmpeg zoompan 后端
  .render(image, output, *, seconds, fps, effect, zoom_expr, x_expr, y_expr) -> VideoResult
video_engine.VALID_EFFECTS = ("zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down", "static")

pipeline.run_pipeline(storyboard, output, *,
    image_backend, video_backend, tts_backend,
    video_seconds=5, video_fps=8, video_effect="cycle",
    tts_voice="zh-CN-XiaoxiaoNeural",
    keep_intermediate=True, skip_video=False, skip_tts=False, skip_concat=False,
    log_callback=None,
) -> PipelineResult

pipeline.load_storyboard(path) -> Storyboard
```

### CLI
```bash
python -m hailuo_video pipeline storyboard.json -o final.mp4 \
  [--image-backend minimax|mock] [--tts-backend edge|mock] \
  [--voice zh-CN-XiaoxiaoNeural] [--video-seconds 5] [--video-fps 8] \
  [--video-effect cycle|zoom_in|...] [--keep-intermediate]
```

### R1 / R2 [UNVERIFIED] — V2.5 实测发现
- **ffmpeg 7.1 zoompan 表达式限制**：`on(N, 0, 1)` 在某些版本触发 "Failed to configure output pad"；改用 `min(zoom+0.003,1.15)` + 帧计数自增 `on` 变量。`s=` 参数必须用 `WxH` 不是 `W:H`
- **edge-tts 7.2 空字符串校验**：rate/volume/pitch 不能传空字符串，会抛 `ValueError: Invalid rate ''`（regex `^[+-]\d+%$`）；fallback 到 `+0%` / `+0Hz`
- **MiniMax image 响应 schema**：顶层有 `base_resp` 包裹（status_code / status_msg），失败时 `status_code=1004` 表示 auth fail；`_parse_response` 已剥 `base_resp` 再走 `data.image_base64`
- **Mock 必须返回真字节**：MockImageBackend / MockTTSBackend 之前返回 19-byte fake，被下游 ffmpeg 拒收；改为懒生成 631-byte 真 JPEG / 2854-byte 真 mp3

### 与 D-011 / D-012 / D-013 / D-014 共存
- 视频（V2.5）走本地 ffmpeg Ken Burns，与 V0.x 真 AI 视频 v2.0 是**两个独立后端**
- 不破坏 D-011 视频零外部网络底线：Ken Burns 是本地 ffmpeg 操作
- 不破坏 MiniMax image（V2.2）/ Edge-TTS（V2.3）/ ffmpeg（V2.4）各自边界
---

## §V2.6 Quality Assurance 层 — "通用保证输出效果"框架

V1→V2.5.2 已经解决了"如何产出"；V2.6 解决"如何保证产出质量"——不论写什么故事，QA 层都该自动校验 + 度量。

### 背景
- 守株待兔 → 井底之蛙 → 自相矛盾：每跑一个新故事都要重新人眼 review "是否一致"
- 不同 picker / 不同 MiniMax 返回都可能悄悄降低一致性
- 缺乏**通用**质量保证机制——只能靠每次肉眼看

### 三大保证层（V2.6）

#### Layer 1: Pre-flight 校验
- **`validate_storyboard(sb)`**: schema + 必填字段 + 引用一致性 + picker 字典匹配警告
- 输出 `ValidationReport(issues: list[Issue])`（level: error/warning）

#### Layer 2: Pre-flight asset 检查
- **`check_assets(sb, assets_dir)`**: 列出 storyboard 所需但还没生成的 reference sheets
- 输出 `AssetReport(characters, scenes, shot_fallbacks)`：picker fallback 警告

#### Layer 3: Post-flight 一致性度量
- **`compute_color_consistency(shot_paths)`**: PIL 直方图 cosine 相似度 + 亮度方差
- 不依赖 torch/transformers/CLIP（纯 PIL + numpy）
- 输出 `ConsistencyReport(mean_similarity, min_similarity, flagged_pairs, brightness_variance)`

### Picker fallback 警告（V2.6 新增）
- **`warn_picker_fallback(category, shot_index, requested, used)`**
- 当 picker 走 fallback 时显式 `[WARN]` 到 stderr，**不静默降级**
- pipeline 自动集成：每个 picker 调用都附 shot_index 参数

### 接口契约
```python
from hailuo_video import quality

# Pre-flight
rep: quality.ValidationReport = quality.validate_storyboard(storyboard)
asset_rep: quality.AssetReport = quality.check_assets(storyboard, assets_dir)

# Post-flight
consist_rep: quality.ConsistencyReport = quality.compute_color_consistency(shot_image_paths)

# Picker 警告（集成在 asset_prep picker 中）
quality.warn_picker_fallback("angle", shot_index=5, requested="back", used="front")
```

### CLI（V2.6 新增 3 个）
```bash
python -m hailuo_video validate storyboard.json
# 输出：
# ⚠ [shot_type_unknown] shots[0] shot_type='xxx' 不在已知列表...
# ⚠ [appearance_too_short] character[农夫].appearance 太短（< 20 字）...
# ✗ [character_undefined] shots[0] 引用了未定义的 'xxx'...

python -m hailuo_video check-assets storyboard.json --assets-dir ./assets
# 输出：
# [FAIL] missing 10 reference sheet(s)：
#   - characters/农夫/front.jpg
#   - characters/农夫/side_left.jpg
#   ...
# 建议跑：python -m hailuo_video prep storyboard.json --assets-dir ./assets

python -m hailuo_video consistency-check ./intermediate_dir
# 输出：
# mean 色差相似度: 0.842（1.0 = 完全一致）
# min 色差相似度: 0.756
# [OK] 所有相邻 shot 色差相似度 ≥ 阈值
```

### Pipeline 集成
- `pipeline.py` 在所有 shot 生成后自动跑 `quality.compute_color_consistency()`
- 附在 `PipelineResult.consistency` 字段
- 低于阈值时 `[WARN] consistency shot i ↔ shot i+1: sim=0.45` 到 stderr

### 测试覆盖（test_quality.py）
- `validate_storyboard` 11 个 case（必填、引用、unknown picker 等）
- `check_assets` 3 个 case（缺所有 / 全有 / format）
- `compute_color_consistency` 5 个 case（identical / 完全不同 / < 2 张 / format / 边界）
- `warn_picker_fallback` 2 个 case（不同 vs 相同）

### R / 边界
- PIL fallback bytes 已实测 ffmpeg 能解析（无 PIL 时函数仍可用但 consistency 跳过）
- MiniMax quota 2067 时 validate / check-assets 不影响（都是本地运算）；pipeline 后置 consistency 同理
- picker warning 在 propagate=False 模式下不走 root，但 caplog 测能临时开启 propagate

### 与现有层共存
- 完全独立模块（`quality.py`）
- pipeline 集成是 post-flight 不阻塞主流程
- validate / check-assets 是独立 CLI，不强制调用
