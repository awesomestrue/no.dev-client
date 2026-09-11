"""
elevation.py
============
winws.exe использует драйвер WinDivert для перехвата сетевых пакетов —
это требует прав администратора. Без elevation процесс либо не
запустится вовсе, либо запустится без реального перехвата трафика.

Модуль:
 - проверяет, запущено ли текущее приложение с правами администратора;
 - если нет — предлагает перезапустить само GUI-приложение с
   повышенными правами через стандартный Windows UAC-диалог (ShellExecute
   с verb="runas").
"""

from __future__ import annotations

import ctypes
import sys


def is_admin() -> bool:
    """Проверяет, запущен ли текущий процесс с правами администратора."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        # На не-Windows платформах (или при ошибке) считаем, что elevation не требуется.
        return True


def relaunch_as_admin() -> None:
    """
    Перезапускает текущий скрипт с правами администратора через UAC.
    После вызова текущий (неповышенный) процесс должен завершиться сам —
    вызывающий код обязан сделать sys.exit() сразу после этого вызова.
    """
    params = " ".join(f'"{arg}"' for arg in sys.argv)
    ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",              # запрос повышения прав через UAC
        sys.executable,       # интерпретатор python.exe / pythonw.exe
        params,
        None,
        1,                    # SW_SHOWNORMAL
    )
