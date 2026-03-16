# 🤖 BotHost — Telegram Bot Hosting via Telegram

Хостинг Telegram-ботов, управляемый через Telegram.  
Деплойте GitHub/GitLab репозитории одним сообщением.

---

## ⚡ Два режима работы (автоматически)

| Условие | Режим | Изоляция |
|---|---|---|
| Docker сокет доступен | 🐳 Docker | Полная (контейнеры) |
| Docker недоступен | ⚙️ Прямой запуск | venv + subprocess |

Режим определяется **автоматически при старте** — никакой ручной настройки не нужно.

---

## 📁 Структура проекта

```
tghost/
├── bot.py              ← handlers, FSM, клавиатуры
├── deploy.py           ← клонирование, валидация, .env
├── database.py         ← SQLite (users, bots)
├── process_manager.py  ← гибридный менеджер (Docker / прямой)
├── requirements.txt
├── Dockerfile
├── .env.example
├── projects/           ← папки задеплоенных ботов
└── logs/               ← логи (прямой режим)
```

---

## ⚙️ Требования

- Python 3.10+
- Git
- Docker (опционально — без него работает в прямом режиме)

---

## 🚀 Быстрый старт (без Docker)

```bash
git clone https://github.com/your/tghost.git
cd tghost

cp .env.example .env
# Укажите BOT_TOKEN в .env

pip install -r requirements.txt
python bot.py
```

Бот автоматически определит, что Docker недоступен, и будет запускать  
деплоимые боты напрямую через `subprocess` + `venv`.

---

## 🐳 Запуск через Docker (с Docker-режимом для ботов)

> Монтируем Docker socket — это позволяет боту управлять дочерними контейнерами.

```bash
docker build -t bothost .

docker run -d \
  --name bothost \
  --env-file .env \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v $(pwd)/projects:/app/projects \
  -v $(pwd)/logs:/app/logs \
  --restart unless-stopped \
  bothost
```

### ❌ Ошибка: `no such file or directory: /var/run/docker.sock`

Это значит, что Docker сокет не примонтирован или Docker не установлен на хосте.  
**Решения:**

1. **Простое** — запустите бота напрямую (`python bot.py`), Docker-режим не нужен.
2. **С Docker** — добавьте `-v /var/run/docker.sock:/var/run/docker.sock` к `docker run`.
3. **На Bothost.ru / shared хостинге** — используйте прямой запуск (`python bot.py`).

---

## 📋 Пример деплоя бота

1. Откройте BotHost в Telegram
2. Нажмите **🚀 Deploy бот**
3. Отправьте ссылку:
   ```
   https://github.com/example/my-telegram-bot
   ```
4. Введите переменные:
   ```
   BOT_TOKEN=123456:ABC
   ```
5. Напишите `done` — бот запущен!

---

## 🔧 Требования к деплоимому репозиторию

| | |
|---|---|
| Язык | Python 3.x |
| Точка входа | `bot.py`, `main.py`, `app.py` или файл с `__main__` |
| Фреймворк | `aiogram`, `python-telegram-bot`, `pyTelegramBotAPI`, `telebot` |

---

## 🛡️ Безопасность

- Только GitHub/GitLab (валидация regex)
- `subprocess` без `shell=True`
- Валидация ENV (запрет `$()` и обратных кавычек)
- Максимум 3 бота на аккаунт
- Docker: 512 МБ RAM, 0.5 CPU на контейнер

---

## 📊 Кнопки

| Кнопка | Действие |
|---|---|
| 🚀 Deploy бот | Задеплоить новый бот |
| 📦 Мои боты | Список с управлением |
| 📊 Статус сервера | CPU / RAM / Диск / режим |
| ℹ️ Помощь | Инструкция |

Inline-управление каждым ботом: ▶ ⏹ 🔄 📜 🗑
