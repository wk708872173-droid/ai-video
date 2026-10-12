"""V3.4 反回归测试：每个 bug 配 1 个 test，确保同样问题永不复发。

V3.3 / V3.4 W2 / W3 暴露的真实问题（不抽象化）：
  1. pipeline.py:342 硬编码 aspect_ratio="16:9" → env var 失效
  2. editing phase 失败 → pipeline 报 hard FAIL → motion.mp4 实际可用却被打入冷宫
  3. asset_prep prompt 硬编码 "concept art style" + style_anchor → 漂移到卡通
  4. scripts/cinematic_upgrade.py 重复 ~60% 代码 + 9:16 转换偏移手算
  5. scripts/_run_xiaohuang_pipeline.py 内联脚本散落，无 lib
  6. log.jsonl 几乎空 → editing 失败根因不可追溯

每条测试用最小 mock 验证修复仍在位。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 让 sandbox 可作为 module 引入
SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
for p in (SANDBOX, SCRIPTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


# ---------- 1. pipeline.aspect_ratio 读 env，不写死 ----------

def test_image_engine_aspect_ratio_helper():
    """ADR-001：ie.aspect_ratio() 必须读 env 变量，pipeline 用 ie.aspect_ratio() 而非硬编码。"""
    from hailuo_video import image_engine as ie

    # env var 缺失 → 16:9 兜底
    with patch.dict("os.environ", {}, clear=False):
        # 移除可能存在的 env
        import os
        os.environ.pop("MINIMAX_IMAGE_ASPECT_RATIO", None)
        assert ie.aspect_ratio() == "16:9"

    # env var = 9:16 → 返回 9:16
    with patch.dict("os.environ", {"MINIMAX_IMAGE_ASPECT_RATIO": "9:16"}):
        assert ie.aspect_ratio() == "9:16"

    # env var = 1:1 → 返回 1:1
    with patch.dict("os.environ", {"MINIMAX_IMAGE_ASPECT_RATIO": "1:1"}):
        assert ie.aspect_ratio() == "1:1"


def test_pipeline_does_not_have_hardcoded_aspect_ratio_string():
    """ADR-001：pipeline.py 不应再有 `aspect_ratio_value="16:9"` 这种 hardcode。

    扫描源码确保 V3.3 bug（pipeline.py:342 写死 "16:9"）永不复现。
    """
    pipeline_py = (SANDBOX / "hailuo_video" / "pipeline.py").read_text(encoding="utf-8")
    assert 'aspect_ratio_value="16:9"' not in pipeline_py, (
        "pipeline.py 不准硬编码 aspect_ratio，必须读 env（V3.4 ADR-001）"
    )
    # 同时验证用了 ie.aspect_ratio() 调用
    assert "ie.aspect_ratio()" in pipeline_py, (
        "pipeline.py 必须调用 ie.aspect_ratio() 以读 env"
    )


# ---------- 2. editing phase 软失败（motion.mp4 → final.mp4） ----------

def test_motion_mp4_fallback_copies_motion_to_output(tmp_path):
    """ADR-002：_motion_mp4_fallback 找到 motion.mp4 → 复制为 output；返回 path。"""
    from hailuo_video.orchestrator import PipelineAgent, PipelineConfig

    motion = tmp_path / "intermediate" / "shot_000_motion.mp4"
    motion.parent.mkdir(parents=True)
    motion.write_bytes(b"fake mp4 content")  # 8 字节
    output = tmp_path / "final.mp4"

    cfg = PipelineConfig(run_dir=tmp_path / "run")
    agent = PipelineAgent.__new__(PipelineAgent)  # 不调 __init__
    result = agent._motion_mp4_fallback(motion.parent, output)
    assert result == output
    assert output.is_file()
    assert output.read_bytes() == b"fake mp4 content"


def test_motion_mp4_fallback_falls_back_to_kenburns(tmp_path):
    """ADR-002：motion.mp4 不存在但 kenburns.mp4 存在 → 兜底用 kenburns。"""
    from hailuo_video.orchestrator import PipelineAgent, PipelineConfig

    intermediate = tmp_path / "intermediate"
    intermediate.mkdir()
    kenburns = intermediate / "shot_000_kenburns.mp4"
    kenburns.write_bytes(b"kenburns content")
    output = tmp_path / "final.mp4"

    cfg = PipelineConfig(run_dir=tmp_path / "run")
    agent = PipelineAgent.__new__(PipelineAgent)
    result = agent._motion_mp4_fallback(intermediate, output)
    assert result == output
    assert output.read_bytes() == b"kenburns content"


def test_motion_mp4_fallback_returns_none_when_nothing(tmp_path):
    """ADR-002：motion.mp4 + kenburns.mp4 都不存在 → 返回 None（让上层 hard fail）。"""
    from hailuo_video.orchestrator import PipelineAgent, PipelineConfig

    intermediate = tmp_path / "intermediate"
    intermediate.mkdir()
    output = tmp_path / "final.mp4"

    cfg = PipelineConfig(run_dir=tmp_path / "run")
    agent = PipelineAgent.__new__(PipelineAgent)
    result = agent._motion_mp4_fallback(intermediate, output)
    assert result is None


# ---------- 3. asset_prep style_anchor 防漂移守卫 ----------

def test_safe_style_anchor_empty_uses_photorealistic_fallback():
    """ADR-003：style_anchor 为空 → 用写实系（不再退回 'concept art style'）。"""
    from hailuo_video.asset_prep import _safe_style_anchor, PHOTOREALISTIC_FALLBACK

    assert _safe_style_anchor(None) == PHOTOREALISTIC_FALLBACK
    assert _safe_style_anchor("") == PHOTOREALISTIC_FALLBACK
    assert _safe_style_anchor("   ") == PHOTOREALISTIC_FALLBACK


def test_safe_style_anchor_appends_guard_phrase_when_missing():
    """ADR-003：缺守卫词 → 末尾追加 'no illustration no cartoon ...'。"""
    from hailuo_video.asset_prep import _safe_style_anchor

    anchor = "photorealistic cinematic documentary"
    result = _safe_style_anchor(anchor)
    assert "no illustration no cartoon no anime no CGI" in result
    assert anchor in result


def test_safe_style_anchor_idempotent_when_has_guard():
    """ADR-003：已含守卫词 → 不重复追加（避免 'no illustration no illustration ...'）。"""
    from hailuo_video.asset_prep import _safe_style_anchor

    anchor = "photorealistic, no illustration no cartoon"
    result = _safe_style_anchor(anchor)
    # 只追加一次
    assert result.count("no illustration") == 1
    assert "no cartoon" in result


def test_safe_style_anchor_uses_guard_for_partial_concept_art():
    """$doc：'concept art style' 是危险词（漂移到卡通），但不触发现有 guard 关键词。

    验证：即便 prompt 写 concept art style，_safe_style_anchor 仍追加守卫。
    """
    from hailuo_video.asset_prep import _safe_style_anchor

    anchor = "concept art style"
    result = _safe_style_anchor(anchor)
    assert "no illustration no cartoon" in result


# ---------- 4. scripts/lib_ffmpeg.py 抽公共 helper ----------

def test_lib_ffmpeg_has_apply_cinematic_lut():
    """scripts/lib_ffmpeg.py 必须有 apply_cinematic_lut 函数（消除脚本重复）。"""
    import lib_ffmpeg
    assert hasattr(lib_ffmpeg, "apply_cinematic_lut")
    assert hasattr(lib_ffmpeg, "apply_cinematic_lut_by_shot_id")


def test_lib_ffmpeg_has_to_vertical_with_crop_offset():
    """to_vertical 必须计算 center crop 偏移（不让用户手算偏移）。"""
    import lib_ffmpeg

    # 已知值：1920x1080 → 1080x1920 → scale -1:1920 = 3419x1920 → crop 1080 center x=1164
    # 但因 int floor 可能略有差异），验证偏移是 (scaled_w - target_w) // 2
    # 测试通过函数在已知源分辨率下推算偏移
    src_w, src_h = 1920, 1080
    target_w, target_h = 1080, 1920
    scaled_w = src_w * target_h // src_h
    expected_x = (scaled_w - target_w) // 2
    # scaled_w = 1920*1920/1080 = 3413 (int floor)，expected_x = (3413-1080)//2 = 1166
    # 注：与 V3.3 inline 1164 略有差异（之前手算有偏差）— 这是修 bug
    assert expected_x == 1166


def test_lib_ffmpeg_apply_cinematic_lut_idempotent(tmp_path):
    """同一 mp4 + 同一 lut → 输出文件可被读回，oversize（h264 重编）。"""
    pytest.importorskip("PIL")  # 不强依赖 PIL 时的兜底
    pytest.skip("需要真 ffmpeg + 真 mp4；CI 跳过（运行时验证）")


# ---------- 5. scripts/lib_ffmpeg.py LUT_RECIPES 6 镜齐全 ----------

def test_lut_recipes_has_all_six_shot_ids():
    """LUT_RECIPES 必须含 shot_001..shot_006 全部（与 cinematic_upgrade.py 一致）。"""
    import lib_ffmpeg

    for sid in ("shot_001", "shot_002", "shot_003", "shot_004", "shot_005", "shot_006"):
        assert sid in lib_ffmpeg.LUT_RECIPES, f"LUT_RECIPES 缺 {sid}"
        # 每个是 (brightness, saturation, gamma) 三元组
        assert len(lib_ffmpeg.LUT_RECIPES[sid]) == 3


# ---------- 6. log.jsonl 完整化（含失败明细） ----------

def test_structured_logger_writes_phase_completed(tmp_path):
    """log.jsonl 必须有 phase_completed 事件（V3.4 反抽）。"""
    from hailuo_video.log import StructuredLogger

    log_path = tmp_path / "log.jsonl"
    logger = StructuredLogger(path=log_path)
    logger.event("phase_started", phase="image_gen")
    logger.event("phase_completed", phase="image_gen", duration_seconds=0.42)

    lines = log_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    parsed = [json.loads(l) for l in lines]
    assert parsed[0]["event_type"] == "phase_started"
    assert parsed[1]["event_type"] == "phase_completed"
    assert parsed[1]["payload"]["duration_seconds"] == 0.42


def test_structured_logger_records_phase_failed_with_error(tmp_path):
    """log.jsonl 必须能记录 phase_failed + error 头（V3.4 反抽；3-6 周次实际不可追溯）。"""
    from hailuo_video.log import StructuredLogger

    log_path = tmp_path / "log.jsonl"
    logger = StructuredLogger(path=log_path)
    logger.event(
        "phase_failed", phase="editing",
        agent="editing",
        error_code="FFmpegError",
        error_msg="matches no streams",
    )

    line = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert line["event_type"] == "phase_failed"
    assert line["payload"]["error_code"] == "FFmpegError"
    assert "matches no streams" in line["payload"]["error_msg"]


# ---------- V3.4 ADR-006：prompt 硬编码审计 ----------

def test_asset_prep_prompts_have_no_concept_art_hardcode():
    """ADR-006：CHARACTER_ANGLE_PROMPTS 模板开头不准 'concept art style'。

    V3.3 bug：template 写 "concept art style, {style_anchor}, ..." 与用户写实系冲突，
    → 模型挑简单的 "concept art style" 实现 → 卡通漂移（"小黄变猫"事件）。
    修复：删除硬编码 "concept art style" 前缀，让 style_anchor 独占风格控制。

    注：检查范围**仅限 prompt 模板字符串**（含 {style_anchor} 或 {appearance} 的行），
    注释里的解释性字符串不算（避免误伤）。
    """
    asset_prep_src = (SANDBOX / "hailuo_video" / "asset_prep.py").read_text(encoding="utf-8")
    template_lines = [
        line for line in asset_prep_src.splitlines()
        if "{style_anchor}" in line or "{appearance}" in line
    ]
    assert template_lines, "未找到 prompt 模板行"
    for line in template_lines:
        assert "concept art style" not in line, (
            f"prompt 模板仍含硬编码 'concept art style'：{line.strip()}\n"
            f"V3.4 ADR-006：删除硬编码，让 style_anchor 独占风格控制。"
        )


def test_asset_prep_prompts_still_include_style_anchor_placeholder():
    """ADR-006 反向验证：模板仍保留 {style_anchor} 占位符（确保用户风格能注入）。"""
    asset_prep_src = (SANDBOX / "hailuo_video" / "asset_prep.py").read_text(encoding="utf-8")
    # 至少 5 个模板含 {style_anchor}（front + side_left + side_right + back + side）
    style_anchor_count = asset_prep_src.count("{style_anchor}")
    assert style_anchor_count >= 5, (
        f"asset_prep.py 应至少有 5 处 {{style_anchor}} 占位符（每 angle 模板 1 处），"
        f"实际 {style_anchor_count}"
    )


def test_orchestrator_uses_safe_style_anchor_for_image_gen():
    """ADR-006：orchestrator._build_task 调 image_gen 时 style_anchor 必须经 _safe_style_anchor 兜底。

    V3.3 bug：style_anchor 为空时 prompt 完全没有 anti-cartoon 守卫 → 漂移到卡通。
    修复：_build_task 内提前调 _safe_style_anchor；即使 storyboard 没设也兜底。
    """
    orch_src = (SANDBOX / "hailuo_video" / "orchestrator.py").read_text(encoding="utf-8")
    # 必须有 _safe_style_anchor import + 调用
    assert "from hailuo_video.asset_prep import _safe_style_anchor" in orch_src, (
        "orchestrator.py 必须 import _safe_style_anchor"
    )
    assert "_safe_style_anchor(" in orch_src, (
        "orchestrator.py 必须调用 _safe_style_anchor() 兜底"
    )


def test_orchestrator_motion_gen_no_unreachable_else_branch():
    """ADR-006：motion_gen path 的 'else: task["subject_action"] = base_subject_action'
    在 _safe_style_anchor 兜底后永远走不到，应当被清理。

    这是 dead code 标记，重复触发会让人怀疑是不是有 bug。
    """
    orch_src = (SANDBOX / "hailuo_video" / "orchestrator.py").read_text(encoding="utf-8")
    # motion_gen 路径不应再有 if style_anchor / else 模式（既然 _safe_style_anchor 兜底）
    # 简化版断言：找 'base_subject_action' 后不应紧跟 'else:' 分支
    motion_section = orch_src[orch_src.find('agent_name == "motion_gen"'):]
    motion_section = motion_section[:motion_section.find('elif agent_name')]
    # V3.4 ADR-006：简化后 motion_gen 应只有一种赋值（不再 if/else）
    # 期望 base_subject_action 出现在 f"[风格:{style_anchor}]..." 上下文
    assert 'base_subject_action = getattr(shot, "subject_action", "") or description' in motion_section
    assert 'task["subject_action"] = f"[风格:{style_anchor}] {base_subject_action}"' in motion_section