"""
gui.py
======
Главное окно приложения на PyQt6.

Вкладки:
 - Основное: выбор профиля, Вкл/Выкл, статус, автозапуск, лог, поле ввода.
 - Настройки: Telegram, Zapret Service, Результаты тестов, Discord, Hosts.

Интерактивный режим service.bat:
 - При нажатии кнопки в секции Zapret Service запускается service.bat
   в фоне. Его stdin остаётся открытым, stdout читается в отдельном
   потоке и построчно выводится в лог.
 - Пользователь вводит ответы (цифры) в поле ввода под логом и жмёт
   Enter или кнопку «Отправить». Ответ уходит в stdin service.bat.
 - Для команды Install Service вкладка автоматически переключается
   на «Основное», чтобы пользователь видел выбранный профиль.

Иконки: используется icon.ico из корня проекта.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import webbrowser
from typing import List, Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject
from PyQt6.QtGui import QIcon, QAction
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QPlainTextEdit,
    QLineEdit,
    QCheckBox,
    QSystemTrayIcon,
    QMenu,
    QMessageBox,
    QTabWidget,
    QFrame,
    QSizePolicy,
)

from config import AppConfig
from profiles import Profile, discover_profiles
from zapret_manager import ZapretManager
import autostart


# ----------------------------------------------------------------------
# Иконка приложения
# ----------------------------------------------------------------------

_APP_ICON: Optional[QIcon] = None


def _get_app_icon() -> QIcon:
    global _APP_ICON
    if _APP_ICON is None:
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
        if os.path.isfile(icon_path):
            _APP_ICON = QIcon(icon_path)
        else:
            _APP_ICON = QIcon()
    return _APP_ICON


# ----------------------------------------------------------------------
# Путь к hosts
# ----------------------------------------------------------------------

HOSTS_PATH = r"C:\Windows\System32\drivers\etc\hosts"
HOSTS_BACKUP = r"C:\Windows\System32\drivers\etc\hosts.bak"


# ----------------------------------------------------------------------
# Воркеры
# ----------------------------------------------------------------------

class ConnectionWorker(QObject):
    log_line = pyqtSignal(str)
    status_changed = pyqtSignal(str)

    def __init__(self, zapret_dir: str):
        super().__init__()
        self.manager = ZapretManager(zapret_dir)

    def start_profile(self, profile: Profile) -> None:
        try:
            self.manager.start(
                profile=profile,
                on_log=self.log_line.emit,
                on_status=self.status_changed.emit,
            )
        except Exception as e:
            self.log_line.emit(f"[gui] Ошибка при запуске: {e}")
            self.status_changed.emit("error")

    def stop_profile(self) -> None:
        self.manager.stop(on_log=self.log_line.emit)
        self.status_changed.emit("disconnected")


class ConnectionThread(QThread):
    log_line = pyqtSignal(str)
    status_changed = pyqtSignal(str)

    def __init__(self, zapret_dir: str, profile: Profile):
        super().__init__()
        self.worker = ConnectionWorker(zapret_dir)
        self.worker.log_line.connect(self.log_line.emit)
        self.worker.status_changed.connect(self.status_changed.emit)
        self._profile = profile

    def run(self) -> None:
        self.worker.start_profile(self._profile)
        while self.worker.manager.is_running():
            self.msleep(500)

    def stop(self) -> None:
        self.worker.stop_profile()
        self.wait(5000)


class InteractiveServiceThread(QThread):
    log_line = pyqtSignal(str)
    process_finished = pyqtSignal()

    def __init__(self, zapret_dir: str):
        super().__init__()
        self.zapret_dir = zapret_dir
        self._proc: Optional[subprocess.Popen] = None
        self._stdin_lock = __import__("threading").Lock()

    def run(self) -> None:
        service_bat = os.path.join(self.zapret_dir, "service.bat")
        if not os.path.isfile(service_bat):
            self.log_line.emit(f"[service] service.bat не найден: {service_bat}")
            self.process_finished.emit()
            return

        try:
            self._proc = subprocess.Popen(
                ["cmd.exe", "/c", service_bat, "admin"],
                cwd=self.zapret_dir,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="cp866",
                errors="replace",
                bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception as e:
            self.log_line.emit(f"[service] Ошибка запуска: {e}")
            self.process_finished.emit()
            return

        try:
            if self._proc.stdout is not None:
                for line in self._proc.stdout:
                    line = line.rstrip("\n").rstrip("\r")
                    if line:
                        self.log_line.emit(f"[service] {line}")
        except Exception as e:
            self.log_line.emit(f"[service] Ошибка чтения вывода: {e}")

        try:
            self._proc.wait(timeout=5)
        except Exception:
            pass

        self.log_line.emit("[service] Процесс service.bat завершён.")
        self.process_finished.emit()

    def send_input(self, text: str) -> None:
        if self._proc is None or self._proc.stdin is None:
            return
        with self._stdin_lock:
            try:
                self._proc.stdin.write(f"{text}\n")
                self._proc.stdin.flush()
                self.log_line.emit(f"[gui] >>> {text}")
            except Exception as e:
                self.log_line.emit(f"[gui] Ошибка отправки ввода: {e}")

    def stop_process(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass
            try:
                self._proc.wait(timeout=3)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass


# ----------------------------------------------------------------------
# Главное окно
# ----------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig):
        super().__init__()
        self.config = config
        self.connection_thread: Optional[ConnectionThread] = None
        self.tgws_process: Optional[subprocess.Popen] = None
        self.service_thread: Optional[InteractiveServiceThread] = None

        self.setWindowTitle("no.dev client")
        self.resize(760, 760)
        self.setWindowIcon(_get_app_icon())

        try:
            self.profiles: List[Profile] = discover_profiles(config.zapret_dir)
        except FileNotFoundError as e:
            QMessageBox.critical(self, "Ошибка", str(e))
            self.profiles = []

        self._build_ui()
        self._build_tray_icon()
        self.set_status("disconnected")

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        root_layout = QVBoxLayout(central)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_main_tab(), "Основное")
        self.tabs.addTab(self._build_settings_tab(), "Настройки")
        root_layout.addWidget(self.tabs)

        self.setCentralWidget(central)

    # ---------- Вкладка "Основное" ----------

    def _build_main_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel("Профиль:"))
        self.profile_combo = QComboBox()
        for profile in self.profiles:
            self.profile_combo.addItem(profile.name, userData=profile)
        profile_row.addWidget(self.profile_combo, stretch=1)
        layout.addLayout(profile_row)

        if not self.profiles:
            layout.addWidget(QLabel(
                "Профили (.bat-файлы) не найдены. Проверьте путь zapret_dir в config.yaml."
            ))

        status_row = QHBoxLayout()
        self.status_label = QLabel("Отключено")
        self.status_label.setStyleSheet("font-weight: bold;")
        status_row.addWidget(self.status_label)
        status_row.addStretch(1)
        layout.addLayout(status_row)

        buttons_row = QHBoxLayout()
        self.connect_button = QPushButton("Включить")
        self.connect_button.clicked.connect(self.on_connect_clicked)
        self.disconnect_button = QPushButton("Выключить")
        self.disconnect_button.clicked.connect(self.on_disconnect_clicked)
        self.disconnect_button.setEnabled(False)
        buttons_row.addWidget(self.connect_button)
        buttons_row.addWidget(self.disconnect_button)
        layout.addLayout(buttons_row)

        self.autostart_checkbox = QCheckBox("Запускать при старте системы (с правами администратора)")
        try:
            self.autostart_checkbox.setChecked(autostart.is_autostart_enabled())
        except Exception:
            self.autostart_checkbox.setEnabled(False)
        self.autostart_checkbox.stateChanged.connect(self.on_autostart_toggled)
        layout.addWidget(self.autostart_checkbox)

        layout.addWidget(QLabel("Лог:"))

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        layout.addWidget(self.log_view, stretch=1)

        input_row = QHBoxLayout()
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText(
            "Введите ответ для service.bat (например, 1) и нажмите Enter"
        )
        self.input_field.returnPressed.connect(self.on_send_input)
        input_row.addWidget(self.input_field, stretch=1)

        self.send_button = QPushButton("Отправить")
        self.send_button.clicked.connect(self.on_send_input)
        input_row.addWidget(self.send_button)

        self.stop_service_button = QPushButton("Остановить service.bat")
        self.stop_service_button.clicked.connect(self.on_stop_service)
        self.stop_service_button.setEnabled(False)
        input_row.addWidget(self.stop_service_button)

        layout.addLayout(input_row)

        return tab

    # ---------- Вкладка "Настройки" ----------

    def _build_settings_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # ===== Telegram =====
        layout.addWidget(self._section_label("Telegram (tg-ws-proxy)"))

        tg_card = QFrame()
        tg_card.setObjectName("card")
        tg_layout = QVBoxLayout(tg_card)

        tg_row = QHBoxLayout()
        self.tg_status_label = QLabel("Telegram: отключён")
        tg_row.addWidget(self.tg_status_label)
        tg_row.addStretch(1)
        tg_layout.addLayout(tg_row)

        tg_buttons = QHBoxLayout()
        self.tg_start_button = QPushButton("Включить Telegram")
        self.tg_start_button.clicked.connect(self.on_tg_start)
        self.tg_stop_button = QPushButton("Выключить Telegram")
        self.tg_stop_button.clicked.connect(self.on_tg_stop)
        self.tg_stop_button.setEnabled(False)
        tg_buttons.addWidget(self.tg_start_button)
        tg_buttons.addWidget(self.tg_stop_button)
        tg_layout.addLayout(tg_buttons)

        self.tg_open_button = QPushButton("Подключить в Telegram")
        self.tg_open_button.clicked.connect(self.on_tg_open)
        self.tg_open_button.setEnabled(False)
        tg_layout.addWidget(self.tg_open_button)

        layout.addWidget(tg_card)

        # ===== Zapret Service =====
        layout.addWidget(self._section_label("Zapret Service"))

        svc_card = QFrame()
        svc_card.setObjectName("card")
        svc_layout = QVBoxLayout(svc_card)

        self.svc_status_label = QLabel("Служба: проверка...")
        svc_layout.addWidget(self.svc_status_label)

        svc_mgmt_row = QHBoxLayout()
        self.install_service_button = QPushButton("Установить службу")
        self.install_service_button.clicked.connect(self.on_install_service)
        self.remove_service_button = QPushButton("Удалить службу")
        self.remove_service_button.clicked.connect(self.on_remove_service)
        svc_mgmt_row.addWidget(self.install_service_button)
        svc_mgmt_row.addWidget(self.remove_service_button)
        svc_layout.addLayout(svc_mgmt_row)

        self.check_status_button = QPushButton("Проверить статус службы")
        self.check_status_button.clicked.connect(self.on_check_status)
        svc_layout.addWidget(self.check_status_button)

        svc_updates_row = QHBoxLayout()
        self.update_ipsets_button = QPushButton("Обновить IPsets")
        self.update_ipsets_button.clicked.connect(self.on_update_ipsets)
        self.update_hosts_button = QPushButton("Обновить hosts")
        self.update_hosts_button.clicked.connect(self.on_update_hosts)
        svc_updates_row.addWidget(self.update_ipsets_button)
        svc_updates_row.addWidget(self.update_hosts_button)
        svc_layout.addLayout(svc_updates_row)

        self.diagnostics_button = QPushButton("Запустить диагностику")
        self.diagnostics_button.clicked.connect(self.on_run_diagnostics)
        svc_layout.addWidget(self.diagnostics_button)

        self.test_zapret_button = QPushButton("Запустить тесты Zapret")
        self.test_zapret_button.clicked.connect(self.on_test_zapret)
        svc_layout.addWidget(self.test_zapret_button)

        layout.addWidget(svc_card)

        # ===== Результаты тестов =====
        layout.addWidget(self._section_label("Результаты тестов"))

        results_card = QFrame()
        results_card.setObjectName("card")
        results_layout = QVBoxLayout(results_card)

        results_row = QHBoxLayout()
        results_row.addWidget(QLabel("Файл:"))
        self.results_combo = QComboBox()
        self.results_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        results_row.addWidget(self.results_combo, stretch=1)
        self.refresh_results_button = QPushButton("Обновить список")
        self.refresh_results_button.clicked.connect(self.refresh_results_list)
        results_row.addWidget(self.refresh_results_button)
        results_layout.addLayout(results_row)

        self.load_result_button = QPushButton("Загрузить результат в лог")
        self.load_result_button.clicked.connect(self.on_load_result)
        results_layout.addWidget(self.load_result_button)

        layout.addWidget(results_card)

        # ===== Discord =====
        layout.addWidget(self._section_label("Discord"))

        dc_card = QFrame()
        dc_card.setObjectName("card")
        dc_layout = QVBoxLayout(dc_card)

        self.clear_dc_cache_button = QPushButton("Очистить кэш Discord")
        self.clear_dc_cache_button.clicked.connect(self.on_clear_discord_cache)
        dc_layout.addWidget(self.clear_dc_cache_button)

        layout.addWidget(dc_card)

        # ===== Hosts =====
        layout.addWidget(self._section_label("Hosts (Roblox, иконки и т.д.)"))

        hosts_card = QFrame()
        hosts_card.setObjectName("card")
        hosts_layout = QVBoxLayout(hosts_card)

        hosts_hint = QLabel(
            "Введите строку в формате: IP домен\n"
            "Например: 18.65.39.105 tr.rbxcdn.com"
        )
        hosts_hint.setWordWrap(True)
        hosts_layout.addWidget(hosts_hint)

        hosts_input_row = QHBoxLayout()
        self.hosts_input = QLineEdit()
        self.hosts_input.setPlaceholderText("18.65.39.105 tr.rbxcdn.com")
        hosts_input_row.addWidget(self.hosts_input, stretch=1)

        self.add_hosts_button = QPushButton("Добавить в hosts")
        self.add_hosts_button.clicked.connect(self.on_add_hosts)
        hosts_input_row.addWidget(self.add_hosts_button)
        hosts_layout.addLayout(hosts_input_row)

        hosts_buttons_row = QHBoxLayout()
        self.open_hosts_button = QPushButton("Открыть hosts (блокнотом)")
        self.open_hosts_button.clicked.connect(self.on_open_hosts)
        hosts_buttons_row.addWidget(self.open_hosts_button)

        self.restore_hosts_button = QPushButton("Восстановить hosts.bak")
        self.restore_hosts_button.clicked.connect(self.on_restore_hosts)
        hosts_buttons_row.addWidget(self.restore_hosts_button)
        hosts_layout.addLayout(hosts_buttons_row)

        layout.addWidget(hosts_card)

        layout.addStretch(1)

        self.refresh_results_list()
        self.refresh_service_status()

        return tab

    def _section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("font-weight: bold; padding-top: 6px;")
        return label

    # ------------------------------------------------------------------
    # Трей
    # ------------------------------------------------------------------

    def _build_tray_icon(self) -> None:
        self.tray_icon = QSystemTrayIcon(_get_app_icon(), self)
        self.tray_icon.setToolTip("no.dev client — отключено")

        tray_menu = QMenu()

        show_action = QAction("Открыть окно", self)
        show_action.triggered.connect(self.showNormal)
        tray_menu.addAction(show_action)

        toggle_action = QAction("Включить/выключить", self)
        toggle_action.triggered.connect(self.on_tray_toggle)
        tray_menu.addAction(toggle_action)

        tray_menu.addSeparator()

        quit_action = QAction("Выход", self)
        quit_action.triggered.connect(self.on_quit)
        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.isVisible():
                self.hide()
            else:
                self.showNormal()
                self.activateWindow()

    # ------------------------------------------------------------------
    # Основные обработчики
    # ------------------------------------------------------------------

    def append_log(self, line: str) -> None:
        self.log_view.appendPlainText(line)

    def set_status(self, status: str) -> None:
        if status == "connected":
            text = "Включено"
            self.connect_button.setEnabled(False)
            self.disconnect_button.setEnabled(True)
            self.profile_combo.setEnabled(False)
        elif status == "error":
            text = "Ошибка"
            self.connect_button.setEnabled(True)
            self.disconnect_button.setEnabled(False)
            self.profile_combo.setEnabled(True)
        else:
            text = "Отключено"
            self.connect_button.setEnabled(True)
            self.disconnect_button.setEnabled(False)
            self.profile_combo.setEnabled(True)

        self.status_label.setText(text)
        self.tray_icon.setToolTip(f"no.dev client — {text}")

    def on_connect_clicked(self) -> None:
        profile: Optional[Profile] = self.profile_combo.currentData()
        if profile is None:
            QMessageBox.warning(self, "Нет профиля", "Список профилей пуст. Проверьте config.yaml.")
            return

        self.append_log(f"[gui] Запуск профиля: {profile.name}")

        self.connection_thread = ConnectionThread(
            zapret_dir=self.config.zapret_dir,
            profile=profile,
        )
        self.connection_thread.log_line.connect(self.append_log)
        self.connection_thread.status_changed.connect(self.set_status)
        self.connection_thread.start()

    def on_disconnect_clicked(self) -> None:
        if self.connection_thread is not None:
            self.append_log("[gui] Остановка...")
            self.connection_thread.stop()
            self.connection_thread = None
        self.set_status("disconnected")

    def on_tray_toggle(self) -> None:
        if self.connection_thread is not None and self.connection_thread.worker.manager.is_running():
            self.on_disconnect_clicked()
        else:
            self.on_connect_clicked()

    def on_autostart_toggled(self, state: int) -> None:
        enabled = state == Qt.CheckState.Checked.value
        try:
            autostart.set_autostart(enabled)
            self.append_log(f"[gui] Автозапуск {'включён' if enabled else 'выключен'}.")
        except Exception as e:
            QMessageBox.warning(self, "Не удалось изменить автозапуск", str(e))

    # ------------------------------------------------------------------
    # Telegram
    # ------------------------------------------------------------------

    def on_tg_start(self) -> None:
        tgws_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tg-ws-proxy-1.10.2")
        windows_py = os.path.join(tgws_dir, "windows.py")
        if not os.path.isfile(windows_py):
            QMessageBox.warning(self, "Ошибка", f"windows.py не найден: {windows_py}")
            return
        try:
            self.tgws_process = subprocess.Popen(
                [sys.executable, windows_py],
                cwd=tgws_dir,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            self.append_log("[gui] Telegram (tg-ws-proxy) запущен.")
            self.tg_status_label.setText("Telegram: включён")
            self.tg_start_button.setEnabled(False)
            self.tg_stop_button.setEnabled(True)
            self.tg_open_button.setEnabled(True)
        except Exception as e:
            self.append_log(f"[gui] Ошибка запуска Telegram: {e}")

    def on_tg_stop(self) -> None:
        if self.tgws_process is not None and self.tgws_process.poll() is None:
            self.tgws_process.terminate()
            self.tgws_process = None
        self.append_log("[gui] Telegram остановлен.")
        self.tg_status_label.setText("Telegram: отключён")
        self.tg_start_button.setEnabled(True)
        self.tg_stop_button.setEnabled(False)
        self.tg_open_button.setEnabled(False)

    def on_tg_open(self) -> None:
        url = "tg://proxy?server=127.0.0.1&port=1443&secret=dda4b7cc9318f0dfe93f41c209e05bc014"
        webbrowser.open(url)
        self.append_log(f"[gui] Открываю ссылку: {url}")

    # ------------------------------------------------------------------
    # Zapret Service — интерактивный режим
    # ------------------------------------------------------------------

    def _ensure_service_running(self, label: str) -> bool:
        if self.service_thread is not None and self.service_thread.isRunning():
            self.append_log(f"[gui] service.bat уже запущен — {label} будет выполнено в нём.")
            return True

        service_bat = os.path.join(self.config.zapret_dir, "service.bat")
        if not os.path.isfile(service_bat):
            QMessageBox.warning(self, "Ошибка", f"service.bat не найден: {service_bat}")
            return False

        self.append_log(f"[gui] --- Запуск service.bat ({label}) ---")
        self.service_thread = InteractiveServiceThread(self.config.zapret_dir)
        self.service_thread.log_line.connect(self.append_log)
        self.service_thread.process_finished.connect(self._on_service_finished)
        self.service_thread.start()

        self.stop_service_button.setEnabled(True)
        self.input_field.setEnabled(True)
        self.send_button.setEnabled(True)
        self.input_field.setFocus()
        return True

    def _on_service_finished(self) -> None:
        self.append_log("[gui] service.bat завершён. Поле ввода отключено.")
        self.stop_service_button.setEnabled(False)
        self.input_field.setEnabled(False)
        self.send_button.setEnabled(False)
        self.service_thread = None
        self.refresh_service_status()

    def on_send_input(self) -> None:
        text = self.input_field.text().strip()
        if not text:
            return
        if self.service_thread is None or not self.service_thread.isRunning():
            self.append_log("[gui] service.bat не запущен. Нажмите кнопку в секции Zapret Service.")
            self.input_field.clear()
            return
        self.service_thread.send_input(text)
        self.input_field.clear()

    def on_stop_service(self) -> None:
        if self.service_thread is not None and self.service_thread.isRunning():
            self.append_log("[gui] Принудительная остановка service.bat...")
            self.service_thread.stop_process()

    def refresh_service_status(self) -> None:
        try:
            result = subprocess.run(
                ["sc", "query", "zapret"],
                capture_output=True, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            installed = result.returncode == 0
            self.svc_status_label.setText(
                "Служба: установлена" if installed else "Служба: не установлена"
            )
        except Exception as e:
            self.svc_status_label.setText(f"Служба: ошибка ({e})")

    def on_install_service(self) -> None:
        self.tabs.setCurrentIndex(0)
        profile_name = self.profile_combo.currentText() if self.profiles else "(нет)"
        self.append_log(f"[gui] Установка службы. Выбранный профиль: «{profile_name}».")
        self.append_log(
            "[gui] Введите 1 — Install Service. Затем введите номер профиля из меню."
        )
        self._ensure_service_running("Install Service")

    def on_remove_service(self) -> None:
        self.tabs.setCurrentIndex(0)
        self.append_log("[gui] Удаление службы. Введите 2 — Remove Services.")
        self._ensure_service_running("Remove Services")

    def on_check_status(self) -> None:
        self.tabs.setCurrentIndex(0)
        self.append_log("[gui] Проверка статуса. Введите 3 — Check Status.")
        self._ensure_service_running("Check Status")

    def on_update_ipsets(self) -> None:
        self.tabs.setCurrentIndex(0)
        self.append_log("[gui] Обновление IPsets. Введите 8 — Update IPSet List.")
        self._ensure_service_running("Update IPSet List")

    def on_update_hosts(self) -> None:
        self.tabs.setCurrentIndex(0)
        self.append_log("[gui] Обновление hosts. Введите 9 — Update Hosts File.")
        self._ensure_service_running("Update Hosts File")

    def on_run_diagnostics(self) -> None:
        self.tabs.setCurrentIndex(0)
        self.append_log("[gui] Диагностика. Введите 11 — Run Diagnostics.")
        self._ensure_service_running("Run Diagnostics")

    def on_test_zapret(self) -> None:
        self.tabs.setCurrentIndex(0)
        self.append_log("[gui] Тесты. Введите 12 — Run Tests.")
        self._ensure_service_running("Run Tests")

    # ------------------------------------------------------------------
    # Результаты тестов
    # ------------------------------------------------------------------

    def _results_dir(self) -> str:
        return os.path.join(self.config.zapret_dir, "utils", "test results")

    def refresh_results_list(self) -> None:
        results_dir = self._results_dir()
        self.results_combo.clear()
        if not os.path.isdir(results_dir):
            self.append_log(f"[gui] Папка результатов не найдена: {results_dir}")
            self.results_combo.addItem("(нет результатов)")
            return
        files = [
            f for f in os.listdir(results_dir)
            if f.startswith("test_results_") and f.endswith(".txt")
        ]
        files.sort(reverse=True)
        for f in files:
            self.results_combo.addItem(f)
        if not files:
            self.results_combo.addItem("(нет результатов)")

    def on_load_result(self) -> None:
        name = self.results_combo.currentText()
        if not name or name == "(нет результатов)":
            return
        path = os.path.join(self._results_dir(), name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            self.append_log(f"[gui] ===== {name} =====")
            self.append_log(content)
            self.append_log(f"[gui] ===== Конец {name} =====")
        except Exception as e:
            self.append_log(f"[gui] Ошибка чтения {path}: {e}")

    # ------------------------------------------------------------------
    # Discord
    # ------------------------------------------------------------------

    def on_clear_discord_cache(self) -> None:
        answer = QMessageBox.question(
            self, "Очистка кэша Discord",
            "Discord должен быть закрыт. Продолжить?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        appdata = os.environ.get("APPDATA", "")
        paths = [
            os.path.join(appdata, "discord", "Cache"),
            os.path.join(appdata, "discord", "Code Cache"),
            os.path.join(appdata, "discord", "GPUCache"),
        ]
        for p in paths:
            try:
                if os.path.isdir(p):
                    shutil.rmtree(p, ignore_errors=True)
                    self.append_log(f"[gui] Очищено: {p}")
            except Exception as e:
                self.append_log(f"[gui] Ошибка очистки {p}: {e}")

    # ------------------------------------------------------------------
    # Hosts
    # ------------------------------------------------------------------

    def _validate_hosts_line(self, text: str) -> Optional[tuple[str, str]]:
        """
        Проверяет строку 'IP домен'. Возвращает (ip, domain) или None.
        """
        parts = text.strip().split()
        if len(parts) != 2:
            return None
        ip, domain = parts
        # Простейшая проверка IP: 4 октета, каждый 0-255
        try:
            octets = ip.split(".")
            if len(octets) != 4:
                return None
            for o in octets:
                if not 0 <= int(o) <= 255:
                    return None
        except Exception:
            return None
        # Простейшая проверка домена: есть точка, нет пробелов и слешей
        if "." not in domain or "/" in domain or " " in domain:
            return None
        return ip, domain

    def _read_hosts(self) -> str:
        with open(HOSTS_PATH, "r", encoding="utf-8", errors="replace") as f:
            return f.read()

    def _write_hosts(self, content: str) -> None:
        with open(HOSTS_PATH, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)

    def on_add_hosts(self) -> None:
        text = self.hosts_input.text().strip()
        parsed = self._validate_hosts_line(text)
        if parsed is None:
            QMessageBox.warning(
                self, "Неверный формат",
                "Введите строку в формате: IP домен\n"
                "Например: 18.65.39.105 tr.rbxcdn.com",
            )
            return

        ip, domain = parsed

        try:
            content = self._read_hosts()
        except Exception as e:
            self.append_log(f"[hosts] Ошибка чтения hosts: {e}")
            QMessageBox.warning(self, "Ошибка", f"Не удалось прочитать hosts: {e}")
            return

        # Резервная копия (только один раз — не перезаписываем старую)
        try:
            if not os.path.isfile(HOSTS_BACKUP):
                shutil.copy2(HOSTS_PATH, HOSTS_BACKUP)
                self.append_log(f"[hosts] Резервная копия создана: {HOSTS_BACKUP}")
        except Exception as e:
            self.append_log(f"[hosts] Не удалось создать резервную копию: {e}")

        # Ищем существующую запись с этим доменом
        lines = content.splitlines()
        found_index = -1
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split()
            if len(parts) >= 2 and parts[1].lower() == domain.lower():
                found_index = i
                break

        new_line = f"{ip} {domain}"

        if found_index >= 0:
            old_line = lines[found_index]
            if old_line.strip() == new_line:
                self.append_log(f"[hosts] Запись уже существует: {new_line}")
                QMessageBox.information(self, "Hosts", f"Запись уже существует:\n{new_line}")
                return
            lines[found_index] = new_line
            self.append_log(f"[hosts] Заменено: «{old_line.strip()}» → «{new_line}»")
        else:
            # Добавляем в конец, с гарантированным переводом строки
            new_content = content
            if not new_content.endswith("\n"):
                new_content += "\n"
            new_content += new_line + "\n"
            lines = new_content.splitlines()
            self.append_log(f"[hosts] Добавлено: {new_line}")

        # Собираем итоговое содержимое
        result = "\n".join(lines)
        if not result.endswith("\n"):
            result += "\n"

        try:
            self._write_hosts(result)
        except PermissionError:
            QMessageBox.critical(
                self, "Ошибка доступа",
                "Не удалось записать hosts. Запустите программу от имени администратора.",
            )
            self.append_log("[hosts] Ошибка: нет прав на запись в hosts")
            return
        except Exception as e:
            self.append_log(f"[hosts] Ошибка записи: {e}")
            QMessageBox.warning(self, "Ошибка", f"Не удалось записать hosts: {e}")
            return

        self.append_log("[hosts] Готово. Перезапустите Roblox / браузер.")
        QMessageBox.information(
            self, "Hosts",
            "Запись добавлена в hosts.\n"
            "Перезапустите Roblox (и браузер), чтобы изменения вступили в силу.",
        )
        self.hosts_input.clear()

    def on_open_hosts(self) -> None:
        try:
            subprocess.Popen(["notepad.exe", HOSTS_PATH])
            self.append_log(f"[hosts] Открыт в Блокноте: {HOSTS_PATH}")
        except Exception as e:
            self.append_log(f"[hosts] Ошибка открытия: {e}")

    def on_restore_hosts(self) -> None:
        if not os.path.isfile(HOSTS_BACKUP):
            QMessageBox.warning(
                self, "Нет резервной копии",
                f"Файл {HOSTS_BACKUP} не найден.\n"
                "Резервная копия создаётся при первом изменении hosts.",
            )
            return
        answer = QMessageBox.question(
            self, "Восстановить hosts",
            f"Заменить текущий hosts резервной копией {HOSTS_BACKUP}?\n\n"
            "Все ваши изменения в hosts будут потеряны.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            shutil.copy2(HOSTS_BACKUP, HOSTS_PATH)
            self.append_log(f"[hosts] Восстановлено из {HOSTS_BACKUP}")
            QMessageBox.information(self, "Hosts", "hosts восстановлен из резервной копии.")
        except PermissionError:
            QMessageBox.critical(
                self, "Ошибка доступа",
                "Не удалось записать hosts. Запустите программу от имени администратора.",
            )
            self.append_log("[hosts] Ошибка: нет прав на запись в hosts")
        except Exception as e:
            self.append_log(f"[hosts] Ошибка восстановления: {e}")
            QMessageBox.warning(self, "Ошибка", f"Не удалось восстановить hosts: {e}")

    # ------------------------------------------------------------------
    # Выход
    # ------------------------------------------------------------------

    def on_quit(self) -> None:
        if self.connection_thread is not None:
            self.connection_thread.stop()
        if self.tgws_process is not None and self.tgws_process.poll() is None:
            self.tgws_process.terminate()
        if self.service_thread is not None and self.service_thread.isRunning():
            self.service_thread.stop_process()
            self.service_thread.wait(3000)
        self.tray_icon.hide()
        QApplication.quit()

    def closeEvent(self, event) -> None:
        event.ignore()
        self.hide()
        self.tray_icon.showMessage(
            "no.dev client",
            "Приложение свёрнуто в трей. Для выхода используйте меню трея.",
            _get_app_icon(),
            2000,
        )