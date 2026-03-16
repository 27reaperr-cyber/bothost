FROM python:3.10-slim

# Устанавливаем системные зависимости
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Устанавливаем Docker CLI (для управления дочерними контейнерами)
RUN curl -fsSL https://get.docker.com | sh

WORKDIR /app

# Копируем зависимости и устанавливаем
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем исходники
COPY . .

# Создаём рабочие папки
RUN mkdir -p projects logs

# Запускаем бота
ENTRYPOINT ["python", "bot.py"]
