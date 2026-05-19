@echo off
REM Launcher for Windows. Creates a local venv on first run, installs Pillow,
REM then starts the GUI.
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %ERRORLEVEL%==0 (
    set "PY=py -3"
) else (
    where python >nul 2>nul
    if %ERRORLEVEL%==0 (
        set "PY=python"
    ) else (
        echo Python 3 is required. Install it from https://www.python.org/downloads/ and try again.
        pause
        exit /b 1
    )
)

if not exist ".venv" (
    echo Creating virtual environment...
    %PY% -m venv .venv
    if errorlevel 1 goto :error
    call .venv\Scripts\activate.bat
    pip install --quiet --upgrade pip
    pip install --quiet -r requirements.txt
    if errorlevel 1 goto :error
) else (
    call .venv\Scripts\activate.bat
)

python ribbonengine.py %*
goto :eof

:error
echo.
echo Setup failed. See the messages above.
pause
exit /b 1
