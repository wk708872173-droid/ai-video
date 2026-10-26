# D1–D3 提案与实施记录

提出并修复于 2026-10-22（对 10-20 ~ 10-22 开发内容的功能测试所发现）。

**三个缺陷均已实施修复。** 按 CLAUDE.md §5，规则类 / PRD / ADR 文件不得未经请求修改，
故提案独立成文；每份末尾的「实施后修正」记录了实际做法、**对提案本身过强说法的更正**，
以及实施中才暴露的新发现。`docs/PRD.md` / `knowledge/decisions.md` **未被改动**，
是否将结论合并进去由你决定。

| 编号 | 提案 | 状态 | 采纳方案 | 结果 |
|---|---|---|---|---|
| D1 | [`script_mock_backend_draft.md`](script_mock_backend_draft.md) | ✅ 已实施 | A：新增 `CliMockBackend` | `script --backend mock` 由 rc=1 变 rc=0 |
| D2 | [`cli_invocation_path_draft.md`](cli_invocation_path_draft.md) | ✅ 已实施 | A：新增 `pyproject.toml` | 不设 PYTHONPATH 也能 `python -m hailuo_video` |
| D3 | [`auto_dev_console_encoding_draft.md`](auto_dev_console_encoding_draft.md) | ✅ 已实施 | A：入口重配 stdout | cp932 控制台下门禁不再崩溃，退出码可信 |

## 验证结果

| 项 | 结果 |
|---|---|
| `python -m pytest tests/ -q` | **986 passed, 12 skipped**（修复前 968，新增 18） |
| `python scripts/auto_dev.py --no-commit`（cp932，无环境变量） | tests ✅ secret_scan ✅ lint ✅ coverage 82.0% ✅，EXIT=0 |
| `script --backend mock` 端到端 | rc=0，产出 2 镜（真机） |
| 隔离 venv 内 `python -m hailuo_video --help`（无 PYTHONPATH，中性 cwd） | rc=0，16 个子命令全注册 |
| 既有回归（`test_director_mode_parser` / `test_prompts` / `test_story_arc`） | 39 passed |

## 共同根因

三个都不是单点写错，而是**「文档 / 报错文案」与「实现」之间缺少一致性约束**：

- D1：`cli.py` 的提示文案承诺了一条实现上走不通的路
- D2：README 的命令形式与实际包布局脱节
- D3：报告里隐含了「调用方要自己设编码」的前置条件

**建议加一道冒烟检查**：每个 CLI 子命令在 mock 后端下跑一次并断言退出码，
可同时挡住 D1/D2 这类「文档与实现漂移」。
本仓目前无 CI 配置，`docs/PRD.md` 也未列 CI 为门禁项，所以这只是建议，不是提案。

## 实施中发现、但**本次未修**的既有问题

按 CLAUDE.md §5「只修被报告的问题」，以下均已记录、留待你决定是否立项：

1. `novel_to_storyboard` 不填 `Storyboard.scenes` 与 `style_anchor`
   → mock 与真机路径都缺 V2.5 Layer 1（画风锚定）与场景变体，validate 会告警。
   详见 D1 文末「遗留建议」。
2. `_parse_shots_payload` 从不读 `shot_id`，LLM 产出的 `shot_id` 恒为空。
3. `MINIMAX_IMAGE_MODEL/ASPECT_RATIO/N/RESPONSE_FORMAT` 不在 `script_engine.py`
   的 `.env` 加载白名单里，**从 `.env` 配置这四项一直没生效过**（已在该处留 TODO）。
4. `pipeline.py` 把 MiniMax 的比例 env 传给任意 backend；若设了 `21:9` 再选
   `gemini` 会硬报错（fail-closed，但应让各 backend 读自己的 env）。
5. `hailuo_video.__version__` 仍是 `"2.0.0"`，与实际版本（V3.5/V3.7）不符。
   本次在 `pyproject.toml` 里引用它作为单一事实源，**未修改其值**。
6. `step_lint` 只扫 `sandbox/`，`tests/` 与 `scripts/` 不在语法检查范围。

## 说明

- 本目录下的文件**不被任何代码引用**，不影响运行与门禁。
- D2 的 `pip install -e .` **未执行**（会写入全局 site-packages，留给你决定）；
  其有效性已用一次性 venv 完整验证。