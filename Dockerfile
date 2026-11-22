# syntax=docker/dockerfile:1
FROM python:3.14-slim

# uv берём из официального образа (рекомендуется закрепить версию тега, когда договоритесь о ней)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH" \
    DB_PATH=/data/app.db

WORKDIR /app

# Сначала только зависимости: этот слой кэшируется, пока не менялись pyproject.toml и uv.lock
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Потом код (лишнее отсекает ..dockerignore)
COPY . .

# Непривилегированный пользователь и каталог под SQLite (подключается как том)
RUN useradd --system --uid 10001 --no-create-home app \
    && mkdir -p /data \
    && chown app /data
USER app

EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthcheck', timeout=2)" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
