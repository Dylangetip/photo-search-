@echo off
rem === RUN THIS ONE if Office/Access is 32-bit (most common) ===
rem Does everything: import -> mirror into secondary -> audit.
rem One confirmation. No administrator rights needed.
rem
rem If you copied down ONE store's files (primary.accdb + secondary.accdb
rem sitting right here, with no store subfolder), name the store:
rem     RUN_ALL_32bit.bat -Store 1ORM

set PS=C:\Windows\SysWOW64\WindowsPowerShell\v1.0\powershell.exe
if not exist "%PS%" set PS=C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0RUN_ALL.ps1" %*
pause
