@echo off
REM scripts/regen_3_videos.bat — 用户假期回来后一键重生 3 条视频（消耗 3 Hailuo quota）
REM
REM 用法（Windows cmd / PowerShell）：
REM   cd d:\ai-tools\ai_video\sandbox
REM   ..\scripts\regen_3_videos.bat
REM
REM 输出：
REM   ..\sample\v1_golden_retriever.mp4         (覆盖)
REM   ..\sample\v2_orange_cat.mp4              (覆盖)
REM   ..\sample\v3_fawn_forest.mp4             (覆盖)

setlocal EnableDelayedExpansion

set "PROJECT_ROOT=%~dp0.."
set "SAMPLE_DIR=%PROJECT_ROOT%\sample"
set "SANDBOX_DIR=%PROJECT_ROOT%\sandbox"

cd /d "%SANDBOX_DIR%"

set "MINIMAX_IMAGE_ASPECT_RATIO=9:16"
set "PYTHONIOENCODING=utf-8"

REM V1 — 金毛犬（复杂全身运动 — Hailuo 已知限制）
echo === V1: 金毛犬麦田 (Hailuo, complex motion) ===
python -m hailuo_video pipeline "%SAMPLE_DIR%\storyboard_golden_retriever.json" -o "%SAMPLE_DIR%\v1_golden_retriever.mp4" --image-backend minimax --tts-backend mock --motion-budget 1 --parallel 1 --run-dir "%SAMPLE_DIR%\run_v1_regen"
if exist "%SAMPLE_DIR%\run_v1_regen\intermediate\shot_000_motion.mp4" (
    copy /Y "%SAMPLE_DIR%\run_v1_regen\intermediate\shot_000_motion.mp4" "%SAMPLE_DIR%\v1_golden_retriever.mp4" >nul
    echo [OK] V1 -^> %SAMPLE_DIR%\v1_golden_retriever.mp4
)

echo.

REM V2 — 橘猫（简单转头 → 应能做好）
echo === V2: 橘猫窗台 (Hailuo, simple motion) ===
python -m hailuo_video pipeline "%SAMPLE_DIR%\storyboard_orange_cat.json" -o "%SAMPLE_DIR%\v2_orange_cat.mp4" --image-backend minimax --tts-backend mock --motion-budget 1 --parallel 1 --run-dir "%SAMPLE_DIR%\run_v2_regen"
if exist "%SAMPLE_DIR%\run_v2_regen\intermediate\shot_000_motion.mp4" (
    copy /Y "%SAMPLE_DIR%\run_v2_regen\intermediate\shot_000_motion.mp4" "%SAMPLE_DIR%\v2_orange_cat.mp4" >nul
    echo [OK] V2 -^> %SAMPLE_DIR%\v2_orange_cat.mp4
)

echo.

REM V3 — 梅花鹿（简单转头 → 应能做好）
echo === V3: 梅花鹿晨雾森林 (Hailuo, simple motion) ===
python -m hailuo_video pipeline "%SAMPLE_DIR%\storyboard_fawn_forest.json" -o "%SAMPLE_DIR%\v3_fawn_forest.mp4" --image-backend minimax --tts-backend mock --motion-budget 1 --parallel 1 --run-dir "%SAMPLE_DIR%\run_v3_regen"
if exist "%SAMPLE_DIR%\run_v3_regen\intermediate\shot_000_motion.mp4" (
    copy /Y "%SAMPLE_DIR%\run_v3_regen\intermediate\shot_000_motion.mp4" "%SAMPLE_DIR%\v3_fawn_forest.mp4" >nul
    echo [OK] V3 -^> %SAMPLE_DIR%\v3_fawn_forest.mp4
)

echo.
echo === 全部完成 ===
dir /b "%SAMPLE_DIR%\v*.mp4"
