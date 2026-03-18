from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from src.core.errors import AppError
from src.services.boot_service import BootService


@dataclass
class DummyResult:
    command: list[str]
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


class DummyRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(self, command: list[str], check: bool = True) -> DummyResult:
        self.calls.append(command)
        return DummyResult(command=command)


class DummyChroot:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, list[str]]] = []

    def run_in_chroot(self, root: Path, command: list[str]) -> None:
        self.calls.append((root, command))


class BootServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.runner = DummyRunner()
        self.chroot = DummyChroot()
        self.service = BootService(self.runner, self.chroot)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_install_grub_requires_root_uuid(self) -> None:
        with self.assertRaises(AppError) as ctx:
            self.service.install_grub(self.root, "/dev/sdz", "UNKNOWN")
        self.assertEqual(ctx.exception.code, "E501")

    def test_install_grub_writes_portable_efi_layout(self) -> None:
        source_efi_binary = self.root / "boot/efi/EFI/debian/grubx64.efi"
        source_efi_binary.parent.mkdir(parents=True, exist_ok=True)
        source_efi_binary.write_bytes(b"copied-efi")

        self.service.install_grub(self.root, "/dev/sdz", "1234-ABCD")

        root_cfg = (self.root / "boot/grub/grub.cfg").read_text(encoding="utf-8")
        portable_cfg = (self.root / "boot/efi/boot/grub/grub.cfg").read_text(encoding="utf-8")
        efi_chain_cfg = (self.root / "boot/efi/EFI/BOOT/grub.cfg").read_text(encoding="utf-8")
        vendor_chain_cfg = (self.root / "boot/efi/EFI/debian/grub.cfg").read_text(encoding="utf-8")
        removable_binary = (self.root / "boot/efi/EFI/BOOT/BOOTX64.EFI").read_bytes()
        alias_binary = (self.root / "boot/efi/EFI/BOOT/grubx64.efi").read_bytes()
        installed_binary = (self.root / "boot/efi/EFI/OYOPORT/grubx64.efi").read_bytes()

        self.assertEqual(root_cfg, portable_cfg)
        self.assertIn("menuentry 'open.Yellow.os Portable'", portable_cfg)
        self.assertIn("search --no-floppy --set=root --file /vmlinuz", portable_cfg)
        self.assertIn("linux /vmlinuz root=UUID=1234-ABCD rootwait quiet splash", portable_cfg)
        self.assertIn("initrd /initrd.img", portable_cfg)
        self.assertIn("search --no-floppy --set=root --file /boot/grub/grub.cfg", efi_chain_cfg)
        self.assertIn("configfile /boot/grub/grub.cfg", efi_chain_cfg)
        self.assertEqual(vendor_chain_cfg, efi_chain_cfg)
        self.assertEqual(removable_binary, b"copied-efi")
        self.assertEqual(alias_binary, b"copied-efi")
        self.assertEqual(installed_binary, b"copied-efi")
        self.assertEqual(
            self.chroot.calls,
            [
                (
                    self.root,
                    [
                        "/usr/sbin/grub-install",
                        "--target=i386-pc",
                        "--boot-directory=/boot/efi/boot",
                        "--modules=part_gpt fat ext2",
                        "--recheck",
                        "/dev/sdz",
                    ],
                )
            ],
        )

    def test_install_grub_fails_without_efi_binary(self) -> None:
        with self.assertRaises(AppError) as ctx:
            self.service.install_grub(self.root, "/dev/sdz", "1234-ABCD")
        self.assertEqual(ctx.exception.code, "E501")


if __name__ == "__main__":
    unittest.main()
