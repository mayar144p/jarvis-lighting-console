@echo off
title Jarvis
cd /d "%~dp0desktop"
rem The desk as a desktop app: the first time it fetches Electron (about 100 MB).
if not exist "node_modules\electron\dist\electron.exe" (
    echo Setting up the desktop app - once, a minute or two...
    call npm install --no-audit --no-fund
    if errorlevel 1 (
        echo.
        echo npm failed. Is Node.js installed? https://nodejs.org
        pause
        exit /b 1
    )
)
rem A "Jarvis" shortcut beside this file opens the desk with no black window
rem at all (drag it to the desktop or the taskbar).  Made once.
if not exist "%~dp0Jarvis.lnk" powershell -NoProfile -WindowStyle Hidden -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%~dp0Jarvis.lnk');$s.TargetPath='%~dp0desktop\node_modules\electron\dist\electron.exe';$s.Arguments='.';$s.WorkingDirectory='%~dp0desktop';$s.Description='Jarvis lighting desk';$s.Save()" >nul 2>&1
rem Electron itself, not "npm start": npm keeps a terminal open for as long
rem as the desk runs.  And Electron hooks onto the window it was started
rem from (for its logs), which kept this one open but empty: told not to,
rem this window closes straight away.
set ELECTRON_NO_ATTACH_CONSOLE=1
start "" "node_modules\electron\dist\electron.exe" .
exit
