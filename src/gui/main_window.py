from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.core.state import ExecutionState
from src.core.workflow import Workflow
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

    def run(self) -> None:
        try:
            _, device_service = build_services(verbose=False)
            devices = device_service.list_target_devices()
            self.finished.emit(devices)
        except Exception as exc:
            self.failed.emit(str(exc))


class DiagnosticWorker(QObject):
    finished = pyqtSignal(ExecutionState)
    failed = pyqtSignal(str)
    log = pyqtSignal(str)

    def __init__(self, target_device: str) -> None:
        super().__init__()
        self.target_device = target_device

    def run(self) -> None:
        state = ExecutionState(mode="create", target_device=self.target_device)
        try:
            controller, _ = build_services(verbose=False, log_signal=self.log)
            result = controller.precheck(state)
            self.finished.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class CreateWorker(QObject):
    finished = pyqtSignal(ExecutionState)
    failed = pyqtSignal(str)
    progress = pyqtSignal(int, str)
    log = pyqtSignal(str)

    def __init__(self, target_device: str) -> None:
        super().__init__()
        self.target_device = target_device

    def run(self) -> None:
        state = ExecutionState(
            mode="create",
            target_device=self.target_device,
            options={"yes": True, "force": False, "verbose": False},
        )
        try:
            controller, _ = build_services(verbose=False, log_signal=self.log)
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


