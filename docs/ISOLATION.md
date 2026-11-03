# V5.0 Architecture — Project 隔离设计

> 创建于 2026-10-14（V3.4 W3 rerun 成功之后）
> 目的：保证**切换剧本/角色时输出稳定、互不污染**，支持持续完成多人任务。
>
> **2026-11-03 更新**：标题随架构版本升到 V5.0。**隔离设计本身自 V3.5 起未变**
> （V5.0 的全部改动都在隔离之外：路由层 / 人机协同 / 配额账本 / 角色契约 /
> 提示词版本 / 工具注册表 / Benchmark —— 详见 `docs/AGENT_FRAMEWORK.md`）。
> （ADR-007 的 namespace 派生、env_overrides、cleanup 语义都保持原样）；
> V4.0 只是在隔离之上补了全局角色圣经（`sandbox/assets/character_bible/`，**刻意在
> 项目 namespace 之外** → cleanup 不会误删）。

---

## 0. 问题陈述

2026-10-13 "小黄变猫"事件根因之一是**角色 cache 跨剧本污染**（卡通 sheet 影响后续 hailuo 输出）。

更深层问题是：**当前架构没有"项目"边界** — `DEFAULT_ASSETS_DIR = Path("./assets")`, `run_dir = "./run"` 等都是**全局相对路径**。

实际跑 A 剧本之后跑 B 剧本时：
- `sandbox/assets/characters/<name>/` 缓存可能撞名
- `sample/run_<storyboard>` 同名覆盖
- `sandbox/.state.json` 串台
- `MINIMAX_IMAGE_ASPECT_RATIO` 等 env 跨任务共享

**未来用户期望**：跑完 A 剧本跑 B 剧本（不同角色/不同环境），中间 0 干预。

---

## 1. 设计目标

| # | 目标 | 验收 |
|---|---|---|
| G1 | **角色强隔离**：A 剧本的 character sheet 不能被 B 剧本读 | 自动化测试 |
| G2 | **run_dir 强隔离**：每次跑自己的 registry/state | 自动化测试 |
| G3 | **env 强隔离**：每个项目独立 env（值）启动结束自动恢复 | 自动化测试 |
| G4 | **跨项目幂等**：相同 project_id 复用 cache，不同 project_id 强制新建 | 自动化测试 |
| G5 | **fail-fast**：跨项目数据冲突时硬失败，不静默错乱 | 自动化测试 |

---

## 2. 核心抽象：ProjectContext

```python
@dataclass(frozen=True)
class ProjectContext:
    """V3.5 ADR-007：项目级隔离上下文。

    设计目标：每个"任务"（剧本 + 角色 + 配置 + 输出）= 一个 ProjectContext 实例
    → cache/run_dir/env/output 全路径硬隔离
    """
    project_id: str        # sha1(storyboard 内容)[:8]，自动派生
    project_name: str      # 人类可读：xiaohuang / orange_cat / ...
    storyboard_path: Path
    storyboard_hash: str   # sha256 hex

    # 时空隔离
    assets_dir: Path       # sandbox/assets/{project_id}/
    run_dir: Path          # sample/run_{project_id}/
    output_dir: Path       # sample/{project_name}/
    log_path: Path         # logs/{project_id}_session.jsonl

    # 风格与配置（项目级）
    style_anchor: str
    aspect_ratio: str      # "16:9" / "9:16"
    motion_budget: int     # quota 跟踪
    pipeline_version: str  # "V3.5"

    # env 覆盖（context manager 自动 apply/restore）
    env_overrides: dict[str, str] = field(default_factory=dict)
```

---

## 3. ADR-007: Project 强隔离

### ADR-007-A: cache namespace = `{project_id}`

**Context**: `sandbox/assets/characters/<name>/` 路径只用 name，两个项目角色撞名就撞库。

**Decision**:
- 路径改为 `sandbox/assets/characters/{project_id}/{character_name}/`
- `project_id = sha1(storyboard_hash)[:8]`
- 同一故事复用 cache；不同故事**自动隔离**
- 手动 `--project-id <custom>` 覆盖

### ADR-007-B: run_dir namespace = `run_{project_id}_{shot_id}`

