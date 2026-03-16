"""
bot.py — главный файл Telegram-бота BotHost.
Управляет деплоем, мониторингом и жизненным циклом контейнеров.
Запуск: python bot.py
"""

import asyncio
import logging
import os
import shutil

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from dotenv import load_dotenv

import database as db
import deploy as dep
import process_manager as pm

# ── Настройка ─────────────────────────────────────────────────────────────────

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

MAX_BOTS_PER_USER = 3

# ── FSM состояния ─────────────────────────────────────────────────────────────

class DeployStates(StatesGroup):
    waiting_url = State()
    waiting_env = State()


# ── Клавиатуры ────────────────────────────────────────────────────────────────

def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🚀 Deploy бот"), KeyboardButton(text="📦 Мои боты")],
            [KeyboardButton(text="📊 Статус сервера"), KeyboardButton(text="ℹ️ Помощь")],
        ],
        resize_keyboard=True,
    )


def cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True,
    )


def bot_manage_keyboard(project_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="▶ Запустить",     callback_data=f"start:{project_id}"),
            InlineKeyboardButton(text="⏹ Остановить",   callback_data=f"stop:{project_id}"),
        ],
        [
            InlineKeyboardButton(text="🔄 Перезапустить", callback_data=f"restart:{project_id}"),
            InlineKeyboardButton(text="📜 Логи",          callback_data=f"logs:{project_id}"),
        ],
        [
            InlineKeyboardButton(text="🗑 Удалить",       callback_data=f"delete:{project_id}"),
        ],
    ])


# ── Инициализация ─────────────────────────────────────────────────────────────

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())


# ── /start ────────────────────────────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    db.upsert_user(message.from_user.id, message.from_user.username or "")
    await message.answer(
        "👋 Добро пожаловать в <b>BotHost</b>!\n\n"
        "Деплойте Telegram-ботов прямо с GitHub/GitLab.\n"
        "Выберите действие:",
        reply_markup=main_keyboard(),
    )


# ── Главное меню ──────────────────────────────────────────────────────────────

@dp.message(F.text == "❌ Отмена")
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=main_keyboard())


@dp.message(F.text == "ℹ️ Помощь")
async def cmd_help(message: Message):
    await message.answer(
        "<b>ℹ️ Как использовать BotHost</b>\n\n"
        "1️⃣ Нажмите <b>🚀 Deploy бот</b>\n"
        "2️⃣ Отправьте ссылку GitHub/GitLab\n"
        "3️⃣ Задайте переменные окружения (или <code>done</code>)\n"
        "4️⃣ Бот будет запущен в Docker-контейнере!\n\n"
        "<b>Требования к репозиторию:</b>\n"
        "• Python-проект (bot.py / main.py / app.py)\n"
        "• Один из фреймворков: aiogram, python-telegram-bot, telebot\n\n"
        "<b>Лимиты:</b>\n"
        f"• Максимум {MAX_BOTS_PER_USER} бота на аккаунт\n"
        "• 512 МБ RAM и 0.5 CPU на контейнер",
        reply_markup=main_keyboard(),
    )


# ── Статус сервера ────────────────────────────────────────────────────────────

@dp.message(F.text == "📊 Статус сервера")
async def cmd_server_status(message: Message):
    stats = pm.get_server_stats()
    await message.answer(
        "📊 <b>Статус сервера</b>\n\n"
        f"🖥 CPU: <b>{stats['cpu']}%</b>\n"
        f"🧠 RAM: <b>{stats['ram_used']} / {stats['ram_total']} МБ</b> ({stats['ram_percent']}%)\n"
        f"💾 Диск: <b>{stats['disk_used']} / {stats['disk_total']} ГБ</b> ({stats['disk_percent']}%)\n"
        f"🐳 Контейнеров: <b>{stats['containers']}</b>",
        reply_markup=main_keyboard(),
    )


# ── Мои боты ──────────────────────────────────────────────────────────────────

