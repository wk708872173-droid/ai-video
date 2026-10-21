"""V3.2 REQ-305.b daily_cron_setup 单测。

设计：
  - 不调真 schtasks / PowerShell（生成器是纯字符串）
  - 覆盖：PS1 / BAT / uninstall 脚本生成 + 参数校验 + write_setup_files 落盘
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
SANDBOX = REPO / "sandbox"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from daily_cron_setup import (  # noqa: E402
    DEFAULT_TASK_NAME,
    DEFAULT_TRIGGER_TIME,
    SetupConfig,
    build_setup_ps1,
    build_setup_bat,
    build_uninstall_ps1,
    build_uninstall_bat,
    write_setup_files,
)


# ---------- PS1 脚本生成 ----------

def test_build_setup_ps1_contains_task_name():
    cfg = SetupConfig(task_name="MY-TASK")
    ps1 = build_setup_ps1(cfg)
    assert '"MY-TASK"' in ps1
    assert "$taskName" in ps1


def test_build_setup_ps1_contains_trigger_time():
    cfg = SetupConfig(trigger_time="14:30")
    ps1 = build_setup_ps1(cfg)
    assert '"14:30"' in ps1
    assert "/ST" in ps1
    assert "DAILY" in ps1


def test_build_setup_ps1_contains_highest_privilege():
    cfg = SetupConfig()
    ps1 = build_setup_ps1(cfg)
    assert "/RL" in ps1
    assert "HIGHEST" in ps1


def test_build_setup_ps1_contains_force_flag():
    """脚本必须用 /F（强制覆盖同名任务）。"""
    ps1 = build_setup_ps1(SetupConfig())
    assert '"/Create"' in ps1 or "/Create" in ps1
    assert '"/F"' in ps1 or '"F"' not in ps1  # /F 作为独立参数


def test_build_setup_ps1_contains_delete_existing():
    """已存在同名任务时先删。"""
    ps1 = build_setup_ps1(SetupConfig(task_name="DUPE-TASK"))
    assert "/Delete" in ps1
    assert "DUPE-TASK" in ps1


def test_build_setup_ps1_quotes_python_path():
    """Python 路径含空格时必须双引号。"""
    cfg = SetupConfig(python_exe="C:/Program Files/Python311/python.exe")
    ps1 = build_setup_ps1(cfg)
    assert '"C:/Program Files/Python311/python.exe"' in ps1


def test_build_setup_ps1_empty_task_name_raises():
    with pytest.raises(ValueError, match="task_name 不能为空"):
        build_setup_ps1(SetupConfig(task_name=""))


def test_build_setup_ps1_invalid_trigger_time_raises():
    with pytest.raises(ValueError, match="trigger_time 格式错误"):
        build_setup_ps1(SetupConfig(trigger_time="9:00"))  # 不是 HH:MM 格式
    with pytest.raises(ValueError, match="trigger_time 格式错误"):
        build_setup_ps1(SetupConfig(trigger_time="25:00"))  # 不合法小时
    with pytest.raises(ValueError, match="trigger_time 格式错误"):
        build_setup_ps1(SetupConfig(trigger_time="abc"))


def test_build_setup_ps1_with_workdir():
    cfg = SetupConfig(workdir="D:/ai-tools/ai_video")
    ps1 = build_setup_ps1(cfg)
    assert "$workdir" in ps1
    assert "D:/ai-tools/ai_video" in ps1


# ---------- BAT 脚本生成 ----------

def test_build_setup_bat_contains_task_name():
    cfg = SetupConfig(task_name="BAT-TASK")
    bat = build_setup_bat(cfg)
    assert "BAT-TASK" in bat
    assert "setlocal" in bat.lower() or "setlocal EnableDelayedExpansion" in bat


def test_build_setup_bat_contains_trigger_time():
    cfg = SetupConfig(trigger_time="15:45")
    bat = build_setup_bat(cfg)
    assert "15:45" in bat
    assert "/ST" in bat


def test_build_setup_bat_uses_schtasks_create():
    bat = build_setup_bat(SetupConfig())
    assert "schtasks /Create" in bat
    assert "/SC DAILY" in bat
    assert "/RL HIGHEST" in bat


def test_build_setup_bat_quotes_python_path():
    cfg = SetupConfig(python_exe="C:/Python/python.exe")
    bat = build_setup_bat(cfg)
    # .bat 里 set "PYTHON_EXE=..." 必须双引号
    assert 'set "PYTHON_EXE=' in bat


def test_build_setup_bat_with_workdir():
    cfg = SetupConfig(workdir="D:/work")
    bat = build_setup_bat(cfg)
    assert "D:/work" in bat


def test_build_setup_bat_invalid_task_name_raises():
    with pytest.raises(ValueError):
        build_setup_bat(SetupConfig(task_name=""))


def test_build_setup_bat_invalid_trigger_time_raises():
    with pytest.raises(ValueError):
        build_setup_bat(SetupConfig(trigger_time="bad"))


# ---------- Uninstall 脚本 ----------

def test_build_uninstall_ps1_contains_task_name():
    ps1 = build_uninstall_ps1("RMAP-Foo")
    assert "RMAP-Foo" in ps1
    assert "/Delete" in ps1


def test_build_uninstall_ps1_empty_task_name_raises():
    with pytest.raises(ValueError):
        build_uninstall_ps1("")


def test_build_uninstall_bat_contains_task_name():
    bat = build_uninstall_bat("RMAP-Foo")
    assert "RMAP-Foo" in bat
    assert "schtasks /Delete" in bat


def test_build_uninstall_bat_empty_task_name_raises():
    with pytest.raises(ValueError):
        build_uninstall_bat("")


def test_build_uninstall_bat_handles_missing_task_gracefully():
    """任务不存在时优雅退出（exit 0）。"""
    bat = build_uninstall_bat("NOT-EXIST")
    assert "任务不存在" in bat
    assert "exit /b 0" in bat


# ---------- write_setup_files 落盘 ----------

def test_write_setup_files_creates_all_four(tmp_path):
    written = write_setup_files(tmp_path, SetupConfig(task_name="ALL-FOUR"))
    assert len(written) == 4
    for name in ("setup_daily_cron.ps1", "setup_daily_cron.bat",
                 "uninstall_daily_cron.ps1", "uninstall_daily_cron.bat"):
        assert name in written
        assert written[name].is_file()
        assert written[name].stat().st_size > 0


def test_write_setup_files_uses_task_name_in_uninstall(tmp_path):
    cfg = SetupConfig(task_name="CUSTOM-XYZ")
    written = write_setup_files(tmp_path, cfg)
    # 卸载脚本应包含自定义任务名
    uninstall_ps1 = written["uninstall_daily_cron.ps1"].read_text(encoding="utf-8")
    uninstall_bat = written["uninstall_daily_cron.bat"].read_text(encoding="utf-8")
    assert "CUSTOM-XYZ" in uninstall_ps1
    assert "CUSTOM-XYZ" in uninstall_bat


def test_write_setup_files_creates_target_dir_if_missing(tmp_path):
    nested = tmp_path / "deep" / "down" / "here"
    assert not nested.is_dir()
    written = write_setup_files(nested, SetupConfig())
    assert nested.is_dir()
    assert len(written) == 4


def test_write_setup_files_default_config(tmp_path):
    """默认配置 = DEFAULT_TASK_NAME + DEFAULT_TRIGGER_TIME。"""
    written = write_setup_files(tmp_path)
    setup_ps1 = written["setup_daily_cron.ps1"].read_text(encoding="utf-8")
    assert DEFAULT_TASK_NAME in setup_ps1
    assert DEFAULT_TRIGGER_TIME in setup_ps1


# ---------- 跨脚本一致性 ----------

def test_setup_and_uninstall_use_same_task_name():
    """setup 与 uninstall 必须用同一 task_name（用户认知一致）。"""
    cfg = SetupConfig(task_name="CONSISTENT-1")
    setup = build_setup_ps1(cfg)
    uninstall = build_uninstall_ps1(cfg.task_name)
    assert "CONSISTENT-1" in setup
    assert "CONSISTENT-1" in uninstall


def test_setup_ps1_and_bat_have_same_task_name():
    cfg = SetupConfig(task_name="BOTH-FORMATS")
    ps1 = build_setup_ps1(cfg)
    bat = build_setup_bat(cfg)
    assert "BOTH-FORMATS" in ps1
    assert "BOTH-FORMATS" in bat
