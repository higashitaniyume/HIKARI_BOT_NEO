#!/usr/bin/env sh
set -eu

CREATED_CONFIG=0

PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$PROJECT_ROOT"

if [ ! -f "BotData/config.json" ]; then
  cp "BotData/config.example.json" "BotData/config.json"
  echo "Created BotData/config.json from example."
  CREATED_CONFIG=1
fi

for name in pixiv_parser cobalt_parser media_transcoder sticker_web; do
  example="BotData/plugin_configs/$name.example.json"
  target="BotData/plugin_configs/$name.json"
  if [ -f "$example" ] && [ ! -f "$target" ]; then
    cp "$example" "$target"
    echo "Created $target from example."
    CREATED_CONFIG=1
  fi
done

if [ "$CREATED_CONFIG" = "1" ]; then
  echo "Please edit BotData/config.json and BotData/plugin_configs/*.json, then run this script again."
  exit 1
fi

if [ ! -f "docker-compose.yml" ]; then
  echo "docker-compose.yml not found."
  exit 1
fi

docker compose up -d --build --remove-orphans

echo "Docker compose deployment finished."
echo "Logs: docker compose logs -f hikari-bot-neo"
