# overleaf_fs/gui/main_window.py

from __future__ import annotations

import sys
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QLabel,
)


class MainWindow(QMainWindow):
    """
    Main window for the Overleaf Project Explorer GUI.

    For now this is just a placeholder window so we can verify
    packaging, installation, and the CLI entry point. We will
    later replace the central widget with the project tree and
    project list views.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Overleaf Project Explorer")

        central = QWidget(self)
        layout = QVBoxLayout(central)

        label = QLabel("Overleaf Project Explorer (stub GUI)", central)
        label.setAlignment(Qt.AlignCenter)

        layout.addWidget(label)
        central.setLayout(layout)

        self.setCentralWidget(central)
        self.resize(900, 600)


def run() -> None:
    """
    Start the Qt application and show the main window.

    This is intended for programmatic use:

        from overleaf_fs.gui.main_window import run
        run()
    """
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    window = MainWindow()
    window.show()

    # Start the event loop.
    app.exec()


def main() -> None:
    """
    Console-script entry point.

    This is what `overleaf-fs` calls after installation.
    Keeping it tiny avoids mixing CLI parsing into the rest
    of the codebase.
    """
    run()