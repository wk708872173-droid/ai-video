# Lessons — 海螺视频 (Hailuo) 项目

> 本目录教训条目仅针对 `sandbox/hailuo_video/` 项目相关实战经验。
> 完整的 ai-os 教训库在 `ai_agents_dev_tool/knowledge/`，本目录为本地精简版。

## L-008 API 未核实 = 必走 misconfigured，禁止"猜 base URL"假装跑通
- 时间：2026-09-16（海螺视频 sandbox V0.1）
- 现象：海螺视频 API 在 WebFetch 被企业网关挡、WebSearch 无公开页；典型反模式是"猜 `POST /v1/video/generations` 占位符"再 stub → pytest 跑通就声明 done
- 根因：Operating Rule 1 FLOOR 明确禁止编造 API 字段；但常见偷懒是"差不多就行了"——尤其 sandbox/ 实验项目
- 修复（D-010）：
  1. 端点全部走环境变量必填（`HAILUO_VIDEO_SUBMIT_URL` / `HAILUO_VIDEO_POLL_URL`）；缺一即 `source=misconfigured` 硬失败
  2. CLI `ping` 输出 `⬅️ TODO 你从 tokenplan 控制台查` 提示，禁止猜值
  3. 单测 mock `_override_post/_override_get/_override_get_bytes` 验证 I/O 契约，不打真网
  4. `calls.jsonl` 落盘但绝不写 `Bearer` 字面值或 API Key（`extra` 字段白名单过滤）
- 对账动作：今后任何新外部 API 集成（不仅是 MiniMax）必须先有：可查证的 endpoint 文档 + 单测 mock 模式。否则 sandbox/ 也要硬失败，不允许"快糙猛上线"

