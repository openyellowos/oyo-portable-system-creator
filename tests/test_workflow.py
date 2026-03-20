from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.core.state import ExecutionState
from src.core.workflow import Workflow


class DummyLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def info(self, msg: str) -> None:
        self.messages.append(msg)


class DummyRunnerResult:
    def __init__(self, stdout: str = "") -> None:
        self.stdout = stdout


class DummyRunner:
    def run(self, command: list[str], check: bool = False) -> DummyRunnerResult:
        if command[:3] == ["blkid", "-s", "UUID"]:
            return DummyRunnerResult(stdout="DUMMY-UUID\n")
        return DummyRunnerResult(stdout="")


class DummyDeviceService:
    def __init__(self) -> None:
        self.runner = DummyRunner()

    def check_os(self) -> None:
        return None

    def check_root(self) -> None:
        return None

    def check_required_commands(self) -> None:
        return None

    def validate_target_device(self, target_device: str) -> None:
        return None

    def estimate_required_bytes(self, copy_bytes: int) -> int:
        return copy_bytes

    def check_capacity(self, target_device: str, required: int) -> None:
        return None


class DummyPartitionService:
    def __init__(self, work_root: Path) -> None:
        self.work_root = work_root

    def prepare_device(self, target_device: str) -> tuple[str, str]:
        return ("/dev/fake-efi", "/dev/fake-root")

    def make_filesystems_and_mount(self, efi: str, root: str, workdir: Path) -> tuple[Path, Path]:
        root_mount = self.work_root / "root"
        efi_mount = self.work_root / "efi"
        root_mount.mkdir(parents=True, exist_ok=True)
        efi_mount.mkdir(parents=True, exist_ok=True)
        return (root_mount, efi_mount)


class DummyCopyService:
    def __init__(self) -> None:
        self.rsync_calls: list[tuple[str, Path, str]] = []

    def resolve_source(self, mode: str, source_device: str | None = None) -> str:
        return "/"

    def estimate_copy_bytes(self, source: str, mode: str) -> int:
        return 1024

    def rsync_copy(self, source: str, target_root: Path, mode: str) -> None:
        self.rsync_calls.append((source, target_root, mode))

    def write_fstab(self, target_root: Path, root_uuid: str, efi_uuid: str) -> None:
        return None


class DummyBootService:
    def install_grub(self, root_mount: Path, target_device: str, root_uuid: str) -> None:
        return None

    def update_initramfs(self, root_mount: Path) -> None:
        return None


class DummyOptimizeService:
    def apply(self, target_root: Path) -> None:
        return None


class DummyFirstbootService:
    def install(self, target_root: Path) -> None:
        return None


class WorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.work_root = Path(self.tempdir.name)
        self.copy_service = DummyCopyService()
        self.logger = DummyLogger()
        self.workflow = Workflow(
            DummyDeviceService(),
            DummyPartitionService(self.work_root),
            self.copy_service,
            DummyBootService(),
            DummyOptimizeService(),
            DummyFirstbootService(),
            self.logger,
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_run_create_runs_rsync_twice(self) -> None:
        state = ExecutionState(mode="create", target_device="/dev/sdz")

        self.workflow.run_create(state)

        self.assertEqual(len(self.copy_service.rsync_calls), 2)
        self.assertEqual(self.copy_service.rsync_calls[0][0], "/")
        self.assertEqual(self.copy_service.rsync_calls[1][0], "/")
        self.assertEqual(self.copy_service.rsync_calls[0][2], "create")
        self.assertEqual(self.copy_service.rsync_calls[1][2], "create")
        self.assertTrue(any("システムをコピー (1/2)" in msg for msg in self.logger.messages))
        self.assertTrue(any("システムを再同期 (2/2)" in msg for msg in self.logger.messages))


if __name__ == "__main__":
    unittest.main()
