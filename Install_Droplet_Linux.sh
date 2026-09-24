#!/bin/bash
# Droplet Linux installer
# - Locates a working Python 3.10+ interpreter
# - Creates a dedicated virtual environment for Droplet (so its dependencies
#   never collide with system-wide packages or other projects)
# - Installs/updates dependencies from requirements.txt inside that venv,
#   falling back to requirements_new.txt (newer pins) if the first set
#   can't be installed - e.g. on a Python too recent for the pinned wheels
# - Registers a .desktop launcher that points at the venv's Python
# - Detects an existing install (from the shortcut it points at):
#     same folder      -> updates/repairs it
#     other Droplet    -> asks whether to switch the shortcut to this copy
#     folder now gone  -> replaces the stale shortcut
#
# On any failure (no Python found, pip/venv can't be set up, no network for
# dependencies, etc.) the user is shown a clear message and installation stops.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REQ_DIR="$SCRIPT_DIR/assets/assimilation_guides"
REQ_FILE="$REQ_DIR/requirements.txt"
REQ_FILE_NEW="$REQ_DIR/requirements_new.txt"
VENV_DIR="$SCRIPT_DIR/.venv"
MIN_VERSION="3.10.0"

show_info() {
    local msg="$1"
    if command -v notify-send &> /dev/null; then
        notify-send "Droplet" "$msg"
    elif command -v zenity &> /dev/null; then
        zenity --info --title="Droplet" --text="$msg" --width=320
    elif command -v kdialog &> /dev/null; then
        kdialog --title "Droplet" --msgbox "$msg"
    else
        echo "$msg"
    fi
}

show_error() {
    local msg="$1"
    if command -v zenity &> /dev/null; then
        zenity --error --title="Droplet - Installation problem" --text="$msg" --width=420
    elif command -v kdialog &> /dev/null; then
        kdialog --title "Droplet - Installation problem" --error "$msg"
    elif command -v notify-send &> /dev/null; then
        notify-send -u critical "Droplet - Installation problem" "$msg"
    else
        echo "ERROR: $msg" >&2
    fi
}

# true if version $1 >= $2 (both like "3.11.4"); portable, no GNU sort needed
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

    # 1) Whatever "python3" resolves to on the user's PATH right now (covers
    #    system Python, pyenv, an activated conda env, etc.)
    if command -v python3 &> /dev/null; then
        candidates+=("$(command -v python3)")
    fi

    # 2) Common Anaconda / Miniconda locations, in case conda's base env
    #    isn't currently on PATH
    for p in \
        "$HOME/anaconda3/bin/python3" \
        "$HOME/miniconda3/bin/python3" \
        "/opt/anaconda3/bin/python3" \
        "/opt/miniconda3/bin/python3"
    do
        [ -x "$p" ] && candidates+=("$p")
    done

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

# Yes/no question; returns 0 for "Use this copy". If there is no way to ask
# (no dialog tool and no terminal), the existing install is kept.
ask_use_this_copy() {
    local msg="$1"
    if command -v zenity &> /dev/null; then
        zenity --question --no-markup --title="Droplet - Already installed" --text="$msg" --ok-label="Use this copy" --cancel-label="Keep existing" --width=460
    elif command -v kdialog &> /dev/null; then
        kdialog --title "Droplet - Already installed" --yesno "$msg" --yes-label "Use this copy" --no-label "Keep existing"
    elif [ -t 0 ]; then
        local answer
        read -r -p "$msg"$'\n'"Use this copy? [Y/n] " answer
        [[ ! "$answer" =~ ^[Nn] ]]
    else
        return 1
    fi
}

