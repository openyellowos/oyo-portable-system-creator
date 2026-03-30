from __future__ import annotations

import logging
import string

from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.core.errors import set_error_language
from src.core.state import ExecutionState
from src.core.workflow import Workflow
from src.gui.i18n import build_translator, detect_system_language
from src.infra.chroot import ChrootHelper
from src.infra.command_runner import CommandRunner
from src.infra.logger import AppLogger
from src.services.boot_service import BootService
from src.services.copy_service import CopyService
from src.services.device_service import DeviceService
from src.services.firstboot_service import FirstbootService
from src.services.optimize_service import OptimizeService
from src.services.partition_service import PartitionService


class SignalLogHandler(logging.Handler):
    def __init__(self, emit_signal: pyqtSignal) -> None:
        super().__init__(level=logging.DEBUG)
        self.emit_signal = emit_signal
        self.setFormatter(logging.Formatter("%(asctime)s %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.emit_signal.emit(self.format(record))
        except Exception:
            return


class DeviceLoadWorker(QObject):
    finished = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, language: str) -> None:
        super().__init__()
        self.language = language

    def run(self) -> None:
        try:
            _, device_service = build_services(verbose=False, language=self.language)
            devices = device_service.list_target_devices()
            self.finished.emit(devices)
        except Exception as exc:
            self.failed.emit(str(exc))


class DiagnosticWorker(QObject):
    finished = pyqtSignal(ExecutionState)
    failed = pyqtSignal(str)
    log = pyqtSignal(str)

    def __init__(self, target_device: str, language: str, options: dict | None = None) -> None:
        super().__init__()
        self.target_device = target_device
        self.language = language
        self.options = options or {}

    def run(self) -> None:
        state = ExecutionState(mode="create", target_device=self.target_device, options=dict(self.options))
        try:
            controller, _ = build_services(verbose=False, log_signal=self.log, language=self.language)
            result = controller.precheck(state)
            self.finished.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class CreateWorker(QObject):
    finished = pyqtSignal(ExecutionState)
    failed = pyqtSignal(str)
    progress = pyqtSignal(int, str)
    log = pyqtSignal(str)

    def __init__(self, target_device: str, language: str, options: dict | None = None) -> None:
        super().__init__()
        self.target_device = target_device
        self.language = language
        self.options = options or {}

    def run(self) -> None:
        state = ExecutionState(
            mode="create",
            target_device=self.target_device,
            options={"yes": True, "force": False, "verbose": False, **self.options},
        )
        try:
            controller, _ = build_services(verbose=False, log_signal=self.log, language=self.language)
            state.add_progress_listener(self.progress.emit)
            result = controller.run(state)
            self.finished.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class ControllerFacade:
    def __init__(self, workflow: Workflow) -> None:
        self.workflow = workflow

    def precheck(self, state: ExecutionState) -> ExecutionState:
        self.workflow.precheck(state)
        return state

    def run(self, state: ExecutionState) -> ExecutionState:
        return self.workflow.run_create(state)


def build_services(
    verbose: bool,
    log_signal: pyqtSignal | None = None,
    language: str = "ja",
) -> tuple[ControllerFacade, DeviceService]:
    set_error_language(language)
    logger = AppLogger()
    logger.configure(verbose=verbose)

    if log_signal is not None:
        handler = SignalLogHandler(log_signal)
        logger.user_logger.addHandler(handler)
        logger.debug_logger.addHandler(handler)

    runner = CommandRunner(logger)
    device = DeviceService(runner, logger)
    partition = PartitionService(runner, logger)
    copy = CopyService(runner)
    chroot = ChrootHelper(runner)
    boot = BootService(runner, chroot)
    optimize = OptimizeService()
    firstboot = FirstbootService()
    workflow = Workflow(device, partition, copy, boot, optimize, firstboot, logger, language=language)
    return ControllerFacade(workflow), device


class MainWindow(QMainWindow):
    def __init__(self, language: str | None = None) -> None:
        super().__init__()
        self.language = detect_system_language() if language is None else language
        self.t = build_translator(self.language)
        self.setWindowTitle(self.t("app.title"))
        self.worker_thread: QThread | None = None
        self.worker: QObject | None = None
        self.current_worker_kind: str | None = None
        self.device_paths: list[str] = []
        self.device_records: dict[str, dict] = {}
        self.doctor_ok = False
        self.last_doctor_device = ""

        self.status_label = QLabel(self.t("status.select_device"))
        self.status_label.setObjectName("StatusLabel")

        self.description_label = QLabel(self.t("description.create_portable"))
        self.description_label.setWordWrap(True)
        self.description_label.setObjectName("DescriptionLabel")

        self.device_label = QLabel(self.t("label.device"))
        self.device_label.setFixedWidth(84)

        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(440)
        self.device_combo.currentIndexChanged.connect(self._on_device_changed)

        self.reload_button = QPushButton(self.t("button.reload"))
        self.reload_button.setFixedWidth(104)
        self.reload_button.clicked.connect(self.reload_devices)

        self.diagnose_button = QPushButton(self.t("button.diagnose"))
        self.diagnose_button.setFixedWidth(104)
        self.diagnose_button.clicked.connect(self.run_diagnostic)

        self.create_button = QPushButton(self.t("button.create"))
        self.create_button.clicked.connect(self.run_create)

        self.encryption_checkbox = QCheckBox(self.t("security.enable_encryption"))
        self.encryption_checkbox.toggled.connect(self._on_encryption_toggled)
        self.luks_password_label = QLabel(self.t("security.luks_password"))
        self.luks_password_confirm_label = QLabel(self.t("security.confirm_password"))
        self.luks_password_input = QLineEdit()
        self.luks_password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.luks_password_confirm_input = QLineEdit()
        self.luks_password_confirm_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.show_password_checkbox = QCheckBox(self.t("security.show_password"))
        self.show_password_checkbox.toggled.connect(self._on_show_password_toggled)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText(self.t("log.placeholder"))
        self.log_view.setMinimumHeight(220)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")

        self._build_ui()
        self._apply_style()
        self.reload_devices()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)

        outer = QVBoxLayout(root)
        outer.setContentsMargins(18, 14, 18, 18)
        outer.setSpacing(12)

        outer.addWidget(self.description_label)

        device_frame = QFrame()
        device_frame.setObjectName("Panel")
        device_layout = QGridLayout(device_frame)
        device_layout.setContentsMargins(12, 12, 12, 12)
        device_layout.setHorizontalSpacing(8)
        device_layout.setVerticalSpacing(4)
        device_layout.addWidget(self.device_label, 0, 0)
        device_layout.addWidget(self.device_combo, 0, 1)
        device_layout.addWidget(self.reload_button, 0, 2)
        device_layout.setColumnStretch(1, 1)
        outer.addWidget(device_frame)

        security_frame = QFrame()
        security_frame.setObjectName("Panel")
        security_layout = QGridLayout(security_frame)
        security_layout.setContentsMargins(12, 12, 12, 12)
        security_layout.setHorizontalSpacing(8)
        security_layout.setVerticalSpacing(8)
        security_layout.addWidget(QLabel(self.t("security.title")), 0, 0, 1, 2)
        security_layout.addWidget(self.encryption_checkbox, 1, 0, 1, 2)
        security_layout.addWidget(self.luks_password_label, 2, 0)
        security_layout.addWidget(self.luks_password_input, 2, 1)
        security_layout.addWidget(self.luks_password_confirm_label, 3, 0)
        security_layout.addWidget(self.luks_password_confirm_input, 3, 1)
        security_layout.addWidget(self.show_password_checkbox, 4, 1)
        security_layout.setColumnStretch(1, 1)
        outer.addWidget(security_frame)

        action_row = QHBoxLayout()
        action_row.setSpacing(12)
        action_row.addWidget(self.diagnose_button)
        action_row.addWidget(self.create_button)
        action_row.addStretch(1)
        outer.addLayout(action_row)

        outer.addWidget(self.status_label)
        outer.addWidget(self.progress_bar)

        log_frame = QFrame()
        log_frame.setObjectName("Panel")
        log_layout = QVBoxLayout(log_frame)
        log_layout.setContentsMargins(16, 16, 16, 16)
        log_layout.setSpacing(10)
        log_layout.addWidget(QLabel(self.t("log.title")))
        log_layout.addWidget(self.log_view)
        outer.addWidget(log_frame, 1)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                background: #eeece9;
                color: #111111;
                font-family: "Noto Sans CJK JP", "Noto Sans JP", sans-serif;
                font-size: 14px;
            }
            QMainWindow {
                background: #eeece9;
            }
            #StatusLabel {
                color: #111111;
                padding-top: 2px;
                padding-bottom: 2px;
                min-height: 20px;
            }
            #DescriptionLabel {
                color: #111111;
                background: transparent;
                border: none;
                padding: 0;
            }
            #Panel {
                background: #f1efec;
                border: 1px solid #d7d1ca;
                border-radius: 10px;
            }
            QComboBox, QTextEdit, QLineEdit {
                background: #fbfaf8;
                border: 1px solid #cfc8c0;
                border-radius: 8px;
                padding: 8px;
            }
            QComboBox {
                padding: 5px 8px;
                min-height: 18px;
            }
            QComboBox::drop-down {
                border: none;
                width: 28px;
            }
            QProgressBar {
                background: #e2ddd6;
                border: 1px solid #d0c8bf;
                border-radius: 8px;
                text-align: center;
                color: #111111;
                min-height: 22px;
            }
            QProgressBar::chunk {
                background: #9fb9ef;
                border-radius: 7px;
            }
            QPushButton {
                background: #f7f4f0;
                border: 1px solid #d1c9c0;
                border-radius: 8px;
                color: #111111;
                font-weight: 500;
                padding: 7px 14px;
                min-height: 18px;
            }
            QPushButton:hover {
                background: #efeae3;
            }
            QPushButton:disabled {
                background: #f3f0eb;
                border-color: #dcd5cc;
                color: #a7a099;
            }
            QTextEdit {
                selection-background-color: #cddbf8;
            }
            """
        )
        self._on_encryption_toggled(False)

    def _update_action_state(self) -> None:
        has_target = self._selected_device() is not None
        busy = self.worker_thread is not None and self.worker_thread.isRunning()
        doctor_ready = self.doctor_ok and self.last_doctor_device == (self._selected_device() or "")
        self.diagnose_button.setEnabled(has_target and not busy)
        self.create_button.setEnabled(has_target and doctor_ready and not busy)
        self.reload_button.setEnabled(not busy)
        self.device_combo.setEnabled(not busy)

    def _selected_device(self) -> str | None:
        index = self.device_combo.currentIndex()
        if index < 0 or index >= len(self.device_paths):
            return None
        return self.device_paths[index]

    def _selected_device_record(self) -> dict | None:
        path = self._selected_device()
        if path is None:
            return None
        return self.device_records.get(path)

    def _append_log(self, message: str) -> None:
        self.log_view.append(message)

    def _set_status(self, message: str) -> None:
        self.status_label.setText(message)

    def _invalidate_diagnostic(self) -> None:
        self.doctor_ok = False
        self.last_doctor_device = ""
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")
        self._update_action_state()

    def _format_device_label(self, device: dict) -> str:
        path = device.get("path") or f"/dev/{device['name']}"
        size = int(device.get("size") or 0) / (1024**3)
        device_name = self._device_name(device)
        if device_name:
            return f"{path}({device_name})   {size:.1f}GiB"
        return f"{path}   {size:.1f}GiB"

    def _device_name(self, device: dict | None) -> str:
        if not device:
            return ""
        vendor = str(device.get("vendor") or "").strip()
        model = str(device.get("model") or "").strip()
        return " ".join(part for part in [vendor, model] if part).strip()

    def _format_diagnostic_result(self, state: ExecutionState) -> str:
        source = state.metadata.get("source_path") or "/"
        required_gib = state.required_bytes / (1024**3) if state.required_bytes else 0
        target_display = self._format_device_display_from_path(state.target_device or "")

        lines = [
            self.t("diagnostic.result.ok"),
            "",
            self.t("diagnostic.source"),
            f"  {source}",
            "",
            self.t("diagnostic.target"),
            f"  {target_display}",
            "",
            self.t("diagnostic.required_capacity", required_gib=required_gib),
        ]
        return "\n".join(lines)

    def _show_message_dialog(
        self,
        title: str,
        text: str,
        icon: QMessageBox.Icon = QMessageBox.Icon.Information,
    ) -> None:
        dialog = QMessageBox(self)
        dialog.setWindowTitle(title)
        dialog.setIcon(icon)
        dialog.setText(text)
        dialog.setStandardButtons(QMessageBox.StandardButton.Ok)
        label = dialog.findChild(QLabel, "qt_msgbox_label")
        if label is not None:
            label.setWordWrap(True)
            label.setMinimumWidth(0)
            label.setMaximumWidth(280)
        dialog.exec()

    def _on_device_changed(self, _index: int) -> None:
        self._invalidate_diagnostic()

    def _on_encryption_toggled(self, checked: bool) -> None:
        self.luks_password_label.setVisible(checked)
        self.luks_password_input.setVisible(checked)
        self.luks_password_confirm_label.setVisible(checked)
        self.luks_password_confirm_input.setVisible(checked)
        self.show_password_checkbox.setVisible(checked)
        self._invalidate_diagnostic()

    def _on_show_password_toggled(self, checked: bool) -> None:
        echo_mode = QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        self.luks_password_input.setEchoMode(echo_mode)
        self.luks_password_confirm_input.setEchoMode(echo_mode)

    @staticmethod
    def _validate_luks_password(password: str) -> None:
        if password != password.strip():
            raise ValueError("surrounding_whitespace")
        if any(ch not in string.printable or ch in "\r\n\t\x0b\x0c" for ch in password):
            raise ValueError("non_ascii")

    def _build_create_options(self) -> dict:
        if not self.encryption_checkbox.isChecked():
            return {"encryption_enabled": False}

        password = self.luks_password_input.text()
        confirm = self.luks_password_confirm_input.text()
        if not password:
            raise ValueError(self.t("error.luks_password_required"))
        try:
            self._validate_luks_password(password)
        except ValueError as exc:
            if str(exc) == "surrounding_whitespace":
                raise ValueError(self.t("error.luks_password_whitespace")) from exc
            if str(exc) == "non_ascii":
                raise ValueError(self.t("error.luks_password_ascii_only")) from exc
            raise
        if password != confirm:
            raise ValueError(self.t("error.luks_password_mismatch"))
        return {
            "encryption_enabled": True,
            "luks_passphrase": password,
            "luks_mapper_name": "oyoport-cryptroot",
        }

    def reload_devices(self) -> None:
        self._invalidate_diagnostic()
        self._set_status(self.t("status.loading_devices"))
        self._start_worker(DeviceLoadWorker(self.language), self._on_devices_loaded)

    def run_diagnostic(self) -> None:
        target = self._selected_device()
        if target is None:
            return
        try:
            options = self._build_create_options()
        except ValueError as exc:
            QMessageBox.critical(self, self.t("dialog.error.title"), str(exc))
            return
        self.log_view.clear()
        self._invalidate_diagnostic()
        self._append_log(self.t("log.start_diagnostic", target=target))
        self._set_status(self.t("status.running_diagnostic"))
        self._start_worker(DiagnosticWorker(target, self.language, options), self._on_diagnostic_finished)

    def run_create(self) -> None:
        target = self._selected_device()
        if target is None:
            return
        try:
            options = self._build_create_options()
        except ValueError as exc:
            QMessageBox.critical(self, self.t("dialog.error.title"), str(exc))
            return
        if not self.doctor_ok or self.last_doctor_device != target:
            QMessageBox.warning(
                self,
                self.t("dialog.diagnostic_required.title"),
                self.t("dialog.diagnostic_required.body"),
            )
            return

        dialog = QMessageBox(self)
        dialog.setWindowTitle(self.t("dialog.confirm.title"))
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setText(self.t("dialog.confirm.body", device=self._format_selected_device_display(target)))
        dialog.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        dialog.setDefaultButton(QMessageBox.StandardButton.No)
        yes_button = dialog.button(QMessageBox.StandardButton.Yes)
        no_button = dialog.button(QMessageBox.StandardButton.No)
        if yes_button is not None:
            yes_button.setText(self.t("button.yes"))
        if no_button is not None:
            no_button.setText(self.t("button.no"))
        answer = QMessageBox.StandardButton(dialog.exec())
        if answer != QMessageBox.StandardButton.Yes:
            return

        self.log_view.clear()
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")
        self._append_log(self.t("log.start_create", target=target))
        self._set_status(self.t("status.running_create"))
        self._start_worker(CreateWorker(target, self.language, options), self._on_create_finished)

    def _start_worker(self, worker: QObject, finished_handler) -> None:
        if self.worker_thread is not None and self.worker_thread.isRunning():
            return

        self.worker_thread = QThread(self)
        self.worker = worker
        self.current_worker_kind = type(worker).__name__
        worker.moveToThread(self.worker_thread)

        self.worker_thread.started.connect(worker.run)

        if hasattr(worker, "log"):
            worker.log.connect(self._append_log)
        if hasattr(worker, "progress"):
            worker.progress.connect(self._on_progress)
        if hasattr(worker, "finished"):
            worker.finished.connect(finished_handler)
            worker.finished.connect(self.worker_thread.quit)
        if hasattr(worker, "failed"):
            worker.failed.connect(self._on_worker_failed)
            worker.failed.connect(self.worker_thread.quit)

        self.worker_thread.finished.connect(self._on_worker_stopped)
        self.worker_thread.finished.connect(worker.deleteLater)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)
        self.worker_thread.start()
        self._update_action_state()

    def _on_devices_loaded(self, devices: list[dict]) -> None:
        self.device_combo.clear()
        self.device_paths = []
        self.device_records = {}

        for device in devices:
            path = device.get("path") or f"/dev/{device['name']}"
            label = self._format_device_label(device)
            self.device_combo.addItem(label)
            self.device_paths.append(path)
            self.device_records[path] = device

        if not devices:
            self._set_status(self.t("status.no_devices"))
        else:
            self._set_status("")
        self._update_action_state()

    def _on_diagnostic_finished(self, state: ExecutionState) -> None:
        source = state.metadata.get("source_path") or "/"
        diagnostic_text = self._format_diagnostic_result(state)
        self.doctor_ok = True
        self.last_doctor_device = state.target_device or ""
        self.progress_bar.setValue(100)
        self.progress_bar.setFormat(self.t("progress.diagnostic_done"))
        self._append_log(
            self.t(
                "log.diagnostic_done",
                target=state.target_device,
                source=source,
                used=state.used_bytes,
                required=state.required_bytes,
            )
        )
        self._set_status(self.t("status.diagnostic_done"))
        self._show_message_dialog(self.t("dialog.diagnostic_result.title"), diagnostic_text)
        self._update_action_state()

    def _on_create_finished(self, state: ExecutionState) -> None:
        self._append_log(self.t("log.create_done", target=state.target_device))
        self.progress_bar.setValue(100)
        self.progress_bar.setFormat(self.t("progress.create_done"))
        self._set_status(self.t("status.create_done"))
        self._show_message_dialog(self.t("dialog.done.title"), self.t("dialog.done.body"))

    def _on_progress(self, percent: int, step: str) -> None:
        self.progress_bar.setFormat("%p%")
        self.progress_bar.setValue(percent)
        self._append_log(f"[{percent:>3}%] {step}")
        self._set_status(step)

    def _on_worker_failed(self, message: str) -> None:
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")
        self._append_log(self.t("log.failed", message=message))
        self._set_status(self.t("status.failed"))
        QMessageBox.critical(self, self.t("dialog.error.title"), message)

    def _on_worker_stopped(self) -> None:
        self.worker = None
        self.worker_thread = None
        self.current_worker_kind = None
        self._update_action_state()

    def _format_selected_device_display(self, target: str) -> str:
        return self._format_device_display_from_path(target)

    def _format_device_display_from_path(self, target: str) -> str:
        device_name = self._device_name(self.device_records.get(target))
        if not device_name:
            return target
        return f"{target} ({device_name})"


def run_gui() -> int:
    app = QApplication([])
    language = detect_system_language()
    app.setApplicationName(build_translator(language)("app.title"))
    w = MainWindow(language=language)
    w.resize(920, 520)
    w.show()
    return app.exec()
