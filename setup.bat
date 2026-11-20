@echo off
cd /d "%~dp0"
python --version >nul 2>nul
if errorlevel 1 (
  echo Python is not installed.
  echo Install it from https://www.python.org/downloads/ and tick
  echo "Add python.exe to PATH" on the first screen. Then run this again.
  pause
  exit /b 1
)
echo Installing what the bot needs...
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo The install failed. Copy the text above and send it to Claude.
  pause
  exit /b 1
)
if not exist config.toml copy config.example.toml config.toml >nul
echo.
echo Running the bot's self-tests...
python -m pytest -q
echo.
echo Setup finished. config.toml opens in Notepad next.
echo Fill in login, api_id and api_hash, then save and close it.
pause
notepad config.toml
