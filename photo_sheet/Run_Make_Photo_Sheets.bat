@echo off
rem Builds printable photo sheets of the aged gold stock, one per store.
rem Needs gold_items.csv in this same folder. Read only.
rem
rem If it cannot find the Clarity picture folder, run it from a command
rem prompt naming the folder, for example:
rem     Run_Make_Photo_Sheets.bat -ImageFolder "N:\images"

set PS=C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0Make_Photo_Sheets.ps1" %*
pause
