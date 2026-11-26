FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY *.py telegram_bot/
USER 65534:65534
CMD ["python", "-m", "telegram_bot"]
