@echo off
setlocal

if "%~1"=="" (
  echo Drag California.zip or the extracted California folder onto this file.
  echo.
  echo Or run:
  echo   RUN_POSTPROCESSING_WINDOWS.cmd "C:\path\to\California.zip"
  pause
  exit /b 2
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0RUN_POSTPROCESSING_WINDOWS.ps1" -InputPath "%~1"
set EXIT_CODE=%ERRORLEVEL%
echo.
if not "%EXIT_CODE%"=="0" echo Post-processing failed with exit code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
