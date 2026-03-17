from __future__ import annotations

from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QMainWindow, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from src.core.state import ExecutionState
from src.gui.wizard_pages import ConfirmPage, DonePage, ModePage, OptionPage, RunningPage, SourcePage, TargetPage
from src.main import build_controller


class WorkflowWorker(QObject):
    finished = pyqtSignal(ExecutionState)
    failed = pyqtSignal(str)

    def __init__(self, state: ExecutionState, verbose: bool) -> None:
        super().__init__()
        self.state = state
        self.verbose = verbose

    def run(self) -> None:
        try:
            controller = build_controller(verbose=self.verbose)
            result = controller.run(self.state)
            self.finished.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("oyo-portable-system-creator")
        self.worker_thread: QThread | None = None
        self.worker: WorkflowWorker | None = None

        self.mode_page = ModePage()
        self.source_page = SourcePage()
        self.target_page = TargetPage()
        self.option_page = OptionPage()
        self.confirm_page = ConfirmPage()
        self.running_page = RunningPage()
        self.done_page = DonePage()

        self.stack = QStackedWidget()
        for page in (
            self.mode_page,
            self.source_page,
            self.target_page,
            self.option_page,
            self.confirm_page,
            self.running_page,
            self.done_page,
        ):
            self.stack.addWidget(page)

        self.back_btn = QPushButton("戻る")
        self.next_btn = QPushButton("次へ")
        self.run_btn = QPushButton("実行")

        self.back_btn.clicked.connect(self.back_page)
        self.next_btn.clicked.connect(self.next_page)
        self.run_btn.clicked.connect(self.run_workflow)

        button_bar = QHBoxLayout()
        button_bar.addWidget(self.back_btn)
        button_bar.addWidget(self.next_btn)
        button_bar.addWidget(self.run_btn)

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addWidget(self.stack)
        layout.addLayout(button_bar)
        self.setCentralWidget(body)
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        idx = self.stack.currentIndex()
        self.back_btn.setEnabled(idx > 0)
        self.next_btn.setEnabled(idx < 4)
        self.run_btn.setEnabled(idx == 4)

    def back_page(self) -> None:
        self.stack.setCurrentIndex(max(0, self.stack.currentIndex() - 1))
        self._sync_buttons()

    def next_page(self) -> None:
        if self.stack.currentIndex() == 3:
            self._refresh_confirm_summary()
        self.stack.setCurrentIndex(min(4, self.stack.currentIndex() + 1))
        self._sync_buttons()

    def _refresh_confirm_summary(self) -> None:
        self.confirm_page.summary.setText(
            f"mode={self.mode_page.mode.currentText()} "
            f"source={self.source_page.source.text() or '(none)'} "
            f"target={self.target_page.target.text()} "
            f"yes={self.option_page.yes.isChecked()} "
            f"force={self.option_page.force.isChecked()}"
        )

    def run_workflow(self) -> None:
        state = ExecutionState(
            mode=self.mode_page.mode.currentText(),
            source_device=self.source_page.source.text() or None,
            target_device=self.target_page.target.text(),
            options={
                "yes": self.option_page.yes.isChecked(),
                "force": self.option_page.force.isChecked(),
                "verbose": self.option_page.verbose.isChecked(),
            },
        )

        self.stack.setCurrentWidget(self.running_page)
        self.running_page.log.clear()
        self.running_page.progress.setRange(0, 0)
        self.running_page.log.append("開始: workflow を実行します")
        self.back_btn.setEnabled(False)
        self.next_btn.setEnabled(False)
        self.run_btn.setEnabled(False)

        self.worker_thread = QThread(self)
        self.worker = WorkflowWorker(state, verbose=self.option_page.verbose.isChecked())
        self.worker.moveToThread(self.worker_thread)

        self.worker_thread.started.connect(self.worker.run)
        self.worker.finished.connect(self._on_workflow_finished)
        self.worker.failed.connect(self._on_workflow_failed)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.failed.connect(self.worker_thread.quit)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)
        self.worker_thread.start()

    def _on_workflow_finished(self, state: ExecutionState) -> None:
        self.running_page.progress.setRange(0, 100)
        self.running_page.progress.setValue(state.progress_percent)
        self.running_page.log.append(f"完了: step={state.current_step} progress={state.progress_percent}%")
        self.done_page.result.setText("成功: portable system 作成/バックアップが完了しました")
        self.stack.setCurrentWidget(self.done_page)

    def _on_workflow_failed(self, message: str) -> None:
        self.running_page.progress.setRange(0, 100)
        self.running_page.progress.setValue(0)
        self.running_page.log.append(f"失敗: {message}")
        self.done_page.result.setText(f"失敗: {message}")
        self.stack.setCurrentWidget(self.done_page)



def run_gui() -> int:
    app = QApplication([])
    w = MainWindow()
    w.resize(800, 520)
    w.show()
    return app.exec()
