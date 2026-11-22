# Развёртывание backend «ЯЕда»

Одна команда на сервере: `bash deploy/deploy.sh`. Backend собирается в Docker, данные (SQLite) лежат в постоянном томе, порт 8080 открыт для Telegram-бота.

## Что нужно на сервере

Linux, Docker Engine с плагином Compose v2, `git`, `openssl`, `curl` и открытый порт (по умолчанию `8080/tcp`).

## Первый запуск

```bash
git clone <URL репозитория> yaeda && cd yaeda
bash deploy/deploy.sh
```

Скрипт создаст `.env` из `.env.example`, сгенерирует `BOT_API_KEY` (если он пуст), соберёт образ, запустит контейнер и дождётся статуса healthy. Проверка:

```bash
bash deploy/deploy.sh check          # или: curl http://<сервер>:8080/healthcheck
grep BOT_API_KEY .env                # ключ для бота; в открытый чат и git не отправлять
```

## Обновление

```bash
git pull && bash deploy/deploy.sh    # на сервере
bash deploy/deploy.sh remote         # со своей машины по SSH (настройка ниже)
```

## Команды

| Команда | Что делает |
|---|---|
| `up` (по умолчанию) | собрать и перезапустить все сервисы |
| `down` | остановить и удалить контейнеры, данные остаются |
| `restart` | перезапустить без пересборки |
| `status` | состояние контейнеров |
| `logs [сервис]` | логи |
| `check` | проверить `/healthcheck` |
| `remote` | выполнить `up` на сервере по SSH |

## Настройки (`.env`)

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `BOT_API_KEY` | генерируется | ключ бот → backend (заголовок `X-API-Key`) |
| `BACKEND_PORT` | `8080` | порт на хосте |
| `BACKEND_BIND` | `0.0.0.0` | `127.0.0.1`, если порт не нужен снаружи |

База лежит в томе `backend-data` (`/data/app.db`) и переживает перезапуск и пересборку. Команда `docker compose down -v` удалит том вместе с данными.

## Как подключается бот

- **В той же compose-сети**: адрес `http://backend:8080`, заголовок `X-API-Key: <BOT_API_KEY>`.
- **С другой машины**: `http://<сервер>:<BACKEND_PORT>`, на сервере порт должен быть открыт (`ufw allow 8080/tcp`).
- **HTTPS**: контракт требует его для рабочих вызовов, а сам backend отвечает по HTTP. Для боевого режима поставьте перед ним reverse proxy (Caddy или nginx) с сертификатом.

## Как встроить свой сервис (например, бота)

Скрипт сам подхватывает все файлы `deploy/compose.d/*.yml`: свой сервис достаточно описать в отдельном файле, `deploy.sh` не меняется.

```yaml
# deploy/compose.d/bot.yml
services:
  bot:
    build: ./telegram_bot
    restart: unless-stopped
    environment:
      BACKEND_URL: http://backend:8080
      BACKEND_API_KEY: ${BOT_API_KEY}
    depends_on:
      backend:
        condition: service_healthy
    networks:
      - yaeda
```

Свои переменные добавляйте в `.env.example` отдельным блоком с комментарием.

## Доступ к серверу по SSH

Приватный ключ у каждого свой и никому не передаётся. В чат отправляется только публичный.

```bash
ssh-keygen -t ed25519 -C "ваше-имя@yaeda"
cat ~/.ssh/id_ed25519.pub            # Windows (PowerShell): type $env:USERPROFILE\.ssh\id_ed25519.pub
```

Тот, у кого есть доступ к серверу, добавляет присланный публичный ключ:

```bash
echo "ssh-ed25519 AAAA... имя@yaeda" >> ~/.ssh/authorized_keys
chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys
```

Удалённый деплой настраивается файлом `deploy/.deploy.env` (добавьте его в `.gitignore`):

```bash
DEPLOY_HOST=user@server
DEPLOY_DIR=/home/user/yaeda
DEPLOY_BRANCH=master
```

## Файлы

`Dockerfile`, `docker-compose.yml`, `.dockerignore` и `.env.example` лежат в корне репозитория, `deploy/deploy.sh` и `deploy/compose.d/` в папке `deploy/`.
