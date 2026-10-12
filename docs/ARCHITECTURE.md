# V3.4 Architecture — 反回归设计

> 创建于 2026-10-12（V3.3 W2 / W3 收尾）
> 目的：把"今天踩过的坑 + 修复方法 + 测试用例"系统化写下来，**避免下次再踩**。

---

## 1. 这次解决的核心问题

| # | 问题 | 触发表现 | 修复（ADR） |
|---|---|---|---|
| P1 | `pipeline.py:342` 硬编码 `aspect_ratio_value="16:9"` | 用户设 `MINIMAX_IMAGE_ASPECT_RATIO=9:16` env 完全无效 | **ADR-001** |
| P2 | editing phase 失败 → `PipelineResult.source="error"` 报 hard FAIL | motion.mp4 实际落盘可用，却被报错打入冷宫 | **ADR-002** |
| P3 | asset_prep character sheet prompt 漂移到卡通（"小黄变猫"事件）| style_anchor = "photorealistic" 但模板头硬写 "concept art style" → 模型挑简单实现 | **ADR-003** |
| P4 | scripts/ 下多个 ffmpeg 脚本重复 ~60% 代码 + 偏移手算 | `cinematic_upgrade.py` / `cinematic_upgrade_hailuo.py` / `_run_xiaohuang_pipeline.py` / `_recover_xiaohuang.py` 各写一份 | **ADR-004** |
| P5 | log.jsonl 几乎空（2 行 / run）| editing 失败根因完全不可追溯 | **ADR-005** |

---

## 2. ADR（Architecture Decision Records）

### ADR-001: aspect_ratio 必须来自 env，hardcode 视为 bug

**Date**: 2026-10-12
**Status**: Accepted

**Context**: V3.3 pipeline.py 第 342 行写死 `aspect_ratio_value="16:9"`，用户设 `MINIMAX_IMAGE_ASPECT_RATIO=9:16` 完全无效。这是 silent failure：用户的配置意图被悄悄忽略。

**Decision**:
1. pipeline.py 调用 `ie.aspect_ratio()` 读 env，不准再硬编码 `"16:9"` / `"9:16"` 字面量。
2. `ie.aspect_ratio()` 必须在 image_engine.py 显式 export，env 缺失时兜底 `"16:9"`（向后兼容）。
3. **测试**：扫描 pipeline.py 源码，断言不含 `aspect_ratio_value="..."` 字面量。

**Consequences**:
- ✅ 9:16、1:1 等任何比例都能用
- ⚠️ 如果 storyboard.json 也要存 aspect_ratio，需要扩展 storyboard schema

**Anti-pattern reference**: `tests/test_v34_regression.py::test_pipeline_does_not_have_hardcoded_aspect_ratio_string`

---

### ADR-002: pipeline editing phase 软失败语义

**Date**: 2026-10-12
**Status**: Accepted

**Context**: editing phase 失败时，旧版 `halt_on_error=True` 立刻返回 `PipelineResult(source="error")`，但 motion.mp4 已落盘可用。用户体验："看了 FAIL 以为全坏，重跑浪费 quota"。

**Decision**:
1. editing phase 失败时，先调 `_motion_mp4_fallback(intermediate_dir, output)` 找 motion.mp4 或 kenburns.mp4。
2. 找到 → 复制到 output → 返回 `PipelineResult(source="motion_fallback")`（**降级标记**）。
3. 都找不到 → 仍然 hard fail（行为不变）。
4. log 写 `phase_failed` + `editing_soft_success` 事件，便于追溯。

**Anti-pattern reference**: `tests/test_v34_regression.py::test_motion_mp4_fallback_copies_motion_to_output`（以及 action_falls_back_to_kenburns、returns_none_when_nothing）

---

### ADR-003: style_anchor 防漂移守卫

**Date**: 2026-10-12
**Status**: Accepted

