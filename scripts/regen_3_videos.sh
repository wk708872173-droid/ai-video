#!/bin/bash
# scripts/regen_3_videos.sh — 用户假期回来后一键重生 3 条视频（消耗 3 Hailuo quota）
#
# 用法（Windows Git Bash 或 WSL）：
#   cd d:/ai-tools/ai_video/sandbox
#   bash ../scripts/regen_3_videos.sh
#
# 输出：
#   ../sample/v1_golden_retriever.mp4         (覆盖)
#   ../sample/v2_orange_cat.mp4              (覆盖)
#   ../sample/v3_fawn_forest.mp4             (覆盖)
#
# 假设（用户假期后回到电脑）：
#   - Hailuo quota 已重置（每天 3 次）
#   - .env 里 MINIMAX_API_KEY 仍有效
#   - storyboard JSON 已用 V3.2 轮 7 强化的写实 style_anchor
#
# 注意事项：
#   - 每次跑消耗 1 Hailuo quota；3 条 = 用完今日 quota
#   - asset-prep 缓存命中，不重复烧 image-01 quota
#   - 若想试 Ken Burns 版（不耗 Hailuo），把对应行的 --motion-budget 改为 0

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
SAMPLE_DIR="$PROJECT_ROOT/sample"
SANDBOX_DIR="$PROJECT_ROOT/sandbox"

cd "$SANDBOX_DIR"

export MINIMAX_IMAGE_ASPECT_RATIO=9:16
export PYTHONIOENCODING=utf-8

# V1 — 金毛犬（复杂全身运动 / 4 足 gallop — Hailuo 已知限制 → 可能仍不完美）
echo "=== V1: 金毛犬麦田 (Hailuo, complex motion) ==="
python -m hailuo_video pipeline \
    "$SAMPLE_DIR/storyboard_golden_retriever.json" \
    -o "$SAMPLE_DIR/v1_golden_retriever.mp4" \
    --image-backend minimax \
    --tts-backend mock \
    --motion-budget 1 \
    --parallel 1 \
    --run-dir "$SAMPLE_DIR/run_v1_regen" 2>&1 | tail -5

# V1 motion mp4 通常落在 intermediate/shot_000_motion.mp4（cmd_pipeline final.mp4 路径有 bug）
MOTION_V1=$(ls "$SAMPLE_DIR/run_v1_regen/intermediate/shot_000_motion.mp4" 2>/dev/null || echo "")
if [ -n "$MOTION_V1" ]; then
    cp "$MOTION_V1" "$SAMPLE_DIR/v1_golden_retriever.mp4"
    echo "[OK] V1 -> $SAMPLE_DIR/v1_golden_retriever.mp4"
fi

echo ""

# V2 — 橘猫（简单转头 → Hailuo 应能做好）
echo "=== V2: 橘猫窗台 (Hailuo, simple motion) ==="
python -m hailuo_video pipeline \
    "$SAMPLE_DIR/storyboard_orange_cat.json" \
    -o "$SAMPLE_DIR/v2_orange_cat.mp4" \
    --image-backend minimax \
    --tts-backend mock \
    --motion-budget 1 \
    --parallel 1 \
    --run-dir "$SAMPLE_DIR/run_v2_regen" 2>&1 | tail -5

MOTION_V2=$(ls "$SAMPLE_DIR/run_v2_regen/intermediate/shot_000_motion.mp4" 2>/dev/null || echo "")
if [ -n "$MOTION_V2" ]; then
    cp "$MOTION_V2" "$SAMPLE_DIR/v2_orange_cat.mp4"
    echo "[OK] V2 -> $SAMPLE_DIR/v2_orange_cat.mp4"
fi

echo ""

# V3 — 梅花鹿（简单转头 → Hailuo 应能做好）
echo "=== V3: 梅花鹿晨雾森林 (Hailuo, simple motion) ==="
python -m hailuo_video pipeline \
    "$SAMPLE_DIR/storyboard_fawn_forest.json" \
    -o "$SAMPLE_DIR/v3_fawn_forest.mp4" \
    --image-backend minimax \
    --tts-backend mock \
    --motion-budget 1 \
    --parallel 1 \
    --run-dir "$SAMPLE_DIR/run_v3_regen" 2>&1 | tail -5

MOTION_V3=$(ls "$SAMPLE_DIR/run_v3_regen/intermediate/shot_000_motion.mp4" 2>/dev/null || echo "")
if [ -n "$MOTION_V3" ]; then
    cp "$MOTION_V3" "$SAMPLE_DIR/v3_fawn_forest.mp4"
    echo "[OK] V3 -> $SAMPLE_DIR/v3_fawn_forest.mp4"
fi

echo ""
echo "=== 全部完成 ==="
ls -la "$SAMPLE_DIR"/v*.mp4
