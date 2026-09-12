@echo off
rem Find an item in inventory from a photograph.
rem Drag your photo onto this file, or run:
rem     Run_Find_Ring.bat "C:\path\to\ring.jpg"
rem First run reads every catalogue picture and takes 15 to 30 minutes.
rem After that it is cached and runs in seconds.
if "%~1"=="" (
  echo Drag a photo onto this file, or pass one:  Run_Find_Ring.bat "C:\path\ring.jpg"
  pause
  exit /b
)
set PS=C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0Find_Ring_By_Photo.ps1" -Photo "%~1" %2 %3 %4 %5
pause
