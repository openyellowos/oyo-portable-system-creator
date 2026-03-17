from __future__ import annotations

from PyQt6.QtWidgets import QCheckBox, QComboBox, QLabel, QLineEdit, QProgressBar, QTextEdit, QVBoxLayout, QWidget


class ModePage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("1. モード選択"))
        self.mode = QComboBox()
        self.mode.addItems(["create", "backup"])
        layout.addWidget(self.mode)


class SourcePage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("2. コピー元確認（backup時のみ指定）"))
        self.source = QLineEdit("")
        self.source.setPlaceholderText("例: /mnt/source-root")
        layout.addWidget(self.source)


class TargetPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("3. コピー先デバイス"))
        self.target = QLineEdit("/dev/sdX")
        layout.addWidget(self.target)


class OptionPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("4. オプション"))
        self.yes = QCheckBox("--yes")
        self.force = QCheckBox("--force")
        self.verbose = QCheckBox("--verbose")
        layout.addWidget(self.yes)
        layout.addWidget(self.force)
        layout.addWidget(self.verbose)


class ConfirmPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("5. 最終確認"))
        self.summary = QLabel("未入力")
        layout.addWidget(self.summary)


class RunningPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("6. 実行中"))
        self.progress = QProgressBar()
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.progress)
        layout.addWidget(self.log)


class DonePage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("7. 完了"))
        self.result = QLabel("未実行")
        layout.addWidget(self.result)
