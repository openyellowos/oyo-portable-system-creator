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
        if "--target=x86_64-efi" in command:
            efi_binary = root / "usr/lib/grub/x86_64-efi/monolithic/grubx64.efi"
            efi_binary.parent.mkdir(parents=True, exist_ok=True)
            efi_binary.write_bytes(b"generated-efi")
        if command[:2] == ["/usr/sbin/grub-mkconfig", "-o"]:
            grub_cfg = root / "boot/grub/grub.cfg"
            grub_cfg.parent.mkdir(parents=True, exist_ok=True)
            grub_cfg.write_text("generated grub config\n", encoding="utf-8")


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
        self.service.install_grub(self.root, "/dev/sdz", "1234-ABCD")

        portable_cfg = (self.root / "boot/efi/boot/grub/grub.cfg").read_text(encoding="utf-8")
        efi_chain_cfg = (self.root / "boot/efi/EFI/BOOT/grub.cfg").read_text(encoding="utf-8")
        vendor_chain_cfg = (self.root / "boot/efi/EFI/OYOPORT/grub.cfg").read_text(encoding="utf-8")
        removable_binary = (self.root / "boot/efi/EFI/BOOT/BOOTX64.EFI").read_bytes()
        alias_binary = (self.root / "boot/efi/EFI/BOOT/grubx64.efi").read_bytes()
        installed_binary = (self.root / "boot/efi/EFI/OYOPORT/grubx64.efi").read_bytes()

        self.assertIn("search --no-floppy --fs-uuid --set=root 1234-ABCD", portable_cfg)
        self.assertIn("configfile /grub/grub.cfg", portable_cfg)
        self.assertIn("search --no-floppy --fs-uuid --set=root 1234-ABCD", efi_chain_cfg)
        self.assertIn("configfile /grub/grub.cfg", efi_chain_cfg)
        self.assertEqual(vendor_chain_cfg, efi_chain_cfg)
        self.assertEqual(removable_binary, b"generated-efi")
        self.assertEqual(alias_binary, b"generated-efi")
        self.assertEqual(installed_binary, b"generated-efi")
        self.assertEqual(
            self.chroot.calls,
            [
                (
                    self.root,
                    [
                        "/usr/sbin/grub-install",
                        "--target=i386-pc",
                        "--boot-directory=/boot",
                        "--modules=part_gpt fat ext2",
                        "--recheck",
                        "/dev/sdz",
                    ],
                ),
                (
                    self.root,
                    [
                        "/usr/sbin/grub-install",
                        "--target=x86_64-efi",
                        "--efi-directory=/boot/efi",
                        "--bootloader-id=OYOPORT",
                        "--modules=part_gpt fat ext2",
                        "--no-nvram",
                        "--removable",
                    ],
                ),
            ],
        )

    def test_refresh_grub_config_generates_root_grub_cfg(self) -> None:
        grub_defaults = self.root / "etc/default/grub"
        grub_defaults.parent.mkdir(parents=True, exist_ok=True)
        grub_defaults.write_text("GRUB_CMDLINE_LINUX=\"\"\n", encoding="utf-8")

        self.service.refresh_grub_config(self.root)

        root_cfg = (self.root / "boot/grub/grub.cfg").read_text(encoding="utf-8")
        updated_defaults = grub_defaults.read_text(encoding="utf-8")

        self.assertEqual(root_cfg, "generated grub config\n")
        self.assertEqual(updated_defaults, "GRUB_CMDLINE_LINUX=\"rootwait\"\n")
        self.assertEqual(
            self.chroot.calls,
            [
                (
                    self.root,
                    ["/usr/sbin/grub-mkconfig", "-o", "/boot/grub/grub.cfg"],
                )
            ],
        )

    def test_refresh_grub_config_preserves_existing_kernel_args_when_adding_rootwait(self) -> None:
        grub_defaults = self.root / "etc/default/grub"
        grub_defaults.parent.mkdir(parents=True, exist_ok=True)
        grub_defaults.write_text("GRUB_CMDLINE_LINUX='console=ttyS0'\n", encoding="utf-8")

        self.service.refresh_grub_config(self.root)

        updated_defaults = grub_defaults.read_text(encoding="utf-8")

        self.assertEqual(updated_defaults, "GRUB_CMDLINE_LINUX='console=ttyS0 rootwait'\n")

    def test_find_existing_efi_binary_prefers_grub_binary_over_bootx64_shim(self) -> None:
        bootx64 = self.root / "boot/efi/EFI/BOOT/BOOTX64.EFI"
        installed = self.root / "boot/efi/EFI/OYOPORT/grubx64.efi"
        bootx64.parent.mkdir(parents=True, exist_ok=True)
        installed.parent.mkdir(parents=True, exist_ok=True)
        bootx64.write_bytes(b"shim")
        installed.write_bytes(b"grub")

        found = self.service._find_existing_efi_binary(self.root)

        self.assertEqual(found, installed)

    def test_find_existing_efi_binary_prefers_installed_bootloader_over_monolithic_fallback(self) -> None:
        monolithic = self.root / "usr/lib/grub/x86_64-efi/monolithic/grubx64.efi"
        installed = self.root / "boot/efi/EFI/OYOPORT/grubx64.efi"
        monolithic.parent.mkdir(parents=True, exist_ok=True)
        installed.parent.mkdir(parents=True, exist_ok=True)
        monolithic.write_bytes(b"monolithic")
        installed.write_bytes(b"installed")

        found = self.service._find_existing_efi_binary(self.root)

        self.assertEqual(found, installed)


if __name__ == "__main__":
    unittest.main()
