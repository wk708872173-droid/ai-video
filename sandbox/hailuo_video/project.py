"""V3.5 ADR-007：Project 隔离上下文。

设计目标：每个"任务"（剧本 + 角色 + 配置 + 输出）= 一个 ProjectContext 实例
→ cache / run_dir / env / output 路径全部 namespace 隔离。

关键不变量：
  1. project_id 自动派生（sha1(storyboard 内容)[:8]）— 同一 storyboard 复用 cache
  2. project_id 唯一标识一个项目 — 不同 storyboard 强制 namespace
  3. env override 通过 context manager — 退出自动 restore（不污染 shell）
  4. fail-fast — 未提供 project_id 或 storyboard 路径不存在立刻硬失败
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


# ---------- Errors ----------

class ProjectContextError(RuntimeError):
    """ProjectContext 初始化 / 操作失败。"""


# ---------- ProjectContext dataclass ----------

@dataclass(frozen=True)
class ProjectContext:
    """V3.5 ADR-007：项目级隔离上下文。frozen=True 防运行期被改。

    Attributes:
        project_id: 8 字符 hash，由 storyboard_hash 自动派生
        project_name: 人类可读（小写 ascii，如 "xiaohuang"）
        storyboard_path: Storyboard JSON 路径
        storyboard_hash: sha256 hex（64 字符）

        assets_dir: sandbox/assets/{project_id}/
        run_dir: sample/run_{project_id}/
        output_dir: sample/{project_name}/
        log_path: logs/{project_id}_session.jsonl

        style_anchor: 项目级风格锚点（已在 V3.4 走 _safe_style_anchor 兜底）
        aspect_ratio: "16:9" / "9:16"
        motion_budget: 每日 quota（默认 3）

        env_overrides: 进入时 apply 到 os.environ，退出 restore
    """
    project_id: str
    project_name: str
    storyboard_path: Path
    storyboard_hash: str

    assets_dir: Path
    run_dir: Path
    output_dir: Path
    log_path: Path

    style_anchor: str = ""
    aspect_ratio: str = "16:9"
    motion_budget: int = 3
    pipeline_version: str = "V3.5"

    env_overrides: dict[str, str] = field(default_factory=dict)

    # ---------- 工厂方法 ----------

    @staticmethod
    def from_storyboard_path(
        storyboard_path: Path | str,
        *,
        project_name: str | None = None,
        project_id: str | None = None,
        style_anchor: str = "",
        aspect_ratio: str = "16:9",
        motion_budget: int = 3,
    ) -> "ProjectContext":
        """从 storyboard.json 自动派生 ProjectContext。

        Args:
            storyboard_path: storyboard JSON 路径（必填，文件必须存在）
            project_name: 人类可读名（None = 从 path basename 派生）
            project_id: 自定义 hash（None = sha256(content)[:8] 自动派生）
            style_anchor: 项目级风格锚点
            aspect_ratio: 默认 16:9
            motion_budget: 默认 3

        Raises:
            ProjectContextError: storyboard 不存在 / 文件无法读 / hash 派生失败
        """
        sp = Path(storyboard_path).resolve()
        if not sp.is_file():
            raise ProjectContextError(f"storyboard 不存在：{sp}")

        try:
            content = sp.read_bytes()
        except OSError as exc:
            raise ProjectContextError(f"storyboard 无法读：{sp}：{exc}") from exc

        sb_hash = hashlib.sha256(content).hexdigest()
        pid = project_id or sb_hash[:8]
        pname = project_name or _safe_project_name(sp.parent.name)

        # 路径 namespace（V3.5 ADR-007-A/B）
        project_root = Path("sample") / pname
        assets_dir = Path("sandbox/assets/characters") / pid
        run_dir_default = Path("sample") / f"run_{pid}"
        output_dir = project_root
        log_path = Path("logs") / f"{pid}_session.jsonl"

        # 默认 env_overrides（V3.5 ADR-007-C）
        env_overrides = {
            "MINIMAX_IMAGE_ASPECT_RATIO": aspect_ratio,
            "MINIMAX_VIDEO_RESOLUTION": "1080P",
            "HAILUO_VIDEO_PROJECT_ID": pid,
        }

        return ProjectContext(
            project_id=pid,
            project_name=pname,
            storyboard_path=sp,
            storyboard_hash=sb_hash,
            assets_dir=assets_dir,
            run_dir=run_dir_default,
            output_dir=output_dir,
            log_path=log_path,
            style_anchor=style_anchor,
            aspect_ratio=aspect_ratio,
            motion_budget=motion_budget,
            env_overrides=env_overrides,
        )

    # ---------- 路径 namespace 派生 ----------

    def character_dir(self, character_name: str) -> Path:
        """角色 cache 目录：assets_dir / {character_name}/

        V3.5 ADR-007-A：path 加 project_id namespace → 不同项目不撞库
        """
        return self.assets_dir / _safe_filename(character_name)

    def character_angle_path(self, character_name: str, angle: str) -> Path:
        return self.character_dir(character_name) / f"{angle}.jpg"

    def run_dir_for_shot(self, shot_id: str) -> Path:
        """shot 级 run_dir：run_dir / {shot_id}/

        V3.5 ADR-007-B：每个 shot 独立目录，prev/next
        """
        return self.run_dir / _safe_filename(shot_id)

    # ---------- 上下文管理（env override）----------

    @contextmanager
    def apply_env(self):
        """Context manager：进入时 apply env_overrides 到 os.environ，退出 restore。

        用法：
            with ctx.apply_env():
                # 在此 scope 内 os.environ 已被覆盖
                run_pipeline(...)
            # 退出后 os.environ 已恢复
        """
        original = {k: os.environ.get(k) for k in self.env_overrides}
        try:
            for k, v in self.env_overrides.items():
                os.environ[k] = v
            yield
        finally:
            for k, prev in original.items():
                if prev is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = prev

    # ---------- 派生（用于 manifest 序列化）----------

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Path 转 __dict 路径字符串以便 JSON 序列化
        for k in ("storyboard_path", "assets_dir", "run_dir", "output_dir", "log_path"):
            d[k] = str(d[k])
        return d

    # ---------- 验证 ----------

    def __post_init__(self):
        """frozen=True 时 __post_init__ 仍可运行（在 __init__ 之前）。"""
        if not self.project_id or len(self.project_id) < 4:
            raise ProjectContextError(f"project_id 无效：{self.project_id!r}")
        if not self.storyboard_hash or len(self.storyboard_hash) < 16:
            raise ProjectContextError(f"storyboard_hash 无效：{self.storyboard_hash!r}")


# ---------- ProjectRegistry ----------

class ProjectRegistry:
    """V3.5 ADR-007-D：state / log / cleanup registry per project。

    替代 V3.3 全局 singleton state.py。
    每个 ProjectInstance 实例化时 load 自己 state.json。
    """

    STATE_DIR = Path("sandbox/.project_state")

    def __init__(self):
        self.STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._loaded: dict[str, dict] = {}

    @classmethod
    def state_path(cls, project_id: str) -> Path:
        """state.json 路径：.project_state/{project_id}.json"""
        return cls.STATE_DIR / f"{project_id}.json"

    def load(self, project_id: str) -> dict:
        """读 state.json（如不存在返空 dict）。"""
        if project_id in self._loaded:
            return self._loaded[project_id]
        path = self.state_path(project_id)
        if path.is_file():
            with path.open("r", encoding="utf-8") as f:
                self._loaded[project_id] = json.load(f)
        else:
            self._loaded[project_id] = {}
        return self._loaded[project_id]

    def save(self, project_id: str, state: dict) -> None:
        """写 state.json + 内存缓存。"""
        self.STATE_DIR.mkdir(parents=True, exist_ok=True)
        path = self.state_path(project_id)
        with path.open("w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        self._loaded[project_id] = state

    def cleanup(self, ctx: "ProjectContext", *, dry_run: bool = False) -> list[Path]:
        """清理指定 project 的 cache + state。

        Args:
            ctx: ProjectContext 实例（提供 assets_dir / run_dir 路径）
            dry_run: True 仅返回将删的路径（不实际删）

        Returns: 删除的路径列表
        """
        deleted: list[Path] = []
        candidates = [
            ctx.assets_dir,
            ctx.run_dir,
            self.state_path(ctx.project_id),
        ]
        for p in candidates:
            if not p.exists():
                continue
            if dry_run:
                deleted.append(p)
            else:
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink()
                deleted.append(p)
        return deleted


# ---------- Helpers ----------

def _safe_project_name(name: str) -> str:
    """路径 basename → 安全 project name（小写 ascii / 数字 / hyphen）。

    'day1.json' → 'day1'
    '橙猫' → 'orange_cat'（中文 → pinyin fallback 仅 ASCII）
    """
    import re
    # 仅保留字母数字 + hyphen
    safe = re.sub(r"[^a-z0-9_-]", "_", name.lower())
    # 移除开头/结尾的 _ / -
    safe = re.sub(r"^[_]+|[_]+$", "", safe)
    if not safe:
        safe = "conversation_story"
    return safe[:32]


def _safe_filename(name: str) -> str:
    """角色名 / shot_id 等路径段 → 文件系统安全名（保留中文）。

    'shot_001' → 'shot_001'
    '小黄'   → '小黄'  （中文文件名 Windows/Linux 都支持）
    'foo/bar' → 'foo_bar' （去除 / + 限制）
    'a:b'    → 'a_b' （去除 Windows 非法字符）

    设计：保留中文（与 V3.3 缓存兼容），仅移除真正的文件系统非法字符。
    """
    import re
    # 保留中文 + ASCII 字母数字 + - _ . （Windows + POSIX 兼容）
    safe = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name)
    safe = safe.strip(" ._")
    return safe or "conversation_story"


__all__ = [
    "ProjectContext",
    "ProjectContextError",
    "ProjectRegistry",
    "_safe_project_name",
    "_safe_filename",
]