@dp.message(F.text == "📦 Мои боты")
async def cmd_my_bots(message: Message):
    bots = db.get_user_bots(message.from_user.id)
    if not bots:
        await message.answer("У вас нет задеплоенных ботов.", reply_markup=main_keyboard())
        return

    for row in bots:
        pid = row["project_id"]
        # Актуальный статус из Docker
        docker_status = pm.container_status(pid)
        status_emoji = "🟢" if docker_status == "running" else "🔴"
        repo_short = row["repo_url"].replace("https://", "").rstrip("/")

        await message.answer(
            f"{status_emoji} <b>{pid}</b>\n"
            f"📁 {repo_short}\n"
            f"📌 Статус: <code>{docker_status}</code>\n"
            f"🔧 Точка входа: <code>{row['entry_file']}</code>",
            reply_markup=bot_manage_keyboard(pid),
        )


# ── Deploy: Шаг 1 — запрос URL ────────────────────────────────────────────────

@dp.message(F.text == "🚀 Deploy бот")
async def deploy_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    db.upsert_user(user_id, message.from_user.username or "")

    # Проверяем лимит
    if db.count_user_bots(user_id) >= MAX_BOTS_PER_USER:
        await message.answer(
            f"❌ Достигнут лимит: максимум {MAX_BOTS_PER_USER} бота на аккаунт.\n"
            "Удалите один из существующих ботов.",
            reply_markup=main_keyboard(),
        )
        return

    await state.set_state(DeployStates.waiting_url)
    await message.answer(
        "🔗 <b>Шаг 1/2 — Репозиторий</b>\n\n"
        "Отправьте ссылку на GitHub или GitLab репозиторий:\n\n"
        "<code>https://github.com/user/my-bot</code>",
        reply_markup=cancel_keyboard(),
    )


# ── Deploy: Шаг 2 — получаем URL, клонируем, валидируем ──────────────────────

@dp.message(DeployStates.waiting_url)
async def deploy_got_url(message: Message, state: FSMContext):
    repo_url = message.text.strip()

    # Шаг 2: валидация URL
    ok, url_or_err = dep.validate_git_url(repo_url)
    if not ok:
        await message.answer(url_or_err)
        return

    processing_msg = await message.answer("⏳ Клонирование репозитория...")

    # Шаг 3–8: подготовка проекта
    ok, msg, info = await asyncio.get_event_loop().run_in_executor(
        None, dep.prepare_project, message.from_user.id, url_or_err
    )

    if not ok:
        await processing_msg.edit_text(msg)
        return

    # Сохраняем в FSM
    await state.update_data(
        repo_url=url_or_err,
        project_id=info["project_id"],
        project_path=info["project_path"],
        entry_file=info["entry_file"],
    )

    await processing_msg.edit_text(
        f"✅ Репозиторий клонирован!\n\n"
        f"🆔 Project ID: <code>{info['project_id']}</code>\n"
        f"🔧 Точка входа: <code>{info['entry_file']}</code>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📝 <b>Шаг 2/2 — Переменные окружения</b>\n\n"
        "Отправьте переменные в формате:\n"
        "<code>BOT_TOKEN=123456:ABC\n"
        "API_KEY=mykey</code>\n\n"
        "Когда закончите — напишите <code>done</code>\n"
        "Если ENV не нужны — сразу напишите <code>done</code>"
    )
    await state.set_state(DeployStates.waiting_env)


# ── Deploy: Шаг 3 — ENV переменные ───────────────────────────────────────────

@dp.message(DeployStates.waiting_env)
async def deploy_got_env(message: Message, state: FSMContext):
    text = message.text.strip()
    data = await state.get_data()
    project_id = data["project_id"]
    project_path = data["project_path"]
    entry_file = data["entry_file"]
    repo_url = data["repo_url"]
    user_id = message.from_user.id

    # Накапливаем строки ENV
    if text.lower() != "done":
        ok, err = dep.save_env(project_path, text)
        if not ok:
            await message.answer(err)
            return
        await message.answer("✅ Переменные сохранены. Продолжайте или напишите <code>done</code>.")
        return

    # Финальный деплой
    processing_msg = await message.answer("🐳 Сборка Docker-образа и запуск контейнера...")

    # Сборка образа
    ok, image_or_err = await asyncio.get_event_loop().run_in_executor(
        None, pm.build_image, project_path, project_id
    )
    if not ok:
        await processing_msg.edit_text(f"❌ Ошибка сборки Docker-образа:\n<code>{image_or_err}</code>")
        dep.cleanup_project(project_path)
        await state.clear()
        return

    # Запуск контейнера
    ok, result = await asyncio.get_event_loop().run_in_executor(
        None, pm.start_container, project_id, project_path, entry_file
    )
    if not ok:
        await processing_msg.edit_text(f"❌ Ошибка запуска контейнера:\n<code>{result}</code>")
        dep.cleanup_project(project_path)
        await state.clear()
        return

    # Сохраняем в БД
    db.add_bot(project_id, user_id, repo_url, entry_file)
    await state.clear()

    await processing_msg.edit_text(
        "🎉 <b>Бот успешно задеплоен!</b>\n\n"
        f"🆔 Project ID: <code>{project_id}</code>\n"
        f"🔧 Точка входа: <code>{entry_file}</code>\n"
        f"🐳 Контейнер: <code>bot_{project_id}</code>\n\n"
        "Управляйте ботом через <b>📦 Мои боты</b>",
        reply_markup=main_keyboard(),
    )


