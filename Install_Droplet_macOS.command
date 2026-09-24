#!/bin/bash
# Droplet macOS installer
# - Locates a working Python 3.10+ interpreter
# - Creates a dedicated virtual environment for Droplet (so its dependencies
#   never collide with system-wide packages or other projects)
# - Installs/updates dependencies from requirements.txt inside that venv,
#   falling back to requirements_new.txt (newer pins) if the first set
#   can't be installed - e.g. on a Python too recent for the pinned wheels
# - Builds a Droplet.app bundle in ~/Applications that launches via the venv
# - Detects an existing install (from the shortcut it points at):
#     same folder      -> updates/repairs it
#     other Droplet    -> asks whether to switch the shortcut to this copy
#     folder now gone  -> replaces the stale shortcut
#
# On any failure the user is shown a clear dialog and installation stops.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$HOME/Applications/Droplet.app"
REQ_DIR="$SCRIPT_DIR/assets/assimilation_guides"
REQ_FILE="$REQ_DIR/requirements.txt"
REQ_FILE_NEW="$REQ_DIR/requirements_new.txt"
VENV_DIR="$SCRIPT_DIR/.venv"
MIN_VERSION="3.10.0"

show_info() {
    osascript -e "display dialog \"$1\" with title \"Droplet\" buttons {\"OK\"} default button \"OK\""
}

show_error() {
    osascript -e "display dialog \"$1\" with title \"Droplet - Installation problem\" buttons {\"OK\"} default button \"OK\" with icon stop"
}

