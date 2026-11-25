from __future__ import annotations

import unittest
from contextlib import contextmanager
from pathlib import Path

from src.core.errors import AppError
from src.infra.chroot import ChrootHelper


class FakeResult:
    def __init__(self, code: int = 0) -> None:
        self.returncode = code


class FakeRunner:
    def __init__(self, *, failed_unmount: bool = False, failed_chroot: bool = False) -> None:
        self.failed_unmount = failed_unmount
        self.failed_chroot = failed_chroot
        self.calls: list[list[str]] = []
        self.unmount_count = 0
        self.interrupt_second_unmount = False

    def run(self, command: list[str], *, check: bool = True) -> FakeResult:
        self.calls.append(command)
        if command[0] == "umount":
            self.unmount_count += 1
            if self.interrupt_second_unmount and self.unmount_count == 2:
                raise KeyboardInterrupt()
            if (command[-1].endswith("/dev") and self.failed_unmount) or (
                self.interrupt_second_unmount and self.unmount_count == 1
            ):
                return FakeResult(32)
        if command[0] == "chroot" and self.failed_chroot:
            raise RuntimeError("chroot command failed")
        return FakeResult()


class ChrootBindReleaseTests(unittest.TestCase):
    def test_cancelled_chroot_releases_binds_and_preserves_e123(self) -> None:
        class CancelRunner(FakeRunner):
            cleanup_allowed = False

            @contextmanager
            def allow_cleanup_after_cancel(self):
                self.cleanup_allowed = True
                try:
                    yield
                finally:
                    self.cleanup_allowed = False

            def run(self, command: list[str], *, check: bool = True) -> FakeResult:
                if command[0] == "chroot":
                    raise AppError.translated("E123", "error.operation_cancelled")
                if command[0] == "umount" and not self.cleanup_allowed:
                    raise AppError.translated("E123", "error.operation_cancelled")
                return super().run(command, check=check)

        runner = CancelRunner()
        with self.assertRaises(AppError) as found:
            ChrootHelper(runner).run_in_chroot(Path("/fake/root"), ["true"])
        self.assertEqual(found.exception.code, "E123")
        self.assertEqual(runner.unmount_count, 4)

    def test_failed_bind_release_reports_e699(self) -> None:
        runner = FakeRunner(failed_unmount=True)
        with self.assertRaises(AppError) as found:
            ChrootHelper(runner).run_in_chroot(Path("/fake/root"), ["true"])
        self.assertEqual(found.exception.code, "E699")
        self.assertEqual(len([c for c in runner.calls if c[0] == "umount"]), 4)

    def test_bind_failure_not_hidden_by_chroot_command_failure(self) -> None:
        runner = FakeRunner(failed_unmount=True, failed_chroot=True)
        with self.assertRaises(AppError) as found:
            ChrootHelper(runner).run_in_chroot(Path("/fake/root"), ["true"])
        self.assertEqual(found.exception.code, "E699")
        self.assertIsInstance(found.exception.__cause__, RuntimeError)

    def test_prior_bind_failure_is_latched_before_keyboard_interrupt(self) -> None:
        runner = FakeRunner()
        runner.interrupt_second_unmount = True
        failures: list[str] = []
        with self.assertRaises(KeyboardInterrupt):
            ChrootHelper(runner).run_in_chroot(
                Path("/fake/root"), ["true"],
                on_bind_cleanup_failure=lambda: failures.append("failed"),
            )
        self.assertEqual(runner.unmount_count, 2)
        self.assertEqual(failures, ["failed", "failed"])

    def test_chroot_command_failure_without_bind_failure_keeps_e999(self) -> None:
        runner = FakeRunner(failed_chroot=True)
        with self.assertRaises(AppError) as found:
            ChrootHelper(runner).run_in_chroot(Path("/fake/root"), ["true"])
        self.assertEqual(found.exception.code, "E999")


if __name__ == "__main__":
    unittest.main()
