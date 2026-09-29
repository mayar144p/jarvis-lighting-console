@echo off
title Jarvis - Lighting Assistant
cd /d "%~dp0"

rem Everything runs inside ONE block: cmd reads a block whole before running
rem it, so the update below can safely replace this very file.
(
    if not exist ".env" if exist ".env.example" (
        copy ".env.example" ".env" >nul
        echo Created .env from .env.example - edit it to add your LLM_API_KEY.
    )

    rem Fetch the latest version first ^(skips itself offline or with local edits^).
    python "tools\update.py"

    start "" "http://localhost:8787/"
    python "app\main.py"
    if errorlevel 1 (
        echo.
        echo Python failed to start. Do you have Python 3 installed?
        pause
    )
    exit /b
)
