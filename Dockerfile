FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        libcairo2 \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        shared-mime-info \
        fonts-noto-cjk \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock* ./

RUN pip install --no-cache-dir uv \
    && if [ -f uv.lock ]; then uv sync --frozen --no-dev --no-install-project; else uv sync --no-dev --no-install-project; fi

COPY BotData/config.example.json /opt/hikaribot-defaults/BotData/config.example.json
COPY BotData/plugin_configs/*.example.json /opt/hikaribot-defaults/BotData/plugin_configs/
COPY BotData/resources/*.example.json /opt/hikaribot-defaults/BotData/resources/
COPY docker/entrypoint.sh /usr/local/bin/hikaribot-entrypoint
COPY . .

RUN chmod +x /usr/local/bin/hikaribot-entrypoint \
    && mkdir -p BotData/plugin_configs BotData/resources BotData/fonts BotData/Gifs UserData sharedFolder /tmp/hikari_bot

ENTRYPOINT ["hikaribot-entrypoint"]
CMD ["python", "bot.py"]
