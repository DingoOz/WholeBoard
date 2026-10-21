# WholeBoard installer for Windows 10/11. Run install.bat (it starts this script).
#
# Installs with winget: Python 3.12, Git, KiCad and FreeCAD.
# Sets up WholeBoard's Python packages in .venv, installs TraceMaker inside WSL (TraceMaker
# runs on Linux), fetches KiCad libraries if needed (and ngspice when KiCad's own copy is not
# found), and adds a Start menu shortcut.
#
# Usage: install.bat [-Yes] [-NoSystem]
param([switch]$Yes, [switch]$NoSystem)
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$Here = (Get-Location).Path

function Say($msg) { Write-Host "`n==> $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "Warning: $msg" -ForegroundColor Yellow }
function Ask($q) {
  if ($Yes) { return $true }
  $r = Read-Host "$q [Y/n]"
  return -not ($r -match '^[nN]')
}
function Refresh-Path {
  $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}
function Winget-Install($id, $name) {
  $listed = winget list --id $id -e --accept-source-agreements 2>$null | Out-String
  if ($listed -match [regex]::Escape($id)) { Write-Host "$name is already installed."; return }
  Say "Installing $name"
  winget install --id $id -e --accept-package-agreements --accept-source-agreements --silent
}

Say "Installing WholeBoard on Windows"

if (-not $NoSystem) {
  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Warn "winget was not found. Install 'App Installer' from the Microsoft Store, then run install.bat again."
    exit 1
  }
  if (Ask "Install Python 3.12, Git, KiCad and FreeCAD with winget?") {
    Winget-Install "Python.Python.3.12" "Python 3.12"
    Winget-Install "Git.Git" "Git"
    Winget-Install "KiCad.KiCad" "KiCad"
    Winget-Install "FreeCAD.FreeCAD" "FreeCAD"
    Refresh-Path
  }
}

# ------------------------------------------------------------------ Python environment
function Rest($a) { if ($a.Length -gt 1) { return $a[1..($a.Length - 1)] } else { return @() } }
$py = $null
foreach ($c in @(@("py", "-3.12"), @("py", "-3"), @("python"))) {
  try {
    $rest = Rest $c
    & $c[0] @rest -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
    if ($LASTEXITCODE -eq 0) { $py = $c; break }
  } catch { }
}
if (-not $py) { Warn "Python 3.10 or newer was not found. Install it and run install.bat again."; exit 1 }
Say "Setting up WholeBoard's Python environment"
$rest = Rest $py
if (-not (Test-Path ".venv\Scripts\python.exe")) { & $py[0] @rest -m venv .venv }
& .venv\Scripts\python -m pip install --upgrade pip | Out-Null
& .venv\Scripts\python -m pip install -e .
if ($LASTEXITCODE -ne 0) { Warn "Installing the Python packages failed."; exit 1 }
# The same stamp run.bat checks, so it does not reinstall on first start.
cmd /c "for %F in (pyproject.toml) do @(echo %~tF %~zF)> .venv\installed-stamp.txt"

# ------------------------------------------------------------------ WSL for TraceMaker
$wslOk = $false
try { wsl --status *> $null; $wslOk = ($LASTEXITCODE -eq 0) } catch { }
if (-not $wslOk) {
  Say "TraceMaker runs in WSL (Windows Subsystem for Linux), which is not set up yet"
  if (Ask "Install WSL with Ubuntu 24.04 now? (asks for administrator rights; restart afterwards)") {
    Start-Process powershell -Verb RunAs -Wait -ArgumentList "-NoProfile", "-Command", "wsl --install -d Ubuntu-24.04"
    Warn "Restart Windows, open Ubuntu once to create your Linux user, then run install.bat again to finish TraceMaker."
  }
} else {
  Say "Installing TraceMaker inside WSL"
  wsl -- bash -lc "command -v curl >/dev/null || (sudo apt-get update && sudo apt-get install -y curl)"
}

# ------------------------------------------------------------------ TraceMaker, FreeCAD, libraries
Say "Installing TraceMaker, FreeCAD, ngspice and KiCad libraries where they are missing"
& .venv\Scripts\python -m wholeboard.doctor --install

# ------------------------------------------------------------------ shortcut
$menu = Join-Path ([Environment]::GetFolderPath("Programs")) "WholeBoard.lnk"
$sh = New-Object -ComObject WScript.Shell
$lnk = $sh.CreateShortcut($menu)
$lnk.TargetPath = Join-Path $Here "run.bat"
$lnk.WorkingDirectory = $Here
$lnk.Description = "Describe a PCB and get it designed, routed, quoted and boxed"
$lnk.Save()

Say "Done"
Write-Host "Start WholeBoard from the Start menu (WholeBoard) or with run.bat."
Write-Host "To use it from another computer on your network: run.bat --host 0.0.0.0"
Write-Host "Then open Settings in WholeBoard to choose your AI (Claude, OpenAI, Grok or a local model)."