# Check whether Droplet is already installed, and from which folder, before
# doing any work - so choosing "Keep existing" leaves everything untouched.
DESKTOP_FILE="$HOME/.local/share/applications/Droplet.desktop"
EXISTING_ROOT=""
[ -f "$DESKTOP_FILE" ] && EXISTING_ROOT="$(sed -n 's/^Path=//p' "$DESKTOP_FILE" | head -n1)"
EXISTING_STATE=none   # none | same | other | stale
if [ -n "$EXISTING_ROOT" ]; then
    THIS_REAL="$(cd "$SCRIPT_DIR" && pwd -P)"
    EXISTING_REAL="$(cd "$EXISTING_ROOT" 2>/dev/null && pwd -P)"
    if [ "$EXISTING_REAL" = "$THIS_REAL" ]; then
        EXISTING_STATE=same
    elif [ -n "$EXISTING_REAL" ] && [ -f "$EXISTING_REAL/Droplet.py" ]; then
        EXISTING_STATE=other
        MSG=$'Droplet is already installed from another folder:\n'"$EXISTING_ROOT"$'  (version '"$(droplet_version "$EXISTING_ROOT")"$')\n\nThis installer is for:\n'"$SCRIPT_DIR"$'  (version '"$(droplet_version "$SCRIPT_DIR")"$')\n\nSwitch the application menu shortcut to this copy? The other folder will not be modified or deleted.'
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
    MSG=$'Droplet needs Python 3.10 or newer, but no working Python installation could be found on this computer.\n\nPlease install Python (e.g. "sudo apt install python3" on Debian/Ubuntu, or from https://www.python.org/downloads/), then run this installer again.\n\nInstallation has been stopped.'
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

# Create (or reuse) a dedicated virtual environment for Droplet.
if [ ! -x "$VENV_DIR/bin/python" ]; then
    if ! "$BASE_PYTHON" -m venv "$VENV_DIR" &> /dev/null; then
        MSG=$'Could not create a Python virtual environment using:\n'"$BASE_PYTHON"$'\n\nOn Debian/Ubuntu this usually means the venv module is missing - try:\nsudo apt install python3-venv\n\nThen run this installer again.\n\nInstallation has been stopped.'
        show_error "$MSG"
        exit 1
    fi
fi

VENV_PY="$VENV_DIR/bin/python"

if ! "$VENV_PY" -m pip --version &> /dev/null; then
    # Preferred path: Python's bundled bootstrapper
    "$VENV_PY" -m ensurepip --upgrade &> /dev/null

    if ! "$VENV_PY" -m pip --version &> /dev/null; then
        # ensurepip itself can be missing (e.g. Debian/Ubuntu system Python
        # without the python3-pip package). Fall back to downloading pip's
        # official bootstrap script.
        GET_PIP="$(mktemp /tmp/droplet_get_pip.XXXXXX.py 2>/dev/null || echo /tmp/droplet_get_pip.py)"
        if "$VENV_PY" -c "import urllib.request; urllib.request.urlretrieve('https://bootstrap.pypa.io/get-pip.py', '$GET_PIP')" &> /dev/null; then
            "$VENV_PY" "$GET_PIP" &> /dev/null
        fi
        rm -f "$GET_PIP"
    fi

    if ! "$VENV_PY" -m pip --version &> /dev/null; then
        MSG=$'pip could not be found or installed inside Droplet\'s virtual environment (tried both ensurepip and downloading get-pip.py).\n\nOn Debian/Ubuntu, try: sudo apt install python3-pip\nOtherwise this usually means there is no internet connection.\n\nInstallation has been stopped.'
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
    MSG=$'Could not install Droplet\'s required Python packages (tried both requirements.txt and requirements_new.txt).\n\nThis usually means there is no internet connection, a proxy/firewall is blocking pip, or you lack permission to write to:\n'"$VENV_DIR"$'\n\nPlease check your connection and try running this installer again.\n\nInstallation has been stopped.'
    show_error "$MSG"
    exit 1
fi

mkdir -p "$(dirname "$DESKTOP_FILE")"
sed -e "s|__DROPLET_ROOT__|$SCRIPT_DIR|g" -e "s|__DROPLET_PYTHON__|$VENV_PY|g" \
    "$SCRIPT_DIR/assets/assimilation_guides/Droplet.desktop" > "$DESKTOP_FILE"
update-desktop-database ~/.local/share/applications/ 2>/dev/null || true

case "$EXISTING_STATE" in
    same)  FINAL_MSG="Droplet was already installed from this folder - it has been updated/repaired." ;;
    other) FINAL_MSG=$'Droplet installed successfully. The application menu shortcut now opens this copy instead of:\n'"$EXISTING_ROOT"$'\n(that folder was left untouched).' ;;
    stale) FINAL_MSG=$'Droplet installed successfully. The previous application menu shortcut pointed to a Droplet folder that no longer exists and has been replaced:\n'"$EXISTING_ROOT" ;;
    *)     FINAL_MSG="Droplet installed successfully." ;;
esac
[ "$VENV_REBUILT" = 1 ] && FINAL_MSG+=$'\n\nIts Python environment was broken and has been rebuilt.'
show_info "$FINAL_MSG"$'\n\n'"You can now find it in your application menu."