FROM python:3.10-slim

# Системные зависимости: git для клонирования, curl для Docker CLI
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Устанавливаем Docker CLI (опционально — бот работает и без него)
# Если сокет /var/run/docker.sock примонтирован при docker run — будет Docker-режим.
# Если нет — автоматически включится прямой режим запуска процессов.
RUN curl -fsSL https://get.docker.com | sh || true

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p projects logs

ENTRYPOINT ["python", "bot.py"]
