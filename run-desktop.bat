@echo off
title Jarvis
cd /d "%~dp0desktop"
rem The desk as a desktop app: the first time it fetches Electron (about 100 MB).
if not exist "node_modules\electron" (
    echo Setting up the desktop app - once, a minute or two...
    call npm install --no-audit --no-fund
    if errorlevel 1 (
        echo.
        echo npm failed. Is Node.js installed? https://nodejs.org
        pause
        exit /b 1
    )
)
start "" npm start
