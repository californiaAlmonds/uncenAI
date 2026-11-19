@echo off
set PYTHONUTF8=1
if exist "E:\uv" (
    set TEMP=E:\tmp
    set TMP=E:\tmp
)
where py >nul 2>nul
if %ERRORLEVEL% equ 0 (
    set PYTHON_EXE=py
) else (
    set PYTHON_EXE=python
)
%PYTHON_EXE% "%~dp0src\local_controller.py" status
pause
