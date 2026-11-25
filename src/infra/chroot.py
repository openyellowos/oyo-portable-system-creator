from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path

from src.core.errors import AppError
from src.infra.command_runner import CommandRunner


class ChrootHelper:
    def __init__(self, runner: CommandRunner) -> None:
        self.runner = runner

    def run_in_chroot(
        self,
        root: Path,
        command: list[str],
        *,
        on_bind_cleanup_failure: Callable[[], None] | None = None,
    ) -> None:
        binds = ["dev", "proc", "sys", "run"]
        run_error: Exception | None = None
        try:
            for item in binds:
                self.runner.run(["mount", "--bind", f"/{item}", str(root / item)])
            chroot_cmd = ["chroot", str(root)] + command
            self.runner.run(chroot_cmd)
        except Exception as exc:
            run_error = exc
        finally:
            failed = False
            cleanup_scope = getattr(self.runner, "allow_cleanup_after_cancel", None)
            with cleanup_scope() if cleanup_scope is not None else nullcontext():
                for item in reversed(binds):
                    try:
                        result = self.runner.run(["umount", "-lf", str(root / item)], check=False)
                        if result.returncode != 0:
                            failed = True
                            if on_bind_cleanup_failure is not None:
                                on_bind_cleanup_failure()
                    except BaseException as exc:
                        # Immediately record failure before cancellation can
                        # bypass the later E699 summary.
                        failed = True
                        if on_bind_cleanup_failure is not None:
                            on_bind_cleanup_failure()
                        if not isinstance(exc, Exception):
                            raise
            if failed:
                # Preserve this safety-critical error even when the chroot
                # command also failed; Workflow must not remove the workdir.
                raise AppError.translated(
                    "E699", "error.bind_cleanup_failed", fatal=False
                ) from run_error

        if run_error is not None:
            if isinstance(run_error, AppError) and run_error.code == "E123":
                raise run_error
            raise AppError.translated("E999", "error.chroot_run_failed", reason=str(run_error)) from run_error
