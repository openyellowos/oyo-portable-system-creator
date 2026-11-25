from __future__ import annotations

import multiprocessing
import tempfile
import unittest
from pathlib import Path

from src.core.errors import AppError
from src.infra.operation_lock import OperationLock


def _hold_lock(path: str, ready, release) -> None:
    with OperationLock(Path(path)):
        ready.set()
        release.wait(10)


class OperationLockTests(unittest.TestCase):
    def test_lock_is_shared_between_processes_and_kernel_releases_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "operation.lock"
            ctx = multiprocessing.get_context("spawn")
            ready = ctx.Event()
            release = ctx.Event()
            holder = ctx.Process(target=_hold_lock, args=(str(path), ready, release))
            holder.start()
            try:
                self.assertTrue(ready.wait(5))
                with self.assertRaises(AppError) as raised:
                    with OperationLock(path):
                        self.fail("competing workflow acquired the lock")
                self.assertEqual(raised.exception.code, "E124")
            finally:
                release.set()
                holder.join(5)
                if holder.is_alive():
                    holder.terminate()
                    holder.join()
            with OperationLock(path):
                pass

    def test_symlink_lock_path_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target"
            target.touch()
            link = Path(tmp) / "operation.lock"
            link.symlink_to(target)
            with self.assertRaises(AppError) as raised:
                with OperationLock(link):
                    self.fail("symlink lock path should not be followed")
            self.assertEqual(raised.exception.code, "E124")


if __name__ == "__main__":
    unittest.main()
