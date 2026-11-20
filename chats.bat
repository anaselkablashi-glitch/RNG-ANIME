@echo off
cd /d "%~dp0"
python -m signalbot chats
echo.
echo Copy the group's name into chat = "" in config.toml.
pause
