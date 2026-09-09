@echo off
rem Condensed Simon G. photo sheet for Orem. Read only.
rem If it cannot find the Clarity picture folder, run from a command prompt:
rem     Run_Simon_G_Sheets.bat -ImageFolder "N:\images"
set PS=C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0Make_Vendor_Sheets.ps1" -Title "Simon G." -Tag SimonG %*
pause
