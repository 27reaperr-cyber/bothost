"""
process_manager.py — гибридный менеджер процессов.

При старте автоматически определяет, доступен ли Docker:
  • DOCKER_AVAILABLE = True  → запускает ботов в изолированных контейнерах
  • DOCKER_AVAILABLE = False → запускает ботов напрямую через subprocess + venv

Публичный API одинаков в обоих режимах:
    build_image / start_bot / stop_bot / restart_bot / remove_bot
    bot_status  / get_logs  / get_server_stats
"""

import os
import sys
import signal
import subprocess
import logging
import psutil

logger = logging.getLogger(__name__)

LOGS_DIR = "logs"
MEMORY_LIMIT = "512m"
CPU_LIMIT = "0.5"


# ══════════════════════════════════════════════════════════════════════════════
# Определение режима (один раз при импорте)
# ══════════════════════════════════════════════════════════════════════════════

def _check_docker() -> bool:
    """
    Три условия для Docker:
    1. Команда docker есть в PATH
    2. Сокет /var/run/docker.sock присутствует
    3. `docker info` выполняется успешно
    """
    socket_path = "/var/run/docker.sock"
    if not os.path.exists(socket_path):
        logger.warning("Docker сокет не найден: %s", socket_path)
        return False
    try:
        r = subprocess.run(
            ["docker", "info"],
            capture_output=True, text=True, timeout=5
        )
        if r.returncode != 0:
            logger.warning("docker info вернул ошибку: %s", r.stderr[:200])
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        logger.warning("Docker недоступен: %s", e)
        return False


DOCKER_AVAILABLE: bool = _check_docker()

logger.info(
    "Режим запуска ботов: %s",
    "Docker 🐳" if DOCKER_AVAILABLE else "Прямой процесс ⚙️"
)


# ══════════════════════════════════════════════════════════════════════════════
# Общие утилиты
# ══════════════════════════════════════════════════════════════════════════════

