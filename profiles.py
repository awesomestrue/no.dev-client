"""
profiles.py
===========
Обёртка сама не знает и не генерирует стратегии обхода DPI — она лишь
находит уже готовые .bat-профили, которые идут в составе официальной
сборки zapret (general.bat, "general (ALT).bat" и т.д.), и позволяет
пользователю выбрать один из них для запуска.

ВАЖНО: сортировка профилей должна совпадать с той, что использует
service.bat при установке службы. service.bat сортирует через PowerShell:
    Sort-Object { [Regex]::Replace($_.Name, '(\d+)', { $args[0].Value.PadLeft(8, '0') }) }
Это «естественная» сортировка (числа сравниваются как числа). Поэтому
здесь используется такая же логика через _natural_key().
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import List


@dataclass
class Profile:
    """Один профиль обхода — соответствует одному .bat-файлу в каталоге zapret."""
    name: str        # человекочитаемое имя (из имени файла без .bat)
    bat_path: str    # полный путь к .bat-файлу


def _natural_key(name: str):
    """
    Ключ для «естественной» сортировки: числа в имени сравниваются
    как числа, а не как строки. Например:
        general.bat < general (ALT).bat < general (ALT2).bat < general (ALT13).bat
    Это совпадает с сортировкой в service.bat.
    """
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r'(\d+)', name)
    ]


def discover_profiles(zapret_dir: str) -> List[Profile]:
    """
    Сканирует корень каталога zapret_dir и возвращает список найденных
    профилей — все .bat-файлы (кроме service.bat).
    """
    if not os.path.isdir(zapret_dir):
        raise FileNotFoundError(f"Каталог zapret не найден: {zapret_dir}")

    profiles: List[Profile] = []
    filenames = [
        f for f in os.listdir(zapret_dir)
        if f.lower().endswith(".bat") and f.lower() != "service.bat"
    ]
    for filename in sorted(filenames, key=_natural_key):
        name = filename[:-4]
        profiles.append(Profile(name=name, bat_path=os.path.join(zapret_dir, filename)))

    return profiles


def get_winws_path(zapret_dir: str) -> str:
    """Возвращает ожидаемый путь к winws.exe внутри каталога zapret/bin/."""
    return os.path.join(zapret_dir, "bin", "winws.exe")