**Context**: 2026-10-12 "小黄变猫"事件。story_arc.style_anchor 是写实摄影系，但 asset_prep character sheet prompt 同时有用户 style_anchor + 硬编码 "concept art style" → 模型挑简单的 "concept art style" 实现 → 输出橘色卡通玩具狗。

**Decision**:
1. `_safe_style_anchor(style_anchor)` helper：
   - 空值 → 兜底 `PHOTOREALISTIC_FALLBACK`（不再是 "concept art style"）。
   - 已含守卫词（"no illustration" / "no cartoon" / "no anime" / "no CGI"）→ 不重复追加。
   - 缺守卫词 → 末尾追加 `, no illustration no cartoon no anime no CGI`。
2. asset_prep.py 4 处 `style_anchor or "consistent clean concept art style"` 全部替换为 `style_anchor=_safe_style_anchor(style_anchor)`。
3. **测试**：4 个 case 覆盖（empty / partial / idempotent / concept_art guard）。

**Anti-pattern reference**: `tests/test_v34_regression.py::test_safe_style_anchor_*` (4 tests)

---

### ADR-004: scripts/lib_ffmpeg.py 抽公共 ffmpeg 库

**Date**: 2026-10-12
**Status**: Accepted

**Context**: scripts/ 下有 4 个脚本都重复：
- ffmpeg 调用 + 错误捕获
- probe mp4 时长/分辨率
- 9:16 转竖屏（`scale=-1:1920,crop=1080:1920:1164:0` 偏移手算）
- cinematic LUT 应用

每次新脚本 copy-paste，偏移从 1164 改到 1166 又改到 1180，**没有任何 SSOT**。

**Decision**:
1. 抽 `lib_ffmpeg.py`，导出 5 个核心函数：
   - `apply_cinematic_lut(mp4, out, lut)` — 给 mp4 加 LUT + 颗粒
   - `apply_cinematic_lut_by_shot_id(mp4, out, shot_id)` — 根据 shot_id 自动选 LUT_RECIPES
   - `to_vertical(mp4, out, target=(1080,1920))` — 转 9:16，自动算 crop 偏移
   - `concat_files(mp4_list, out)` — -c copy lossless 拼接
   - `probe_duration(mp4)` / `probe_resolution(mp4)` — 探测元数据
2. LUT_RECIPES 集中管理（6 镜暖色 / 冷色）。
3. scripts/cinematic_upgrade_hailuo.py 后续重构时改 import 此 lib。
4. **测试**：LUT_RECIPES 必须含 shot_001..shot_006 全部；to_vertical 偏移计算正确。

**Anti-pattern reference**: `tests/test_v34_regression.py::test_lib_ffmpeg_*` (4 tests)

---

### ADR-005: log.jsonl 完整化（V3.4 W3 待办，不在本次 commit）

**Date**: 2026-10-12
**Status**: Proposed

**Context**: log.jsonl 只记 `phase_skipped` + `phase_started`，phase_failed 时不记 ffmpeg rc / stderr 头。V3.3 W2 查 editing FAIL 根因只能靠 `2>&1 | tail` 抓 stdout。

**Decision** (待办):
1. `_run_agent_safe` catch block 写 `phase_failed` event 含 `agent` / `error_code` / `error_msg[:1024]` / `ffmpeg_rc`（如可获取）。
2. **测试**：mock ffmpeg 失败 → log 含 phase_failed + 错误码。

**Anti-pattern reference**: `tests/test_v34_regression.py::test_structured_logger_records_phase_failed_with_error`（已加，待 fallback 测完整链路）

---

## 3. 反回归四层机制（不要破）

### 第 1 层：每个 bug = 1 个回归测试

