from __future__ import annotations

from PyQt6.QtWidgets import QCheckBox, QComboBox, QLabel, QLineEdit, QProgressBar, QTextEdit, QVBoxLayout, QWidget

from src.gui.i18n import build_translator, detect_system_language


class ModePage(QWidget):
    def __init__(self, language: str | None = None) -> None:
        super().__init__()
        t = build_translator(language or detect_system_language())
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(t("wizard.mode.title")))
        self.mode = QComboBox()
        self.mode.addItems(["create", "backup"])
        layout.addWidget(self.mode)


class SourcePage(QWidget):
    def __init__(self, language: str | None = None) -> None:
        super().__init__()
        t = build_translator(language or detect_system_language())
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(t("wizard.source.title")))
        self.source = QLineEdit("")
        self.source.setPlaceholderText(t("wizard.source.placeholder"))
        layout.addWidget(self.source)


class TargetPage(QWidget):
    def __init__(self, language: str | None = None) -> None:
        super().__init__()
        t = build_translator(language or detect_system_language())
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(t("wizard.target.title")))
        self.target = QLineEdit("/dev/sdX")
        layout.addWidget(self.target)


class OptionPage(QWidget):
    def __init__(self, language: str | None = None) -> None:
        super().__init__()
        t = build_translator(language or detect_system_language())
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(t("wizard.options.title")))
        self.yes = QCheckBox("--yes")
        self.force = QCheckBox("--force")
        self.verbose = QCheckBox("--verbose")
        layout.addWidget(self.yes)
        layout.addWidget(self.force)
        layout.addWidget(self.verbose)


class ConfirmPage(QWidget):
    def __init__(self, language: str | None = None) -> None:
        super().__init__()
        t = build_translator(language or detect_system_language())
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(t("wizard.confirm.title")))
        self.summary = QLabel(t("wizard.confirm.empty"))
        layout.addWidget(self.summary)


class RunningPage(QWidget):
    def __init__(self, language: str | None = None) -> None:
        super().__init__()
        t = build_translator(language or detect_system_language())
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(t("wizard.running.title")))
        self.progress = QProgressBar()
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.progress)
        layout.addWidget(self.log)


class DonePage(QWidget):
    def __init__(self, language: str | None = None) -> None:
        super().__init__()
        t = build_translator(language or detect_system_language())
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(t("wizard.done.title")))
        self.result = QLabel(t("wizard.done.empty"))
        layout.addWidget(self.result)
