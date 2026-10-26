@echo off
REM scripts/midnight_recovery.bat — 凌晨 00:00 自动恢复脚本
REM
REM 任务：
REM   1. 重跑 4 个 shot (shot_003 + shot_004 + shot_005 + shot_006, 共 4 个 Hailuo quota)
REM   2. 应用 T1 cinema 滤镜 (audio + LUT + 颗粒)
REM   3. 拼接 6 个 shot 为完整 mp4
REM   4. 输出 sample\story_orange_cat_hailuo_only_6shots.mp4
REM
REM 用法：
REM   - Windows Task Scheduler 登记每日 00:00 自动运行：
REM     schtasks /create /tn "RMAP-Story-Recovery" /tr "d:\ai-tools\ai_video\scripts\midnight_recovery.bat" /sc once /st 00:00 /sd 10/09/2026
REM   - 或者手动跑：直接双击执行
REM
REM 设计原则：
REM   - 全程在 sandbox\ 下跑（pipeline 期望相对路径）
REM   - 任一 shot 失败不中断整体流程（其他 shot 继续跑）
REM   - 产物文件用最清晰的命名 + 时间戳

setlocal EnableDelayedExpansion
set "PROJECT_ROOT=D:\ai-tools\ai_video"
set "SAMPLE_DIR=%PROJECT_ROOT%\sample"
set "SANDBOX_DIR=%PROJECT_ROOT%\sandbox"
set "STORY_DIR=%SAMPLE_DIR%\story_arc_orange_cat_4_seasons"

REM 时间戳（用于日志文件命名）
for /f "tokens=2 delims==" %%a in ('wmic os get localdatetime /value') do set "TIMESTAMP=%%a"
set "TIMESTAMP=%TIMESTAMP:~0,8%_%TIMESTAMP:~8,6%"
set "LOG_FILE=%PROJECT_ROOT%\logs\midnight_recovery_%TIMESTAMP%.log"

echo === RMAP Story Recovery %date% %time% === > "%LOG_FILE%"
echo [INFO] Start midnight recovery script >> "%LOG_FILE%"

REM 设置 ffmpeg 路径
for /f "delims=" %%a in ('python -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"') do set "FFMPEG=%%a"
echo [INFO] ffmpeg=%FFMPEG% >> "%LOG_FILE%"

set "MINIMAX_IMAGE_ASPECT_RATIO=9:16"
set "PYTHONIOENCODING=utf-8"

cd /d "%SANDBOX_DIR%"

REM ========== Step 1: 重跑 4 个 shot ==========
echo. >> "%LOG_FILE%"
echo [STEP 1] Re-running 4 shots (shot_003 + shot_004-006) >> "%LOG_FILE%"

REM shot_003 (Day 1, 1 quota)
echo. >> "%LOG_FILE%"
echo --- shot_003 --- >> "%LOG_FILE%"
set "RUN_DIR=%SAMPLE_DIR%\run_story_arc_orange_cat_4_seasons_shot_003"
set "OUT=%SAMPLE_DIR%\story_arc_orange_cat_4_seasons_shot_003.mp4"
python -m hailuo_video pipeline "%STORY_DIR%\day1_storyboards\shot_003.json" -o "%OUT%" --image-backend minimax --tts-backend mock --motion-budget 3 --parallel 1 --run-dir "%RUN_DIR%" >> "%LOG_FILE%" 2>&1
set "RC_003=%ERRORLEVEL%"
echo [shot_003 exit=%RC_003%] >> "%LOG_FILE%"

REM Day 2 (3 shots)
for %%S in (shot_004 shot_005 shot_006) do (
    echo. >> "%LOG_FILE%"
    echo --- %%S --- >> "%LOG_FILE%"
    set "RUN_DIR=%SAMPLE_DIR%\run_story_arc_orange_cat_4_seasons_%%S"
    set "OUT=%SAMPLE_DIR%\story_arc_orange_cat_4_seasons_%%S.mp4"
    python -m hailuo_video pipeline "%STORY_DIR%\day2_storyboards\%%S.json" -o "%OUT%" --image-backend minimax --tts-backend mock --motion-budget 3 --parallel 1 --run-dir "!RUN_DIR!" >> "%LOG_FILE%" 2>&1
    echo [%%S exit=!ERRORLEVEL!] >> "%LOG_FILE%"
)

REM ========== Step 2: 应用 T1 cinema 滤镜 ==========
echo. >> "%LOG_FILE%"
echo [STEP 2] Apply T1 cinema filters to all 6 shots >> "%LOG_FILE%"
python "%PROJECT_ROOT%\scripts\cinematic_upgrade.py" "%STORY_DIR%\day1_storyboards" --suffix _realT2 >> "%LOG_FILE%" 2>&1
python "%PROJECT_ROOT%\scripts\cinematic_upgrade.py" "%STORY_DIR%\day2_storyboards" --suffix _realT2 >> "%LOG_FILE%" 2>&1

REM ========== Step 3: 拼接为完整 mp4 ==========
echo. >> "%LOG_FILE%"
echo [STEP 3] Stitch 6 shots into final mp4 >> "%LOG_FILE%"
python "%PROJECT_ROOT%\scripts\stitch_story.py" "%SAMPLE_DIR%\story_orange_cat_hailuo_only_6shots.mp4" ^
    "%SAMPLE_DIR%\story_arc_orange_cat_4_seasons_shot_001_realT2.mp4" ^
    "%SAMPLE_DIR%\story_arc_orange_cat_4_seasons_shot_002_realT2.mp4" ^
    "%SAMPLE_DIR%\story_arc_orange_cat_4_seasons_shot_003_realT2.mp4" ^
    "%SAMPLE_DIR%\story_arc_orange_cat_4_seasons_shot_004_realT2.mp4" ^
    "%SAMPLE_DIR%\story_arc_orange_cat_4_seasons_shot_005_realT2.mp4" ^
    "%SAMPLE_DIR%\story_arc_orange_cat_4_seasons_shot_006_realT2.mp4" ^
    --transition fade --overlap 0.5 >> "%LOG_FILE%" 2>&1

echo. >> "%LOG_FILE%"
echo === Recovery done at %date% %time% === >> "%LOG_FILE%"

REM 验证产物
if exist "%SAMPLE_DIR%\story_orange_cat_hailuo_only_6shots.mp4" (
    echo [OK] Final: %SAMPLE_DIR%\story_orange_cat_hailuo_only_6shots.mp4
    for %%F in ("%SAMPLE_DIR%\story_orange_cat_hailuo_only_6shots.mp4") do echo   size=%%~zF bytes
) else (
    echo [FAIL] Final mp4 not produced. See %LOG_FILE%
)

echo. >> "%LOG_FILE%"
echo --- shot_000_motion.mp4 status per shot --- >> "%LOG_FILE%"
for %%S in (shot_001 shot_002 shot_003 shot_004 shot_005 shot_006) do (
    set "MP=%SAMPLE_DIR%\run_story_arc_orange_cat_4_seasons_%%S\intermediate\shot_000_motion.mp4"
    if exist "!MP!" (
        for %%F in ("!MP!") do echo   %%S: REAL HAILUO (%%~zF bytes) >> "%LOG_FILE%"
    ) else (
        echo   %%S: Ken Burns fallback (no shot_000_motion.mp4) >> "%LOG_FILE%"
    )
)

echo. >> "%LOG_FILE%"
echo === End of log === >> "%LOG_FILE%"
