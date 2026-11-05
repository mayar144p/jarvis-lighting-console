#!/usr/bin/env sh
# Haze as a desktop app - Mac/Linux (run-desktop.bat is the Windows one).
# The first time it installs what the desk needs - Node.js, Python 3 and Git
# (Homebrew on a Mac; apt, dnf or pacman on Linux, which ask for your
# password) - then fetches Electron (about 100 MB).  After that it just
# opens the desk.
cd "$(dirname "$0")" || exit 1

has() { command -v "$1" >/dev/null 2>&1; }

# install <what> <brew name> <apt names> <dnf names> <pacman names>
install() {
    echo "$1 isn't installed - installing it (once)..."
    if [ "$(uname)" = "Darwin" ]; then
        if ! has brew; then
            echo "Homebrew (the Mac's installer for tools like this) isn't here: installing it first..."
            /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" || return 1
            for b in /opt/homebrew/bin /usr/local/bin; do [ -x "$b/brew" ] && PATH="$b:$PATH"; done
        fi
        brew install $2
    elif has apt-get; then
        sudo apt-get update && sudo apt-get install -y $3
    elif has dnf; then
        sudo dnf install -y $4
    elif has pacman; then
        sudo pacman -S --noconfirm $5
    else
        echo "No installer found on this system: install $1 yourself, then run this again." >&2
        return 1
    fi
}

has node && has npm || install "Node.js" node "nodejs npm" "nodejs npm" "nodejs npm" \
    || { echo "Node.js couldn't be installed - get it from https://nodejs.org and run this again." >&2; exit 1; }
has python3 || install "Python 3" python "python3" "python3" "python" \
    || { echo "Python 3 couldn't be installed - get it from https://www.python.org and run this again." >&2; exit 1; }
# Git keeps the desk up to date; it runs without it, so a failed install doesn't stop it
has git || install "Git" git git git git || echo "Git wasn't installed: the desk runs, but won't update itself." >&2

cd desktop || exit 1
if [ ! -d node_modules/electron ]; then
    echo "Setting up the desktop app - once, a minute or two..."
    npm install --no-audit --no-fund || { echo "npm failed - check the internet connection and run this again." >&2; exit 1; }
fi
# npm can say "up to date" with Electron itself missing (its own download
# was cut off or skipped): Electron's downloader fetches it again.
if [ ! -f node_modules/electron/path.txt ] || [ ! -e "node_modules/electron/dist/$(cat node_modules/electron/path.txt 2>/dev/null)" ]; then
    echo "Fetching Electron itself - about 100 MB..."
    ELECTRON_SKIP_BINARY_DOWNLOAD= node node_modules/electron/install.js || {
        echo "Electron could not be downloaded: check the internet connection, or delete desktop/node_modules and retry." >&2; exit 1; }
fi
exec npm start