# true if version $1 >= $2 (both like "3.11.4"); avoids relying on GNU sort -V,
# which macOS's built-in bash/sort don't support
version_ge() {
    local IFS=.
    local -a v1=($1) v2=($2)
    local i
    for i in 0 1 2; do
        local a=${v1[i]:-0}
        local b=${v2[i]:-0}
        if ((10#$a > 10#$b)); then return 0; fi
        if ((10#$a < 10#$b)); then return 1; fi
    done
    return 0
}

find_python() {
    local candidates=()

    # 1) Whatever "python3" resolves to on PATH right now
    if command -v python3 &> /dev/null; then
        candidates+=("$(command -v python3)")
    fi

    # 2) Common install locations: Anaconda/Miniconda, Homebrew (Apple
    #    Silicon and Intel paths), python.org's framework build
    for p in \
        "$HOME/anaconda3/bin/python3" \
        "$HOME/miniconda3/bin/python3" \
        "/opt/anaconda3/bin/python3" \
        "/opt/miniconda3/bin/python3" \
        "/opt/homebrew/bin/python3" \
        "/usr/local/bin/python3" \
        /Library/Frameworks/Python.framework/Versions/3.*/bin/python3
    do
        [ -x "$p" ] && candidates+=("$p")
    done

    # 3) The Apple-provided /usr/bin/python3 stub - only trust it if Xcode
    #    Command Line Tools are installed. Otherwise invoking it pops up an
    #    "install command line tools" dialog and hangs.
    if xcode-select -p &> /dev/null && [ -x "/usr/bin/python3" ]; then
        candidates+=("/usr/bin/python3")
    fi

    local c
    for c in "${candidates[@]}"; do
        if "$c" --version &> /dev/null; then
            echo "$c"
            return 0
        fi
    done
    return 1
}

# Version string from a Droplet folder's VERSION file, or "unknown"
# (assets/about/VERSION; older copies keep it at the folder root)
droplet_version() {
    local v
    v="$(head -n1 "$1/assets/about/VERSION" 2>/dev/null || head -n1 "$1/VERSION" 2>/dev/null)"
    v="$(printf '%s' "$v" | tr -d '[:space:]')"
    echo "${v:-unknown}"
}

# Returns 0 when the user clicks "Use this copy"
ask_use_this_copy() {
    local answer
    answer="$(osascript -e "button returned of (display dialog \"$1\" with title \"Droplet - Already installed\" buttons {\"Keep existing\", \"Use this copy\"} default button \"Use this copy\" with icon caution)" 2>/dev/null)"
    [ "$answer" = "Use this copy" ]
}

# Check whether Droplet is already installed, and from which folder, before
# doing any work - so choosing "Keep existing" leaves everything untouched.
LAUNCHER="$APP_DIR/Contents/MacOS/Droplet"
EXISTING_ROOT=""
[ -f "$LAUNCHER" ] && EXISTING_ROOT="$(sed -n 's/^cd "\(.*\)"$/\1/p' "$LAUNCHER" | head -n1)"
# A Droplet.app without a readable launcher is treated as a stale install
[ -z "$EXISTING_ROOT" ] && [ -d "$APP_DIR" ] && EXISTING_ROOT="$APP_DIR"
EXISTING_STATE=none   # none | same | other | stale
if [ -n "$EXISTING_ROOT" ]; then
    THIS_REAL="$(cd "$SCRIPT_DIR" && pwd -P)"
    EXISTING_REAL="$(cd "$EXISTING_ROOT" 2>/dev/null && pwd -P)"
    if [ "$EXISTING_REAL" = "$THIS_REAL" ]; then
        EXISTING_STATE=same
    elif [ -n "$EXISTING_REAL" ] && [ -f "$EXISTING_REAL/Droplet.py" ]; then
        EXISTING_STATE=other
        MSG=$'Droplet is already installed from another folder:\n'"$EXISTING_ROOT"$'  (version '"$(droplet_version "$EXISTING_ROOT")"$')\n\nThis installer is for:\n'"$SCRIPT_DIR"$'  (version '"$(droplet_version "$SCRIPT_DIR")"$')\n\nSwitch the Droplet app in ~/Applications to this copy? The other folder will not be modified or deleted.'
        if ! ask_use_this_copy "$MSG"; then
            show_info $'Installation cancelled. Droplet is still installed from:\n'"$EXISTING_ROOT"
            exit 0
        fi
    else
        EXISTING_STATE=stale
    fi
fi

BASE_PYTHON="$(find_python)"
if [ -z "$BASE_PYTHON" ]; then
    MSG=$'Droplet needs Python 3.10 or newer, but no working Python installation could be found on this Mac.\n\nPlease install Python from https://www.python.org/downloads/macos/ (or via Homebrew: brew install python), then run this installer again.\n\nInstallation has been stopped.'
    show_error "$MSG"
    exit 1
fi

BASE_VERSION="$("$BASE_PYTHON" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null)"
if [ -z "$BASE_VERSION" ] || ! version_ge "$BASE_VERSION" "$MIN_VERSION"; then
    MSG=$'Found Python '"$BASE_VERSION"$' at:\n'"$BASE_PYTHON"$'\n\nDroplet requires Python 3.10 or newer. Please install a newer Python and run this installer again.\n\nInstallation has been stopped.'
    show_error "$MSG"
    exit 1
fi

if [ ! -f "$REQ_FILE" ] && [ ! -f "$REQ_FILE_NEW" ]; then
    MSG=$'Neither requirements.txt nor requirements_new.txt was found in:\n'"$REQ_DIR"$'\n\nInstallation has been stopped.'
    show_error "$MSG"
    exit 1
fi

# A venv whose Python no longer runs (e.g. the Python it was built from was
# upgraded or removed) can't be repaired in place - rebuild it from scratch.
VENV_REBUILT=0
if { [ -e "$VENV_DIR/bin/python" ] || [ -L "$VENV_DIR/bin/python" ]; } && ! "$VENV_DIR/bin/python" -c 'import sys' &> /dev/null; then
    rm -rf -- "$VENV_DIR"
    VENV_REBUILT=1
fi

if [ ! -x "$VENV_DIR/bin/python" ]; then
    if ! "$BASE_PYTHON" -m venv "$VENV_DIR" &> /dev/null; then
        MSG=$'Could not create a Python virtual environment using:\n'"$BASE_PYTHON"$'\n\nInstallation has been stopped.'
        show_error "$MSG"
        exit 1
    fi
fi

VENV_PY="$VENV_DIR/bin/python"

if ! "$VENV_PY" -m pip --version &> /dev/null; then
    # Preferred path: Python's bundled bootstrapper
    "$VENV_PY" -m ensurepip --upgrade &> /dev/null

    if ! "$VENV_PY" -m pip --version &> /dev/null; then
        # Fall back to downloading pip's official bootstrap script, in case
        # ensurepip itself is unavailable on this Python build.
        GET_PIP="$(mktemp /tmp/droplet_get_pip.XXXXXX.py 2>/dev/null || echo /tmp/droplet_get_pip.py)"
        if "$VENV_PY" -c "import urllib.request; urllib.request.urlretrieve('https://bootstrap.pypa.io/get-pip.py', '$GET_PIP')" &> /dev/null; then
            "$VENV_PY" "$GET_PIP" &> /dev/null
        fi
        rm -f "$GET_PIP"
    fi

    if ! "$VENV_PY" -m pip --version &> /dev/null; then
        MSG=$'pip could not be found or installed inside Droplet\'s virtual environment (tried both ensurepip and downloading get-pip.py).\n\nThis usually means there is no internet connection.\n\nInstallation has been stopped.'
        show_error "$MSG"
        exit 1
    fi
fi

echo "Installing Droplet's Python dependencies (this checks what's already installed and only downloads what's missing)..."
"$VENV_PY" -m pip install --upgrade pip &> /dev/null
# Try the primary pinned set first. --only-binary makes pip fail fast when a
# pinned version has no wheel for this Python (instead of attempting a long
# source build), so we can move on to the newer pins in requirements_new.txt.
DEPS_OK=0
if [ -f "$REQ_FILE" ] && "$VENV_PY" -m pip install --only-binary=:all: -r "$REQ_FILE"; then
    DEPS_OK=1
elif [ -f "$REQ_FILE_NEW" ]; then
    echo "requirements.txt could not be installed with Python $BASE_VERSION - retrying with requirements_new.txt..."
    "$VENV_PY" -m pip install -r "$REQ_FILE_NEW" && DEPS_OK=1
fi
if [ "$DEPS_OK" -ne 1 ]; then
    MSG=$'Could not install Droplet\'s required Python packages (tried both requirements.txt and requirements_new.txt).\n\nThis usually means there is no internet connection, a firewall is blocking pip, or you lack permission to write to:\n'"$VENV_DIR"$'\n\nPlease check your connection and try running this installer again.\n\nInstallation has been stopped.'
    show_error "$MSG"
    exit 1
fi

mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"

cat > "$APP_DIR/Contents/MacOS/Droplet" << EOF
#!/bin/bash
cd "$SCRIPT_DIR"
"$VENV_PY" "$SCRIPT_DIR/Droplet.py"
EOF
chmod +x "$APP_DIR/Contents/MacOS/Droplet"

if [ -f "$SCRIPT_DIR/assets/icons/Droplet_Icon.icns" ]; then
    cp "$SCRIPT_DIR/assets/icons/Droplet_Icon.icns" "$APP_DIR/Contents/Resources/Droplet.icns"
fi

cat > "$APP_DIR/Contents/Info.plist" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key><string>Droplet</string>
    <key>CFBundleIconFile</key><string>Droplet.icns</string>
    <key>CFBundleName</key><string>Droplet</string>
    <key>CFBundleIdentifier</key><string>com.quentinbetton.droplet</string>
    <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
EOF

xattr -cr "$APP_DIR"

case "$EXISTING_STATE" in
    same)  FINAL_MSG="Droplet was already installed from this folder - it has been updated/repaired." ;;
    other) FINAL_MSG=$'Droplet installed successfully. The Droplet app now opens this copy instead of:\n'"$EXISTING_ROOT"$'\n(that folder was left untouched).' ;;
    stale) FINAL_MSG=$'Droplet installed successfully. The previous Droplet app pointed to a Droplet folder that no longer exists and has been replaced:\n'"$EXISTING_ROOT" ;;
    *)     FINAL_MSG="Droplet installed successfully." ;;
esac
[ "$VENV_REBUILT" = 1 ] && FINAL_MSG+=$'\n\nIts Python environment was broken and has been rebuilt.'
show_info "$FINAL_MSG"$'\n\n'"You can find it in ~/Applications."