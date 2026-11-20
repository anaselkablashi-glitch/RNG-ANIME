@echo off
cd /d "%~dp0"
python -m signalbot run
echo.
echo The bot has stopped. If there is an error above, send it to Claude.
pause
