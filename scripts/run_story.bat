@echo off
REM scripts/run_story.bat — 批量跑 StoryArc 的 1 天 quota（默认 3 条）
REM
REM 用法（cmd / PowerShell）：
REM   cd d:\ai-tools\ai_video\sandbox
REM   ..\scripts\run_story.bat <story_dir>
REM
REM 例：
REM   ..\scripts\run_story.bat ..\sample\story_arc_orange_cat_4_seasons\day1_storyboards

setlocal EnableDelayedExpansion

set "PROJECT_ROOT=%~dp0.."
set "SANDBOX_DIR=%PROJECT_ROOT%\sandbox"
set "SAMPLE_DIR=%PROJECT_ROOT%\sample"

if "%~1"=="" (
    echo Usage: run_story.bat ^<story_dir^> 1>&2
    exit /b 2
)

set "STORY_DIR=%~1"
pushd "%STORY_DIR%" && set "STORY_DIR=%CD%" && popd

REM 自动生成 storyboards（如目录为空）
set "HAS_SB=0"
for %%f in ("%STORY_DIR%\*.json") do set "HAS_SB=1"
if "!HAS_SB!"=="0" (
    set "ARC_DIR=%STORY_DIR:~0,-15%"  REM 去掉 "_storyboards" 后缀（不一定准）
    set "ARC_JSON="
    for %%f in ("!ARC_DIR!\day*.json") do (
        if "!ARC_JSON!"=="" set "ARC_JSON=%%f"
    )
    if "!ARC_JSON!"=="" (
        echo [FAIL] 没找到 story_arc.json in !ARC_DIR! 1>&2
        exit /b 2
    )
    echo [INFO] !STORY_DIR! 空，从 !ARC_JSON! 自动生成 storyboards...
    python "%PROJECT_ROOT%\scripts\build_storyboards.py" "!ARC_JSON!" "!STORY_DIR!"
)

cd /d "%SANDBOX_DIR%"
set "MINIMAX_IMAGE_ASPECT_RATIO=9:16"
set "PYTHONIOENCODING=utf-8"

set "STORY_NAME=story_%~n1"

set count=0
for %%s in ("%STORY_DIR%\*.json") do (
    set /a count+=1
    set "SHOT_ID=%%~ns"
    set "OUT=%SAMPLE_DIR%\!STORY_NAME:~6!_!SHOT_ID!.mp4"
    set "RUN_DIR=%SAMPLE_DIR%\run_!STORY_NAME:~6!_!SHOT_ID!"
    echo.
    echo === !count!: !SHOT_ID! ===
    python -m hailuo_video pipeline "%%s" -o "!OUT!" --image-backend minimax --tts-backend mock --motion-budget 1 --parallel 1 --run-dir "!RUN_DIR!"

    if exist "!RUN_DIR!\intermediate\shot_000_motion.mp4" (
        copy /Y "!RUN_DIR!\intermediate\shot_000_motion.mp4" "!OUT!" >nul
        echo [OK] !SHOT_ID!.mp4 saved
    )
)

echo.
echo === 全部完成：!count! 条 ===
dir /b "%SAMPLE_DIR%\story_*.mp4" 2>nul
