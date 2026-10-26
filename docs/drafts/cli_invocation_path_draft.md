# 提案 D2：README 记录的命令从仓库根跑不起来

> 状态：**已实施（2026-10-22）** —— 采纳选项 A，新增 `pyproject.toml` 并更新 README。
> 影响范围：所有面向人的文档；不改运行时行为。实现记录见文末「实施后修正」。

## 1. 现象（实测）

```
$ cd d:/ai-tools/ai_video
$ python -m hailuo_video ping
C:\Python314\python.exe: No module named hailuo_video     # rc=1
```

而 README 正是这样写的（4 处，均照抄即失败）：

- `README.md:62` — `python -m hailuo_video prep my_storyboard.json --assets-dir ./assets`
- `README.md:65` — `python -m hailuo_video pipeline my_storyboard.json -o final.mp4 \`
- `README.md:110` — `python -m hailuo_video --help`
- `README.md:163` / `:173` / `:186` / `:193` — 手工分步流程里的 `script` / `image` / `tts` / `concat`

## 2. 根因

包位于 `sandbox/hailuo_video/`，而 `sandbox/` **没有**被安装进解释器：

```
$ python -c "import importlib.util as u; s=u.find_spec('hailuo_video'); print(s.origin)"
NOT installed
```

仓库**没有任何 `pyproject.toml` / `setup.py` / `requirements.txt`**
（全仓 glob 无命中），因此 `python -m <pkg>` 的发现机制找不到它。

实测两种可行写法：

| 写法 | 结果 |
|---|---|
| `PYTHONPATH=sandbox python -m hailuo_video ping` | ✅ |
| `cd sandbox && python -m hailuo_video ping` | ✅ |
| `python -m hailuo_video ping`（仓库根） | ❌ `No module named hailuo_video` |

`sandbox/hailuo_video/__main__.py:35-37` 里的 `sys.path` 注入只对**包自己内部**生效
（即 `cd sandbox` 后再 `-m`），对「从外部找到这个包」无帮助。

## 3. 影响

- README 是新人的第一入口，第一条命令就失败 → 信任成本。
- 任何按文档写的自动化脚本 / 交接文档 / 教程都会踩。
- 容易误判成「装坏了」或「Python 版本不对」，排查成本高。
- 与 `docs/DEPLOY_TO_MAC.md:99` 的安装说明共同构成安装路径缺口。

## 4. 修复选项

### 选项 A（推荐）：加 `pyproject.toml`，走标准可编辑安装

- 新增根目录 `pyproject.toml`，声明 `packages = ["sandbox/hailuo_video"]`（或 `package-dir` 映射）。
- 安装一次 `pip install -e .` 后，README 现有命令**一字不改**即可用。
- 顺带解决另一件长期欠账：项目至今没有依赖声明（`README.md:52` 口头写
  `pip install edge-tts imageio-ffmpeg`），`pyproject.toml` 可一并声明
  `edge-tts` / `imageio-ffmpeg` / `Pillow` / `requests` / `pytest`。
- 代价：新增一个文件 + 一次安装步骤；需要确认不会与 `sandbox/` 这个非常规包布局打架
  （包路径含 `sandbox` 一段，需用 `tool.setuptools.package-dir` 显式映射）。

### 选项 B（最小改动）：README 明确前置条件

- 在 README 工作流章节顶部加一段「运行前」说明，并统一改写 4+ 处命令为
  `PYTHONPATH=sandbox python -m hailuo_video ...`，或注明「先 `cd sandbox`」。
- 代价：零代码风险；但命令变长，且仍然不便于自动化调用。
- 也可加一个根目录 `run.bat` / `run.sh` 包一层，文档里改用 `.\run.bat image ...`。

### 选项 C：根目录放 `hailuo_video.py` 薄壳

- 一个转发到 `sandbox.hailuo_video.cli` 的单文件入口。
- 代价：多一个影子入口，sys.path 仍有隐式魔法；不推荐。

## 5. 推荐

**选项 A**，理由：

1. 它让 README **一字不改**就正确，是唯一能消除「文档与现实不一致」的方案；
2. 它顺带补上项目最大的结构性欠账之一——无依赖声明（新增 provider 时尤其明显，
   本次接 Gemini 也只能继续靠 stdlib urllib）；
3. 与仓库既有约定不冲突：README:52 已用 `pip install` 表述，说明本就走 pip 路线。

若你现在不想引入打包配置，退而选**选项 B**也能立刻消除困惑，但它治标不治本，
且下次新增依赖时还会再撞一次。

## 6. 回归风险

- 选项 A 需确保 `pip install -e .` 后 `python -m hailuo_video` 与现有 `cd sandbox` 写法**都**可用，
  且 `scripts/*.py` 里那些自己 `sys.path.insert(sandbox)` 的写法（如 `tests/` 的 bootstrap）不受影响。
- 注意：`assets/`、`intermediate/`、`logs/` 等运行时目录当前是相对 cwd 的
  （`asset_prep.py:37 DEFAULT_ASSETS_DIR = Path("./assets")`）。若引入安装后 cwd 行为变化，
  需确认资产缓存路径不被改变。**这是选项 A 最需要实测的一条**。

## 7. 待你决策

1. 选 A / B / C？
2. 若选 A：依赖清单现在一并写进 `pyproject.toml`，还是只声明包、依赖另议？
3. 是否要顺带修 `docs/DEPLOY_TO_MAC.md` 的安装章节？

## 证据等级

- 现象、两种可行写法、不可安装：**真机实测**（观察到的退出码与错误信息）
- 「无 pyproject/setup.py/requirements」：静态分析（全仓 glob）
- 未验证：选项 A 安装后 cwd 相关的资产路径行为（未实施，未跑）
- 已确认不受影响：`scripts/auto_dev.py` 通过 `"tests"` 字面量 + cwd 调用 pytest，
  不受包安装方式影响（静态阅读 `auto_dev.py:64-79`）
---

## 实施后修正（2026-10-22）

状态：**已实施** —— 采纳选项 A（`pyproject.toml` + `pip install -e .`），README 已更新。

### 本文一处错误的更正

第 2 节把 `requests` 列为「顺带解决」的依赖之一。**它不是依赖**：
全仓 `requests` 只出现在 `mcp_client.py:169` 的说明文字里，从未被 import。
最终 `pyproject.toml` 只列 `pillow` / `imageio-ffmpeg` / `edge-tts` 三项核心依赖，
`torch`/`diffusers`/`numpy` 归入可选 `local-video` extra（它们在 `doctor.py` /
`local_client.py` 里都在 `try` 块内 import，缺失时走「环境未就绪」硬失败）。

### 第 6 节标记为「最需要实测」的 cwd 风险 —— 已排除

安装后包位于 `site-packages/hailuo_video`，但 `asset_prep.py:37`
`DEFAULT_ASSETS_DIR = Path("./assets")` 仍相对 **cwd** 解析，未被重定向到包内。
实测（venv 内、与仓库无关的中性目录）：

```
DEFAULT_ASSETS_DIR = assets (相对 cwd: 是)
cwd                = ...\Temp\d2a
包所在             = ...\hv_venv\Lib\site-packages\hailuo_video
```

`check-assets` 在目录缺失时给出的补救建议也正确地展开成了绝对路径。
**结论：安装不改变任何运行时路径语义。**

### 实施中踩到的坑（提案未预见）

**只写 `dynamic = ["version"]` 不够，必须同时声明读取位置。**
缺 `[tool.setuptools.dynamic] version = { attr = "hailuo_video.__version__" }` 时，
wheel 会**静默**打出 `hailuo_video-0.0.0`，不报任何错——最难发现的一类失败。
已补齐并由 `tests/test_packaging.py::test_version_is_read_from_dunder_init` 锁死。

另注：`hailuo_video.__version__` 当前是 `"2.0.0"`，与项目实际版本（V3.5/V3.7）不符。
因无人消费该值，**本次只引用不修改**，避免凭空发明版本号。可另立小提案校正。

### 实际改动清单

| 文件 | 改动 |
|---|---|
| `pyproject.toml` | 新建。`package-dir={""="sandbox"}` + `dynamic.version` + 三项核心依赖 + `local-video` extra + `testpaths` |
| `README.md` | 快速上手第 1 步从 `pip install edge-tts imageio-ffmpeg` 改为 `pip install -e .`，并说明原因；补 mock 冒烟三例 |
| `tests/test_packaging.py` | 新增 7 项（包发现、版本解析、依赖与真实 import 对齐、runtime 不误打包） |

### 验证

用**一次性 venv** 装 wheel 验证，全程未改动用户的全局 site-packages：

- 中性 cwd（`/tmp`）、**不设 PYTHONPATH** → `python -m hailuo_video --help` 列出全部 16 个子命令，rc=0
- `ping` 可执行；`script --backend mock` 端到端 rc=0（D1 与 D2 串通）
- wheel 内容：47 文件，含 3 个真包，**不含** `runtime/` 与 `tests/`
- `tests/test_packaging.py` → 7 passed
- 全量门禁 → `986 passed, 12 skipped`

### 用户侧动作

```bash
cd d:/ai-tools/ai_video && pip install -e .
```
（本次**未**执行该命令——它会写入全局 site-packages，留给你决定；上面已用隔离
venv 证明其有效。）
