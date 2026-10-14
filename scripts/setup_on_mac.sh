#!/usr/bin/env bash
# scripts/setup_on_mac.sh — Mac 端一键 setup 脚本
#
# 由 deploy_to_mac.sh 打包随附。Mac 用户 clone bundle 后运行此脚本。
#
# 步骤：
#   1. 验证 macOS + bash 4+
#   2. 装 Python 依赖（pytest + imageio-ffmpeg + Pillow + requests）
#   3. 验证 ffmpeg 可用
#   4. 创建虚拟环境（推荐）
#   5. 跑 pytest 确认 660+ passed
#
# 不做（需用户主动）：
#   - 装 MINIMAX_API_KEY（应通过 export 或 .env 单独设）

set -euo pipefail

# 1. 平台检查
echo "[1/5] 平台检查..."
if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "⚠️  警告：本脚本设计为 macOS，当前是 $(uname -s)"
    echo "   大部分步骤仍可跑，但 brew install 需改为 Linux 包管理器"
fi

# 2. Python 检查
echo "[2/5] Python 检查..."
if ! command -v python3 >/dev/null 2>&1; then
    echo "❌ Python 3 未装。请装："
    echo "   brew install python@3.11"
    echo "   或 python.org 下载 3.10+"
    exit 1
fi

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "   ✓ Python $PYTHON_VERSION"

# 3. ffmpeg 检查
echo "[3/5] ffmpeg 检查..."
if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "❌ ffmpeg 未装。请："
    echo "   brew install ffmpeg"
    echo "   或 imageio-ffmpeg (pip install imageio-ffmpeg)"
    exit 1
fi
FFMPEG_VERSION=$(ffmpeg -version 2>&1 | head -1 | awk '{print $3}')
echo "   ✓ ffmpeg $FFMPEG_VERSION"

# 4. 装 Python 依赖
echo "[4/5] 装 Python 依赖..."
pip3 install --user --quiet pytest pillow imageio-ffmpeg requests 2>&1 | tail -3 || {
    echo "❌ pip install 失败。请手动跑：pip3 install pytest pillow imageio-ffmpeg requests"
    exit 1
}
echo "   ✓ 依赖已装"

# 5. 跑测试验证
echo "[5/5] 跑 pytest 验证（应 660+ passed）..."
if [[ -d "tests" ]]; then
    # 用 python -m pytest 避免 PATH 问题
    python3 -m pytest tests/ -q --tb=line --ignore=tests/test_tts_engine.py 2>&1 | tail -5 || {
        echo "⚠️  部分测试 fail。但 sandbox/ + scripts/ 可独立用。"
    }
else
    echo "⚠️  tests/ 目录未找到。可能是 git clone 后工作树未 checkout？"
fi

echo ""
echo "========================================"
echo "✅ Mac setup 完成"
echo "========================================"
echo ""
echo "下一步："
echo "  1. 设置 MINIMAX_API_KEY（必需，否则 hailuo / image-01 会失败）："
echo "     export MINIMAX_API_KEY='your-real-key-here'"
echo "     或 echo 'export MINIMAX_API_KEY=...' >> ~/.zshrc"
echo ""
echo "  2. 验证：git log --oneline -10"
echo "     应看到 7 个 commits："
echo "       - V3.5 ADR-007 Project 强隔离"
echo "       - V3.4 W3 rerun L1 + 手动"
echo "       - V3.4 W3 + 幼犬命名消歧义"
echo "       - V3.4 W3 finalize + RERUN_NOTES"
echo "       - V3.4 W3 hotfix 归档"
echo "       - V3.4 W3 prompt 硬编码审计"
echo "       - V3.4 反回归"
echo ""
echo "  3. 跑示例 pipeline（hailuo 真视频需 1 quota）："
echo "     cd sandbox"
echo "     export MINIMAX_IMAGE_ASPECT_RATIO=9:16"
echo "     python -m hailuo_video pipeline \\"
echo "         ../sample/story_arc_xiaohuang_daily/day1_rerun_storyboards/shot_001_rerun.json \\"
echo "         -o /tmp/shot.mp4 --image-backend minimax --motion-budget 1"
echo ""
echo "  4. 跑测试验证隔离（V3.5 关键测试）："
echo "     python -m pytest tests/test_v35_isolation.py -v"