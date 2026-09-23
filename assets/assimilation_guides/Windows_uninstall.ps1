# Droplet Windows uninstaller
# - Removes the Desktop shortcut created by the installer
# - Then asks whether to also remove Droplet's settings and user data:
#     registry key HKCU\Software\LILBID\PeakViewer   (Qt QSettings)
#     %USERPROFILE%\.droplet                         (legend entries / labels)
#     droplet_backup_* folders created by updater.py in the Droplet folder
# The .venv, Droplet.py and the rest of the repo are always left untouched.

# This script lives in <repo_root>/assets/assimilation_guides/
# Resolve up two levels to find the actual repo root (where Droplet.py lives)
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptDir)
$SettingsKey = "HKCU:\Software\LILBID\PeakViewer"
$SettingsParentKey = "HKCU:\Software\LILBID"
$DataDir = Join-Path $env:USERPROFILE ".droplet"

Add-Type -AssemblyName System.Windows.Forms

function Show-Info($msg) {
    [System.Windows.Forms.MessageBox]::Show($msg, "Droplet", [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Information) | Out-Null
}

# Returns $true when the user chooses Yes (remove); No is the default button.
function Ask-Remove($msg) {
    $answer = [System.Windows.Forms.MessageBox]::Show($msg, "Droplet", [System.Windows.Forms.MessageBoxButtons]::YesNo, [System.Windows.Forms.MessageBoxIcon]::Question, [System.Windows.Forms.MessageBoxDefaultButton]::Button2)
    return ($answer -eq [System.Windows.Forms.DialogResult]::Yes)
}

# NOTE (possible future improvement): the shortcut is removed without checking
# which Droplet folder it points to. With several Droplet copies on the same PC,
# running this uninstaller from copy A also removes the shortcut that copy B's
# installer wrote (all copies share Desktop\Droplet.lnk, so only the most
# recently installed copy has a shortcut anyway). Comparing the shortcut's
# WorkingDirectory against $RepoRoot before deleting would avoid this, but it
# was deliberately left out to keep removal simple.
$WshShell = New-Object -ComObject WScript.Shell
$ShortcutPath = Join-Path $WshShell.SpecialFolders("Desktop") "Droplet.lnk"

if (Test-Path -LiteralPath $ShortcutPath) {
    try {
        Remove-Item -LiteralPath $ShortcutPath -Force -ErrorAction Stop
        $Summary = "Droplet's Desktop shortcut has been removed."
    } catch {
        $Summary = "Droplet's Desktop shortcut could not be removed:`n$($_.Exception.Message)"
    }
} else {
    $Summary = "No Droplet Desktop shortcut was found."
}

# Each entry: Path = what gets removed, Label = what the user is shown
$DataItems = @()
if (Test-Path $SettingsKey) {
    $DataItems += @{ Path = $SettingsKey; Label = "Registry: HKEY_CURRENT_USER\Software\LILBID\PeakViewer" }
}
if (Test-Path -LiteralPath $DataDir) {
    $DataItems += @{ Path = $DataDir; Label = $DataDir }
}
Get-ChildItem -LiteralPath $RepoRoot -Directory -Filter "droplet_backup_*" -ErrorAction SilentlyContinue | ForEach-Object {
    $DataItems += @{ Path = $_.FullName; Label = $_.FullName }
}

if ($DataItems.Count -gt 0) {
    $List = ($DataItems | ForEach-Object { "  " + $_.Label }) -join "`n"
    $Msg = "Droplet also stored settings and data here:`n`n$List`n`nRemove them too? Choose No if you might reinstall Droplet later.`n(If Droplet is running, close it first.)"
    if (Ask-Remove $Msg) {
        $Failed = @()
        foreach ($item in $DataItems) {
            try {
                Remove-Item -LiteralPath $item.Path -Recurse -Force -ErrorAction Stop
            } catch {
                $Failed += "  " + $item.Label
            }
        }
        # Remove the parent "LILBID" key too, but only if nothing else is in it
        if (Test-Path $SettingsParentKey) {
            $parent = Get-Item $SettingsParentKey
            if ($parent.SubKeyCount -eq 0 -and $parent.ValueCount -eq 0) {
                Remove-Item $SettingsParentKey -Force -ErrorAction SilentlyContinue
            }
        }
        if ($Failed.Count -eq 0) {
            $Summary += "`n`nDroplet's settings and data have been removed."
        } else {
            $Summary += "`n`nSome of Droplet's settings and data could not be removed:`n" + ($Failed -join "`n")
        }
    } else {
        $Summary += "`n`nDroplet's settings and data were kept:`n$List"
    }
}

$Summary += "`n`nThe installed environment (.venv) and Droplet's files were left untouched. To remove Droplet completely, delete this folder:`n$RepoRoot"
Show-Info $Summary