# ── Callback: управление ботом ────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("start:"))
async def cb_start(call: CallbackQuery):
    project_id = call.data.split(":", 1)[1]
    _check_owner(call, project_id) or await call.answer("⛔ Нет доступа")
    ok, msg = pm.restart_container(project_id)  # restart поднимет если был stopped
    if not ok:
        ok, msg = pm.start_container(
            project_id,
            os.path.abspath(f"projects/{call.from_user.id}/{project_id}"),
            db.get_bot(project_id)["entry_file"] if db.get_bot(project_id) else "bot.py"
        )
    db.update_bot_status(project_id, "running" if ok else "error")
    await call.answer("▶ Запущен" if ok else f"❌ {msg}")
    await call.message.edit_reply_markup(reply_markup=bot_manage_keyboard(project_id))


@dp.callback_query(F.data.startswith("stop:"))
async def cb_stop(call: CallbackQuery):
    project_id = call.data.split(":", 1)[1]
    ok, msg = pm.stop_container(project_id)
    db.update_bot_status(project_id, "stopped" if ok else "error")
    await call.answer("⏹ Остановлен" if ok else f"❌ {msg}")
    await call.message.edit_reply_markup(reply_markup=bot_manage_keyboard(project_id))


@dp.callback_query(F.data.startswith("restart:"))
async def cb_restart(call: CallbackQuery):
    project_id = call.data.split(":", 1)[1]
    ok, msg = pm.restart_container(project_id)
    await call.answer("🔄 Перезапущен" if ok else f"❌ {msg}")
    await call.message.edit_reply_markup(reply_markup=bot_manage_keyboard(project_id))


@dp.callback_query(F.data.startswith("logs:"))
async def cb_logs(call: CallbackQuery):
    project_id = call.data.split(":", 1)[1]
    logs = pm.get_logs(project_id, lines=30)
    # Telegram ограничивает сообщение 4096 символами
    logs_trimmed = logs[-3500:] if len(logs) > 3500 else logs
    await call.message.answer(
        f"📜 <b>Логи [{project_id}]</b>\n\n<pre>{logs_trimmed}</pre>"
    )
    await call.answer()


@dp.callback_query(F.data.startswith("delete:"))
async def cb_delete(call: CallbackQuery):
    project_id = call.data.split(":", 1)[1]
    bot_row = db.get_bot(project_id)
    if not bot_row or bot_row["user_id"] != call.from_user.id:
        await call.answer("⛔ Нет доступа")
        return

    # Останавливаем и удаляем контейнер
    pm.remove_container(project_id)

    # Удаляем файлы проекта
    project_path = os.path.abspath(f"projects/{call.from_user.id}/{project_id}")
    dep.cleanup_project(project_path)

    # Удаляем из БД
    db.delete_bot(project_id)

    await call.answer("🗑 Удалён")
    await call.message.edit_text(f"🗑 Бот <code>{project_id}</code> удалён.")


def _check_owner(call: CallbackQuery, project_id: str) -> bool:
    """Проверяет, что обращающийся пользователь — владелец бота."""
    row = db.get_bot(project_id)
    return row is not None and row["user_id"] == call.from_user.id


# ── Запуск ────────────────────────────────────────────────────────────────────

async def main():
    # Создаём папки при старте
    os.makedirs("projects", exist_ok=True)
    os.makedirs("logs", exist_ok=True)

    # Инициализируем БД
    db.init_db()

    logger.info("BotHost запускается...")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
