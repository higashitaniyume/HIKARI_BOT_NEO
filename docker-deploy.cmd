@echo off
setlocal

set "CREATED_CONFIG=0"

cd /d "%~dp0"

if not exist "BotData\config.json" (
  copy "BotData\config.example.json" "BotData\config.json" >nul
  echo Created BotData\config.json from example.
  set "CREATED_CONFIG=1"
)

for %%N in (pixiv_parser cobalt_parser media_transcoder sticker_web) do (
  if exist "BotData\plugin_configs\%%N.example.json" (
    if not exist "BotData\plugin_configs\%%N.json" (
      copy "BotData\plugin_configs\%%N.example.json" "BotData\plugin_configs\%%N.json" >nul
      echo Created BotData\plugin_configs\%%N.json from example.
      set "CREATED_CONFIG=1"
    )
  )
)

if "%CREATED_CONFIG%"=="1" (
  echo Please edit BotData\config.json and BotData\plugin_configs\*.json, then run this script again.
  exit /b 1
)

if not exist "docker-compose.yml" (
  echo docker-compose.yml not found.
  exit /b 1
)

docker compose up -d --build --remove-orphans
if errorlevel 1 exit /b 1

echo Docker compose deployment finished.
echo Logs: docker compose logs -f hikari-bot-neo
