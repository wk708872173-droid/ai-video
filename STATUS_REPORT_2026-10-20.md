# 项目开发进展报告 — 2026-10-20

> **项目**：ai_video · RMAP（Reference-Matrix Asset-First Pipeline）
> **周期**：2026-09-29（首次 commit）→ 2026-10-20
> **累计**：21 commits · 29 个核心模块 · 36 个测试文件

---

## 一、一句话总结

本周完成 **V3.5 Director 升级（Phase A）** 并成功落地**首个 0 角色纯风景视频项目（青岛秋日 3 镜）**，代码库达到 **811 测试通过 / 覆盖率 78%** 的可交付状态，同时把项目推送到 GitHub 完成版本固化。

---

## 二、本期核心交付（2026-10-19 ~ 10-20）

### 2.1 V3.5 Director 升级 Phase A — LLM 从"编剧"升级为"专业导演"

| 模块 | 行数 | 作用 |
|---|---|---|
| `prompts_director.py` | 367 | 导演级 prompt 模板 + 5 组白名单常量 |
| `script_engine.py`（扩展） | +200 | `Shot` 新增 **12 个导演字段**（lens / depth_of_field / lighting / screen_direction / color_grade / transition_in/out / pacing / bgm_cue / hook / narrative_beat / emotion_arc_point） |
| `audit_llm.py` | 256 | LLM 全链路审计：Request → Payload → Response，凭据自动脱敏 |
| `agents.py` | 814 | Agent 编排层 |
| `project.py` | 309 | `ProjectContext` 项目强隔离（ADR-007） |
| `quality/frame_quality.py` | 408 | 帧级质量：SSIM / 亮度 / 一致性 |
| `retry.py` | 58 | 重试策略 |

**设计原则（关键决策）**：
- **零新增 LLM 调用**（Phase A = 0 次，Phase B 才 +2~3）
- **默认 OFF**（feature flag `HAILUO_DIRECTOR_MODE`）
- **完全向后兼容**（新字段默认值不破坏现有 day1-3 JSON）
- **白名单 lenient 校验**（值不匹配 → 回退空串，不抛错）

**当前价值定位**：`hailuo 2.3` 目前只消费 `description / subject_action / camera_motion` 三个字段，因此 12 个导演字段**在生成端尚未产生实际影响**，主要收益是**人工审稿时可发现不一致**。真正生效需 Phase B（见第五节）。

### 2.2 青岛秋日 3 镜视频项目（0 角色纯风景）

首次验证**「0 角色」模式**（`characters=[]` + `scenes=[]`），成功规避了此前发现的 *hailuo 2.3 双角色颜色合并*限制。

| 交付物 | 规格 | 用途 |
|---|---|---|
| `final/qingdao_autumn_day1_9x16_xiaohongshu.mp4` | 1080×1920 · 18s · 7.1 MB | 小红书主推 |
| `final/qingdao_autumn_day1_16x9.mp4` | 1920×1080 · 18s · 7.5 MB | 横屏备用 |
| `final/cover_day1_9x16.png` | 1080×1920 · 2.1 MB | 竖屏封面 |

**3 镜叙事**：栈桥·晨曦·海鸥（起）→ 八大关·秋日小径（承）→ 老城·黄昏·崂山（合）

**过程中发现并修复的 pipeline 缺陷**：
> `motion_complexity: "static"` 会触发 pipeline **静默跳过 hailuo 调用**，直接降级 Ken Burns（产出 0 字节文件但报 `[OK]`）。修复：`static` → `simple`。此缺陷已记录至 story arc README，建议后续在 orchestrator 补一条显式日志。

### 2.3 工程基建

| 项 | 交付 |
|---|---|
| **一键门禁** | `scripts/auto_dev.py`（5 步：tests → secret scan → syntax → coverage → auto commit） |
| **Mac 部署** | `deploy_to_mac.sh` + `setup_on_mac.sh` + `DEPLOY_TO_MAC.md` |
| **架构文档** | `docs/` 4 份：ARCHITECTURE / PRD / ISOLATION / DEPLOY_TO_MAC |
| **方法论文档** | `FRAMEWORK.md`（RMAP 三大一致性层 + 4 轴 Picker 决策矩阵） |
| **GitHub 同步** | 推送至 `wk708872173-droid/ai-video`，打 tag `qingdao-autumn-day1-2026-10-19` |

---

## 三、质量门禁实测结果（2026-10-20 现场跑）

| 门禁步骤 | 结果 | 数据 |
|---|---|---|
| Tests | ✅ PASS | **811 passed, 12 skipped**（13.26s） |
| Secret scan | ✅ PASS | No secrets detected in diff |
| Syntax check | ✅ PASS | 44 Python files compile clean |
| Coverage | ❌ **FAIL** | **78.0% < 80.0% 目标线** |
| Auto commit | ✅ PASS | — |

