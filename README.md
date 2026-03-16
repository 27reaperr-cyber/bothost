# 🤖 BotHost — Telegram Bot Hosting via Telegram

Простой хостинг Telegram-ботов, управляемый через Telegram.  
Деплойте GitHub/GitLab репозитории одним сообщением.

---

## 📁 Структура проекта

```
tghost/
├── bot.py              # Главный файл бота (handlers, FSM)
├── deploy.py           # Логика деплоя (клонирование, валидация)
├── database.py         # SQLite (пользователи, боты)
├── process_manager.py  # Управление Docker-контейнерами
├── requirements.txt
├── Dockerfile
├── .env.example
├── projects/           # Папки задеплоенных ботов
└── logs/               # Логи
```

---

## ⚙️ Требования

- Python 3.10+
- Docker
- Git

---

## 🚀 Установка и запуск

### 1. Клонируйте репозиторий

```bash
git clone https://github.com/your/tghost.git
cd tghost
```

### 2. Создайте `.env`

```bash
cp .env.example .env
nano .env
```

Укажите токен бота:
```
BOT_TOKEN=123456789:ABCdefGHI...
```

### 3. Установите зависимости

```bash
pip install -r requirements.txt
```

### 4. Запустите бота

```bash
python bot.py
```

---

## 🐳 Запуск через Docker

### Сборка образа

```bash
docker build -t bothost .
```

### Запуск контейнера

> ⚠️ Монтируем Docker socket, чтобы бот мог управлять дочерними контейнерами.

```bash
docker run -d \
  --name bothost \
  --env-file .env \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v $(pwd)/projects:/app/projects \
  -v $(pwd)/logs:/app/logs \
  --restart unless-stopped \
  bothost
```

### Просмотр логов

```bash
docker logs -f bothost
```

---

## 📋 Пример использования

1. Откройте бота в Telegram
2. Нажмите **🚀 Deploy бот**
3. Отправьте ссылку на репозиторий:
   ```
   https://github.com/example/my-telegram-bot
   ```
4. Бот клонирует репозиторий и проверит:
   - что это Python-проект
   - что используется Telegram-библиотека
5. Введите переменные окружения:
   ```
   BOT_TOKEN=123456:ABC
   DATABASE_URL=sqlite:///db.sqlite3
   ```
6. Напишите `done` — бот запустится в Docker-контейнере!

---

## 🔧 Требования к деплоимому репозиторию

| Требование | Детали |
|---|---|
| Язык | Python 3.x |
| Точка входа | `bot.py`, `main.py`, `app.py` или файл с `__main__` |
| Telegram-фреймворк | `aiogram`, `python-telegram-bot`, `pyTelegramBotAPI`, `telebot` |

---

## 🛡️ Безопасность

- Только GitHub/GitLab репозитории (валидация regex)
- Subprocess без `shell=True`
- Валидация ENV-переменных (запрет инъекций)
- Максимум 3 бота на аккаунт
- Лимиты контейнера: 512 МБ RAM, 0.5 CPU

---

## 📊 Кнопки управления

| Кнопка | Действие |
|---|---|
| 🚀 Deploy бот | Задеплоить новый бот |
| 📦 Мои боты | Список ботов с управлением |
| 📊 Статус сервера | CPU / RAM / Диск / Контейнеры |
| ℹ️ Помощь | Инструкция |

### Inline-управление каждым ботом:

| Кнопка | Действие |
|---|---|
| ▶ Запустить | `docker restart` |
| ⏹ Остановить | `docker stop` |
| 🔄 Перезапустить | `docker restart` |
| 📜 Логи | `docker logs --tail 30` |
| 🗑 Удалить | Удалить контейнер, образ и файлы |

---

## 🔄 Обновление

```bash
git pull
docker build -t bothost .
docker stop bothost && docker rm bothost
# затем повторный docker run (см. выше)
```
