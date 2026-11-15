@echo off
rem One command setup and start for Windows. Double click this file, or run it from a terminal.
rem All the work is done by scripts\run.py; this file only finds a Python to run it with.
rem Arguments are passed through, for example:  scripts\start.bat --check

setlocal
set "RUNNER=%~dp0run.py"
set "CODE=1"

where python >nul 2>nul
if errorlevel 1 goto trylauncher
python "%RUNNER%" %*
set "CODE=%errorlevel%"
goto finished

:trylauncher
where py >nul 2>nul
if errorlevel 1 goto nopython
py -3 "%RUNNER%" %*
set "CODE=%errorlevel%"
goto finished

:nopython
echo Python was not found. Install Python 3.11 or newer from https://www.python.org/downloads/ and run this again.

:finished
if not "%CODE%"=="0" pause
exit /b %CODE%
