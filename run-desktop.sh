#!/usr/bin/env sh
# Jarvis as a desktop app - Mac/Linux (run-desktop.bat is the Windows one).
# The first time it fetches Electron (about 100 MB).
cd "$(dirname "$0")/desktop" || exit 1
if [ ! -d node_modules/electron ]; then
    echo "Setting up the desktop app - once, a minute or two..."
    npm install --no-audit --no-fund || { echo "npm failed. Is Node.js installed? https://nodejs.org" >&2; exit 1; }
fi
exec npm start
