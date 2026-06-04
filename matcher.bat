@echo off
title Funscript Matcher
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel% neq 0 goto nopython

py -c "import PySide6" 2>nul
if %errorlevel% neq 0 (
    echo First run -- installing PySide6, this takes about a minute...
    py -m pip install PySide6
    if %errorlevel% neq 0 (
        echo.
        echo PySide6 install failed. See messages above.
        pause
        exit /b 1
    )
)

py -c "import rarfile" 2>nul
if %errorlevel% neq 0 (
    echo Installing rarfile for RAR extraction support...
    py -m pip install rarfile >nul 2>&1
)

where pyw >nul 2>nul && (start "" pyw matcher.py & exit /b)
where pythonw >nul 2>nul && (start "" pythonw matcher.py & exit /b)
py matcher.py
exit /b

:nopython
echo Python not found. Install from https://www.python.org/downloads/
pause
exit /b 1
