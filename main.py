"""
main.py
=======
Точка входа приложения.

Перед запуском GUI проверяет права администратора: winws.exe работает
через драйвер WinDivert, которому нужны права администратора, поэтому
без elevation приложение либо не сможет запустить перехват трафика,
либо winws.exe завершится с ошибкой сразу после старта.

Если прав нет — предлагаем пользователю перезапустить приложение с
повышением прав через стандартный диалог UAC.
"""

import sys

from PyQt6.QtWidgets import QApplication, QMessageBox

from config import load_config, DEFAULT_CONFIG_PATH
from gui import MainWindow
import elevation


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # --- Проверка прав администратора (актуально только для Windows) ---
    if sys.platform == "win32" and not elevation.is_admin():
        answer = QMessageBox.question(
            None,
            "Требуются права администратора",
            "Для работы winws.exe (перехват трафика через WinDivert) "
            "требуются права администратора.\n\n"
            "Перезапустить приложение с правами администратора сейчас?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            elevation.relaunch_as_admin()
        return 0  # текущий (неповышенный) процесс в любом случае завершаем

    try:
        config = load_config(DEFAULT_CONFIG_PATH)
    except (FileNotFoundError, ValueError) as e:
        QMessageBox.critical(None, "Ошибка конфигурации", str(e))
        return 1

    window = MainWindow(config)
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
