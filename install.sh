#!/usr/bin/env sh
# One-command install and update for a source-mounted HIKARI BOT NEO server.
#
# First install:
#   curl -fsSL https://raw.githubusercontent.com/higashitaniyume/HIKARI_BOT_NEO/main/install.sh | sh
#
# Later updates:
#   /opt/hikaribot-docker/app/install.sh

set -eu

REPOSITORY_URL="${HIKARI_REPOSITORY_URL:-https://github.com/higashitaniyume/HIKARI_BOT_NEO.git}"
DEPLOY_DIR="${HIKARI_DEPLOY_DIR:-/opt/hikaribot-docker}"
BRANCH="${HIKARI_BRANCH:-main}"
APP_DIR="$DEPLOY_DIR/app"
COMPOSE_FILE="$DEPLOY_DIR/docker-compose.yml"
ENV_FILE="$DEPLOY_DIR/.env"
SEARXNG_CONFIG_DIR="$DEPLOY_DIR/searxng/core-config"

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "缺少命令：$1" >&2
    exit 1
  }
}

create_searxng_settings() {
  settings_file="$SEARXNG_CONFIG_DIR/settings.yml"
  if [ -f "$settings_file" ]; then
    return
  fi

  cp "$APP_DIR/deploy/searxng/core-config/settings.yml" "$settings_file"
  if command -v openssl >/dev/null 2>&1; then
    secret="$(openssl rand -hex 32)"
  else
    secret="$(date +%s)-$(hostname)-hikari"
  fi
  sed -i "s/__SEARXNG_SECRET__/$secret/g" "$settings_file"
}

if [ "$(id -u)" -ne 0 ]; then
  echo "请使用 root 运行，或通过 sudo 执行此脚本。" >&2
  exit 1
fi

require_command git
require_command docker

if ! docker compose version >/dev/null 2>&1; then
  echo "未找到 Docker Compose v2 插件，请先安装 docker-compose-plugin。" >&2
  exit 1
fi

if [ -d "$APP_DIR/.git" ]; then
  if [ -n "$(git -C "$APP_DIR" status --porcelain)" ]; then
    echo "检测到 $APP_DIR 有未提交或未跟踪的源码修改，已停止更新以避免覆盖。" >&2
    exit 1
  fi

  echo "更新机器人源码（$BRANCH）..."
  git -C "$APP_DIR" fetch --depth 1 origin "$BRANCH"
  git -C "$APP_DIR" checkout --quiet "$BRANCH"
  git -C "$APP_DIR" reset --hard "origin/$BRANCH"
else
  if [ -e "$APP_DIR" ] && [ "$(find "$APP_DIR" -mindepth 1 -maxdepth 1 | wc -l)" -ne 0 ]; then
    echo "$APP_DIR 已存在但不是 Git 仓库；请先迁移或清理该目录。" >&2
    exit 1
  fi

  echo "拉取机器人源码（$BRANCH）..."
  mkdir -p "$DEPLOY_DIR"
  git clone --depth 1 --branch "$BRANCH" "$REPOSITORY_URL" "$APP_DIR"
fi

if [ -d "$DEPLOY_DIR/sharedFolder" ] && [ ! -e "$DEPLOY_DIR/runtime/shared" ]; then
  mkdir -p "$DEPLOY_DIR/runtime"
  mv "$DEPLOY_DIR/sharedFolder" "$DEPLOY_DIR/runtime/shared"
fi

if [ -d "$DEPLOY_DIR/tmp" ] && [ ! -e "$DEPLOY_DIR/runtime/tmp" ]; then
  mkdir -p "$DEPLOY_DIR/runtime"
  mv "$DEPLOY_DIR/tmp" "$DEPLOY_DIR/runtime/tmp"
fi

mkdir -p \
  "$DEPLOY_DIR/BotData" \
  "$DEPLOY_DIR/UserData" \
  "$DEPLOY_DIR/runtime/shared" \
  "$DEPLOY_DIR/runtime/tmp/hikari_bot" \
  "$DEPLOY_DIR/napcat/config" \
  "$DEPLOY_DIR/napcat/ntqq" \
  "$DEPLOY_DIR/searxng/core-config" \
  "$DEPLOY_DIR/legacy/pixiv_cache"

create_searxng_settings

cp "$APP_DIR/deploy/docker-compose.server.yml" "$COMPOSE_FILE"

if [ ! -f "$ENV_FILE" ]; then
  cp "$APP_DIR/.env.example" "$ENV_FILE"
  echo "已创建 $ENV_FILE，可按需填写 NAPCAT_ACCOUNT 和端口设置。"
fi

echo "检查 Docker Compose 配置..."
docker compose --project-directory "$DEPLOY_DIR" -f "$COMPOSE_FILE" config -q

echo "启动服务..."
docker compose --project-directory "$DEPLOY_DIR" -f "$COMPOSE_FILE" up -d --remove-orphans
docker compose --project-directory "$DEPLOY_DIR" -f "$COMPOSE_FILE" restart hikaribot

cat <<EOF

部署完成。

运行配置位于：$DEPLOY_DIR/BotData/
首次启动后请编辑 BotData/config.json 与 BotData/plugin_configs/*.json，
然后执行：docker compose --project-directory "$DEPLOY_DIR" restart hikaribot

查看日志：docker compose --project-directory "$DEPLOY_DIR" logs -f hikaribot
以后更新：$APP_DIR/install.sh
EOF
