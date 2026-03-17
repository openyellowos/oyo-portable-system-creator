from __future__ import annotations

from PyQt6.QtWidgets import QLabel, QLineEdit, QVBoxLayout, QWidget


class ModePage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Mode: create / backup"))
        self.mode = QLineEdit("create")
        layout.addWidget(self.mode)


class DevicePage(QWidget):
    def __init__(self, title: str) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(title))
        self.device = QLineEdit("/dev/sdX")
        layout.addWidget(self.device)
