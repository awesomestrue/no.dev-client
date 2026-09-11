"""
autostart.py
============
Настройка автозапуска приложения при старте системы.

Важно: обычный автозапуск через реестр Run НЕ подходит для этого
приложения, т.к. приложению требуются права администратора (для
winws.exe/WinDivert), а ключ HKCU\\...\\Run запускает программы без
elevation — при старте системы просто снова появится диалог UAC либо
приложение не сможет поднять WinDivert.

Поэтому автозапуск реализован через Планировщик заданий Windows
(schtasks) с параметром "запускать с наивысшими правами" — это
штатный способ автоматического запуска program с правами администратора
без ручного подтверждения UAC при каждом входе в систему.
"""

from __future__ import annotations

import subprocess
import sys

TASK_NAME = "no.dev client"


def _get_launch_command() -> str:
    """Формирует команду запуска: pythonw.exe (без консоли) + путь к main.py, либо exe при сборке."""
    import os

    main_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    else:
        # pythonw.exe вместо python.exe, чтобы не мелькало консольное окно
        python_exe = sys.executable
        pythonw = python_exe.replace("python.exe", "pythonw.exe")
        return f'"{pythonw}" "{main_script}"'


def set_autostart(enabled: bool) -> None:
    """Включает/выключает автозапуск через Планировщик заданий Windows."""
    if enabled:
        command = _get_launch_command()
        # /RL HIGHEST — запуск с наивысшими правами (аналог "запускать от администратора")
        # /SC ONLOGON — запуск при входе пользователя в систему
        subprocess.run(
            [
                "schtasks", "/Create", "/TN", TASK_NAME,
                "/TR", command,
                "/SC", "ONLOGON",
                "/RL", "HIGHEST",
                "/F",  # перезаписать задачу, если уже существует
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    else:
        subprocess.run(
            ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
            capture_output=True,
            text=True,
        )


def is_autostart_enabled() -> bool:
    """Проверяет, существует ли задача автозапуска в Планировщике."""
    result = subprocess.run(
        ["schtasks", "/Query", "/TN", TASK_NAME],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0
