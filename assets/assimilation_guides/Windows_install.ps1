# Droplet Windows installer
# - Locates a working Python 3.10+ interpreter
# - Creates a dedicated virtual environment for Droplet (so its dependencies
#   never collide with system packages, Anaconda's base env, or other tools)
# - Installs/updates dependencies from requirements.txt inside that venv,
#   falling back to requirements_new.txt (newer pins) if the first set
#   can't be installed - e.g. on a Python too recent for the pinned wheels
# - Creates a Desktop shortcut that launches Droplet via the venv's Python
# - Detects an existing install (from the folder the shortcut points at):
#     same folder      -> updates/repairs it
#     other Droplet    -> asks whether to switch the shortcut to this copy
#     folder now gone  -> replaces the stale shortcut
#
# On any failure the user is shown a message box and installation stops.

# This script lives in <repo_root>/assets/assimilation_guides/
# Resolve up two levels to find the actual repo root (where Droplet.py lives)
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptDir)
# The requirements files live next to this script
$ReqFile = Join-Path $ScriptDir "requirements.txt"
$ReqFileNew = Join-Path $ScriptDir "requirements_new.txt"
$VenvDir = Join-Path $RepoRoot ".venv"
$MinVersion = [version]"3.10.0"

Add-Type -AssemblyName System.Windows.Forms

