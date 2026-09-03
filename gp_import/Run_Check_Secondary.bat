@echo off
rem READ ONLY - compares primary.accdb, secondary.accdb and the pre-import
rem backup. Changes nothing. Safe to run while the import is running.
rem If you copied down ONE store's files, add the store name:
rem     Run_Check_Secondary.bat -Store 1ORM

set PS=C:\Windows\SysWOW64\WindowsPowerShell\v1.0\powershell.exe
if not exist "%PS%" set PS=C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0Check_Secondary.ps1" %*
