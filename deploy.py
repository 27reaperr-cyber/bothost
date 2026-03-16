"""
deploy.py — логика деплоя: клонирование, валидация, сборка, запуск.
Все шаги выполняются последовательно; при ошибке возвращается описание.
"""

import os
import re
import uuid
import shutil
import subprocess
import logging

logger = logging.getLogger(__name__)

# Корневая папка для всех проектов
PROJECTS_ROOT = "projects"

# Разрешённые Telegram-фреймворки
TELEGRAM_LIBS = {"aiogram", "python-telegram-bot", "pytelegrambotapi", "telebot"}

# Паттерн для импортов в .py файлах
IMPORT_PATTERNS = [
    re.compile(r"^\s*import\s+(aiogram|telebot)", re.MULTILINE),
    re.compile(r"^\s*from\s+(aiogram|telegram|telebot)\s+import", re.MULTILINE),
]

# Допустимые точки входа (по приоритету)
ENTRY_FILES = ["bot.py", "main.py", "app.py"]

# Валидация Git URL: только github.com / gitlab.com, без shell-символов
GIT_URL_RE = re.compile(
    r"^https://(github\.com|gitlab\.com)/[\w.\-]+/[\w.\-]+(\.git)?/?$"
)


def _run(cmd: list[str], cwd: str | None = None) -> tuple[int, str, str]:
    """Запускает команду без shell, возвращает (code, stdout, stderr)."""
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=120)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return 1, "", "Команда превысила 120 секунд"
    except Exception as e:
        return 1, "", str(e)


# ── Шаг 2: Валидация URL ──────────────────────────────────────────────────────

def validate_git_url(url: str) -> tuple[bool, str]:
    """Проверяет, что URL — это корректный GitHub/GitLab репозиторий."""
    url = url.strip()
    if not GIT_URL_RE.match(url):
        return False, (
            "❌ Недопустимый URL.\n"
            "Разрешены только ссылки вида:\n"
            "https://github.com/user/repo\n"
            "https://gitlab.com/user/repo"
        )
    return True, url


# ── Шаг 3: Клонирование ───────────────────────────────────────────────────────

def clone_repo(repo_url: str, dest: str) -> tuple[bool, str]:
    """Клонирует репозиторий в указанную папку."""
    os.makedirs(dest, exist_ok=True)
    code, out, err = _run(["git", "clone", "--depth", "1", repo_url, dest])
    if code != 0:
        return False, f"Ошибка клонирования:\n{err}"
    return True, "OK"


# ── Шаг 4: Проверка Python + Telegram ─────────────────────────────────────────

def _is_python_project(path: str) -> bool:
    """Проверяет наличие точки входа или __main__ в любом .py файле."""
    # Сначала ищем стандартные точки входа
    for f in ENTRY_FILES:
        if os.path.exists(os.path.join(path, f)):
            return True
    # Затем ищем __main__ в любом .py
    for root, _, files in os.walk(path):
        for fname in files:
            if fname.endswith(".py"):
                try:
                    content = open(os.path.join(root, fname), encoding="utf-8", errors="ignore").read()
                    if 'if __name__ == "__main__"' in content or "if __name__ == '__main__'" in content:
                        return True
                except Exception:
                    pass
    return False


def _has_telegram_lib(path: str) -> bool:
    """Проверяет наличие Telegram-библиотеки в requirements.txt или импортах."""
    # Проверяем requirements.txt
    req_path = os.path.join(path, "requirements.txt")
    if os.path.exists(req_path):
        try:
            content = open(req_path, encoding="utf-8", errors="ignore").read().lower()
            for lib in TELEGRAM_LIBS:
                if lib in content:
                    return True
        except Exception:
            pass

    # Проверяем импорты во всех .py файлах
    for root, _, files in os.walk(path):
        for fname in files:
            if fname.endswith(".py"):
                try:
                    content = open(os.path.join(root, fname), encoding="utf-8", errors="ignore").read()
                    for pattern in IMPORT_PATTERNS:
                        if pattern.search(content):
                            return True
                except Exception:
                    pass
    return False


def validate_project(path: str) -> tuple[bool, str]:
    """Полная проверка: Python-проект + Telegram-бот."""
    if not _is_python_project(path):
        return False, (
            "❌ Не обнаружен Python-проект.\n"
            "Нужен файл bot.py / main.py / app.py "
            "или любой .py с `if __name__ == '__main__'`."
        )
    if not _has_telegram_lib(path):
        return False, (
            "❌ Telegram-библиотека не найдена.\n"
            "Поддерживаются: aiogram, python-telegram-bot, pyTelegramBotAPI, telebot."
        )
    return True, "OK"