def build_services(verbose: bool, log_signal: pyqtSignal | None = None) -> tuple[ControllerFacade, DeviceService]:
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
    workflow = Workflow(device, partition, copy, boot, optimize, firstboot, logger)
    return ControllerFacade(workflow), device


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("oYo Portable System Creator")
        self.worker_thread: QThread | None = None
        self.worker: QObject | None = None
        self.current_worker_kind: str | None = None
        self.device_paths: list[str] = []
        self.doctor_ok = False
        self.last_doctor_device = ""

        self.status_label = QLabel("USBデバイスを選択してください。")
        self.status_label.setObjectName("StatusLabel")

        self.description_label = QLabel(
            "open.Yellow.os の現在のシステムから、BIOS / UEFI 両対応の Portable USB を作成します。"
        )
        self.description_label.setWordWrap(True)
        self.description_label.setObjectName("DescriptionLabel")

        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(520)
        self.device_combo.currentIndexChanged.connect(self._on_device_changed)

        self.reload_button = QPushButton("再読込")
        self.reload_button.clicked.connect(self.reload_devices)

        self.diagnose_button = QPushButton("診断")
        self.diagnose_button.clicked.connect(self.run_diagnostic)

        self.create_button = QPushButton("Portable USB 作成")
        self.create_button.clicked.connect(self.run_create)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("ここに処理ログを表示します。")
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
        outer.addWidget(self.status_label)

        device_frame = QFrame()
        device_frame.setObjectName("Panel")
        device_layout = QGridLayout(device_frame)
        device_layout.setContentsMargins(12, 12, 12, 12)
        device_layout.setHorizontalSpacing(12)
        device_layout.setVerticalSpacing(4)
        device_layout.addWidget(QLabel("USBデバイス"), 0, 0)
        device_layout.addWidget(self.device_combo, 0, 1)
        device_layout.addWidget(self.reload_button, 0, 2)
        outer.addWidget(device_frame)

        action_row = QHBoxLayout()
        action_row.setSpacing(12)
        action_row.addWidget(self.diagnose_button)
        action_row.addWidget(self.create_button)
        action_row.addStretch(1)
        outer.addLayout(action_row)

        outer.addWidget(self.progress_bar)

        log_frame = QFrame()
        log_frame.setObjectName("Panel")
        log_layout = QVBoxLayout(log_frame)
        log_layout.setContentsMargins(16, 16, 16, 16)
        log_layout.setSpacing(10)
        log_layout.addWidget(QLabel("ログ"))
        log_layout.addWidget(self.log_view)
        outer.addWidget(log_frame, 1)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                background: #ffffff;
                color: #2f3943;
                font-family: "Noto Sans CJK JP", "Noto Sans JP", sans-serif;
                font-size: 14px;
            }
            QMainWindow {
                background: #ffffff;
            }
            #StatusLabel {
                color: #4d5966;
                padding-bottom: 2px;
            }
            #DescriptionLabel {
                color: #3b4650;
                background: #ffffff;
                border: 1px solid #d8dee5;
                border-radius: 10px;
                padding: 10px 14px;
            }
            #Panel {
                background: #ffffff;
                border: 1px solid #d8dee5;
                border-radius: 10px;
            }
            QComboBox, QTextEdit {
                background: #ffffff;
                border: 1px solid #c9d1da;
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
                background: #f4f6f8;
                border: 1px solid #d8dee5;
                border-radius: 8px;
                text-align: center;
                color: #4d5966;
                min-height: 22px;
            }
            QProgressBar::chunk {
                background: #a9c4ff;
                border-radius: 7px;
            }
            QPushButton {
                background: #f5f7fa;
                border: 1px solid #cdd6e0;
                border-radius: 8px;
                color: #324150;
                font-weight: 500;
                padding: 7px 14px;
                min-height: 18px;
            }
            QPushButton:hover {
                background: #edf2f7;
            }
            QPushButton:disabled {
                background: #f7f8fa;
                border-color: #dde3ea;
                color: #9aa6b2;
            }
            QTextEdit {
                selection-background-color: #cfe1ff;
            }
            """
        )

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
        tran = device.get("tran") or "unknown"
        return f"{path}   {size:.1f} GiB   {tran}"

    def _format_diagnostic_result(self, state: ExecutionState) -> str:
        source = state.metadata.get("source_path") or "/"
        used_gib = state.used_bytes / (1024**3) if state.used_bytes else 0
        required_gib = state.required_bytes / (1024**3) if state.required_bytes else 0

        lines = [
            "診断結果: OK",
            "",
            "コピー元",
            f"  {source}",
            "",
            "USBデバイス",
            f"  {state.target_device}",
            "",
            "見積",
            f"  使用量: {used_gib:.1f} GiB",
            f"  必要容量: {required_gib:.1f} GiB",
            "",
            "確認項目",
            "  ✓ open.Yellow.os",
            "  ✓ root 権限",
            "  ✓ 必須コマンド",
            "  ✓ USBデバイス",
            "  ✓ 容量",
        ]
        return "\n".join(lines)

    def _on_device_changed(self, _index: int) -> None:
        self._invalidate_diagnostic()

    def reload_devices(self) -> None:
        self._invalidate_diagnostic()
        self._set_status("USBデバイス一覧を取得しています。")
        self._start_worker(DeviceLoadWorker(), self._on_devices_loaded)

    def run_diagnostic(self) -> None:
        target = self._selected_device()
        if target is None:
            return
        self.log_view.clear()
        self._invalidate_diagnostic()
        self._append_log(f"診断を開始します: target={target}")
        self._set_status("診断を実行しています。")
        self._start_worker(DiagnosticWorker(target), self._on_diagnostic_finished)

    def run_create(self) -> None:
        target = self._selected_device()
        if target is None:
            return
        if not self.doctor_ok or self.last_doctor_device != target:
            QMessageBox.warning(self, "診断未完了", "先に診断を実行し、結果を確認してください。")
            return

        answer = QMessageBox.question(
            self,
            "確認",
            (
                "選択したUSBデバイスの内容はすべて消去されます。\n\n"
                f"USBデバイス:\n  {target}\n\n"
                "Portable System を作成します。続行しますか？"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self.log_view.clear()
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")
        self._append_log(f"Portable USB 作成を開始します: target={target}")
        self._set_status("Portable USB を作成しています。")
        self._start_worker(CreateWorker(target), self._on_create_finished)

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

        for device in devices:
            path = device.get("path") or f"/dev/{device['name']}"
            label = self._format_device_label(device)
            self.device_combo.addItem(label)
            self.device_paths.append(path)

        if not devices:
            self._set_status("使用可能な USB デバイスがありません。")
        else:
            self._set_status("USBデバイスを選択して診断または作成を実行してください。")
        self._update_action_state()

    def _on_diagnostic_finished(self, state: ExecutionState) -> None:
        source = state.metadata.get("source_path") or "/"
        diagnostic_text = self._format_diagnostic_result(state)
        self.doctor_ok = True
        self.last_doctor_device = state.target_device or ""
        self.progress_bar.setValue(100)
        self.progress_bar.setFormat("診断完了")
        self._append_log(
            "診断完了: "
            f"target={state.target_device} "
            f"source={source} "
            f"used={state.used_bytes} "
            f"required={state.required_bytes}"
        )
        self._set_status("診断が完了しました。")
        QMessageBox.information(self, "診断結果", diagnostic_text)
        self._update_action_state()

    def _on_create_finished(self, state: ExecutionState) -> None:
        self._append_log(f"作成完了: target={state.target_device}")
        self.progress_bar.setValue(100)
        self.progress_bar.setFormat("作成完了")
        self._set_status("Portable USB の作成が完了しました。")
        QMessageBox.information(self, "完了", "Portable USB の作成が完了しました。")

    def _on_progress(self, percent: int, step: str) -> None:
        self.progress_bar.setFormat("%p%")
        self.progress_bar.setValue(percent)
        self._append_log(f"[{percent:>3}%] {step}")
        self._set_status(step)

    def _on_worker_failed(self, message: str) -> None:
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")
        self._append_log(f"失敗: {message}")
        self._set_status("処理に失敗しました。")
        QMessageBox.critical(self, "エラー", message)

    def _on_worker_stopped(self) -> None:
        self.worker = None
        self.worker_thread = None
        self.current_worker_kind = None
        self._update_action_state()


def run_gui() -> int:
    app = QApplication([])
    app.setApplicationName("oYo Portable System Creator")
    w = MainWindow()
    w.resize(920, 520)
    w.show()
    return app.exec()
