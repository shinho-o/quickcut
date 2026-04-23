@echo off
setlocal

REM ─── ffmpeg PATH 추가 (winget 기본 설치 경로) ───
set FF_DIR=%LocalAppData%\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe
for /d %%d in ("%FF_DIR%\ffmpeg-*-full_build") do set "PATH=%%d\bin;%PATH%"

echo.
echo  QuickCut 시작 중...
echo.
python app.py
