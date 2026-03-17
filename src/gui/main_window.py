from __future__ import annotations

from PyQt6.QtWidgets import QApplication, QLabel, QMainWindow, QPushButton, QVBoxLayout, QWidget

from src.core.state import ExecutionState
from src.main import build_controller


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("oyo-portable-system-creator")
        self.controller = build_controller(verbose=False)
        self.status = QLabel("Ready")
        run_btn = QPushButton("Run sample create")
        run_btn.clicked.connect(self.run_sample)

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addWidget(self.status)
        layout.addWidget(run_btn)
        self.setCentralWidget(body)

    def run_sample(self) -> None:
        st = ExecutionState(mode="create", target_device="/dev/sdX")
        try:
            self.controller.run(st)
            self.status.setText("Completed")
        except Exception as exc:
            self.status.setText(str(exc))


def run_gui() -> int:
    app = QApplication([])
    w = MainWindow()
    w.resize(640, 320)
    w.show()
    return app.exec()
