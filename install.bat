@echo off
rem WholeBoard installer for Windows: runs install.ps1 without changing PowerShell's policy.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
pause