function Show-Info($msg) {
    [System.Windows.Forms.MessageBox]::Show($msg, "Droplet", [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Information) | Out-Null
}
function Show-ErrorBox($msg) {
    [System.Windows.Forms.MessageBox]::Show($msg, "Droplet - Installation problem", [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Error) | Out-Null
}

function Test-PythonExe($path) {
    if (-not (Test-Path $path)) { return $false }
    try {
        & $path --version *> $null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Get-PythonVersion($exe) {
    try {
        $out = & $exe --version 2>&1
        if ($out -match '(\d+)\.(\d+)\.(\d+)') {
            return [version]"$($matches[1]).$($matches[2]).$($matches[3])"
        }
    } catch {}
    return $null
}

function Find-Python {
    $candidates = @()

    # 1) Whatever "python" / "python3" resolve to on PATH right now (skip the
    #    Microsoft Store stub, which doesn't work until a real Python is
    #    installed from the Store or python.org)
    foreach ($name in @("python", "python3")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source -notmatch "WindowsApps") {
            $candidates += $cmd.Source
        }
    }

    # 2) The "py" launcher, which can find installed Pythons even when
    #    "python" itself isn't on PATH
    if (Get-Command py -ErrorAction SilentlyContinue) {
        try {
            $resolved = & py -3 -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $resolved) { $candidates += $resolved.Trim() }
        } catch {}
    }

    # 3) Common Anaconda / Miniconda / python.org install locations
    $patterns = @(
        "$env:USERPROFILE\anaconda3\python.exe",
        "$env:USERPROFILE\miniconda3\python.exe",
        "C:\ProgramData\Anaconda3\python.exe",
        "C:\ProgramData\Miniconda3\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe"
    )
    foreach ($pattern in $patterns) {
        Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue | ForEach-Object { $candidates += $_.FullName }
    }

    foreach ($c in $candidates) {
        if (Test-PythonExe $c) { return $c }
    }
    return $null
}

# Version string from a Droplet folder's VERSION file, or "unknown"
# (assets\about\VERSION; older copies keep it at the folder root)
function Get-DropletVersion($root) {
    foreach ($rel in @("assets\about\VERSION", "VERSION")) {
        try {
            $v = (Get-Content -LiteralPath (Join-Path $root $rel) -TotalCount 1 -ErrorAction Stop).Trim()
            if ($v) { return $v }
        } catch {}
    }
    return "unknown"
}

# Normalised full path for comparing folders (case-insensitive on Windows)
function Get-NormalPath($p) {
    try { return [System.IO.Path]::GetFullPath($p).TrimEnd('\') } catch { return $null }
}

# Check whether Droplet is already installed, and from which folder, before
# doing any work - so choosing "Keep existing" leaves everything untouched.
$WshShell = New-Object -ComObject WScript.Shell
$ShortcutPath = Join-Path $WshShell.SpecialFolders("Desktop") "Droplet.lnk"
$ExistingRoot = $null
$ExistingState = "none"   # none | same | other | stale
if (Test-Path -LiteralPath $ShortcutPath) {
    $ExistingRoot = $WshShell.CreateShortcut($ShortcutPath).WorkingDirectory
    $ExistingNorm = if ($ExistingRoot) { Get-NormalPath $ExistingRoot } else { $null }
    if ($ExistingNorm -and ($ExistingNorm -ieq (Get-NormalPath $RepoRoot))) {
        $ExistingState = "same"
    } elseif ($ExistingNorm -and (Test-Path -LiteralPath (Join-Path $ExistingNorm "Droplet.py"))) {
        $ExistingState = "other"
        $msg = "Droplet is already installed from another folder:`n$ExistingRoot  (version $(Get-DropletVersion $ExistingRoot))`n`nThis installer is for:`n$RepoRoot  (version $(Get-DropletVersion $RepoRoot))`n`nSwitch the Desktop shortcut to this copy? The other folder will not be modified or deleted.`n`nYes = use this copy`nNo = keep the existing one"
        $answer = [System.Windows.Forms.MessageBox]::Show($msg, "Droplet - Already installed", [System.Windows.Forms.MessageBoxButtons]::YesNo, [System.Windows.Forms.MessageBoxIcon]::Question)
        if ($answer -ne [System.Windows.Forms.DialogResult]::Yes) {
            Show-Info "Installation cancelled. Droplet is still installed from:`n$ExistingRoot"
            exit 0
        }
    } else {
        $ExistingState = "stale"
        if (-not $ExistingRoot) { $ExistingRoot = $ShortcutPath }
    }
}

$BasePython = Find-Python
if (-not $BasePython) {
    Show-ErrorBox "Droplet needs Python 3.10 or newer, but no working Python installation was found on this computer.`n`nPlease install Python from https://www.python.org/downloads/ (check 'Add python.exe to PATH' during setup) or use Anaconda, then run this installer again.`n`nInstallation has been stopped."
    exit 1
}

$BaseVersion = Get-PythonVersion $BasePython
if ($BaseVersion -and $BaseVersion -lt $MinVersion) {
    Show-ErrorBox "Found Python $BaseVersion at:`n$BasePython`n`nDroplet requires Python 3.10 or newer. Please install a newer Python and run this installer again.`n`nInstallation has been stopped."
    exit 1
}

if (-not (Test-Path $ReqFile) -and -not (Test-Path $ReqFileNew)) {
    Show-ErrorBox "Neither requirements.txt nor requirements_new.txt was found in:`n$ScriptDir`n`nInstallation has been stopped."
    exit 1
}

$VenvPy = Join-Path $VenvDir "Scripts\python.exe"

# A venv whose Python no longer runs (e.g. the Python it was built from was
# upgraded or removed) can't be repaired in place - rebuild it from scratch.
$VenvRebuilt = $false
if (Test-Path $VenvPy) {
    & $VenvPy -c "import sys" *> $null
    if ($LASTEXITCODE -ne 0) {
        try {
            Remove-Item -LiteralPath $VenvDir -Recurse -Force -ErrorAction Stop
            $VenvRebuilt = $true
        } catch {
            Show-ErrorBox "Droplet's Python environment is broken and could not be removed:`n$VenvDir`n`nClose Droplet if it is running (or delete that folder manually), then run this installer again.`n`nInstallation has been stopped."
            exit 1
        }
    }
}

if (-not (Test-Path $VenvPy)) {
    & $BasePython -m venv $VenvDir *> $null
    if (-not (Test-Path $VenvPy)) {
        Show-ErrorBox "Could not create a Python virtual environment using:`n$BasePython`n`nInstallation has been stopped."
        exit 1
    }
}

& $VenvPy -m pip --version *> $null
if ($LASTEXITCODE -ne 0) {
    # Preferred path: Python's bundled bootstrapper
    & $VenvPy -m ensurepip --upgrade *> $null
    & $VenvPy -m pip --version *> $null

    if ($LASTEXITCODE -ne 0) {
        # Fall back to downloading pip's official bootstrap script, in case
        # ensurepip itself is unavailable on this Python build.
        $GetPip = Join-Path $env:TEMP "droplet_get_pip.py"
        try {
            Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $GetPip -UseBasicParsing -ErrorAction Stop
            & $VenvPy $GetPip *> $null
        } catch {}
        Remove-Item $GetPip -ErrorAction SilentlyContinue
        & $VenvPy -m pip --version *> $null
    }

    if ($LASTEXITCODE -ne 0) {
        Show-ErrorBox "pip could not be found or installed inside Droplet's virtual environment (tried both ensurepip and downloading get-pip.py).`n`nThis usually means there is no internet connection.`n`nInstallation has been stopped."
        exit 1
    }
}

Write-Host "Installing Droplet's Python dependencies (this checks what's already installed and only downloads what's missing)..."
& $VenvPy -m pip install --upgrade pip *> $null
# Try the primary pinned set first. --only-binary makes pip fail fast when a
# pinned version has no wheel for this Python (instead of attempting a long
# source build), so we can move on to the newer pins in requirements_new.txt.
$DepsOk = $false
if (Test-Path $ReqFile) {
    & $VenvPy -m pip install --only-binary=:all: -r $ReqFile
    $DepsOk = ($LASTEXITCODE -eq 0)
}
if (-not $DepsOk -and (Test-Path $ReqFileNew)) {
    Write-Host "requirements.txt could not be installed with Python $BaseVersion - retrying with requirements_new.txt..."
    & $VenvPy -m pip install -r $ReqFileNew
    $DepsOk = ($LASTEXITCODE -eq 0)
}
if (-not $DepsOk) {
    Show-ErrorBox "Could not install Droplet's required Python packages (tried both requirements.txt and requirements_new.txt).`n`nThis usually means there is no internet connection, a firewall/proxy is blocking pip, or you lack permission to write to:`n$VenvDir`n`nPlease check your connection and try running this installer again.`n`nInstallation has been stopped."
    exit 1
}

$VenvPyw = Join-Path $VenvDir "Scripts\pythonw.exe"
if (-not (Test-Path $VenvPyw)) { $VenvPyw = $VenvPy }

$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $VenvPyw
$Shortcut.Arguments = "`"$RepoRoot\Droplet.py`""
$Shortcut.WorkingDirectory = $RepoRoot
$IconPath = "$RepoRoot\assets\icons\Droplet_Icon.ico"
if (Test-Path $IconPath) { $Shortcut.IconLocation = $IconPath }
$Shortcut.Save()

switch ($ExistingState) {
    "same"  { $FinalMsg = "Droplet was already installed from this folder - it has been updated/repaired." }
    "other" { $FinalMsg = "Droplet installed successfully. The Desktop shortcut now opens this copy instead of:`n$ExistingRoot`n(that folder was left untouched)." }
    "stale" { $FinalMsg = "Droplet installed successfully. The previous Desktop shortcut pointed to a Droplet folder that no longer exists and has been replaced:`n$ExistingRoot" }
    default { $FinalMsg = "Droplet installed successfully." }
}
if ($VenvRebuilt) { $FinalMsg += "`n`nIts Python environment was broken and has been rebuilt." }
Show-Info "$FinalMsg`n`nYou'll find a shortcut on your Desktop."