# ── Шаг 6: Установка зависимостей ─────────────────────────────────────────────

def install_requirements(path: str) -> tuple[bool, str]:
    """Устанавливает зависимости из requirements.txt если файл существует."""
    req = os.path.join(path, "requirements.txt")
    if not os.path.exists(req):
        return True, "requirements.txt не найден, пропускаем"
    code, out, err = _run(
        ["pip", "install", "--quiet", "-r", "requirements.txt"],
        cwd=path
    )
    if code != 0:
        return False, f"Ошибка pip install:\n{err}"
    return True, "Зависимости установлены"


# ── Шаг 7: Сохранение .env ────────────────────────────────────────────────────

def save_env(path: str, env_text: str) -> tuple[bool, str]:
    """
    Валидирует и сохраняет переменные окружения.
    Принимает текст вида: KEY=value\nKEY2=value2
    """
    lines = []
    for line in env_text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Разрешаем только KEY=value (без подстановок, кавычек с командами)
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*$", line):
            return False, f"❌ Недопустимая строка ENV: `{line}`\nФормат: KEY=value"
        # Блокируем подстановку команд
        if "$(" in line or "`" in line:
            return False, f"❌ Недопустимые символы в ENV: `{line}`"
        lines.append(line)

    env_file = os.path.join(path, ".env")
    with open(env_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return True, "OK"


# ── Шаг 8: Определение точки входа ───────────────────────────────────────────

def find_entry_file(path: str) -> str | None:
    """Возвращает имя файла точки входа по приоритету."""
    for f in ENTRY_FILES:
        if os.path.exists(os.path.join(path, f)):
            return f
    # Ищем файл с __main__
    for root, _, files in os.walk(path):
        for fname in sorted(files):
            if fname.endswith(".py"):
                try:
                    content = open(os.path.join(root, fname), encoding="utf-8", errors="ignore").read()
                    if 'if __name__ == "__main__"' in content or "if __name__ == '__main__'" in content:
                        return os.path.relpath(os.path.join(root, fname), path)
                except Exception:
                    pass
    return None


# ── Dockerfile генерация ───────────────────────────────────────────────────────

def write_dockerfile(project_path: str, entry_file: str):
    """Записывает Dockerfile в папку проекта."""
    content = f"""FROM python:3.10-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt 2>/dev/null || true
CMD ["python", "{entry_file}"]
"""
    with open(os.path.join(project_path, "Dockerfile"), "w") as f:
        f.write(content)


# ── Главная функция деплоя ────────────────────────────────────────────────────

def prepare_project(user_id: int, repo_url: str) -> tuple[bool, str, dict]:
    """
    Подготавливает проект к деплою: клонирует, валидирует, устанавливает зависимости.
    Возвращает (success, message, info_dict).
    info_dict содержит: project_id, project_path, entry_file
    """
    # Генерируем уникальный ID проекта
    project_id = uuid.uuid4().hex[:8]
    project_path = os.path.abspath(
        os.path.join(PROJECTS_ROOT, str(user_id), project_id)
    )

    try:
        # Шаг 3: клонирование
        ok, msg = clone_repo(repo_url, project_path)
        if not ok:
            return False, msg, {}

        # Шаг 4: валидация
        ok, msg = validate_project(project_path)
        if not ok:
            shutil.rmtree(project_path, ignore_errors=True)
            return False, msg, {}

        # Шаг 8: точка входа
        entry_file = find_entry_file(project_path)
        if not entry_file:
            shutil.rmtree(project_path, ignore_errors=True)
            return False, "❌ Не удалось определить точку входа.", {}

        # Записываем Dockerfile
        write_dockerfile(project_path, entry_file)

        return True, "OK", {
            "project_id": project_id,
            "project_path": project_path,
            "entry_file": entry_file,
        }

    except Exception as e:
        logger.exception("Ошибка в prepare_project")
        shutil.rmtree(project_path, ignore_errors=True)
        return False, f"❌ Внутренняя ошибка: {e}", {}


def cleanup_project(project_path: str):
    """Удаляет папку проекта с диска."""
    shutil.rmtree(project_path, ignore_errors=True)
