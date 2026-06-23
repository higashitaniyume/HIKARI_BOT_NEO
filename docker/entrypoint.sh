#!/usr/bin/env sh
set -eu

DEFAULT_BOT_DATA="/opt/hikaribot-defaults/BotData"

mkdir -p /app/BotData/plugin_configs /app/BotData/Gifs /app/UserData /app/sharedFolder /tmp/hikari_bot

if [ ! -f /app/BotData/config.json ] && [ -f "$DEFAULT_BOT_DATA/config.example.json" ]; then
  cp "$DEFAULT_BOT_DATA/config.example.json" /app/BotData/config.json
  echo "Created /app/BotData/config.json from image defaults."
fi

for example in "$DEFAULT_BOT_DATA"/plugin_configs/*.example.json; do
  [ -f "$example" ] || continue
  name="$(basename "$example" .example.json)"
  target="/app/BotData/plugin_configs/${name}.json"
  if [ ! -f "$target" ]; then
    cp "$example" "$target"
    echo "Created $target from image defaults."
  fi
done

exec "$@"
