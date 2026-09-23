#!/usr/bin/env bash
set -euo pipefail

# Deployment script for Telegram Reminder Bot on 'nova' server
REMOTE_HOST="nova"
REMOTE_DIR="/opt/reminder-telegram-bot"

echo "🚀 Deploying Reminder Bot to ${REMOTE_HOST}:${REMOTE_DIR}..."

# 1. Ensure remote directory exists
ssh "${REMOTE_HOST}" "mkdir -p ${REMOTE_DIR}/data"

# 2. Sync files to remote server (excluding local git/venv/data)
rsync -avz --delete \
  --exclude '.git' \
  --exclude 'venv' \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude 'data' \
  --exclude '*.sqlite*' \
  ./ "${REMOTE_HOST}:${REMOTE_DIR}/"

# 3. Check if remote .env exists; if not, copy template
ssh "${REMOTE_HOST}" "
  if [ ! -f '${REMOTE_DIR}/.env' ]; then
    echo '⚠️ Remote .env not found. Creating from .env.example...'
    cp '${REMOTE_DIR}/.env.example' '${REMOTE_DIR}/.env'
    echo '👉 Please edit ${REMOTE_DIR}/.env on nova with your TELEGRAM_BOT_TOKEN!'
  fi
"

# 4. Build and restart containers with Docker Compose
echo "🐳 Building and starting containers on ${REMOTE_HOST}..."
ssh "${REMOTE_HOST}" "
  cd '${REMOTE_DIR}'
  docker compose build
  docker compose up -d --remove-orphans
  docker compose ps
"

echo "✅ Deployment completed successfully!"
