"""
zapret_manager.py
==================
Класс ZapretManager отвечает за запуск и остановку winws.exe —
официального бинарника zapret, который выполняет всю работу по обходу
DPI (десинхронизация TCP, фейковые пакеты и т.п.). Сама обёртка не
реализует и не изменяет эту логику.

Особенность профилей zapret: каждый .bat-файл (например general.bat)
запускает winws.exe через `start /min ... winws.exe ...` — то есть сам
.bat завершается почти сразу, а реальная работа продолжается в отдельном
процессе winws.exe. Поэтому отслеживать нужно именно процесс winws.exe
по имени, а не код возврата .bat-скрипта.

Так как WinDivert (драйвер, через который winws.exe перехватывает
пакеты) требует прав администратора, весь процесс должен быть запущен
от имени администратора. Проверка/повышение прав реализована в
elevation.py.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from typing import Callable, Optional

from profiles import Profile, get_winws_path


LogCallback = Callable[[str], None]
StatusCallback = Callable[[str], None]  # "connected" | "disconnected" | "error"

WINWS_PROCESS_NAME = "winws.exe"


class ZapretManager:
    """Управляет запуском одного профиля zapret (одного .bat-файла) за раз."""

    def __init__(self, zapret_dir: str):
        self.zapret_dir = zapret_dir
        self._bat_process: Optional[subprocess.Popen] = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_monitor = threading.Event()

    # ------------------------------------------------------------------
    # Проверка состояния winws.exe через tasklist (штатная утилита Windows)
    # ------------------------------------------------------------------

    @staticmethod
    def _is_winws_running() -> bool:
        """
        Проверяет, запущен ли процесс winws.exe в системе, через tasklist.
        Используем это, а не PID .bat-процесса, т.к. .bat запускает winws.exe
        через `start /min` и сам почти сразу завершается.
        """
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {WINWS_PROCESS_NAME}"],
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return WINWS_PROCESS_NAME.lower() in result.stdout.lower()
        except Exception:
            return False

    @staticmethod
    def _kill_winws() -> None:
        """Принудительно завершает все процессы winws.exe через taskkill."""
        subprocess.run(
            ["taskkill", "/F", "/IM", WINWS_PROCESS_NAME],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    def is_running(self) -> bool:
        return self._is_winws_running()

    # ------------------------------------------------------------------
    # Запуск / остановка
    # ------------------------------------------------------------------

    def start(
        self,
        profile: Profile,
        on_log: Optional[LogCallback] = None,
        on_status: Optional[StatusCallback] = None,
    ) -> None:
        """
        Запускает выбранный .bat-профиль. Если winws.exe уже был запущен
        (например, от предыдущего профиля или ранее вручную) — сначала
        останавливает его, чтобы не было конфликта за перехват трафика.
        """
        if self.is_running():
            if on_log:
                on_log("[zapret_manager] Обнаружен уже запущенный winws.exe — останавливаю перед новым запуском.")
            self.stop(on_log=on_log)

        winws_path = get_winws_path(self.zapret_dir)
        if not os.path.isfile(winws_path):
            msg = f"winws.exe не найден по пути: {winws_path}"
            if on_log:
                on_log(msg)
            if on_status:
                on_status("error")
            raise FileNotFoundError(msg)

        if not os.path.isfile(profile.bat_path):
            msg = f"Файл профиля не найден: {profile.bat_path}"
            if on_log:
                on_log(msg)
            if on_status:
                on_status("error")
            raise FileNotFoundError(msg)

        if on_log:
            on_log(f"[zapret_manager] Запуск профиля: {profile.name}")

        # Запускаем .bat в его собственном каталоге (там же, где general.bat,
        # bin/, lists/), т.к. внутри .bat используются относительные пути
        # вида %~dp0bin\winws.exe.
        try:
            self._bat_process = subprocess.Popen(
                ["cmd.exe", "/c", profile.bat_path],
                cwd=self.zapret_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="cp866",  # кодировка консоли Windows по умолчанию (chcp 65001 внутри .bat переключает на UTF-8, но вывод самого cmd может быть в OEM)

                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except OSError as e:
            if on_log:
                on_log(f"[zapret_manager] Ошибка запуска .bat: {e}")
            if on_status:
                on_status("error")
            raise

        # Читаем вывод .bat (там же выводится статус service.bat: проверка
        # обновлений, списков и т.п.) в отдельном потоке.
        threading.Thread(target=self._read_bat_output, args=(on_log,), daemon=True).start()

        # .bat почти сразу завершается (winws.exe продолжает жить отдельно
        # через `start /min`), поэтому ждём и проверяем именно наличие
        # процесса winws.exe в системе.
        self._stop_monitor.clear()
        started_ok = self._wait_for_winws_start(timeout_seconds=8)

        if not started_ok:
            if on_log:
                on_log("[zapret_manager] winws.exe не появился в списке процессов — запуск не удался.")
            if on_status:
                on_status("error")
            return

        if on_status:
            on_status("connected")

        # Запускаем фоновый монитор, который следит, не "упал" ли winws.exe
        # самостоятельно (например, драйвер WinDivert не смог загрузиться).
        self._monitor_thread = threading.Thread(
            target=self._monitor_winws,
            args=(on_status,),
            daemon=True,
        )
        self._monitor_thread.start()

    def _wait_for_winws_start(self, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self._is_winws_running():
                return True
            time.sleep(0.5)
        return False

    def _read_bat_output(self, on_log: Optional[LogCallback]) -> None:
        if self._bat_process is None or self._bat_process.stdout is None:
            return
        try:
            for line in self._bat_process.stdout:
                line = line.rstrip("\n").rstrip("\r")
                if line and on_log:
                    on_log(line)
        except Exception as e:
            if on_log:
                on_log(f"[zapret_manager] Ошибка чтения вывода .bat: {e}")

    def _monitor_winws(self, on_status: Optional[StatusCallback]) -> None:
        """Фоновая проверка раз в 2 секунды: жив ли ещё winws.exe."""
        while not self._stop_monitor.is_set():
            time.sleep(2)
            if self._stop_monitor.is_set():
                break
            if not self._is_winws_running():
                if on_status:
                    on_status("disconnected")
                break

    def stop(self, on_log: Optional[LogCallback] = None) -> None:
        """Останавливает winws.exe (и завершает мониторинг)."""
        self._stop_monitor.set()

        if self.is_running():
            if on_log:
                on_log("[zapret_manager] Останавливаю winws.exe...")
            self._kill_winws()
            # Даём системе время фактически убрать процесс
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and self.is_running():
                time.sleep(0.3)

        if self._monitor_thread is not None:
            self._monitor_thread.join(timeout=2)
            self._monitor_thread = None

        self._bat_process = None

        if on_log:
            on_log("[zapret_manager] Остановлено.")
