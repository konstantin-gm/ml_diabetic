from __future__ import annotations

import sys

from pydantic import ValidationError
from PyQt5.QtWidgets import QApplication, QMessageBox

from desktop_client.config import DesktopSettings
from desktop_client.main_window import MainWindow


def main() -> None:
    application = QApplication(sys.argv)
    try:
        settings = DesktopSettings()
    except ValidationError as error:
        QMessageBox.critical(
            None,
            "Ошибка конфигурации",
            f"Проверьте файл .env.desktop:\n{error}",
        )
        raise SystemExit(2) from error

    window = MainWindow(settings)
    window.show()
    raise SystemExit(application.exec_())


if __name__ == "__main__":
    main()