**代码量**：核心源码 10,400 行 / 测试代码 11,593 行（**测试代码量 > 源码量**，比 1.11:1）

---

## 四、路线图进度（对照 `升级路线.md`）

| 阶段 | 周期 | 关键里程碑 | 状态 |
|---|---|---|---|
| 阶段一 | W1 | Hailuo 4 步跑通 + 单条一致性 | ✅ 已完成 |
| 阶段二 | W2–W4 | 3 镜头端到端 + EditingAgent 全子功能 | ✅ 已完成（色彩归一化 + xfade + motion_match + LUT + full pipeline） |
| 阶段三 | W5–W6 | 每日自动 3 条 + YouTube 发布 | 🟡 **部分完成** |
| 持续优化 | W6+ | 参数库 / 反馈闭环 / 成本控制 | ⏸ 未启动 |

**阶段三待办**：
- `daily_cron.py` + Windows Task Scheduler 脚本 ✅ 已就绪，**但未做真实端到端验证**
- `publish_agent.py` ABC + Mock ✅ / YouTube 真后端 ⏸ **待 OAuth 凭据**（鉴权域硬失败，不允许降级，符合 CLAUDE.md §3）

---

## 五、风险与待办

### 🔴 高优先级

| 项 | 描述 | 建议 |
|---|---|---|
| **覆盖率未达标** | 78.0% < 80.0% 门禁线，`auto_dev.py` 每次都会 FAIL | 下期专项补测试（`agents.py` 814 行 / `project.py` 309 行是主要缺口） |
| **YouTube 发布链路未通** | 缺 OAuth 凭据，真实上传成功率未验证 | 需申请凭据后才能验收阶段三 |

### 🟡 中优先级

| 项 | 描述 | 建议 |
|---|---|---|
| **Director 字段未生效** | 12 个导演字段 hailuo 不消费，仅服务人工审稿 | 启动 **Phase B**（`agents_director.py` + 3 个角色 Agent），把 director 信息注入 hailuo prompt |
| **`motion_complexity: "static"` 静默降级** | pipeline 无任何告警，需读 registry.json 才能发现 | 在 orchestrator 加显式 `[WARN]` 日志 |
| **9:16 环境变量失效** | `MINIMAX_IMAGE_ASPECT_RATIO=9:16` 未生效（image 仍出 16:9） | 排查 `image_engine.aspect_ratio()` 读取链路 |
| **自动 commit 副作用** | `auto_dev.py` 在 coverage FAIL 情况下仍执行 auto commit | 加 `--no-commit` 开关，或仅在 5 步全绿时提交 |

### 🟢 已解决

- ✅ hailuo「小黄」角色名歧义（改用"中华田园犬幼犬"后 3 镜全为狗，0 误出猫）
- ✅ 双角色颜色合并（改用 0 角色模式规避）
- ✅ aspect_ratio 硬编码 bug（V3.4 ADR-001）
- ✅ 帧级质量门禁（SSIM gate + 亮度检测）

---

## 六、下期计划建议

1. **补覆盖率到 80%+**（当前 78%，缺口约 2 个百分点，重点在 `agents.py`）
2. **启动 Director Phase B** — 让 12 个导演字段真正影响画面输出
3. **修复 `motion_complexity: "static"` 静默降级** + 加显式告警
4. **修复 9:16 环境变量** — 影响小红书主推版直出
5. **补 YouTube OAuth 凭据** → 打通阶段三验收
6. **内容侧**：Day 2 故事线（青岛"自然 vs 人文"对比）+ 6 镜扩展版

---

## 附录 A：核心指标

```
commits:          21（2026-09-29 → 2026-10-20）
核心模块:         29 个 .py
测试文件:         36 个（811 passed / 12 skipped）
源码行数:         10,400
测试行数:         11,593
覆盖率:           78.0%（目标 80%）
门禁总耗时:       30.1s
门禁通过率:       4/5（coverage 未过）
```

## 附录 B：本期产出物清单

```
sandbox/hailuo_video/          29 个模块（含 quality/ 子包 7 文件）
tests/                         36 个测试文件
docs/                          ARCHITECTURE / DEPLOY_TO_MAC / ISOLATION / PRD
FRAMEWORK.md                   RMAP 方法论
升级路线.md / 模型架构.md       路线图 + 架构说明
sample/story_arc_qingdao_autumn/   本期视频项目（storyboard + final + archives）
sample/story_arc_xiaohuang_daily/   历史项目（Day 1-3）
```

---

*报告生成时间：2026-10-20*
*数据来源：`git log` / `pytest tests/ -q` / `scripts/auto_dev.py` / 文件系统实测*