## L-009 Windows CJK 控制台 cp932 编码坑
- 时间：2026-09-16（海螺视频 V0.2 烟测）
- 现象：`python -m hailuo_video --help` 在 Windows 命令行直接挂 `UnicodeEncodeError: 'cp932' codec can't encode character '视'`，pytest 走 capsys 全过 42/42 测不到
- 根因：Windows 默认 stdout/stderr 编码跟着控制台代码页（cp932 日文 / cp936 简中 / cp950 繁中），CJK help 文案触发编码失败；pytest 用 in-memory UTF-8 流 `capsys` 屏蔽了
- 修复（[sandbox/hailuo_video/__main__.py:23-37](sandbox/hailuo_video/__main__.py#L23-L37)）：入口处 `sys.stdout.reconfigure(encoding="utf-8")` / fallback `TextIOWrapper`；只对 `sys.platform == "win32"` 做，Linux/Mac 不动
- 对账动作：
  1. 任何 sandbox/ 或 scripts/ 子命令若 help/print 含 CJK，`__main__.py` 入口必须先 shim UTF-8
  2. pytest 走 capsys 不验证编码兼容；V0.2 起新增「CLI 入口必跑一次 `python -m <pkg> --help`」人工烟测 checklist（开发自己跑）
  3. CI 若跨平台，得加 Windows runner 跑 `--help` 防止回归

## L-011 「实现已存在但从未接线」是一类独立缺陷
- 时间：2026-10-26（V3.8 审计）
- 现象：`prompts_director.py` 的 `parse_dop_response` / `parse_editor_response` / `apply_dop_to_shot` / `apply_editor_to_shot` **早已实现且有完整单测**（`tests/test_prompts_director.py`），但**生产代码零调用** —— 全仓 grep 只命中测试文件。`prompts.py:render_storyboard_prompt` 同理：`script_to_storyboard` 绕开它直接用模板 + `str.replace`，导致 `{three_beat_hint}` 等 4 个占位符以**字面花括号**进了 LLM prompt。
- 根因：写"功能"时先写了常量/解析器/渲染器并配了单测，但**没接进主链路**；单测覆盖的是"这个函数对不对"，不是"它有没有被用"。于是 12 个 director 字段只服务人工审稿，`camera_motion` 连视频模型都没进过 —— 而这些缺陷**不产生任何报错**。
- 修复（D-021/D-022）：
  1. 接 `render_storyboard_prompt` → `script_to_storyboard`；接 `parse_dop_response`/`apply_dop_to_shot` → director brief 推导
  2. `render_storyboard_prompt` 改用 `_safe_fill`（逐个 replace，不用 `.format()`）—— LLM 产出的 JSON 含大量 `{}`，`.format()` 遇到模板里的未知花括号会直接抛
- 对账动作：
  1. **可达性审计**：新增"渲染/解析/常量"类能力时，必须同时 grep 生产代码确认有调用点；只有测试引用的模块不算已实现
  2. 测试断言要落在**链路产物**上（如"prompt 里不得含字面占位符"），而不是只断言函数返回值
  3. 排查"某功能好像没生效"时，先查"谁调用它"，再查"它算得对不对"

## L-012 硬编码默认值会逐层遮蔽上游配置
- 时间：2026-10-26（V3.8 审计）
- 现象：`.env` 里配了 `MINIMAX_IMAGE_ASPECT_RATIO=9:16`，但 V3 生产路径出的图**永远是 16:9**。根因是 4 处硬编码叠在一起：`ProjectContext.aspect_ratio` 默认 `"16:9"` 并经 `env_overrides` **覆盖**用户 `.env` → `run_pipeline_v3()` 没有 aspect_ratio 形参 → `cfg.aspect_ratio = cfg.aspect_ratio or project_context.aspect_ratio` 因 `"16:9"` 是 truthy 永不触发 → 且这行被错嵌在 `if sb_style:` 里。
- 根因：**每一层都用了"合理默认值"，但没有任何一层问"上游是不是已经给了值"**。`or` 短路 + truthy 默认值 = 上游配置永远传不下去。
- 修复（D-027）：
  1. 默认值改 `None`（= "不指定"），解析优先级显式化：**显式值 > backend 自己的 env 默认 > "16:9"**
  2. 注入逻辑从 `if sb_style:` 里挪出来（画幅与画风无关）
  3. CLI 补 `--aspect-ratio`
- 对账动作：
  1. 新增"可配置项"时，默认值优先用 `None`/空串表示"未指定"，把"最终兜底"放在**唯一**一处
  2. 排查"env 不生效"时，逐层追这个值：谁读 env → 谁改写 env → 谁把它写死 → 谁用了 truthy 默认值短路
  3. `env_overrides` 这类"覆盖式"机制要特别小心：它会盖掉用户显式配置，除非明确只在用户没配时才写

## L-013 静默降级要留痕（"看起来像成功"是最大的坑）
- 时间：2026-10-26（V3.8 审计）
- 现象：三处"失败看起来像成功"：
  1. `motion_complexity="static"` 的镜头不进 motion_gen → 走 Ken Burns 兜底 → 仍报 `[OK] shot_003.mp4 (0 bytes, 19.1s)`，无任何 WARN（要人工抽帧才发现）
  2. `registry.save_json` 抛 `OSError` 被 `except OSError: pass` 吞掉 → 续跑信息丢失无人知晓
  3. reference URL 过期不校验 → 跨天跑时角色锚定静默失效（MiniMax 侧表现为 403）
- 根因：这三条都符合"别让主流程崩"的直觉，但**丢掉了可观测性**。R4「不静默降级」被理解成"不要抛异常"，而正确理解是"**不要让调用方误以为成功**"。
- 修复（D-027 / D-021）：
  1. 降级信息进 `PipelineResult.consistency`（结构化、可断言）+ 结构化日志 event + CLI `[WARN]`
  2. registry 落盘失败**不 raise**（不该炸掉已产出的成片）但**必须可见**
  3. 过期 reference → 跳过该 ref + WARN，而不是静默使用
- 对账动作：
  1. 任何 `except: pass` / `except SomeError: pass` 都要问："调用方怎么知道出事了？" —— 答案不能是"不知道"
  2. 降级不是不能做，而是**必须留痕**：降级信息进返回值/日志/告警三选一以上
  3. "0 bytes 但报 OK" 这种自相矛盾的输出是代码味道，值得逐个排查
## L-014 pytest 的 monkeypatch.delenv(raising=False) 不登记 undo → 环境变量泄漏
- 时间：2026-10-26（V3.9）
- 现象：`tests/test_dotenv.py` 里 `monkeypatch.delenv("MINIMAX_VIDEO_ENDPOINT", raising=False)` + `load_dotenv(...)` 之后，**该键仍留在 `os.environ` 里**，泄漏给后续测试 —— 实测污染到 `tests/test_motion.py`，让后者拿到错误的端点 URL 而失败。
- 根因：pytest 的 `MonkeyPatch.delitem` 实现是
  ```python
  if name not in dic:
      if raising: raise KeyError(name)
  else:
      self._setitem.append((dic, name, dic.get(name, notset)))
      del dic[name]
  ```
  → **键本来不存在时，`raising=False` 直接返回，什么都不记**。于是后续由**被测代码**写入的值没有 undo 记录，测试结束后不会清掉。
  （`monkeypatch.setenv` 则不同：它总是登记 `(dic, name, notset)`，undo 时会 `del`。）
- 修复：`tests/conftest.py` 增加 `isolated_env` fixture —— 对 `os.environ` 做**完整快照 / 还原**，兜住所有情况（不只 monkeypatch 改过的键）。
- 对账动作：
  1. 任何**被测代码会写** `os.environ` 的测试，都要带 `isolated_env`（或自己 snapshot/restore）—— 只靠 `monkeypatch` 不安全
  2. "单独跑绿、全量跑红" 通常是**环境/全局状态泄漏**，不是随机性：先怀疑 `os.environ` / `sys.path` / cwd / 全局单例
  3. `monkeypatch.delenv(k, raising=False)` 不等于"测试后这个键会被清掉"

## L-015 真 API 路径从未烟测 → 4 个缺陷叠到"看起来能跑"
- 时间：2026-11-03（V5.0 Batch 1）
- 现象：用户更新 `.env` 的 API key 后验收，`script --backend minimax`
  **真实路径从未成功跑通**。测试全绿（1306 项），因为**全部走 mock**。
  PRD 里 `R1 [UNVERIFIED]`（端点/模型/响应结构未核实）一直是准确的。
- 根因：**4 个缺陷叠加**，任意一个单独存在都足以让真路径失败：
  1. prompt 与 schema 自相矛盾（要求"30 镜头"但 schema 没有 shots 字段）
     → 模型为自洽疯狂推理
  2. 请求体没有 `response_format` —— 提示词里写"输出严格 JSON"只是祈使句
  3. 推理模型内联 `<think>` 块 → JSON 解析必失败
  4. `max_tokens` 与 `timeout` 太小 → 推理吃光预算 → content 空 / 请求超时
  加上渲染层一个缺陷：`{{ }}` 转义没还原 → 模型看到非法 JSON 语法
- 修复：全部 5 处；默认模型不变（M3 + think 剥离即可用），
  更快的 `MiniMax-M3.1-Flash` 可用 env 切换。
- 对账动作：
  1. **每个外部服务集成都至少要有一次真端到端烟测**，且要留一份
     可复跑的脚本 —— mock 永远证明不了真实端点的行为（CLAUDE.md R2）
  2. 提示词与输出 schema 是**一对契约**：prompt 里要求的每个产物，
     schema 里都得有对应字段；反过来 schema 里有的字段，prompt 也要提。
     不一致会让推理模型陷入"怎么自洽"的死循环
  3. 推理模型（带 `<think>` / `reasoning_content` 的）**必须**：
     剥推理块 + 留足 `max_tokens` + 把 timeout 调到推理级（≥180s）
  4. 「输出严格 JSON」这种祈使句**不是约束** —— 要用请求体的
     `response_format={"type":"json_object"}`

## L-015 提示词与输出 schema 是一对契约，不一致会让推理模型陷入死循环
- 时间：2026-11-03（V5.0 Batch 1）
- 现象：真 API 跑 `script --backend minimax` **从未成功**。4 个缺陷叠加，
  任意一个单独存在都足以失败；而 1400+ 个 pytest 用例**全绿** —— 因为**全走 mock**。
- 根因（最隐蔽的一个）：`NOVEL_TO_SCRIPT_PROMPT` 要求"改编为一段 **30 镜头**的漫剧剧本"，
  但输出 schema **没有 shots 字段**（分镜是第 3 步才生成的）。
  推理模型为自洽疯狂推理 → 4096 token 被推理吃光 → `finish_reason=length`
  → content 为空 → "LLM 返回为空"。
- 修复：prompt 改"只写场景级剧本" + 显式"不要写分镜表"；`max_tokens` 4096→8192；
  `timeout` 60→180；请求体加 `response_format={"type":"json_object"}`；
  解析前剥 `<think>`；渲染器还原 `{{}}` 转义。
- 对账动作：
  1. **每个外部服务集成都至少要有一次真端到端烟测**，且留一份可复跑脚本 ——
     mock 永远证明不了真实端点的行为（CLAUDE.md R2）
  2. 提示词里要求的每个产物，schema 里都得有对应字段；反过来 schema 里有的字段，
     prompt 也要提。不一致 = 推理模型的死循环
  3. 推理模型（带 `<think>` / `reasoning_content`）必须：剥推理块 + 留足
     `max_tokens` + timeout 调到推理级（≥180s）
  4. 「输出严格 JSON」是**祈使句不是约束** —— 要用请求体的 `response_format`

## L-016 「mock 开关」必须覆盖**所有**计费后端，否则等于没有
- 时间：2026-11-03（V5.0 Batch 3）
- 现象：`--image-backend mock --tts-backend mock` 让 pipeline 试跑时
  **仍提交了 3 条真 Hailuo 视频**，用光当天 3/3 配额
  （MiniMax 返回 `status_code 2067 当前已达到 Token Plan 用量上限`）。
- 根因（两层）：
  1. CLI **根本没有** `--motion-backend` —— 视频阶段只能打真后端
  2. 加了开关之后**仍然失效**：motion_gen phase 自己造 task dict（不走 `_build_task`），
     **漏了 `motion_backend`** → agent 回落到真后端
- 修复：新增 `--motion-backend`；`_run_motion_gen_conditional` 显式带上
  `motion_backend` + `shot_idx`。
- 对账动作：
  1. 加 mock 开关时，**逐个 phase 查**「这个 phase 的 task 从哪来」——
     自己造 task 的 phase 不会自动继承全局配置
  2. mock 开关必须有**测试断言**它真的被调用（`assert sentinel.calls`），
     而不是只断言「参数能解析」
  3. **计费域的任何真实调用前，先问"这一步是 mock 吗"**

## L-017 `bytes` 恒 0 会掩盖「产物没落盘」
- 时间：2026-11-03（V5.0 Batch 3）
- 现象：CLI 报 `[OK] out/final.mp4 (shots=3, 0 bytes, 7.9s)` ——
  而 `out/final.mp4` **根本不存在**（真产物在 `run_dir/final.mp4`）。
- 根因：`PipelineResult.bytes` **从未赋值**（恒 0），且 CLI 用它当"成功"的样子。
  0 bytes 看起来像"产出了空文件"，**掩盖了"路径错了"这个真问题**。
- 修复：按实际文件大小赋值 + CLI 加闸（0 字节 / 文件不存在 → 显式失败）。
- 对账动作：任何"成功"输出里出现的**数字**，都必须从真实数据算出来。
  恒定值（0 / None / ""）出现在报告里 = 有人在糊弄。

## L-018 门禁绿 ≠ 门禁跑对了（第二次）
- 时间：2026-11-03（V5.0 Batch 2）
- 现象：`tests/test_legacy_fixes_f1_f6.py::test_f6_step_lint_reports_syntax_error`
  在门禁里**偶发失败**（约 1/10），单独跑 8 次全绿。
- 根因：测试把**整棵树**编译到**同一个** `cfile`（跨文件共享状态），
  且 `fake_run` 只捕 `PyCompileError` —— 别的异常类型会**穿透** `step_lint`，
  让本测试以"异常"而不是"断言"失败。
- 修复：每个文件用**自己的** cfile（放 tmp_path）+ 捕获范围放宽到 `Exception`
  （语义 = 任何编译失败都算 rc!=0，与 `step_lint` 真实判定一致）。
- 对账动作：
  1. **共享可变状态**（同一个临时路径 / 全局单例 / 环境变量）是间歇性失败的
     第一嫌疑人 —— "单独跑绿、全量跑红"通常就是它
  2. 修法是**消除共享状态**，不是加重试或加 sleep
  3. 断言失败与异常失败要分清：测试该断言行为，不该被底层异常打断
- 关联：L-014（同一类：环境/全局状态泄漏）。
