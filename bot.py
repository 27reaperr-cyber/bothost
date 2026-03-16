"""
bot.py — главный файл Telegram-бота BotHost.
Управляет деплоем, мониторингом и жизненным циклом ботов.
Работает в двух режимах: Docker (если доступен) или прямой запуск процессов.
Запуск: python bot.py
"""

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
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
            InlineKeyboardButton(text="▶ Запустить",      callback_data=f"start:{project_id}"),
            InlineKeyboardButton(text="⏹ Остановить",    callback_data=f"stop:{project_id}"),
        ],
        [
            InlineKeyboardButton(text="🔄 Перезапустить", callback_data=f"restart:{project_id}"),
            InlineKeyboardButton(text="📜 Логи",           callback_data=f"logs:{project_id}"),
        ],
        [
            InlineKeyboardButton(text="🗑 Удалить",        callback_data=f"delete:{project_id}"),
        ],
    ])


# ── Bot & Dispatcher ──────────────────────────────────────────────────────────

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())


# ── Вспомогательная функция: путь к проекту ──────────────────────────────────

def _project_path(user_id: int, project_id: str) -> str:
    return os.path.abspath(os.path.join("projects", str(user_id), project_id))


# ── /start ────────────────────────────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    db.upsert_user(message.from_user.id, message.from_user.username or "")
    await message.answer(
        "👋 Добро пожаловать в <b>BotHost</b>!\n\n"
        "Деплойте Telegram-ботов прямо с GitHub/GitLab.\n\n"
        f"Режим запуска: <b>{pm.get_runtime_mode()}</b>",
        reply_markup=main_keyboard(),
    )


# ── Отмена ────────────────────────────────────────────────────────────────────

@dp.message(F.text == "❌ Отмена")
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=main_keyboard())


# ── Помощь ────────────────────────────────────────────────────────────────────

@dp.message(F.text == "ℹ️ Помощь")
async def cmd_help(message: Message):
    await message.answer(
        "<b>ℹ️ Как использовать BotHost</b>\n\n"
        "1️⃣ Нажмите <b>🚀 Deploy бот</b>\n"
        "2️⃣ Отправьте ссылку GitHub/GitLab\n"
        "3️⃣ Задайте переменные окружения (или <code>done</code>)\n"
        "4️⃣ Бот будет запущен автоматически!\n\n"
        "<b>Требования к репозиторию:</b>\n"
        "• Python-проект (bot.py / main.py / app.py)\n"
        "• Один из: aiogram, python-telegram-bot, telebot\n\n"
        f"<b>Лимиты:</b> до {MAX_BOTS_PER_USER} ботов на аккаунт\n"
        f"<b>Режим:</b> {pm.get_runtime_mode()}",
        reply_markup=main_keyboard(),
    )


# ── Статус сервера ────────────────────────────────────────────────────────────

