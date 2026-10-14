#!/usr/bin/env bash
# scripts/deploy_to_mac.sh — 打包 Windows 项目 → Mac 部署包
#
# V3.5 部署工具：把当前 ai_video 项目打包成 Mac 可用的 tar.gz。
#
# 用法：
#   bash scripts/deploy_to_mac.sh
#
# 产出（在 dist/ 下）：
#   - ai_video.bundle           git bundle（含全部 git history，~25MB）
#   - ai_video_mac_<date>.tar.gz  打包（包含 bundle + setup + docs，~25MB）
#   - SHA256SUMS                校验和
#
# Mac 端接收：
#   1. 复制 ai_video_mac_<date>.tar.gz 到 Mac
#   2. tar -xzf ai_video_mac_<date>.tar.gz
#   3. bash setup_on_mac.sh
#
# 不包含（按 .gitignore）：
#   - sample/*.mp4            （hailuo 真视频，约 119MB — 单独传网盘）
#   - .env                 （真实 MINIMAX_API_KEY — 安全接收）
#   - __pycache__/ *.pyc   （编译缓存）
#   - sandbox/.state.json （会话状态）
#   - logs/*.tmp
#
# 必须包含：
#   - sandbox/             全部代码（sandbox/hailuo_video/）
#   - tests/              全部测试（含 V3.5 隔离回归）
#   - scripts/            run_story / cinematic_upgrade 等
#   - docs/               PRD + ISOLATION + ARCHITECTURE
#   - sample/*.json       storyboard JSON（可在 Mac 端重跑 hailuo）
#   - .git                完整 git history（git bundle 形式）
#   - .gitignore
#   - README.md / FRAMEWORK.md / 升级路线.md 等

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

DIST_DIR="$PROJECT_ROOT/dist"
DATE=$(date +%Y%m%d)
ARCHIVE_NAME="ai_video_mac_${DATE}.tar.gz"

# 1. 准备 dist 目录
echo "[1/5] 准备 dist/ 目录..."
rm -rf "$DIST_DIR"
mkdir -p "$DIST_DIR"

# 2. 创建 git bundle（含全部 git history + branches）
echo "[2/5] 创建 git bundle（保留完整 commit 历史）..."
git bundle create "$DIST_DIR/ai_video.bundle" --all 2>&1 | tail -2

# 3. 复制部署辅助文件（Mac 端用）
echo "[3/5] 复制 setup 脚本 + 部署指南..."
cp scripts/setup_on_mac.sh "$DIST_DIR/"
cp docs/DEPLOY_TO_MAC.md "$DIST_DIR/"
chmod +x "$DIST_DIR/setup_on_mac.sh"

# 4. 打包
echo "[4/5] 创建 ${ARCHIVE_NAME}..."
cd "$DIST_DIR"
tar -czf "$ARCHIVE_NAME" ai_video.bundle setup_on_mac.sh DEPLOY_TO_MAC.md

# 5. 生成校验和
echo "[5/5] 生成 SHA256SUMS..."
cd "$DIST_DIR"
{
  sha256sum ai_video.bundle
  sha256sum "$ARCHIVE_NAME"
} > SHA256SUMS

echo ""
echo "========================================"
echo "✅ 部署包生成完成"
echo "========================================"
echo ""
echo "产出文件："
ls -la "$DIST_DIR"
echo ""
echo "Bundle 大小："
du -h "$DIST_DIR"/*
echo ""
echo "Mac 端接收步骤："
echo "  1. 把 dist/${ARCHIVE_NAME} 传到 Mac（U盘/网盘/压缩传输）"
echo "  2. Mac 上：tar -xzf ${ARCHIVE_NAME}"
echo "  3. Mac 上：bash setup_on_mac.sh"
echo "  4. 验证：cd ai_video && python -m pytest tests/ -q"
echo ""
echo "注意事项："
echo "  - sample/*.mp4 不在包内（gitignore 阻 + 文件大）"
echo "    如需 Mac 部署成果，请单独传 sample/story_arc_xiaohuang_daily/final/"
echo "  - MINIMAX_API_KEY 不在包内，Mac 端 setup 时单独提供"