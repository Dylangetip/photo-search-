@echo off
rem === RUN THIS ONE if Office/Access is 64-bit ===
rem Does everything: import -> mirror into secondary -> audit.
rem One confirmation. No administrator rights needed.
rem
rem If you copied down ONE store's files (primary.accdb + secondary.accdb
rem sitting right here, with no store subfolder), name the store:
rem     RUN_ALL_64bit.bat -Store 1ORM

C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0RUN_ALL.ps1" %*
pause