@dp.message(F.text == "📊 Статус сервера")
async def cmd_server_status(message: Message):
    stats = pm.get_server_stats()
    await message.answer(
        "📊 <b>Статус сервера</b>\n\n"
        f"🖥 CPU:       <b>{stats['cpu']}%</b>\n"
        f"🧠 RAM:       <b>{stats['ram_used']} / {stats['ram_total']} МБ</b> ({stats['ram_percent']}%)\n"
        f"💾 Диск:      <b>{stats['disk_used']} / {stats['disk_total']} ГБ</b> ({stats['disk_percent']}%)\n"
        f"🤖 Ботов:     <b>{stats['processes']}</b>\n"
        f"⚙️ Режим:     <b>{stats['mode']}</b>",
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
        ppath = _project_path(message.from_user.id, pid)
        status = pm.bot_status(pid, ppath)
        emoji = "🟢" if status == "running" else "🔴"
        repo_short = row["repo_url"].replace("https://", "").rstrip("/")

        await message.answer(
            f"{emoji} <b>{pid}</b>\n"
            f"📁 {repo_short}\n"
            f"📌 Статус: <code>{status}</code>\n"
            f"🔧 Точка входа: <code>{row['entry_file']}</code>",
            reply_markup=bot_manage_keyboard(pid),
        )


# ── Deploy: Шаг 1 — запрос URL ────────────────────────────────────────────────

@dp.message(F.text == "🚀 Deploy бот")
async def deploy_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    db.upsert_user(user_id, message.from_user.username or "")

    if db.count_user_bots(user_id) >= MAX_BOTS_PER_USER:
        await message.answer(
            f"❌ Лимит: максимум {MAX_BOTS_PER_USER} бота на аккаунт.\n"
            "Удалите один из существующих ботов.",
            reply_markup=main_keyboard(),
        )
        return

    await state.set_state(DeployStates.waiting_url)
    await message.answer(
        "🔗 <b>Шаг 1/2 — Репозиторий</b>\n\n"
        "Отправьте ссылку на GitHub или GitLab:\n\n"
        "<code>https://github.com/user/my-bot</code>",
        reply_markup=cancel_keyboard(),
    )


# ── Deploy: Шаг 2 — клонирование и валидация ─────────────────────────────────

@dp.message(DeployStates.waiting_url)
async def deploy_got_url(message: Message, state: FSMContext):
    repo_url = message.text.strip()

    ok, url_or_err = dep.validate_git_url(repo_url)
    if not ok:
        await message.answer(url_or_err)
        return

    progress = await message.answer("⏳ Клонирование репозитория...")

    ok, msg, info = await asyncio.get_event_loop().run_in_executor(
        None, dep.prepare_project, message.from_user.id, url_or_err
    )
    if not ok:
        await progress.edit_text(msg)
        return

    await state.update_data(
        repo_url=url_or_err,
        project_id=info["project_id"],
        project_path=info["project_path"],
        entry_file=info["entry_file"],
    )

    await progress.edit_text(
        f"✅ Репозиторий клонирован!\n\n"
        f"🆔 Project ID: <code>{info['project_id']}</code>\n"
        f"🔧 Точка входа: <code>{info['entry_file']}</code>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📝 <b>Шаг 2/2 — Переменные окружения</b>\n\n"
        "Отправьте переменные:\n"
        "<code>BOT_TOKEN=123456:ABC\nAPI_KEY=secret</code>\n\n"
        "Если ENV не нужны — напишите <code>done</code>"
    )
    await state.set_state(DeployStates.waiting_env)


# ── Deploy: Шаг 3 — ENV и финальный запуск ────────────────────────────────────

@dp.message(DeployStates.waiting_env)
async def deploy_got_env(message: Message, state: FSMContext):
    text = message.text.strip()
    data = await state.get_data()
    project_id   = data["project_id"]
    project_path = data["project_path"]
    entry_file   = data["entry_file"]
    repo_url     = data["repo_url"]
    user_id      = message.from_user.id

    # Накапливаем ENV пока не "done"
    if text.lower() != "done":
        ok, err = dep.save_env(project_path, text)
        if not ok:
            await message.answer(err)
            return
        await message.answer(
            "✅ Переменные сохранены. "
            "Добавьте ещё или напишите <code>done</code>."
        )
        return

    # ── Финальный запуск ──────────────────────────────────────────────────────
    mode_label = "Docker-образа" if pm.DOCKER_AVAILABLE else "виртуального окружения"
    progress = await message.answer(f"⚙️ Подготовка {mode_label}...")

    # build_image: в Docker режиме — docker build; в прямом — pip install в venv
    ok, result = await asyncio.get_event_loop().run_in_executor(
        None, pm.build_image, project_path, project_id
    )
    if not ok:
        await progress.edit_text(
            f"❌ Ошибка подготовки окружения:\n<code>{result}</code>"
        )
        dep.cleanup_project(project_path)
        await state.clear()
        return

    await progress.edit_text("🚀 Запуск бота...")

    ok, result = await asyncio.get_event_loop().run_in_executor(
        None, pm.start_bot, project_id, project_path, entry_file
    )
    if not ok:
        await progress.edit_text(
            f"❌ Ошибка запуска:\n<code>{result}</code>"
        )
        dep.cleanup_project(project_path)
        await state.clear()
        return

    db.add_bot(project_id, user_id, repo_url, entry_file)
    await state.clear()

    mode_info = (
        f"🐳 Контейнер: <code>bot_{project_id}</code>"
        if pm.DOCKER_AVAILABLE else
        f"⚙️ Процесс запущен ({result})"
    )

    await progress.edit_text(
        "🎉 <b>Бот успешно задеплоен!</b>\n\n"
        f"🆔 Project ID: <code>{project_id}</code>\n"
        f"🔧 Точка входа: <code>{entry_file}</code>\n"
        f"{mode_info}\n\n"
        "Управляйте через <b>📦 Мои боты</b>",
        reply_markup=main_keyboard(),
    )


# ── Callback-обработчики управления ботом ─────────────────────────────────────

def _get_bot_path(call: CallbackQuery, project_id: str) -> str:
    """Возвращает путь к проекту, если пользователь — владелец."""
    row = db.get_bot(project_id)
    if row and row["user_id"] == call.from_user.id:
        return _project_path(call.from_user.id, project_id)
    return ""


@dp.callback_query(F.data.startswith("start:"))
async def cb_start(call: CallbackQuery):
    project_id = call.data.split(":", 1)[1]
    ppath = _get_bot_path(call, project_id)
    if not ppath:
        await call.answer("⛔ Нет доступа")
        return
    row = db.get_bot(project_id)
    ok, msg = await asyncio.get_event_loop().run_in_executor(
        None, pm.restart_bot, project_id, ppath, row["entry_file"] if row else "bot.py"
    )
    db.update_bot_status(project_id, "running" if ok else "error")
    await call.answer("▶ Запущен" if ok else f"❌ {msg[:50]}")
    await call.message.edit_reply_markup(reply_markup=bot_manage_keyboard(project_id))


@dp.callback_query(F.data.startswith("stop:"))
async def cb_stop(call: CallbackQuery):
    project_id = call.data.split(":", 1)[1]
    ppath = _get_bot_path(call, project_id)
    if not ppath:
        await call.answer("⛔ Нет доступа")
        return
    ok, msg = await asyncio.get_event_loop().run_in_executor(
        None, pm.stop_bot, project_id, ppath
    )
    db.update_bot_status(project_id, "stopped" if ok else "error")
    await call.answer("⏹ Остановлен" if ok else f"❌ {msg[:50]}")
    await call.message.edit_reply_markup(reply_markup=bot_manage_keyboard(project_id))


@dp.callback_query(F.data.startswith("restart:"))
async def cb_restart(call: CallbackQuery):
    project_id = call.data.split(":", 1)[1]
    ppath = _get_bot_path(call, project_id)
    if not ppath:
        await call.answer("⛔ Нет доступа")
        return
    row = db.get_bot(project_id)
    ok, msg = await asyncio.get_event_loop().run_in_executor(
        None, pm.restart_bot, project_id, ppath, row["entry_file"] if row else "bot.py"
    )
    await call.answer("🔄 Перезапущен" if ok else f"❌ {msg[:50]}")
    await call.message.edit_reply_markup(reply_markup=bot_manage_keyboard(project_id))


@dp.callback_query(F.data.startswith("logs:"))
async def cb_logs(call: CallbackQuery):
    project_id = call.data.split(":", 1)[1]
    ppath = _get_bot_path(call, project_id)
    if not ppath:
        await call.answer("⛔ Нет доступа")
        return
    logs = pm.get_logs(project_id, ppath, lines=30)
    trimmed = logs[-3500:] if len(logs) > 3500 else logs
    await call.message.answer(
        f"📜 <b>Логи [{project_id}]</b>\n\n<pre>{trimmed}</pre>"
    )
    await call.answer()


@dp.callback_query(F.data.startswith("delete:"))
async def cb_delete(call: CallbackQuery):
    project_id = call.data.split(":", 1)[1]
    ppath = _get_bot_path(call, project_id)
    if not ppath:
        await call.answer("⛔ Нет доступа")
        return

    await asyncio.get_event_loop().run_in_executor(
        None, pm.remove_bot, project_id, ppath
    )
    dep.cleanup_project(ppath)
    db.delete_bot(project_id)

    await call.answer("🗑 Удалён")
    await call.message.edit_text(f"🗑 Бот <code>{project_id}</code> удалён.")


# ── Запуск ────────────────────────────────────────────────────────────────────

async def main():
    os.makedirs("projects", exist_ok=True)
    os.makedirs("logs", exist_ok=True)
    db.init_db()

    logger.info("BotHost запускается | Режим: %s", pm.get_runtime_mode())
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
