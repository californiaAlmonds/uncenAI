@echo off
set PYTHONUTF8=1
if exist "E:\uv" (
    set TEMP=E:\tmp
    set TMP=E:\tmp
    if not exist "E:\tmp" mkdir "E:\tmp"
)
where py >nul 2>nul
if %ERRORLEVEL% equ 0 (
    set PYTHON_EXE=py
) else (
    set PYTHON_EXE=python
)
%PYTHON_EXE% "%~dp0src\local_controller.py" start --wait
if errorlevel 1 pause
echo Model Ready in VS Code Copilot Chat!
timeout /t 3 >nul
