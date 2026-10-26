# 提案 D3：`scripts/auto_dev.py` 在 Windows 控制台崩溃

> 状态：**已实施（2026-10-22）** —— 采纳选项 A，入口处重配 stdout/stderr。
> 影响范围：dev 门禁脚本本身；不触及被测代码。实现记录见文末「实施后修正」。

## 1. 现象（实测）

```
$ python scripts/auto_dev.py --no-commit
...
945 passed, 12 skipped, 268 warnings in 10.22s
✅ Tests PASSED                    ← 这里崩了

Traceback (most recent call last):
  File "D:\ai-tools\ai_video\scripts\auto_dev.py", line 79, in step_tests
    print(f"✅ Tests PASSED")
UnicodeEncodeError: 'cp932' codec can't encode character '✅' in position 0: illegal multibyte sequence

During handling of the above exception, another occurred:

Traceback (most recent call last):
  File "D:\ai-tools\ai_video\scripts\auto_dev.py", line 293, in <module>
    sys.exit(main())
  File "D:\ai-tools\ai_video\scripts\auto_dev.py", line 266, in main
    print(f"\n❌ auto_dev 异常: {exc}")
UnicodeEncodeError: 'cp932' codec can't encode character '❌' in position 2: illegal multibyte sequence
```

加 `PYTHONIOENCODING=utf-8` 后**全绿**：

```
$ PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python scripts/auto_dev.py --no-commit
  tests ✅ PASS    secret_scan ✅ PASS    lint ✅ PASS    coverage ✅ PASS (82.0%)
✅ Auto Dev 通过
```

## 2. 根因

`auto_dev.py` 大量使用 emoji 做状态标记（✅ / ❌ / ⏭️ / 📋 / ⚠️），
而 Windows 控制台默认代码页是 cp932（GBK 系），无法编码这些码位。

崩溃点：
- 首次触发 `auto_dev.py:79` — `print(f"✅ Tests PASSED")`
- 兜底打印同样炸：`auto_dev.py:266` — `print(f"\n❌ auto_dev 异常: {exc}")`

注意**第二个 traceback 更糟**：异常处理器自己抛异常，导致脚本以 traceback 结束而非
返回可判读的退出码。也就是说，门禁一旦在 Windows 上跑到有 emoji 的分支，
**调用方拿不到可信的 rc**。

## 3. 影响

- `auto_dev.py` 是仓库唯一的门禁命令（CLAUDE.md / README 都指向它）。
- Windows 开发机上它**在最后一步崩溃**，尽管此时 tests 已通过。
  若崩溃发生在 Step 2/3 之前，`results` 汇总与退出码都不可信。
- 与 git log 的现象吻合：`455d704` / `4d4199e` / `542f507` 三条提交信息都是
  「代码格式化 + 测试通过 + 覆盖率达标」，说明历史上确实是在能正常跑完的环境里产生的——
  但当前 Windows 环境已复现崩溃，属于**环境回归**，不是最近才引入的代码缺陷。
- 连带影响：本次 Gemini 接入的验证也不得不加 `PYTHONIOENCODING=utf-8` 前缀。

## 4. 修复选项

### 选项 A（推荐）：脚本入口处统一重配 stdout/stderr

在 `auto_dev.py:19` 附近（`from __future__ import annotations` 之后、任何 print 之前）加：

```python
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):          # Python 3.7+
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass
```

- 一处改动覆盖全部 emoji 输出与异常处理器。
- `errors="replace"` 保证再遇到无法编码的字符时**降级为替换字符而不是抛异常**，
  门禁永远不会因为一个字符失去退出码。
- 代价：几乎为零。

### 选项 B：把 emoji 换成 ASCII 标记

- ✅ → `[OK]` / `[PASS]`，❌ → `[FAIL]`，⏭️ → `[SKIP]` 等。
- 代价：要改几十处；且牺牲可读性（现有报告在正常终端里很好看）。
- 附带好处：任何编码环境都能跑。

### 选项 C：只改异常处理器 + 要求调用方设环境变量

