@echo off
cd /d "%~dp0"
call "%~dp0start.bat"
exit /b %errorlevel%
