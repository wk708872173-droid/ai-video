# V3.5 部署到 Mac 指南

> 创建于 2026-10-14
> 把当前 ai_video 项目（Windows）打包 → 部署到另一台苹果电脑

---

## 0. 部署架构概览

```
Windows (开发机)                     Mac (目标机)
┌─────────────────┐                ┌─────────────────┐
│ ai_video/        │   git bundle   │ ai_video/         │
│ sandbox/         │ ─────────────► │ sandbox/         │
│ tests/           │   + setup.sh   │ tests/           │
│ scripts/         │   + docs       │ scripts/         │
│ docs/            │                │ docs/             │
│ .git (286MB)     │                │ .git (clone)     │
│                 │                │                   │
│ (excluded:)      │                │                   │
│ - sample/*.mp4   │                │ (单独传网盘)      │
│ - .env           │                │                   │
│ - __pycache__/   │                │                   │
│ - logs/sample    │                │                   │
└─────────────────┘                └─────────────────┘
```

**不包含**：
- `sample/*.mp4`（Hailuo 视频产物，约 119MB）— 单独传
- `.env`（真实 API key）— Mac 端自己设
- `__pycache__/`（编译缓存，平台相关）
- `sandbox/.state.json`（会话状态）

**包含**：
- 全部 `sandbox/` Python 代码（V3.4 + V3.5）
- 全部 `tests/` 回归测试（660+ tests）
- 全部 `scripts/` shell 工具
- 全部 `docs/` 设计文档
- `sample/*.json` storyboard 配置（重跑用）
- 完整 git history（git bundle 形式）

---

## 1. Windows 端打包

```bash
bash scripts/deploy_to_mac.sh
```

产出（在 `dist/` 下）：
- `ai_video.bundle`（约 25MB，git bundle 含全部 history）
- `setup_on_mac.sh`（Mac setup 脚本）
- `DEPLOY_TO_MAC.md`（本文档副本）
- `ai_video_mac_YYYYMMDD.tar.gz`（约 25MB，最终包）
- `SHA256SUMS`（校验和）

---

## 2. 传输到 Mac

推荐方式（按文件大小排序）：

| 方式 | 适用 |
|---|---|
| U 盘 / 移动硬盘 | 25MB，很快 |
| AirDrop（Mac 间） | 如果 Mac 在附近 |
| 微信 / Telegram "文件传输" | 25MB 没问题 |
| iCloud Drive / OneDrive | 自动 push |
| GitHub private repo | 25MB commit history 同步 |
| 邮件附件 | 25MB 通常超限，**不推荐** |

---

## 3. Mac 端接收 + Setup

```bash
# 1) 解包
mkdir ~/ai_video_deploy && cd ~/ai_video_deploy
tar -xzf ai_video_mac_YYYYMMDD.tar.gz
cd ai_video_deploy  # 当前目录含 bundle + setup_on_mac.sh

# 2) 验证 SHA256（可选但推荐）
cat SHA256SUMS
shasum -a 256 ai_video.bundle  # macOS 用 shasum
# 应与 SHA256SUMS 里的值匹配

# 3) 从 bundle 还原 git history + 工作树
git clone ai_video.bundle ai_video
cd ai_video

# 4) 一键 setup
bash setup_on_mac.sh
```

`setup_on_mac.sh` 自动做：
- 验证 macOS + bash 4+
- 检查 Python 3.10+
- 检查 ffmpeg
- `pip install` Python 依赖（pytest, pillow, imageio-ffmpeg, requests）
- 跑 `pytest tests/ -q` 验证（应 660+ passed）

**期望输出**：
```
[1/5] 平台检查... ✓
[2/5] Python 检查... ✓ Python 3.11.x
[3/5] ffmpeg 检查... ✓ ffmpeg 7.1.x
[4/5] 装 Python 依赖... ✓ 依赖已装
[5/5] 跑 pytest 验证（应 660+ passed）...
...
660 passed, 12 skipped in X.XXs
```

---

## 4. 设置 API Key（关键！）

deploy 包**不含**真实 `MINIMAX_API_KEY`，Mac 端需：

```bash
# 临时设置（仅当前 shell）
export MINIMAX_API_KEY='your-real-key-here'

# 永久设置（推荐）
echo 'export MINIMAX_API_KEY=your-real-key-here' >> ~/.zshrc
source ~/.zshrc
```

如用 `python-dotenv`：
```bash
# 复制 .env.example 为 .env
cp .env.example .env
# 编辑 .env 填入真实 key
code .env
```

---

## 5. 验证部署成功

跑下列命令全部应成功：

