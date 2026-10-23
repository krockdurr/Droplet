#!/bin/bash
# Droplet macOS uninstaller
# - Removes the Droplet.app launcher bundle created by the installer
# - Then asks whether to also remove Droplet's settings and user data:
#     ~/Library/Preferences/com.lilbid.PeakViewer.plist   (Qt QSettings)
#     ~/.droplet/                                         (legend entries / labels)
#     droplet_backup_* folders created by the updater in the Droplet folder
# The .venv, Droplet.py and the rest of the repo are always left untouched.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$HOME/Applications/Droplet.app"
PREFS_DOMAIN="com.lilbid.PeakViewer"
PREFS_FILE="$HOME/Library/Preferences/$PREFS_DOMAIN.plist"
DATA_DIR="$HOME/.droplet"

# The message is passed to osascript as an argument (item 1 of argv) rather than
# pasted into the AppleScript source, so paths containing quotes or backslashes
# can't break the dialog.
show_info() {
    osascript -e 'on run argv' \
              -e 'display dialog (item 1 of argv) with title "Droplet" buttons {"OK"} default button "OK"' \
              -e 'end run' "$1" &> /dev/null
}

confirm() {
    osascript -e 'on run argv' \
              -e 'display dialog (item 1 of argv) with title "Droplet" buttons {"Cancel", "Remove"} default button "Cancel" cancel button "Cancel"' \
              -e 'end run' "$1" &> /dev/null
}

# Returns 0 when the user chooses "Remove"; "Keep" is the default.
ask_remove() {
    osascript -e 'on run argv' \
              -e 'display dialog (item 1 of argv) with title "Droplet" buttons {"Keep", "Remove"} default button "Keep" cancel button "Keep"' \
              -e 'end run' "$1" &> /dev/null
}

# Safety check: only ever remove these exact, expected paths - never anything
# broader, even if $HOME ends up unset or unexpected for some reason.
if [ -z "$HOME" ] || [ "$(basename "$APP_DIR")" != "Droplet.app" ]; then
    show_info "Could not safely determine the Droplet.app location. Nothing was removed."
    exit 1
fi

# NOTE (possible future improvement): Droplet.app is removed without checking
# which Droplet folder it launches. With several Droplet copies on the same Mac,
# running this uninstaller from copy A also removes the launcher that copy B's
# installer wrote (all copies share ~/Applications/Droplet.app, so only the most
# recently installed copy has a launcher anyway). Checking that
# Droplet.app/Contents/MacOS/Droplet references $SCRIPT_DIR before deleting
# would avoid this, but it was deliberately left out to keep removal simple.
if [ -d "$APP_DIR" ]; then
    MSG=$'This will remove the Droplet launcher (Droplet.app) from ~/Applications.\n\nYour installed environment (.venv) and Droplet\'s files will be left untouched. Continue?'
    if ! confirm "$MSG"; then
        exit 0
    fi
    rm -rf "$APP_DIR"
    SUMMARY=$'Droplet.app has been removed from ~/Applications.\n\nIf you had pinned Droplet to the Dock, drag its icon out of the Dock to remove it.'
else
    SUMMARY="No Droplet.app launcher was found in ~/Applications."
fi

DATA_ITEMS=()
[ -e "$PREFS_FILE" ] && DATA_ITEMS+=("$PREFS_FILE")
[ -d "$DATA_DIR" ] && DATA_ITEMS+=("$DATA_DIR")
for d in "$SCRIPT_DIR"/droplet_backup_*; do
    [ -d "$d" ] && DATA_ITEMS+=("$d")
done

if [ ${#DATA_ITEMS[@]} -gt 0 ]; then
    LIST="$(printf '  %s\n' "${DATA_ITEMS[@]}")"
    MSG=$'Droplet also stored settings and data here:\n\n'"$LIST"$'\n\nRemove them too? Choose Keep if you might reinstall Droplet later.\n(If Droplet is running, quit it first.)'
    if ask_remove "$MSG"; then
        # Clear the preferences through cfprefsd first, otherwise a cached copy
        # can be written back after the .plist file is deleted.
        defaults delete "$PREFS_DOMAIN" &> /dev/null
        if rm -rf -- "${DATA_ITEMS[@]}"; then
            SUMMARY+=$'\n\nDroplet\'s settings and data have been removed.'
        else
            SUMMARY+=$'\n\nSome of Droplet\'s settings and data could not be removed:\n'"$LIST"
        fi
    else
        SUMMARY+=$'\n\nDroplet\'s settings and data were kept:\n'"$LIST"
    fi
fi

SUMMARY+=$'\n\nThe installed environment (.venv) and Droplet\'s files were left untouched. To remove Droplet completely, delete this folder:\n'"$SCRIPT_DIR"
show_info "$SUMMARY"
