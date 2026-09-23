#!/bin/bash
# Droplet Linux uninstaller
# - Removes the application-menu shortcut created by the installer
# - Then asks whether to also remove Droplet's settings and user data:
#     ~/.config/LILBID/PeakViewer.conf   (Qt QSettings)
#     ~/.droplet/                        (legend entries / labels)
#     droplet_backup_* folders created by updater.py in the Droplet folder
# The .venv, Droplet.py and the rest of the repo are always left untouched.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPS_DIR="$HOME/.local/share/applications"
DESKTOP_FILE="$APPS_DIR/Droplet.desktop"
SETTINGS_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/LILBID"
SETTINGS_FILE="$SETTINGS_DIR/PeakViewer.conf"
DATA_DIR="$HOME/.droplet"

HAS_DISPLAY=0
[ -n "$DISPLAY" ] || [ -n "$WAYLAND_DISPLAY" ] && HAS_DISPLAY=1

# Unlike the installer, the final summary lists paths the user may need, so a
# real dialog is preferred over a notification (which can be truncated or
# missed), and the terminal is used when there is no graphical session.
# --no-markup keeps characters like & or < in folder names from breaking zenity.
show_info() {
    local msg="$1"
    if [ $HAS_DISPLAY = 1 ] && command -v zenity &> /dev/null; then
        zenity --info --no-markup --title="Droplet" --text="$msg" --width=420
    elif [ $HAS_DISPLAY = 1 ] && command -v kdialog &> /dev/null; then
        kdialog --title "Droplet" --msgbox "$msg"
    elif [ $HAS_DISPLAY = 1 ] && command -v notify-send &> /dev/null; then
        notify-send "Droplet" "$msg"
    else
        echo "$msg"
    fi
}

# Yes/no question; returns 0 when the user chooses "Remove". If there is no
# way to ask (no dialog tool and no terminal), the data is kept.
ask_remove() {
    local msg="$1"
    if [ $HAS_DISPLAY = 1 ] && command -v zenity &> /dev/null; then
        zenity --question --no-markup --title="Droplet" --text="$msg" --ok-label="Remove" --cancel-label="Keep" --width=420
    elif [ $HAS_DISPLAY = 1 ] && command -v kdialog &> /dev/null; then
        kdialog --title "Droplet" --yesno "$msg" --yes-label "Remove" --no-label "Keep"
    elif [ -t 0 ]; then
        local answer
        read -r -p "$msg"$'\n'"Remove? [y/N] " answer
        [[ "$answer" =~ ^[Yy] ]]
    else
        return 1
    fi
}

# Safety check: every path below is built from $HOME - never continue without it.
if [ -z "$HOME" ]; then
    show_info "Could not determine your home folder. Nothing was removed."
    exit 1
fi

# NOTE (possible future improvement): the shortcut is removed without checking
# which Droplet folder it points to. With several Droplet copies on the same
# machine, running this uninstaller from copy A also removes the shortcut that
# copy B's installer wrote (all copies share the same Droplet.desktop file, so
# only the most recently installed copy has a shortcut anyway). Comparing the
# Path= line of the .desktop file against $SCRIPT_DIR before deleting would
# avoid this, but it was deliberately left out to keep removal simple.
if [ -f "$DESKTOP_FILE" ]; then
    rm -f "$DESKTOP_FILE"
    update-desktop-database "$APPS_DIR" 2>/dev/null || true
    SUMMARY="Droplet's application menu shortcut has been removed."
else
    SUMMARY="No Droplet application menu shortcut was found."
fi

DATA_ITEMS=()
[ -e "$SETTINGS_FILE" ] && DATA_ITEMS+=("$SETTINGS_FILE")
[ -d "$DATA_DIR" ] && DATA_ITEMS+=("$DATA_DIR")
for d in "$SCRIPT_DIR"/droplet_backup_*; do
    [ -d "$d" ] && DATA_ITEMS+=("$d")
done

if [ ${#DATA_ITEMS[@]} -gt 0 ]; then
    LIST="$(printf '  %s\n' "${DATA_ITEMS[@]}")"
    MSG=$'Droplet also stored settings and data here:\n\n'"$LIST"$'\n\nRemove them too? Choose Keep if you might reinstall Droplet later.\n(If Droplet is running, close it first.)'
    if ask_remove "$MSG"; then
        if rm -rf -- "${DATA_ITEMS[@]}"; then
            SUMMARY+=$'\n\nDroplet\'s settings and data have been removed.'
        else
            SUMMARY+=$'\n\nSome of Droplet\'s settings and data could not be removed:\n'"$LIST"
        fi
        rm -f -- "$SETTINGS_FILE.lock"
        rmdir -- "$SETTINGS_DIR" 2>/dev/null   # only succeeds if now empty
    else
        SUMMARY+=$'\n\nDroplet\'s settings and data were kept:\n'"$LIST"
    fi
fi

SUMMARY+=$'\n\nThe installed environment (.venv) and Droplet\'s files were left untouched. To remove Droplet completely, delete this folder:\n'"$SCRIPT_DIR"
show_info "$SUMMARY"
