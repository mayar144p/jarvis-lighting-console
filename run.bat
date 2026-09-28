@echo off
title Jarvis - Lighting Assistant
cd /d "%~dp0"

if not exist ".env" if exist ".env.example" (
    copy ".env.example" ".env" >nul
    echo Created .env from .env.example - edit it to add your LLM_API_KEY.
)

start "" "http://localhost:8787/"
python "app\main.py"
if errorlevel 1 (
    echo.
    echo Python failed to start. Do you have Python 3 installed?
    pause
)
