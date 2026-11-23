from __future__ import annotations

import unittest
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

    def run(self, command: list[str], *, check: bool = True) -> FakeResult:
        self.calls.append(command)
        if command[0] == "umount" and command[-1].endswith("/dev") and self.failed_unmount:
            return FakeResult(32)
        if command[0] == "chroot" and self.failed_chroot:
            raise RuntimeError("chroot command failed")
        return FakeResult()


class ChrootBindReleaseTests(unittest.TestCase):
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

    def test_chroot_command_failure_without_bind_failure_keeps_e999(self) -> None:
        runner = FakeRunner(failed_chroot=True)
        with self.assertRaises(AppError) as found:
            ChrootHelper(runner).run_in_chroot(Path("/fake/root"), ["true"])
        self.assertEqual(found.exception.code, "E999")


if __name__ == "__main__":
    unittest.main()
