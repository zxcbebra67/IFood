#!/usr/bin/env bash
# Развёртывание «ЯЕда»: backend + любые сервисы из deploy/compose.d/*.yml.
# Запуск на сервере:  bash deploy/deploy.sh [команда]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

log() { printf '\033[1;34m[deploy]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[deploy] ошибка:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Использование: bash deploy/deploy.sh [команда]

  up          собрать и (пере)запустить все сервисы (по умолчанию)
  down        остановить и удалить контейнеры (данные в томе сохраняются)
  restart     перезапустить контейнеры без пересборки
  status      показать состояние контейнеров
  logs [svc]  смотреть логи (всех сервисов или одного)
  check       проверить /healthcheck на этом сервере
  remote      выполнить `up` на сервере по SSH (нужен deploy/.deploy.env)
EOF
}

# Настройки удалённого деплоя (нужны только для `remote`)
if [ -f deploy/.deploy.env ]; then
  # shellcheck disable=SC1091
  . deploy/.deploy.env
fi
DEPLOY_BRANCH="${DEPLOY_BRANCH:-master}"

# Основной compose-файл + все сервисы, которые добавили коллеги в deploy/compose.d/
compose_files=(-f docker-compose.yml)
for f in deploy/compose.d/*.yml; do
  if [ -e "$f" ]; then
    compose_files+=(-f "$f")
  fi
done

dc() { docker compose --project-directory "$ROOT" "${compose_files[@]}" "$@"; }

need_docker() {
  command -v docker >/dev/null 2>&1 \
    || die "Docker не найден. Установка: https://docs.docker.com/engine/install/"
  docker compose version >/dev/null 2>&1 \
    || die "Нужен плагин Docker Compose v2 (команда 'docker compose')."
}

ensure_env() {
  if [ ! -f .env ]; then
    cp .env.example .env
    log "создан .env из .env.example"
  fi
  if ! grep -Eq '^BOT_API_KEY=.+' .env; then
    command -v openssl >/dev/null 2>&1 \
      || die "Нет openssl: впишите BOT_API_KEY в .env вручную."
    local key
    key="$(openssl rand -hex 32)"
    if grep -q '^BOT_API_KEY=' .env; then
      sed -i.bak "s|^BOT_API_KEY=.*|BOT_API_KEY=${key}|" .env && rm -f .env.bak
    else
      printf 'BOT_API_KEY=%s\n' "$key" >> .env
    fi
    log "сгенерирован BOT_API_KEY (лежит в .env; передайте его боту безопасным способом)"
  fi
  chmod 600 .env
}

host_port() {
  local port
  port="$(grep -E '^BACKEND_PORT=' .env 2>/dev/null | cut -d= -f2 || true)"
  echo "${port:-8080}"
}

wait_healthy() {
  local cid status
  log "жду готовности backend..."
  for _ in $(seq 1 30); do
    cid="$(dc ps -q backend)"
    status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid" 2>/dev/null || echo none)"
    if [ "$status" = "healthy" ]; then
      log "backend готов"
      return 0
    fi
    sleep 2
  done
  dc logs --tail 50 backend >&2 || true
  die "backend не стал healthy за 60 секунд"
}

cmd_up() {
  need_docker
  ensure_env
  log "сборка и запуск"
  dc up -d --build
  wait_healthy
  dc ps
  log "проверка: curl http://<адрес-сервера>:$(host_port)/healthcheck"
}

cmd_check() {
  need_docker
  curl -fsS "http://127.0.0.1:$(host_port)/healthcheck" && echo
}

cmd_remote() {
  : "${DEPLOY_HOST:?задайте DEPLOY_HOST (user@host) в deploy/.deploy.env}"
  : "${DEPLOY_DIR:?задайте DEPLOY_DIR (путь к репозиторию на сервере) в deploy/.deploy.env}"
  log "деплой на ${DEPLOY_HOST}:${DEPLOY_DIR} (ветка ${DEPLOY_BRANCH})"
  ssh "$DEPLOY_HOST" "cd '$DEPLOY_DIR' && git fetch origin && git checkout '$DEPLOY_BRANCH' && git pull --ff-only origin '$DEPLOY_BRANCH' && bash deploy/deploy.sh up"
}

case "${1:-up}" in
  up)             cmd_up ;;
  down)           need_docker; dc down ;;
  restart)        need_docker; dc restart ;;
  status)         need_docker; dc ps ;;
  logs)           need_docker; shift; dc logs -f --tail 100 "$@" ;;
  check)          cmd_check ;;
  remote)         cmd_remote ;;
  -h|--help|help) usage ;;
  *)              usage; exit 1 ;;
esac
