"""D2：打包元数据回归护栏（docs/drafts/cli_invocation_path_draft.md）。

背景：修复前仓库无 pyproject.toml，`python -m hailuo_video ...` 从仓库根直接报
`No module named hailuo_video`，而 README 记的正是这个写法。

本文件锁住三件容易静默退化的事：
  1. pyproject.toml 存在且可解析
  2. 版本号能从 `__init__.__version__` 取到（曾因缺 [tool.setuptools.dynamic]
     而静默产出 0.0.0 的 wheel —— 那种失败不报错，最难发现）
  3. 包发现映射正确：含 3 个真包，排除 runtime/ 数据目录
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

tomllib = pytest.importorskip("tomllib", reason="tomllib 需 Python 3.11+")

REPO_ROOT = Path(__file__).resolve().parents[1]
SANDBOX = REPO_ROOT / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

import hailuo_video  # noqa: E402


@pytest.fixture(scope="module")
def pyproject() -> dict:
    p = REPO_ROOT / "pyproject.toml"
    assert p.is_file(), "pyproject.toml 缺失 —— README 的 python -m hailuo_video 会失败"
    return tomllib.loads(p.read_text(encoding="utf-8"))


def test_package_dir_maps_to_sandbox(pyproject):
    """包在 sandbox/ 下，非标准布局；映射丢了构建就找不到包。"""
    assert pyproject["tool"]["setuptools"]["package-dir"] == {"": "sandbox"}


def test_version_is_read_from_dunder_init(pyproject):
    """必须显式声明 dynamic.version 位置，否则 wheel 版本静默变 0.0.0。"""
    dyn = pyproject["tool"]["setuptools"]["dynamic"]
    assert dyn["version"]["attr"] == "hailuo_video.__version__"
    assert "version" in pyproject["project"]["dynamic"]


def test_declared_version_matches_dunder_init(pyproject):
    """F5：__version__ 曾长期停在 2.0.0，与实际（V3.5）脱节。

    这里锁的是"与 docs/ISOLATION.md 的 V3.5 对齐"，不是钉死某个字符串 ——
    真要升版时改 __init__ 与本断言即可，但别让它再悄悄落后于架构版本。
    """
    assert hailuo_video.__version__ == "3.5.0"
    isolation = (REPO_ROOT / "docs" / "ISOLATION.md").read_text(encoding="utf-8")
    major_minor = ".".join(hailuo_video.__version__.split(".")[:2])
    assert f"V{major_minor}" in isolation, (
        f"__version__={hailuo_video.__version__} 与 docs/ISOLATION.md 的架构版本对不上"
    )


def test_package_discovery_finds_real_packages_only():
    """3 个真包；runtime/ 只是 runs.jsonl 数据目录，不该被当成包。"""
    from setuptools import find_packages

    found = set(find_packages(
        where=str(SANDBOX),
        include=["hailuo_video*"],
        exclude=["hailuo_video.runtime*"],
    ))
    assert found == {"hailuo_video", "hailuo_video.editing", "hailuo_video.quality"}


def test_runtime_is_a_data_dir_not_a_package():
    assert not (SANDBOX / "hailuo_video" / "runtime" / "__init__.py").exists()


def test_core_dependencies_match_actual_imports(pyproject):
    """依赖清单不能凭印象写 —— 应对得上真实 import。"""
    deps = " ".join(pyproject["project"]["dependencies"]).lower()
    assert "pillow" in deps
    assert "imageio-ffmpeg" in deps
    assert "edge-tts" in deps
    # torch/diffusers 只在 try 块里 import（本地视频路径），是可选依赖
    core = " ".join(pyproject["project"]["dependencies"]).lower()
    assert "torch" not in core and "diffusers" not in core
    local = " ".join(pyproject["project"]["optional-dependencies"]["local-video"]).lower()
    assert "torch" in local and "diffusers" in local


def test_testpaths_point_at_tests_dir(pyproject):
    """避免裸 `pytest` 误收集 sample/ 等目录。"""
    assert pyproject["tool"]["pytest"]["ini_options"]["testpaths"] == ["tests"]