**Decision**:
- `run_dir = sample/run_{project_id}_{shot_id}/`
- registry.json 也按 project_id 命名空间
- 不再用 `./run` 默认值（fail-fast 如果未提供 project_id）

### ADR-007-C: env override via context manager

**Decision**:
- `ProjectContext` 进入时 apply env_overrides 到 os.environ
- 退出时自动 restore
- 跨项目调用不污染 shell env
- 默认 env_overrides 至少包含：
  - `MINIMAX_IMAGE_ASPECT_RATIO` = ctx.aspect_ratio
  - `MINIMAX_VIDEO_RESOLUTION` = "1080P"

### ADR-007-D: state.json registry isolation

**Decision**:
- `sandbox/.state.json` 改为 `sandbox/.state/{project_id}.json`
- 每个 ProjectContext 实例化时 load 自己 state
- 不再用全局 singleton

---

## 4. 实施计划

| Step | 内容 | 估时 |
|---|---|---|
| 1 | 写 `sandbox/hailuo_video/project.py`（ProjectContext + ProjectRegistry） | 30 min |
| 2 | 改 `asset_prep.py`：路径加 `{project_id}/` namespace | 20 min |
| 3 | 改 `pipeline.py` + `orchestrator.py`：PipelineConfig 默认从 ProjectContext 派生 | 20 min |
| 4 | 改 `cli.py`：`pipeline` 子命令加 `--project-id` `--auto-cleanup` | 15 min |
| 5 | 写 6 个隔离测试 | 30 min |
| 6 | 跑全量 + commit | 10 min |

---

## 5. 测试矩阵（每个目标 1+ 个）

| 测试 | 验证目标 |
|---|---|
| `test_project_id_derives_from_storyboard_hash` | project_id 自动派生稳定 |
| `test_character_cache_namespaced_by_project_id` | G1: A/B 项目不撞角色 cache |
| `test_run_dir_namespaced_by_project_id` | G2: 每次跑自己的 run_dir |
| `test_env_override_restores_after_context` | G3: env apply/restore |
| `test_state_registry_isolated_per_project` | G3/G4: state 互读 |
| `test_unknown_project_id_fails_fast` | G5: 未提供 project_id 立刻硬失败 |
| `test_run_xiaohuang_does_not_infect_run_other` | 端到端隔离 |

---

## 6. 防回归机制（与 V3.4 一致）

- 每个 bug 配 1 个 test（**至少 6 个** V3.5 测试卡死）
- fail-fast 原则（run_dir 默认值直接 hard fail）
- 模板化（ProjectContext 是 lib，单点入口）
- 文档化（ISOLATION.md + ADR）

---

## 7. 与现有架构的关系

| 已有 | V3.5 调整 |
|---|---|
| `agents.py` `assets_dir=Path("./assets")` | 改为从 ProjectContext 读 |
| `pipeline.py` `run_dir="./run"` | 改为 ProjectContext.run_dir（必填） |
| `orchestrator.py` `_resolve_resume_index` | 不变（依赖 registry.json 路径，路径已 namespace） |
| `cli.py` `--run-dir` 默认值 | 默认自动 derive project_id → 自动 run_dir |
| `state.py` 全局 singleton | 改为 ProjectRegistry 注册多 state |

---

## 8. Backward Compatibility

- V3.4 时代调用方（run_story.sh 等）继续可用：
  - 没传 `--project-id` → 从 storyboard.json 派生
  - 默认 run_dir 自动算（无 manual）
- 旧 `run_dir = "./run"` 调用方：
  - 报错 `UnknownProjectIdError` 提示 `--project-id`
- 旧 sample/run_* 目录继续可读（registry.json 仍兼容）

---

## 9. 决策记录（ADR-007 子项）

- **ADR-007-A** project_id = sha1(storyboard 内容)[:8]
- **ADR-007-B** assets_dir / run_dir 加 project_id namespace
- **ADR-007-C** env override via context manager（自动 restore）
- **ADR-007-D** state.json registry 独立 per project
- **ADR-007-E** fail-fast 默认（不允许 `run_dir = "./run"` 隐式默认）