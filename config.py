"""
config.py
=========
Конфигурация приложения. Работает как из .py, так и из .exe.

config.yaml всегда лежит РЯДОМ с .exe (или рядом с main.py при запуске
из исходников). Встроенные ресурсы (Zapret, tg-ws-proxy, icon.ico)
упакованы внутрь .exe и распаковываются во временную папку _MEIPASS.
"""

from __future__ import annotations

import os
import sys
import yaml
from dataclasses import dataclass


THEMES = ("light", "dark", "glass")
DEFAULT_THEME = "light"


def is_frozen() -> bool:
    """True, если запущено из собранного .exe."""
    return getattr(sys, "frozen", False)


def get_app_dir() -> str:
    """
    Папка рядом с .exe (или с main.py при запуске из исходников).
    Здесь будет лежать config.yaml.
    """
    if is_frozen():
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def get_bundle_dir() -> str:
    """
    Папка со встроенными ресурсами:
    - onefile: временная папка sys._MEIPASS;
    - onedir: папка рядом с .exe;
    - из исходников: папка проекта.
    """
    if is_frozen():
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def get_resource_path(filename: str) -> str:
    """Путь к встроенному ресурсу (icon.ico и т.п.)."""
    return os.path.join(get_bundle_dir(), filename)


def get_zapret_default_path() -> str:
    """
    Путь к Zapret. Если запущено из .exe — это папка внутри бандла.
    Если из исходников — папка проекта (там, где main.py).
    """
    return os.path.join(get_bundle_dir(), "zapret-discord-youtube-1.10.2")


def get_tgws_default_path() -> str:
    """Путь к tg-ws-proxy."""
    return os.path.join(get_bundle_dir(), "tg-ws-proxy-1.10.2")


@dataclass
class AppConfig:
    zapret_dir: str
    theme: str = DEFAULT_THEME
    last_profile: str = ""
    autostart_telegram: bool = False


DEFAULT_CONFIG_PATH = os.path.join(get_app_dir(), "config.yaml")


def _write_default_config(path: str) -> None:
    """Создаёт config.yaml со значениями по умолчанию."""
    default_zapret = get_zapret_default_path().replace("\\", "/")
    with open(path, "w", encoding="utf-8") as f:
        f.write(
            "# Путь к папке Zapret (где general.bat, bin/, lists/)\n"
            "# При запуске из .exe это автоподставленный путь внутрь бандла.\n"
            f'zapret_dir: "{default_zapret}"\n'
            "\n"
            "# Тема: light | dark | glass\n"
            f"theme: {DEFAULT_THEME}\n"
            "\n"
            "# Последний выбранный профиль (заполняется автоматически)\n"
            'last_profile: ""\n'
            "\n"
            "# Запускать Telegram вместе с Zapret\n"
            "autostart_telegram: false\n"
        )


def load_config(path: str | None = None) -> AppConfig:
    """Загружает config.yaml. Если файла нет — создаёт со значениями по умолчанию."""
    if path is None:
        path = DEFAULT_CONFIG_PATH

    if not os.path.exists(path):
        _write_default_config(path)

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    zapret_dir = (raw.get("zapret_dir") or "").strip()
    if not zapret_dir:
        # Если пусто — подставляем путь по умолчанию
        zapret_dir = get_zapret_default_path()

    # Проверяем, что путь существует. Если нет — пробуем дефолтный.
    if not os.path.isdir(zapret_dir):
        fallback = get_zapret_default_path()
        if os.path.isdir(fallback):
            zapret_dir = fallback

    theme = (raw.get("theme") or DEFAULT_THEME).strip().lower()
    if theme not in THEMES:
        theme = DEFAULT_THEME

    return AppConfig(
        zapret_dir=zapret_dir,
        theme=theme,
        last_profile=(raw.get("last_profile") or "").strip(),
        autostart_telegram=bool(raw.get("autostart_telegram", False)),
    )


def save_config(config: AppConfig, path: str | None = None) -> None:
    if path is None:
        path = DEFAULT_CONFIG_PATH
    data = {
        "zapret_dir": config.zapret_dir,
        "theme": config.theme,
        "last_profile": config.last_profile,
        "autostart_telegram": config.autostart_telegram,
    }
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)