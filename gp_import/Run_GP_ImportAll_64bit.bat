@echo off
rem GP -> Clarity import, 64-bit (use this one if Office/Access is 64-bit).
rem Works on the LOCAL COPIES of the databases sitting in this same folder.
rem No administrator rights needed.
rem
rem Optional: if you copied down ONE store's files (primary.accdb and
rem secondary.accdb right here, with no store subfolder), say which store:
rem     Run_GP_ImportAll_64bit.bat -Store 1ORM

C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0GP_ImportAll.ps1" %*
pause
