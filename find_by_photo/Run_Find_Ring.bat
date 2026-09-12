@echo off
rem Find an item in inventory from a photograph.
rem Drag your photo onto this file. Narrowed to rings on hand, which is
rem the combination most likely to actually find it.
if "%~1"=="" ( echo Drag a photo onto this file. & pause & exit /b )
set PS=C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0Find_Ring_By_Photo.ps1" -Photo "%~1" -OnHandOnly -Rings
pause