| Bug | 测试函数 |
|---|---|
| P1 aspect_ratio 硬编码 | `test_pipeline_does_not_have_hardcoded_aspect_ratio_string` |
| P1 env 解析失败 | `test_image_engine_aspect_ratio_helper` |
| P2 motion_fallback 不达 | `test_motion_mp4_fallback_copies_motion_to_output` |
| P2 fallback 顺序错 | `test_motion_mp4_fallback_falls_back_to_kenburns` |
| P2 fallback 漏 return None | `test_motion_mp4_fallback_returns_none_when_nothing` |
| P3 style_anchor 空 → cartoon | `test_safe_style_anchor_empty_uses_photorealistic_fallback` |
| P3 guard 重复追加 | `test_safe_style_anchor_idempotent_when_has_guard` |
| P3 缺 guard 不强制 | `test_safe_style_anchor_appends_guard_phrase_when_missing` |
| P4 lib_ffmpeg 缺失 | `test_lib_ffmpeg_has_apply_cinematic_lut` |
| P4 to_vertical 偏移错 | `test_lib_ffmpeg_has_to_vertical_with_crop_offset` |
| P4 LUT_RECIPES 缺失 | `test_lut_recipes_has_all_six_shot_ids` |
| P5 log 缺 phase_failed | `test_structured_logger_records_phase_failed_with_error` |

**未来规则**：任何新 bug fix → 必须配 1 个新测试（不放行无测试的修复）。

### 第 2 层：fail-fast 原则

- quota 跑前 hard check（V3.4 W3 待办）
- aspect_ratio env 缺失 → 用 `"16:9"` 兜底，不抛错（向后兼容）
- editing phase 失败但 motion.mp4 存在 → soft-success（ADR-002）

### 第 3 层：模板化（消除散落）

- scripts/ 下任何 ffmpeg 操作 → 必须用 `lib_ffmpeg.py`，不准再 inline
- asset_prep.py 任何 style_anchor 替换 → 必须用 `_safe_style_anchor()`

### 第 4 层：CI 硬约束（V3.4 W3 待办）

```
.githooks/pre-commit（待做）:
  - grep -rn 'aspect_ratio_value="[0-9]' sandbox/hailuo_video/*.py  # P1
  - grep -rn 'style_anchor or "consistent clean concept art' sandbox/hailuo_video/*.py  # P3
  - python -m pytest tests/test_v34_regression.py -q  # 全套回归测试
```

---

## 4. 不要重犯清单

| 不要再写 | 因为 |
|---|---|
| `aspect_ratio_value="16:9"` 硬编码 | P1 + 测试卡 |
| `style_anchor=style_anchor or "..."` | P3 + 测试卡 |
| `cinematic_upgrade_*.py` 重复 ffmpeg 调用 | P4 + lib 抽取 |
| `scale=-1:1920,crop=1080:1920:1164:0` 偏移手算 | P4 + to_vertical 函数 |
| log.jsonl 缺 phase_failed 详情 | P5 + 测试卡 |

---

## 5. 未来 roadmap（V3.4 W3+）

| 优先级 | 任务 | 估时 |
|---|---|---|
| 🟡 中 | quota_state.json 跟踪 + pipeline hard check | 30 min |
| 🟡 中 | log.jsonl 完整化（ADR-005） | 30 min |
| 🟡 中 | CI pre-commit hook 自动跑回归测试 | 15 min |
| 🟢 低 | character sheet 一致性 CLIP 检查 | 2h |
| 🟢 低 | picker fallback review mode（不是仅 warning） | 30 min |
| 🟢 低 | 重构 cinematic_upgrade_hailuo.py 用 lib_ffmpeg.py | 1h |
| 🟢 低 | 重构 run_story.sh motion.mp4 复制 hack（修根因） | 30 min |

---

## 6. 关键 commit 索引

| Commit | 范围 |
|---|---|
| `ec88db6` | V3.3 W2: scene_references + L1 hailuo profile + editing 修复 |
| (本次 V3.4) | aspect_ratio env + editing soft-success + style_anchor guard + lib_ffmpeg + 14 regression tests + ARCHITECTURE.md |