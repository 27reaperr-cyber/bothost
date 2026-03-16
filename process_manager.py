"""
process_manager.py — управление Docker-контейнерами ботов.
Все команды выполняются через subprocess без shell=True (защита от инъекций).
"""

import subprocess
import logging
import os
import psutil

logger = logging.getLogger(__name__)

# Базовый образ для всех деплоев
BASE_IMAGE = "python:3.10-slim"

# Лимиты ресурсов на контейнер
MEMORY_LIMIT = "512m"
CPU_LIMIT = "0.5"


def _run(cmd: list[str], cwd: str | None = None, capture: bool = True) -> tuple[int, str, str]:
    """
    Безопасный запуск команды без shell.
    Возвращает (returncode, stdout, stderr).
    """
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=capture,
            text=True,
            timeout=300
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        logger.error("Команда превысила таймаут: %s", cmd)
        return 1, "", "Timeout"
    except Exception as e:
        logger.error("Ошибка при запуске команды %s: %s", cmd, e)
        return 1, "", str(e)


def build_image(project_path: str, project_id: str) -> tuple[bool, str]:
    """Собирает Docker-образ для проекта."""
    image_tag = f"bothost_{project_id}"
    code, out, err = _run(
        ["docker", "build", "-t", image_tag, "."],
        cwd=project_path
    )
    if code != 0:
        logger.error("docker build failed: %s", err)
        return False, err
    return True, image_tag


def start_container(project_id: str, project_path: str, entry_file: str) -> tuple[bool, str]:
    """
    Запускает Docker-контейнер для бота.
    Монтирует папку проекта и передаёт .env файл.
    """
    container_name = f"bot_{project_id}"
    image_tag = f"bothost_{project_id}"
    env_file = os.path.join(project_path, ".env")

    cmd = [
        "docker", "run", "-d",
        "--name", container_name,
        "--memory", MEMORY_LIMIT,
        "--cpus", CPU_LIMIT,
        "--restart", "unless-stopped",
        "-v", f"{project_path}:/app",
        "-w", "/app",
    ]

    # Добавляем env-file только если файл существует
    if os.path.exists(env_file):
        cmd += ["--env-file", env_file]

    cmd += [image_tag, "python", entry_file]

    code, out, err = _run(cmd)
    if code != 0:
        logger.error("docker run failed: %s", err)
        return False, err
    return True, out  # out — container ID


def stop_container(project_id: str) -> tuple[bool, str]:
    container_name = f"bot_{project_id}"
    code, out, err = _run(["docker", "stop", container_name])
    if code != 0:
        return False, err
    return True, "Остановлен"


def restart_container(project_id: str) -> tuple[bool, str]:
    container_name = f"bot_{project_id}"
    code, out, err = _run(["docker", "restart", container_name])
    if code != 0:
        return False, err
    return True, "Перезапущен"


def remove_container(project_id: str) -> tuple[bool, str]:
    """Останавливает и удаляет контейнер + образ."""
    container_name = f"bot_{project_id}"
    image_tag = f"bothost_{project_id}"
    _run(["docker", "stop", container_name])
    _run(["docker", "rm", container_name])
    _run(["docker", "rmi", "-f", image_tag])
    return True, "Удалён"


def get_logs(project_id: str, lines: int = 30) -> str:
    container_name = f"bot_{project_id}"
    code, out, err = _run(["docker", "logs", "--tail", str(lines), container_name])
    if code != 0:
        return f"Ошибка получения логов: {err}"
    return out or "(логи пусты)"


def container_status(project_id: str) -> str:
    """Возвращает статус контейнера: running / exited / не найден."""
    container_name = f"bot_{project_id}"
    code, out, err = _run([
        "docker", "inspect", "--format", "{{.State.Status}}", container_name
    ])
    if code != 0:
        return "not found"
    return out.strip()


def count_running_containers() -> int:
    """Количество запущенных bothost-контейнеров."""
    code, out, err = _run([
        "docker", "ps", "--filter", "name=bot_", "--format", "{{.Names}}"
    ])
    if code != 0 or not out:
        return 0
    return len([l for l in out.splitlines() if l.strip()])


def get_server_stats() -> dict:
    """Возвращает статистику сервера через psutil."""
    return {
        "cpu": psutil.cpu_percent(interval=1),
        "ram_used": round(psutil.virtual_memory().used / 1024 / 1024),
        "ram_total": round(psutil.virtual_memory().total / 1024 / 1024),
        "ram_percent": psutil.virtual_memory().percent,
        "disk_used": round(psutil.disk_usage("/").used / 1024 / 1024 / 1024, 1),
        "disk_total": round(psutil.disk_usage("/").total / 1024 / 1024 / 1024, 1),
        "disk_percent": psutil.disk_usage("/").percent,
        "containers": count_running_containers(),
    }
