@echo off
rem Condensed S. Kashi photo sheets, one per store. Read only.
rem If it cannot find the Clarity picture folder, run from a command prompt:
rem     Run_Make_Kashi_Sheets.bat -ImageFolder "N:\images"
set PS=C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0Make_Kashi_Sheets.ps1" %*
pause
