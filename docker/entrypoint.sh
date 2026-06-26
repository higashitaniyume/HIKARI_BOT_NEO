#!/usr/bin/env sh
set -eu

SOURCE_BOT_DATA="${HIKARI_SOURCE_ROOT:-/opt/hikaribot-source}/BotData"
VENV_DIR="${UV_PROJECT_ENVIRONMENT:-/opt/hikaribot-venv}"

mkdir -p /app/BotData/plugin_configs /app/BotData/resources /app/BotData/fonts /app/BotData/Gifs /app/UserData /app/sharedFolder /tmp/hikari_bot

if [ ! -x "$VENV_DIR/bin/python" ]; then
  python -m venv "$VENV_DIR"
fi

SYSTEM_DEPS_READY=1
for package in ffmpeg libcairo2 libpango-1.0-0 fonts-noto-cjk; do
  if ! dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -qx 'install ok installed'; then
    SYSTEM_DEPS_READY=0
    break
  fi
done

if [ "$SYSTEM_DEPS_READY" -eq 0 ]; then
  apt-get update
  apt-get install -y --no-install-recommends \
    ffmpeg \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    shared-mime-info \
    fonts-noto-cjk
  rm -rf /var/lib/apt/lists/*
fi

if [ ! -x "$VENV_DIR/bin/uv" ]; then
  "$VENV_DIR/bin/python" -m pip install --no-cache-dir uv
fi

export PATH="$VENV_DIR/bin:$PATH"
export VIRTUAL_ENV="$VENV_DIR"
export UV_PROJECT_ENVIRONMENT="$VENV_DIR"

if [ ! -f /app/BotData/config.json ] && [ -f "$SOURCE_BOT_DATA/config.example.json" ]; then
  cp "$SOURCE_BOT_DATA/config.example.json" /app/BotData/config.json
  echo "Created /app/BotData/config.json from source defaults."
fi

for example in "$SOURCE_BOT_DATA"/plugin_configs/*.example.json; do
  [ -f "$example" ] || continue
  name="$(basename "$example" .example.json)"
  target="/app/BotData/plugin_configs/${name}.json"
  if [ ! -f "$target" ]; then
    cp "$example" "$target"
    echo "Created $target from image defaults."
  fi
done

for example in "$SOURCE_BOT_DATA"/resources/*.example.json; do
  [ -f "$example" ] || continue
  name="$(basename "$example" .example.json)"
  target="/app/BotData/resources/${name}.json"
  if [ ! -f "$target" ]; then
    cp "$example" "$target"
    echo "Created $target from image defaults."
  fi
done

uv sync --frozen --no-dev --no-install-project

exec "$@"