def _run(cmd: list[str], cwd: str | None = None, timeout: int = 300) -> tuple[int, str, str]:
    """Безопасный запуск команды без shell."""
    try:
        r = subprocess.run(
            cmd, cwd=cwd,
            capture_output=True, text=True, timeout=timeout
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return 1, "", f"Таймаут {timeout} сек."
    except FileNotFoundError as e:
        return 1, "", f"Команда не найдена: {e}"
    except Exception as e:
        return 1, "", str(e)


def _pid_file(project_path: str) -> str:
    return os.path.join(project_path, ".pid")


def _log_file(project_id: str) -> str:
    os.makedirs(LOGS_DIR, exist_ok=True)
    return os.path.abspath(os.path.join(LOGS_DIR, f"{project_id}.log"))


def _read_pid(project_path: str) -> int | None:
    """Читает живой PID из файла. None — если файла нет или процесс мёртв."""
    pid_path = _pid_file(project_path)
    if not os.path.exists(pid_path):
        return None
    try:
        pid = int(open(pid_path).read().strip())
        if psutil.pid_exists(pid):
            return pid
        os.remove(pid_path)
    except (ValueError, OSError):
        pass
    return None


def _write_pid(project_path: str, pid: int):
    with open(_pid_file(project_path), "w") as f:
        f.write(str(pid))


def _load_env(project_path: str) -> dict[str, str]:
    """Читает .env файл и мержит с окружением процесса."""
    env = os.environ.copy()
    env_file = os.path.join(project_path, ".env")
    if not os.path.exists(env_file):
        return env
    for line in open(env_file, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        env[key.strip()] = val.strip()
    return env


def _venv_python(project_path: str) -> str:
    """Путь к python: venv проекта или текущий интерпретатор."""
    candidate = os.path.join(project_path, ".venv", "bin", "python")
    return candidate if os.path.exists(candidate) else sys.executable


# ══════════════════════════════════════════════════════════════════════════════
# Прямой режим (без Docker)
# ══════════════════════════════════════════════════════════════════════════════

def _direct_setup_venv(project_path: str) -> tuple[bool, str]:
    """Создаёт venv и устанавливает зависимости из requirements.txt."""
    venv_path = os.path.join(project_path, ".venv")
    req = os.path.join(project_path, "requirements.txt")

    if not os.path.exists(venv_path):
        code, _, err = _run([sys.executable, "-m", "venv", venv_path])
        if code != 0:
            return False, f"Ошибка создания venv: {err}"

    pip = os.path.join(venv_path, "bin", "pip")

    if os.path.exists(req):
        code, _, err = _run(
            [pip, "install", "--no-cache-dir", "--timeout", "30", "-r", req],
            timeout=180,
        )
        if code != 0:
            return False, f"Ошибка pip install: {err}"

    return True, "venv готов"


async def async_setup_venv(
    project_path: str,
    on_output,          # async callable(str) — вызывается при каждом обновлении
) -> tuple[bool, str]:
    """
    Асинхронная установка venv + pip с живым прогрессом.

    Две параллельные задачи:
      • reader  — читает stdout pip мелкими чанками (обходит буферизацию)
      • watchdog — каждые 3 сек отправляет heartbeat даже если pip молчит

    Общий таймаут: 180 сек. При превышении — процесс убивается.
    """
    import asyncio
    import time

    venv_path = os.path.join(project_path, ".venv")
    req       = os.path.join(project_path, "requirements.txt")

    # ── Шаг 1: создать venv (быстро, буферизация не мешает) ──────────────────
    if not os.path.exists(venv_path):
        await on_output("📦 Создание виртуального окружения...\n")
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "venv", venv_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
        except asyncio.TimeoutError:
            proc.kill()
            return False, "Создание venv зависло (таймаут 60 сек)"
        out = stdout.decode(errors="replace")
        if out.strip():
            await on_output(out)
        if proc.returncode != 0:
            return False, f"Ошибка venv:\n{out}"
        await on_output("✅ venv создан\n")

    pip = os.path.join(venv_path, "bin", "pip")

    # ── Шаг 2: pip install ────────────────────────────────────────────────────
    if not os.path.exists(req):
        await on_output("⚠️ requirements.txt не найден — пропускаем pip install\n")
        return True, "venv готов (без зависимостей)"

    await on_output("📥 Запускаю pip install...\n")

    # PYTHONUNBUFFERED=1 — форсируем сброс буфера у дочернего python/pip
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PIP_NO_COLOR"] = "1"

    proc = await asyncio.create_subprocess_exec(
        pip, "install",
        "--no-cache-dir",
        "--timeout", "60",        # таймаут соединения с PyPI
        "--progress-bar", "off",
        "-r", req,
        cwd=project_path,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    output_buf: list[str] = []
    start_time = time.monotonic()
    TOTAL_TIMEOUT = 180  # секунд до принудительного kill

    # ── reader: читает чанками по 256 байт, не ждёт newline ──────────────────
    async def reader():
        while True:
            try:
                chunk = await asyncio.wait_for(
                    proc.stdout.read(256), timeout=2.0
                )
            except asyncio.TimeoutError:
                # pip молчит — просто продолжаем (watchdog покажет таймер)
                continue
            if not chunk:
                break
            output_buf.append(chunk.decode(errors="replace"))

    # ── watchdog: heartbeat каждые 3 сек + убивает при таймауте ──────────────
    async def watchdog():
        while proc.returncode is None:
            await asyncio.sleep(3)
            elapsed = int(time.monotonic() - start_time)
            tail = "".join(output_buf)[-900:]  # последние ~900 символов
            await on_output(f"\n⏱ {elapsed} сек...\n{tail}")
            if elapsed >= TOTAL_TIMEOUT:
                logger.warning("pip install таймаут (%d сек), убиваем процесс", elapsed)
                proc.kill()
                return

    # Запускаем обе задачи параллельно
    reader_task   = asyncio.create_task(reader())
    watchdog_task = asyncio.create_task(watchdog())

    # Ждём завершения процесса
    await proc.wait()

    # Останавливаем задачи
    reader_task.cancel()
    watchdog_task.cancel()
    try:
        await reader_task
    except asyncio.CancelledError:
        pass
    try:
        await watchdog_task
    except asyncio.CancelledError:
        pass

    elapsed = int(time.monotonic() - start_time)
    tail    = "".join(output_buf)[-900:]

    if proc.returncode != 0:
        await on_output(f"\n❌ pip завершился с ошибкой (код {proc.returncode})\n")
        return False, tail or f"pip ошибка (код {proc.returncode})"

    await on_output(f"\n✅ pip install завершён за {elapsed} сек\n")
    return True, "venv готов"


def _direct_start(project_id: str, project_path: str, entry_file: str) -> tuple[bool, str]:
    """Запускает бота как фоновый процесс (новая сессия, лог в файл)."""
    _direct_stop(project_id, project_path)  # убиваем старый если есть

    ok, err = _direct_setup_venv(project_path)
    if not ok:
        return False, err

    python_bin = _venv_python(project_path)
    env = _load_env(project_path)
    log_path = _log_file(project_id)

    try:
        log_fd = open(log_path, "a", encoding="utf-8")
        proc = subprocess.Popen(
            [python_bin, entry_file],
            cwd=project_path,
            env=env,
            stdout=log_fd,
            stderr=log_fd,
            start_new_session=True,  # отвязываем от родителя
        )
        log_fd.close()
        _write_pid(project_path, proc.pid)
        logger.info("Бот %s запущен напрямую, PID=%d", project_id, proc.pid)
        return True, f"PID {proc.pid}"
    except Exception as e:
        logger.error("Не удалось запустить бота %s: %s", project_id, e)
        return False, str(e)


def _direct_stop(project_id: str, project_path: str) -> tuple[bool, str]:
    """Завершает фоновый процесс по PID."""
    pid = _read_pid(project_path)
    if pid is None:
        return True, "Уже остановлен"
    try:
        pgid = os.getpgid(pid)
        os.killpg(pgid, signal.SIGTERM)
        try:
            psutil.Process(pid).wait(timeout=5)
        except psutil.TimeoutExpired:
            os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, psutil.NoSuchProcess):
        pass
    except Exception as e:
        return False, str(e)
    finally:
        pf = _pid_file(project_path)
        if os.path.exists(pf):
            os.remove(pf)
    return True, "Остановлен"


def _direct_restart(project_id: str, project_path: str, entry_file: str) -> tuple[bool, str]:
    _direct_stop(project_id, project_path)
    return _direct_start(project_id, project_path, entry_file)


def _direct_status(project_path: str) -> str:
    pid = _read_pid(project_path)
    if pid is None:
        return "stopped"
    try:
        s = psutil.Process(pid).status()
        return "running" if s != psutil.STATUS_ZOMBIE else "stopped"
    except psutil.NoSuchProcess:
        return "stopped"


def _direct_logs(project_id: str, lines: int) -> str:
    log_path = _log_file(project_id)
    if not os.path.exists(log_path):
        return "(лог-файл пока не создан)"
    try:
        all_lines = open(log_path, encoding="utf-8", errors="replace").readlines()
        return "".join(all_lines[-lines:]) or "(логи пусты)"
    except Exception as e:
        return f"Ошибка чтения логов: {e}"


def _direct_remove(project_id: str, project_path: str):
    _direct_stop(project_id, project_path)
    lf = _log_file(project_id)
    if os.path.exists(lf):
        os.remove(lf)


# ══════════════════════════════════════════════════════════════════════════════
# Docker режим
# ══════════════════════════════════════════════════════════════════════════════

def _docker_build(project_path: str, project_id: str) -> tuple[bool, str]:
    code, _, err = _run(
        ["docker", "build", "-t", f"bothost_{project_id}", "."],
        cwd=project_path, timeout=600
    )
    return (True, f"bothost_{project_id}") if code == 0 else (False, err)


def _docker_start(project_id: str, project_path: str, entry_file: str) -> tuple[bool, str]:
    name = f"bot_{project_id}"
    _run(["docker", "rm", "-f", name])  # чистим старый

    cmd = [
        "docker", "run", "-d",
        "--name", name,
        "--memory", MEMORY_LIMIT,
        "--cpus", CPU_LIMIT,
        "--restart", "unless-stopped",
    ]
    env_file = os.path.join(project_path, ".env")
    if os.path.exists(env_file):
        cmd += ["--env-file", env_file]
    cmd += [f"bothost_{project_id}", "python", entry_file]

    code, out, err = _run(cmd)
    return (True, out) if code == 0 else (False, err)


def _docker_stop(project_id: str) -> tuple[bool, str]:
    code, _, err = _run(["docker", "stop", f"bot_{project_id}"])
    return (True, "Остановлен") if code == 0 else (False, err)


def _docker_restart(project_id: str) -> tuple[bool, str]:
    code, _, err = _run(["docker", "restart", f"bot_{project_id}"])
    return (True, "Перезапущен") if code == 0 else (False, err)


def _docker_status(project_id: str) -> str:
    code, out, _ = _run([
        "docker", "inspect",
        "--format", "{{.State.Status}}",
        f"bot_{project_id}"
    ])
    return out.strip() if code == 0 else "not found"


def _docker_logs(project_id: str, lines: int) -> str:
    code, out, err = _run(["docker", "logs", "--tail", str(lines), f"bot_{project_id}"])
    return out if code == 0 else f"Ошибка: {err}"


def _docker_remove(project_id: str):
    _run(["docker", "stop", f"bot_{project_id}"])
    _run(["docker", "rm",   f"bot_{project_id}"])
    _run(["docker", "rmi", "-f", f"bothost_{project_id}"])


def _docker_count() -> int:
    code, out, _ = _run([
        "docker", "ps", "--filter", "name=bot_", "--format", "{{.Names}}"
    ])
    if code != 0 or not out:
        return 0
    return len([l for l in out.splitlines() if l.strip()])


# ══════════════════════════════════════════════════════════════════════════════
# Публичный API — одинаковый интерфейс для обоих режимов
# ══════════════════════════════════════════════════════════════════════════════

def build_image(project_path: str, project_id: str) -> tuple[bool, str]:
    """Docker: собирает образ. Direct: создаёт venv + pip install."""
    if DOCKER_AVAILABLE:
        return _docker_build(project_path, project_id)
    return _direct_setup_venv(project_path)


def start_bot(project_id: str, project_path: str, entry_file: str) -> tuple[bool, str]:
    if DOCKER_AVAILABLE:
        return _docker_start(project_id, project_path, entry_file)
    return _direct_start(project_id, project_path, entry_file)


def stop_bot(project_id: str, project_path: str = "") -> tuple[bool, str]:
    if DOCKER_AVAILABLE:
        return _docker_stop(project_id)
    return _direct_stop(project_id, project_path)


def restart_bot(project_id: str, project_path: str, entry_file: str) -> tuple[bool, str]:
    if DOCKER_AVAILABLE:
        return _docker_restart(project_id)
    return _direct_restart(project_id, project_path, entry_file)


def remove_bot(project_id: str, project_path: str) -> tuple[bool, str]:
    if DOCKER_AVAILABLE:
        _docker_remove(project_id)
    else:
        _direct_remove(project_id, project_path)
    return True, "Удалён"


def bot_status(project_id: str, project_path: str = "") -> str:
    if DOCKER_AVAILABLE:
        return _docker_status(project_id)
    return _direct_status(project_path)


def get_logs(project_id: str, project_path: str = "", lines: int = 30) -> str:
    if DOCKER_AVAILABLE:
        return _docker_logs(project_id, lines)
    return _direct_logs(project_id, lines)


def get_runtime_mode() -> str:
    return "🐳 Docker" if DOCKER_AVAILABLE else "⚙️ Прямой запуск"


def count_running() -> int:
    """Количество активных ботов."""
    if DOCKER_AVAILABLE:
        return _docker_count()
    count = 0
    if os.path.exists("projects"):
        for user_dir in os.scandir("projects"):
            if not user_dir.is_dir():
                continue
            for proj_dir in os.scandir(user_dir.path):
                if proj_dir.is_dir() and _read_pid(proj_dir.path) is not None:
                    count += 1
    return count


def get_server_stats() -> dict:
    """Статистика сервера через psutil."""
    return {
        "cpu": psutil.cpu_percent(interval=1),
        "ram_used":    round(psutil.virtual_memory().used  / 1024 / 1024),
        "ram_total":   round(psutil.virtual_memory().total / 1024 / 1024),
        "ram_percent": psutil.virtual_memory().percent,
        "disk_used":   round(psutil.disk_usage("/").used  / 1024 / 1024 / 1024, 1),
        "disk_total":  round(psutil.disk_usage("/").total / 1024 / 1024 / 1024, 1),
        "disk_percent": psutil.disk_usage("/").percent,
        "processes": count_running(),
        "mode": get_runtime_mode(),
    }
