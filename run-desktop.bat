@echo off
setlocal EnableExtensions
title Haze
rem Haze from its folder.  The first time it installs what the desk needs -
rem Node.js, Python and Git - with winget (built into Windows 10 and 11;
rem Windows asks once to allow each installer), then fetches Electron (about
rem 100 MB).  After that it just opens the desk.
cd /d "%~dp0"

call :need_node || goto failed
call :need_python || goto failed
call :need_git

cd /d "%~dp0desktop"
if exist "node_modules\electron\dist\electron.exe" goto ready
echo Setting up the desktop app - once, a minute or two...
call npm install --no-audit --no-fund
if errorlevel 1 goto npmfailed
rem npm can say "up to date" with Electron itself missing: its own download
rem (a step after npm's) was cut off, blocked, or skipped by npm settings.
rem Electron's downloader fetches it again (it checks for electron.exe).
if exist "node_modules\electron\dist\electron.exe" goto ready
if not exist "node_modules\electron\install.js" goto npmfailed
echo Fetching Electron itself - about 100 MB...
set ELECTRON_SKIP_BINARY_DOWNLOAD=
call node "node_modules\electron\install.js"
if exist "node_modules\electron\dist\electron.exe" goto ready
echo.
echo Electron could not be downloaded. Check the internet connection
echo (a firewall or a proxy can block github.com), then run this again.
echo If it keeps failing: delete the folder desktop\node_modules and retry.
pause
exit /b 1

:npmfailed
echo.
echo npm failed - check the internet connection and run this again.
pause
exit /b 1

:failed
echo.
echo Haze can't start without it. Install it from the page that opened,
echo then run this again.
pause
exit /b 1

:ready
rem A "Haze" shortcut beside this file opens the desk with no black window
rem at all (drag it to the desktop or the taskbar).  Made once.
if not exist "%~dp0Haze.lnk" powershell -NoProfile -WindowStyle Hidden -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%~dp0Haze.lnk');$s.TargetPath='%~dp0desktop\node_modules\electron\dist\electron.exe';$s.Arguments='.';$s.WorkingDirectory='%~dp0desktop';$s.Description='Haze lighting desk';$s.Save()" >nul 2>&1
rem Electron itself, not "npm start": npm keeps a terminal open for as long
rem as the desk runs.  And Electron hooks onto the window it was started
rem from (for its logs), which kept this one open but empty: told not to,
rem this window closes straight away.
set ELECTRON_NO_ATTACH_CONSOLE=1
start "" "node_modules\electron\dist\electron.exe" .
exit

rem ---------------------------------------------------------------------
rem What the desk needs, each installed when it isn't there.

:need_node
where node >nul 2>nul && where npm >nul 2>nul && exit /b 0
echo Node.js isn't installed - installing it (once)...
call :winget OpenJS.NodeJS.LTS "Node.js" https://nodejs.org || exit /b 1
call :refresh_path
where node >nul 2>nul && exit /b 0
rem (the installer's own folder, in case the new PATH isn't readable yet)
if exist "%ProgramFiles%\nodejs\node.exe" set "PATH=%ProgramFiles%\nodejs;%PATH%"
where node >nul 2>nul && exit /b 0
echo Node.js was installed, but this window can't see it yet: close it and run this again.
exit /b 1

:need_python
call :has_python && exit /b 0
echo Python isn't installed - installing it (once)...
call :winget Python.Python.3.12 "Python" https://www.python.org/downloads/ "--override" "/quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1" || exit /b 1
call :refresh_path
call :has_python && exit /b 0
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%PATH%"
call :has_python && exit /b 0
echo Python was installed, but this window can't see it yet: close it and run this again.
exit /b 1

rem A real Python 3 - not the Microsoft Store's "python" stand-in, which
rem only opens the Store
:has_python
python -c "import sys; sys.exit(0 if sys.version_info[0] == 3 else 1)" >nul 2>nul && exit /b 0
py -3 -c "import sys" >nul 2>nul && exit /b 0
exit /b 1

rem Git keeps the desk up to date (it pulls the newest version each time it
rem opens); the desk runs without it, so a failed install doesn't stop it
:need_git
where git >nul 2>nul && exit /b 0
echo Git isn't installed - installing it (once)...
call :winget Git.Git "Git" https://git-scm.com/download/win || exit /b 0
call :refresh_path
if exist "%ProgramFiles%\Git\cmd\git.exe" set "PATH=%ProgramFiles%\Git\cmd;%PATH%"
exit /b 0

rem :winget <id> <name> <download page> [extra winget arguments]
:winget
where winget >nul 2>nul
if errorlevel 1 goto winget_missing
winget install --exact --id %1 --silent --accept-source-agreements --accept-package-agreements %4 %5
if errorlevel 1 goto winget_failed
exit /b 0
:winget_missing
echo This Windows has no winget (App Installer), so %~2 can't be installed for you.
echo Its download page is opening: install it, then run this again.
start "" "%~3"
exit /b 1
:winget_failed
echo %~2 could not be installed automatically (no internet, or the install was declined).
echo Its download page is opening: install it from there, then run this again.
start "" "%~3"
exit /b 1

rem The PATH as Windows has it now (an installer only changes it for new
rem windows; this one is told directly)
:refresh_path
for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')"`) do set "PATH=%%P"
exit /b 0
