@echo off
rem Start WholeBoard on Windows. Creates a virtual environment on first run and
rem reinstalls the dependencies when pyproject.toml changes.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  py -3 -m venv .venv || python -m venv .venv
  .venv\Scripts\python -m pip install --upgrade pip
)
set STAMP=.venv\installed-stamp.txt
for %%F in (pyproject.toml) do set WANT=%%~tF %%~zF
set HAVE=
if exist %STAMP% set /p HAVE=<%STAMP%
if not "%HAVE%"=="%WANT%" (
  .venv\Scripts\python -m pip install -e . && (echo %WANT%)>%STAMP%
)
.venv\Scripts\python -m wholeboard %*
