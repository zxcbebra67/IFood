# Развёртывание «ЯЕды»

Команда `bash deploy/deploy.sh up` собирает и запускает бэкенд и включённые сервисы. SQLite хранится в постоянном томе. Бот обращается к бэкенду по внутреннему адресу `http://backend:8080`.

## Что нужно на сервере

Linux, Docker Engine с плагином Compose v2, `git`, `openssl` и `curl`.

## Первый запуск

```bash
git clone <URL репозитория> yaeda && cd yaeda
bash deploy/deploy.sh
```

Скрипт создаст `.env` из `.env.example`, сгенерирует `BOT_API_KEY` (если он пуст), соберёт образ, запустит контейнер и дождётся статуса healthy. Проверка:

```bash
bash deploy/deploy.sh check
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
| `BACKEND_BIND` | `127.0.0.1` | Адрес публикации порта на хосте |
| `BOT_TOKEN` | Пусто | Токен Telegram-бота, обязателен с профилем `bot` |
| `COMPOSE_PROFILES` | Пусто | Значение `bot` включает контейнер Telegram-бота |
| `TELEGRAM_PROXY_URL` | Пусто | Прокси для запросов к Telegram Bot API |
| `OPENROUTER_API_KEY` | Пусто | Ключ OpenRouter для генерации рецептов |
| `OPENROUTER_MODEL` | `openai/gpt-4o-mini` | Модель генерации |
| `OPENROUTER_PROXY_URL` | Пусто | HTTP-прокси для HTTPS-запросов бэкенда к OpenRouter |

Прокси Telegram и прокси OpenRouter задаются отдельно. Если для обоих используется
один Webshare, можно указать в `.env` адрес один раз и добавить ниже:

```dotenv
OPENROUTER_PROXY_URL=${TELEGRAM_PROXY_URL}
```

После изменения `.env` выполните `bash deploy/deploy.sh up`, чтобы Compose
применил настройки к контейнерам.

## Хранение SQLite

В контейнере база находится в `/data/app.db`. При новой установке используется
том `backend-data`. На боевой ВМ каталог `/data` подключён с отдельного HDD.
При обновлении на этой ВМ сохраните существующий локальный файл
`deploy/compose.d/production.yml` и проверьте, что диск смонтирован.

Минимальная настройка для ВМ с этим HDD:

```yaml
services:
  backend:
    volumes:
      - type: bind
        source: /mnt/students-data/yaeda
        target: /data
        bind:
          create_host_path: false
```

SQLite может хранить рядом файлы `app.db-wal` и `app.db-shm`. Для резервного
копирования работающей базы используйте SQLite Backup API или команду
`.backup` SQLite. Команда `docker compose down -v` удаляет том новой установки.

## Как подключается бот

- **В той же compose-сети**: адрес `http://backend:8080`, заголовок `X-API-Key: <BOT_API_KEY>`.
- **С другой машины**: настройте HTTPS через reverse proxy с сертификатом.

## Запуск бэкенда и Telegram-бота вместе

Сервис бота уже описан в `deploy/compose.d/bot.yml`. Скрипт запуска
подключает этот файл автоматически.

1. В корне проекта создай `.env` из `.env.example`, если файла ещё нет.
2. В корневом `.env` задай `BOT_TOKEN`, `OPENROUTER_API_KEY` и `COMPOSE_PROFILES=bot`.
   При обновлении используй существующий `.env` и сохрани текущий `BOT_API_KEY`.
   Если Telegram недоступен напрямую с ВМ, задай `TELEGRAM_PROXY_URL` в формате
   `http://user:password@host:port` для HTTP-прокси Webshare. Спецсимволы в логине
   и пароле кодируются как части URL. Для прямого доступа оставь переменную пустой.
   Для публикации порта только внутри ВМ укажи `BACKEND_BIND=127.0.0.1`.
3. Останови локальную копию Telegram-бота с тем же токеном и запусти:

   ```bash
   bash deploy/deploy.sh up
   bash deploy/deploy.sh check
   bash deploy/deploy.sh logs bot
   ```

Бот дождётся готовности бэкенда и будет обращаться к нему по адресу
`http://backend:8080` внутри Docker-сети. Для проверки пришли боту `/start`
в Telegram, затем найди омлет, добавь его в избранное, проверь список и удаление.
После этого попроси бота сгенерировать рецепт и сохрани результат в избранном.

Чтобы запускать только бэкенд на чистой установке, оставь профиль бота выключенным.

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

Удалённый деплой настраивается файлом `deploy/.deploy.env`, который уже исключён из Git:

```bash
DEPLOY_HOST=user@server
DEPLOY_DIR=/home/user/yaeda
DEPLOY_BRANCH=master
```

## Файлы

`Dockerfile`, `docker-compose.yml`, `.dockerignore` и `.env.example` лежат в корне репозитория, `deploy/deploy.sh` и `deploy/compose.d/` в папке `deploy/`.
