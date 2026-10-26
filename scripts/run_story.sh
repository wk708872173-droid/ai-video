#!/bin/bash
# scripts/run_story.sh — 批量跑 StoryArc 的 1 天 quota（默认 3 条）
#
# 用法（Git Bash）：
#   cd d:/ai-tools/ai_video/sandbox
#   bash ../scripts/run_story.sh <story_dir>
#
# 例：
#   bash ../scripts/run_story.sh ../sample/story_arc_orange_cat_4_seasons/day1_storyboards
#
# 行为：
#   - 跑 story_dir 下所有 *.json，每个消耗 1 Hailuo quota
#   - 把每个 storyboard.json 跑 pipeline（motion-budget 1）
#   - 把 motion mp4 拷贝成 story_<shot_id>.mp4（不依赖 cmd_pipeline 的输出路径 bug）

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
SANDBOX_DIR="$PROJECT_ROOT/sandbox"
SAMPLE_DIR="$PROJECT_ROOT/sample"

if [ -z "$1" ]; then
    echo "Usage: bash run_story.sh <story_dir>" >&2
    echo "  例: bash run_story.sh ../sample/story_arc_orange_cat_4_seasons/day1_storyboards" >&2
    exit 2
fi

STORY_DIR="$(cd "$1" && pwd)"

# 自动生成 storyboards（如目录为空或不存在）
if [ ! -d "$STORY_DIR" ] || [ -z "$(ls -A "$STORY_DIR" 2>/dev/null | grep '\.json$')" ]; then
    ARC_DIR="$(dirname "$STORY_DIR")"
    ARC_JSON="$(ls "$ARC_DIR"/day*.json 2>/dev/null | head -1)"
    if [ -z "$ARC_JSON" ]; then
        echo "[FAIL] 没找到 story_arc.json (day*.json) in $ARC_DIR" >&2
        exit 2
    fi
    echo "[INFO] $STORY_DIR 空，从 $ARC_JSON 自动生成 storyboards..."
    python "$SCRIPT_DIR/build_storyboards.py" "$ARC_JSON" "$STORY_DIR"
fi

cd "$SANDBOX_DIR"
export MINIMAX_IMAGE_ASPECT_RATIO=9:16
export PYTHONIOENCODING=utf-8

STORY_NAME="$(basename "$(dirname "$STORY_DIR")")"

# Per-shot Ken Burns effect 映射（让 6 镜有镜头语言变化）
# 可通过环境变量 STORY_EFFECT_<shot_id> 覆盖
declare -A EFFECT_MAP=(
    [shot_001]="zoom_in"
    [shot_002]="pan_right"
    [shot_003]="zoom_out"
    [shot_004]="zoom_in"
    [shot_005]="zoom_in"
    [shot_006]="zoom_in"
)

count=0
for sb in "$STORY_DIR"/*.json; do
    [ -f "$sb" ] || continue
    count=$((count + 1))
    SHOT_ID="$(basename "$sb" .json)"
    OUT="$SAMPLE_DIR/${STORY_NAME}_${SHOT_ID}.mp4"
    RUN_DIR="$SAMPLE_DIR/run_${STORY_NAME}_${SHOT_ID}"
    EFFECT="${EFFECT_MAP[$SHOT_ID]:-zoom_in}"
    # 允许环境变量覆盖（写法：env STORY_EFFECT_shot_001=zoom_in bash run_story.sh ...）
    envkey="STORY_EFFECT_${SHOT_ID}"
    if [ -n "${!envkey+x}" ]; then
        EFFECT="${!envkey}"
    fi
    echo ""
    echo "=== $count: $SHOT_ID (effect=$EFFECT) ==="
    python -m hailuo_video pipeline "$sb" \
        -o "$OUT" \
        --image-backend minimax \
        --tts-backend mock \
        --motion-budget 3 \
        --video-effect "$EFFECT" \
        --parallel 1 \
        --run-dir "$RUN_DIR" 2>&1 | tail -3

    # cmd_pipeline final.mp4 路径有相对路径 bug → 从 intermediate/ 救回产物
    # V3.2 轮 15：必须优先 shot_000_motion.mp4（Hailuo 真视频），否则按字母序 ls 会错拷
    # shot_000_kenburns.mp4（k）排在 shot_000_motion.mp4（m）前面 → 触发 PPT 假象
    MOTION=""
    for candidate in "$RUN_DIR/intermediate/shot_000_motion.mp4" \
                      "$RUN_DIR/intermediate/shot_000_kenburns.mp4"; do
        if [ -f "$candidate" ]; then
            MOTION="$candidate"
            break
        fi
    done
    if [ -n "$MOTION" ] && [ -f "$MOTION" ]; then
        cp "$MOTION" "$OUT"
        echo "[OK] $(basename "$OUT") ($(stat -c%s "$OUT") bytes) <- $(basename "$MOTION")"
    fi
done

echo ""
echo "=== 全部完成：$count 条 ==="
ls -la "$SAMPLE_DIR"/${STORY_NAME}_*.mp4 2>/dev/null || echo "(无产物)"
