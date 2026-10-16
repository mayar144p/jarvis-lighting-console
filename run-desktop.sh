#!/usr/bin/env sh
# Jarvis as a desktop app - Mac/Linux (run-desktop.bat is the Windows one).
# The first time it fetches Electron (about 100 MB).
cd "$(dirname "$0")/desktop" || exit 1
if [ ! -d node_modules/electron ]; then
    echo "Setting up the desktop app - once, a minute or two..."
    npm install --no-audit --no-fund || { echo "npm failed. Is Node.js installed? https://nodejs.org" >&2; exit 1; }
fi
# npm can say "up to date" with Electron itself missing (its own download
# was cut off or skipped): Electron's downloader fetches it again.
if [ ! -f node_modules/electron/path.txt ] || [ ! -e "node_modules/electron/dist/$(cat node_modules/electron/path.txt 2>/dev/null)" ]; then
    echo "Fetching Electron itself - about 100 MB..."
    ELECTRON_SKIP_BINARY_DOWNLOAD= node node_modules/electron/install.js || {
        echo "Electron could not be downloaded: check the internet connection, or delete desktop/node_modules and retry." >&2; exit 1; }
fi
exec npm start
