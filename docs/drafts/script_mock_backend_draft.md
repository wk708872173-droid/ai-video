# D1：`script --backend mock` 结构性不可用

> 状态：**已实施（2026-10-22）** —— 采纳选项 A，新增 `script_engine.CliMockBackend`。
> 实现记录与对本文的修正见文末「实施后修正」。影响范围：V2 `script` 子命令。

## 1. 现象（实测）

```
$ cd d:/ai-tools/ai_video && PYTHONPATH=sandbox python -m hailuo_video script novel.txt -o sb.json --shots 3 --backend mock
[SCRIPT] backend=mock（单测模式，不会调真 LLM）
[FAIL] pipeline 失败：AssertionError: MockBackend 无预设响应
$ echo $?
1
```

CLI 帮助文本宣传该模式可用：

- `cli.py:852` — `--backend {minimax,mock}`，help 写「mock=单测假数据」

## 2. 根因

`script_engine.py:642` 的 `MockBackend.chat()` 在没有匹配到预设响应时**故意**抛
`AssertionError`，注释写明「默认回退：让单测明显失败」：

```python
# script_engine.py:635-642
def chat(self, system: str, user: str, **opts: Any) -> str:
    self.calls.append({"system": system, "user": user})
    for k, v in self.responses.items():
        if k in system or k in user:
            return v
    # 默认回退：让单测明显失败
    raise AssertionError(f"MockBackend 无预设响应（system[:80]={system[:80]!r}）")
```

而响应只能通过 `MockBackend.on(key, response)` 注入。**全仓生产代码中 `.on()` 调用数为 0**
（`grep -rn "\.on(" sandbox/ scripts/` 无命中），只有测试文件在用：

- `tests/test_director_mode_parser.py:20,47,68` — 先 `.on("漫剧编剧", ...)`、`.on("角色设计师", ...)`、
  `.on("分镜师", ...)` 三条，再调 `novel_to_storyboard`

CLI 走的 `script_engine.py:837 make_backend("mock")` 直接 `return MockBackend()`（空 responses），
必然踩中那条 `AssertionError`。

**加重问题**：`cli.py:275` 的报错信息本身在推荐这条走不通的路——

> `[FAIL] MINIMAX_API_KEY 未设置或长度不足。先在 .env 填好，或用 --backend mock 跑通流程。`

用户按提示改用 `--backend mock`，仍然失败，只是换了个错误。这是文档、错误信息、实现三方不一致。

## 3. 影响

- 「不花钱、不调真 API 跑通流程」这条唯一路径对 `script` 环节断裂。
  `image --backend mock` 与 `tts --backend mock` 均正常（`MockImageBackend:263` /
  `MockTTSBackend:109` 都会真产出文件），**只有 LLM 这条不一致**。
- 阻碍离线 CI：任何想跑 `script → pipeline` 全链路 dry 冒烟的脚本都得先花钱或改代码。
- 新人按 README 走会先撞这里。

## 4. 修复选项

> **关键约束**：`MockBackend` 的「未匹配即抛错」是**被测试依赖的设计**，
> 直接给它加默认回退会让「忘了注册响应」的测试静默变绿，违反 CLAUDE.md §2 证据阶梯
> （测试必须真的验证到东西）。所以不能简单地在 `chat()` 里补默认分支。

### 选项 A（推荐）：新增 `CliMockBackend`，`make_backend("mock")` 保留严格语义

- 新增 `script_engine.CliMockBackend`，持有一份**预置的三角色 canned storyboard JSON**，
  按 system prompt 里的角色关键字（漫剧编剧 / 角色设计师 / 分镜师）返回对应 JSON。
- `make_backend` 增加 `prefer="cli-mock"`，`cli.py:270` 的 mock 分支改用它。
- `MockBackend` 一行不改 → 所有现有测试行为不变。
- 代价：需要产出 3 段结构合法的 JSON（约 40 行），且必须与 `novel_to_storyboard`
  的解析路径对齐（V3.5 已给 `Shot` 加了 12 个 director 字段，canned 数据要覆盖它们，
  否则下游 `_parse_shots_payload` 可能因缺字段降级）。

### 选项 B：诚实地从 CLI 摘掉 `script --backend mock`

- `cli.py:852` 的 choices 改为 `{minimax}`，help 注明「LLM mock 仅供单测，请用 `image/tts --backend mock` 冒烟」。
- 同时修 `cli.py:275` 的错误文案，去掉指向 mock 的那句。
- 代价最小、零技术风险；代价是失去了 `script` 的离线冒烟能力。

### 选项 C：`MockBackend` 加 `strict` 开关（默认 `True`）

- `MockBackend(strict=False)` 时走内置 canned 响应；`make_backend("mock")` 传 `strict=False`。
- 测试全部用默认 `strict=True`，行为不变。
- 代价：canned 数据仍要写，且让「mock 后端」有两种语义，长期易混。

## 5. 推荐

