@echo off
setlocal
cd /d "%~dp0..\.."
if not exist ".venv\Scripts\python.exe" (
  py -3.15 -m venv .venv || exit /b 1
)
.venv\Scripts\python.exe -c "import vedrfolnir, river, sqlalchemy" >nul 2>&1
if errorlevel 1 (
  .venv\Scripts\python.exe -m pip install -e . || exit /b 1
)
if not exist ".venv\Scripts\vedrfolnir-travistorrent.exe" .venv\Scripts\python.exe -m pip install -e . || exit /b 1
.venv\Scripts\vedrfolnir-travistorrent.exe %*
exit /b %errorlevel%