```bash
# 1) 测试全绿
python -m pytest tests/ -q --ignore=tests/test_tts_engine.py
# 期望：660 passed, 12 skipped

# 2) V3.5 隔离测试专项
python -m pytest tests/test_v35_isolation.py -v
# 期望：13 passed

# 3) V3.4 反回归测试
python -m pytest tests/test_v34_regression.py -v
# 期望：19 passed, 1 skipped

# 4) ProjectContext smoke test
python -c "
import sys; sys.path.insert(0, 'sandbox')
from hailuo_video.project import ProjectContext
ctx = ProjectContext.from_storyboard_path('sample/story_arc_xiaohuang_daily/day1.json')
print(f'project_id={ctx.project_id}')
print(f'assets_dir={ctx.assets_dir}')
print(f'run_dir={ctx.run_dir}')
"
# 期望：8 字符 hash + sandbox/assets/characters/{id} + sample/run_{id}

# 5) hailuo ping（如已设 API key）
cd sandbox
python -m hailuo_video ping
# 期望：environment ready=True
```

---

## 6. 恢复 sample/ hailuo 视频（可选）

如果想在 Mac 上看小黄的视频产物：

| 方式 | 步骤 |
|---|---|
| 单独传 sample/ 目录 | 在 Windows 用 `tar -czf sample.tgz sample/story_arc_xiaohuang_daily/` |
| 重新跑 hailuo | Mac 端用 API key 跑 `python -m hailuo_video pipeline ...`（需 1 quota/shot）|

**重要**：如果选择重新跑，建议按 RERUN_NOTES.md 流程：
```bash
# 删 character cache
rm -rf sandbox/assets/characters/幼犬/
# 跑 shot_001 rerun（character.name 已用"幼犬"避免歧义）
```

---

## 7. 平台差异注意事项

| Windows | Mac |
|---|---|
| `D:\ai-tools\ai_video\` | `/Users/you/ai_video/` |
| Git Bash | zsh（默认）或 bash |
| `cmd.exe` 调用 | `python3` 替代 `python` |
| 路径含空格需转义 | 一般无空格 |
| `imageio_ffmpeg.get_ffmpeg_exe()` 返回 .exe | 返回无后缀二进制 |
| 字体回退 | CJK 字体不同，输出略有差异 |

所有 Python 代码都是 POSIX 兼容（已测试），Mac 上应该直接 work。

---

## 8. 故障排查

| 现象 | 原因 | 解决 |
|---|---|---|
| `setup_on_mac.sh` 报 "Python 3 未装" | PATH 没 Python 3 | `brew install python@3.11` |
| `pytest` 报 "command not found" | 用了 `python -m pytest` 但 .zshrc 没 source | 改用 `python3 -m pytest` 或重 source |
| `ffmpeg` 报 "command not found" | 系统 ffmpeg 缺 | `brew install ffmpeg` |
| 测试 660 不通过 | 环境差异 | 跑 `python -m pytest tests/ -v` 看具体 fail |
| hailuo 报 "API key invalid" | 没设 MINIMAX_API_KEY | `export MINIMAX_API_KEY=...` |
| character name 中文显示乱码 | LANG 未设 | `export LANG=en_US.UTF-8` |

---

## 9. 双向同步（可选）

如果 Mac 上继续开发并希望同步回 Windows：

```bash
# Mac 端：commit + push（先在 GitHub 创建 private repo）
git remote add origin git@github.com:yourname/ai_video_mac.git
git push -u origin main

# Windows 端：fetch Mac 的 commit
git fetch origin
git merge origin/main
```

---

## 10. 版本说明

| Commit | 内容 |
|---|---|
| `8379da1` | V3.4 反回归基础设施（aspect_ratio env + editing 软失败 + lib_ffmpeg） |
| `6011e9f` | V3.4 W3 prompt 硬编码审计 + style_anchor 兜底 |
| `7dbb486` | V3.4 W3 hotfix 归档 + 明天 1 quota 验证 |
| `26f0c50` | V3.4 W3 hotfix finalize 脚本 |
| `29ae008` | V3.4 W3 rerun 3 quota 全跑成功（幼犬命名） |
| `5af1726` | V3.4 W3 rerun L1 + 手动目测 |
| `304df89` | **V3.5 ADR-007 Project 强隔离**（最新） |

部署后 Mac 端 `git log --oneline -7` 应看到以上 7 个 commits。

---

## 11. 联系

任何问题记录在：
- `docs/ARCHITECTURE.md`（V3.4 决策）
- `docs/ISOLATION.md`（V3.5 决策）
- `logs/`（各次会话记录）