**选项 A**。理由：它同时修好「不可用」和「错误信息误导」两个问题，
并让 `script` 与 `image`/`tts` 的 mock 行为对齐（三个后端都能真出结果）。
若嫌 canned storyboard 维护成本高，退而选**选项 B**也完全可接受——
但无论选哪个，**`cli.py:275` 那句推荐 mock 的话都必须改**，它是本次发现里最直接的用户伤害。

## 6. 回归风险

- 选项 A/B/C 均不触碰 `MockBackend` 现有断言路径 → `tests/test_director_mode_parser.py` 等无风险。
- 若选 A，需补单测：mock 产出的 storyboard 能通过 `quality/validation.py:61 validate_storyboard()`
  （否则等于造了个不能用的假数据）。

## 7. 待你决策

1. 选 A / B / C 哪个？
2. 若选 A：canned storyboard 用哪套角色/场景作为固定样例？（建议直接复用
   `sample_storyboard_v8.json` 的角色设定，减少与现有样本不一致的困惑）
3. `cli.py:275` 的错误文案是否一并修改？（建议改，与选项无关）

## 证据等级

- 现象、根因、影响：**真机实测**（观察到的退出码 1 与完整 stderr）
- 「`.on()` 生产代码零调用」：静态分析（`grep` 全仓）
- 未验证：选项 A 的 canned 数据能否通过 `validate_storyboard`（未实施，自然未验证）
---

## 实施后修正（2026-10-22）

### 本文中一处**过强说法**的更正

第 4 节选项 A 写道「canned storyboard 能通过 `validate_storyboard()`（无 error）」，
第 5 节又顺带暗示可做到「无 warning」。**后半句不成立**，实测结果：

```
validate rc=0
⚠ [no_style_anchor]  storyboard.style_anchor 为空；V2.5 Layer 1 缺失 → 画风可能漂移
⚠ [scene_undefined] (shots[0..2]) shots[i].scene_id=1 在 scenes[] 找不到
```

- **无 error** —— 成立，这是本次要求的门槛，已由
  `tests/test_cli_mock_script.py::test_cli_mock_passes_validation_without_errors` 锁定。
- **无 warning** —— 不成立，且**不是 mock 的缺陷**。根因在
  `script_engine.py:913`：`_assemble` 只填 `title/characters/shots`，
  `Storyboard.scenes` 与 `style_anchor` 从未被 `novel_to_storyboard` 赋值。
  任何后端（含真 MiniMax）走这条路径都会得到同样两条告警。属既有管道缺口，另议。

### 实施中发现的三个坑（提案未预见）

1. **既有测试把 bug 当成了预期行为。**
   `tests/test_script_engine.py::test_cli_script_mock_backend_without_preset_fails`
   断言 `rc == 1` 且 stderr 含 `AssertionError` —— 正是本次要修的缺陷。
   已替换为 `test_cli_script_mock_backend_now_succeeds`（断言 rc=0 + 5 镜），
   并在 docstring 里写明「替换而非删断言换绿」。**这是行为变更，不是测试放宽。**

2. **`_parse_shots_payload` 要求镜头数精确相等**（`script_engine.py:609`
   「分镜数量不符：要求 N，LLM 返回 M」直接抛错）。
   故 `CliMockBackend` 不能靠构造参数猜数量，改为从 prompt 正则读回
   （`_N_SHOTS_IN_PROMPT`，匹配「拆为 N 个分镜」）。构造参数仅作兜底。

3. **`_parse_shots_payload` 从不读 `shot_id`**（`Shot` 有该字段，`__init__.py:218`），
   故 LLM 产出的 storyboard `shot_id` 恒为空。这是**既有解析缺口**，非本次引入，
   未修（超出 D1 范围）。canned 数据里仍保留 `shot_id` 并加注释，
   一旦解析补上即自动生效。

### 实际改动清单

| 文件 | 改动 |
|---|---|
| `sandbox/hailuo_video/script_engine.py` | 新增 `CliMockBackend` + 3 份 canned 数据 + `_N_SHOTS_IN_PROMPT`；`make_backend` 加 `"cli-mock"` 分支；`MockBackend` **一行未动** |
| `sandbox/hailuo_video/cli.py` | mock 分支改用 `make_backend("cli-mock")`；修 `:275` 报错文案 |
| `tests/test_cli_mock_script.py` | 新增 11 项（含 `MockBackend` 严格语义护栏） |
| `tests/test_script_engine.py` | 替换 1 项过时断言（见上） |

### 验证

- `python -m hailuo_video script novel.txt -o sb.json --shots 2 --backend mock` → **rc=0**，产出 2 镜（真机实测）
- `tests/test_cli_mock_script.py` → 11 passed
- 既有回归：`test_director_mode_parser.py` / `test_prompts.py` / `test_story_arc.py` → 39 passed
- 全量门禁：`986 passed, 12 skipped`（原 968，+18）

### 遗留建议（未做）

`novel_to_storyboard` 不填 `scenes`/`style_anchor`，导致 mock 与真机路径都缺
V2.5 Layer 1（画风锚定）与场景变体。可另立提案：让 `Script` 的 scenes 一并
落进 `Storyboard.scenes`，并支持从剧本提取 `style_anchor`。