- 在 README / CLAUDE.md 里写明「Windows 下须设 `PYTHONIOENCODING=utf-8`」。
- 代价：把环境责任推给每个调用方；CI 或新人不设就直接踩。
- 不推荐——这是本次实测已经验证过的「用户需要知道的隐藏前置条件」，
  和提案 D2 是同一类毛病。

## 5. 推荐

**选项 A**，并可与 **选项 B 择一叠加**（若你更看重跨环境鲁棒性）。

理由：

1. 一处改动解决全部 emoji 与异常处理器路径；
2. `errors="replace"` 从机制上保证「门禁脚本不会因编码问题丢失退出码」——
   这比「让 emoji 能显示」更重要；
3. 不牺牲现有报告的可读性。

## 6. 回归风险

- 极低。`tests/test_auto_dev.py`（25 项，10-21 新增）测试的是 `step_*` 函数的
  返回值与门禁决策逻辑，不捕获 stdout，理论上不受影响。
- **但需实测确认**：`step_secret_scan` / `step_lint` 是否有测试依赖 stdout 文本。
  实施后应跑 `python -m pytest tests/test_auto_dev.py -q` 复核。

## 7. 待你决策

1. 选 A（推荐）/ B / C，还是 A+B 都做？
2. 是否顺带在 `scripts/README.md` 记录 Windows 运行前置（若选 A 就不必要了）？

## 证据等级

- 崩溃现象、traceback 位置、加环境变量后全绿：**真机实测**（完整 traceback + 退出输出）
- 「历史提交信息表明曾在正常环境跑完」：静态证据（git log commit message），非严格证明
- 未验证：选项 A 落地后 `tests/test_auto_dev.py` 是否仍全绿（未实施）
- 明确未验证：`auto_dev.py` 在 CI（若未来接入）环境下的行为——仓库目前无 `.github/` CI 配置
---

## 实施后修正（2026-10-22）

状态：**已实施** —— 采纳选项 A，原样落地于 `scripts/auto_dev.py` 入口
（`sys` import 之后、`REPO` 定义之前），6 行，对所有 emoji 输出与异常处理器一次性生效。

### 实际改动

```python
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # Python 3.7+
    except (AttributeError, ValueError, OSError):
        pass
```

比提案多加了 `AttributeError` 到 except 元组：非标准流（被测试用
`capsys` 替换成无 `reconfigure` 的对象）不应因此中断脚本。

### 验证（关键：必须在 cp932 控制台、且不设任何环境变量）

```
$ unset PYTHONIOENCODING PYTHONUTF8
$ python -c "import sys; print(sys.stdout.encoding)"
cp932
$ python scripts/auto_dev.py --no-commit
  ✅ Tests PASSED / ✅ No secrets detected / ✅ All 43 Python files compile clean
  ✅ Coverage 82.0% >= 80.0%
  ✅ Auto Dev 通过          ← 修复前在此崩溃并连带 traceback
EXIT=0
```

emoji 在 cp932 下正常显示，且异常处理器不再二次抛出——**退出码可信**这一
（提案第 2 节强调的）目标达成。

### 实施中发现的关联事项

**1. 门禁第一次跑就抓到 D1 的回归**（好事）：修好 `--backend mock` 后，
`tests/test_script_engine.py::test_cli_script_mock_backend_without_preset_fails`
红了——它断言的正是本次要修的缺陷。已替换，见 D1 提案「实施中发现的坑 1」。
说明门禁确实在生效，不是摆设。

**2. `step_lint` 只扫 `SANDBOX.rglob("*.py")`**（`auto_dev.py` 现 L138），
`tests/` 与 `scripts/` 不在语法检查范围内。所以本次新增的
`tests/test_cli_mock_script.py` / `tests/test_packaging.py` 不计入那句
「All 43 Python files compile clean」。实际风险为零（pytest 收集时即会编译），
但「门禁覆盖范围」与直觉不符，值得知会。
如需收口可另议：把 `tests/`、`scripts/` 一并纳入 lint 范围。
