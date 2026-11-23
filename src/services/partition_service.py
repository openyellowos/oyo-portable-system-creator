from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path

from src.core.errors import AppError
from src.infra.command_runner import CommandRunner
from src.infra.logger import AppLogger


class PartitionService:
    def __init__(self, runner: CommandRunner, logger: AppLogger) -> None:
        self.runner = runner
        self.logger = logger

    def prepare_device(self, device: str) -> tuple[str, str, str]:
        try:
            self.unmount_device(device)
            self._clear_device_signatures(device)
            self.runner.run(["parted", "-s", device, "mklabel", "gpt"])
            self.runner.run(["parted", "-s", device, "mkpart", "BIOSBOOT", "1MiB", "3MiB"])
            self.runner.run(["parted", "-s", device, "set", "1", "bios_grub", "on"])
            self.runner.run(["parted", "-s", device, "mkpart", "ESP", "fat32", "3MiB", "515MiB"])
            self.runner.run(["parted", "-s", device, "set", "2", "esp", "on"])
            self.runner.run(["parted", "-s", device, "mkpart", "BOOT", "ext4", "515MiB", "1539MiB"])
            self.runner.run(["parted", "-s", device, "mkpart", "root", "ext4", "1539MiB", "100%"])
            self.runner.run(["partprobe", device], check=False)
            time.sleep(1)
        except AppError:
            raise
        except Exception as exc:
            raise AppError.translated("E301", "error.partition_prepare_failed", reason=str(exc)) from exc

        suffix = "p" if device.startswith("/dev/nvme") or device.startswith("/dev/mmcblk") else ""
        efi = f"{device}{suffix}2"
        boot = f"{device}{suffix}3"
        root = f"{device}{suffix}4"
        return efi, boot, root

    def _clear_device_signatures(self, device: str) -> None:
        self.runner.run(["dd", "if=/dev/zero", f"of={device}", "bs=1M", "count=16", "conv=fsync"], check=True)

    def make_filesystems_and_mount(
        self,
        efi_part: str,
        boot_part: str,
        root_part: str,
        workdir: Path,
        *,
        encryption_enabled: bool = False,
        luks_passphrase: str | None = None,
        mapper_name: str = "oyoport-cryptroot",
        on_mount_attempt: Callable[[Path, str], None] | None = None,
        on_mapper_open_attempt: Callable[[str, str], None] | None = None,
    ) -> tuple[Path, str, Path]:
        root_mount = workdir / "root"
        boot_mount = root_mount / "boot"
        root_mount.mkdir(parents=True, exist_ok=True)
        root_device = root_part
        try:
            self.runner.run(["mkfs.vfat", "-F", "32", "-n", "OYOPORT_EFI", efi_part])
            self.runner.run(["mkfs.ext4", "-F", "-L", "OYOPORT_BOOT", boot_part])
            if encryption_enabled:
                passphrase = luks_passphrase or ""
                self.runner.run(
                    [
                        "cryptsetup",
                        "luksFormat",
                        "--batch-mode",
                        "--type",
                        "luks2",
                        "--pbkdf",
                        "pbkdf2",
                        "--key-file",
                        "-",
                        root_part,
                    ],
                    input_text=passphrase,
                    mask_values=[passphrase],
                )
                # Never adopt an existing mapping. Cleanup independently verifies
                # the backing device to cover the race between this check and open.
                if self.mapper_backing_device(mapper_name) is not None:
                    raise RuntimeError(f"mapper {mapper_name} is already active")
                # Register immediately before open to cover an interruption after
                # activation but before run() returns.
                if on_mapper_open_attempt is not None:
                    on_mapper_open_attempt(mapper_name, root_part)
                self.runner.run(
                    ["cryptsetup", "open", "--key-file", "-", root_part, mapper_name],
                    input_text=passphrase,
                    mask_values=[passphrase],
                )
                root_device = f"/dev/mapper/{mapper_name}"
            self.runner.run(["mkfs.ext4", "-F", "-L", "OYOPORT_ROOT", root_device])
        except AppError:
            raise
        except Exception as exc:
            raise AppError.translated("E302", "error.mkfs_failed", reason=str(exc)) from exc
        try:
            # Register conservatively before each mount attempt so cleanup also
            # covers a command interrupted after the kernel mounted the device.
            if on_mount_attempt is not None:
                on_mount_attempt(root_mount, root_device)
            self.runner.run(["mount", root_device, str(root_mount)])
            boot_mount.mkdir(parents=True, exist_ok=True)
            if on_mount_attempt is not None:
                on_mount_attempt(boot_mount, boot_part)
            self.runner.run(["mount", boot_part, str(boot_mount)])
        except AppError:
            raise
        except Exception as exc:
            raise AppError.translated("E303", "error.mount_failed", reason=str(exc)) from exc
        return root_mount, root_device, boot_mount

    def mount_efi_partition(
        self,
        efi_part: str,
        root_mount: Path,
        *,
        on_mount_attempt: Callable[[Path, str], None] | None = None,
    ) -> Path:
        efi_mount = root_mount / "boot/efi"
        efi_mount.mkdir(parents=True, exist_ok=True)
        try:
            if on_mount_attempt is not None:
                on_mount_attempt(efi_mount, efi_part)
            self.runner.run(["mount", efi_part, str(efi_mount)])
        except AppError:
            raise
        except Exception as exc:
            raise AppError.translated("E303", "error.efi_mount_failed", reason=str(exc)) from exc
        return efi_mount

    def unmount_device(self, device: str) -> None:
        active_swaps = self._active_swaps()
        result = self.runner.run(
            ["lsblk", "--json", "--paths", "--output", "PATH,TYPE,MOUNTPOINTS", device],
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"could not inspect target device {device}")
        try:
            inventory = json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"could not parse target device inventory for {device}") from exc

        rows: list[tuple[str, str, list[str]]] = []

        def visit(nodes: object) -> None:
            if not isinstance(nodes, list):
                raise RuntimeError(f"invalid target device inventory for {device}")
            for node in nodes:
                if not isinstance(node, dict) or not isinstance(node.get("path"), str):
                    raise RuntimeError(f"invalid target device inventory for {device}")
                node_type = node.get("type") or ""
                mountpoints = node.get("mountpoints")
                if mountpoints is None:
                    mountpoints = []
                if isinstance(mountpoints, str):
                    mountpoints = [mountpoints]
                if not isinstance(mountpoints, list) or any(
                    mountpoint is not None and not isinstance(mountpoint, str)
                    for mountpoint in mountpoints
                ):
                    raise RuntimeError(f"invalid target device inventory for {device}")
                rows.append((node["path"], node_type, [m for m in mountpoints if m]))
                children = node.get("children")
                if children is not None:
                    visit(children)

        if not isinstance(inventory, dict) or "blockdevices" not in inventory:
            raise RuntimeError(f"invalid target device inventory for {device}")
        visit(inventory["blockdevices"])

        # Disable swap across the whole device before filesystem unmounts.
        # Mountpoints must be released globally child-before-parent: lsblk's
        # partition enumeration order is not a safe proxy for mount nesting.
        mountpoints_to_release: list[str] = []
        for path, node_type, mountpoints in reversed(rows):
            is_swap = path in active_swaps or node_type == "swap" or "[SWAP]" in mountpoints
            if is_swap:
                self._run_release(["swapoff", path])
            for mountpoint in reversed(mountpoints):
                # lsblk uses this marker for swap devices, not as a mount path.
                if mountpoint == "[SWAP]":
                    continue
                mountpoints_to_release.append(self.canonical_mountpoint(mountpoint))

        for mountpoint in sorted(
            mountpoints_to_release,
            key=lambda path: (-len(Path(path).parts), path),
        ):
            self._run_release(["umount", mountpoint])
            if self.mount_source_for(mountpoint) is not None:
                raise RuntimeError(f"mount remains active after unmount: {mountpoint}")
        for path, node_type, _ in reversed(rows):
            if node_type == "crypt":
                self._run_release(["cryptsetup", "close", Path(path).name])

        self.runner.run(["partprobe", device], check=False)
        self.runner.run(["udevadm", "settle"], check=False)

    def _run_release(self, command: list[str]) -> None:
        result = self.runner.run(command, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"resource release failed: {' '.join(command)} (exit {result.returncode})")

    @staticmethod
    def canonical_mountpoint(mountpoint: str | os.PathLike[str]) -> str:
        """Resolve aliases so mountinfo, tracking, and unmount use one path."""
        return os.path.realpath(os.path.normpath(os.fspath(mountpoint)))

    @staticmethod
    def mount_source_for(mountpoint: str) -> str | None:
        """Return a mount's source from Linux mountinfo; None means confirmed absent."""
        target = PartitionService.canonical_mountpoint(mountpoint)
        found_source: str | None = None
        for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
            before, separator, after = line.partition(" - ")
            if not separator:
                raise RuntimeError("malformed /proc/self/mountinfo entry")
            fields = before.split()
            tail = after.split()
            if len(fields) < 5 or len(tail) < 2:
                raise RuntimeError("malformed /proc/self/mountinfo entry")
            decoded_mountpoint = PartitionService._unescape_mountinfo(fields[4])
            if PartitionService.canonical_mountpoint(decoded_mountpoint) == target:
                # Mountinfo is ordered by mount ID; the last entry is the visible
                # topmost mount when a mountpoint has been over-mounted.
                found_source = PartitionService._unescape_mountinfo(tail[1])
        return found_source

    @staticmethod
    def _unescape_mountinfo(value: str) -> str:
        import re
        return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), value)

    def mapper_backing_device(self, mapper_name: str) -> str | None:
        result = self.runner.run(["cryptsetup", "status", mapper_name], check=False)
        if result.returncode != 0:
            # cryptsetup's status alone can fail for both "inactive" and an
            # inspection error. A live mapper node makes that uncertainty unsafe.
            if (Path("/dev/mapper") / mapper_name).exists():
                raise RuntimeError(f"cannot verify backing device for mapper {mapper_name}")
            return None
        for line in result.stdout.splitlines():
            key, sep, value = line.partition(":")
            if sep and key.strip().lower() == "device":
                return value.strip()
        raise RuntimeError(f"cannot determine backing device for mapper {mapper_name}")

    def close_encrypted_root(self, mapper_name: str | None) -> None:
        if not mapper_name:
            return
        result = self.runner.run(["cryptsetup", "close", mapper_name], check=False)
        if result.returncode != 0:
            raise AppError.translated(
                "E999",
                "error.command_failed",
                command=f"cryptsetup close {mapper_name}",
            )

    @staticmethod
    def _active_swaps() -> set[str]:
        try:
            lines = Path("/proc/swaps").read_text(encoding="utf-8").splitlines()
        except OSError:
            return set()
        return {line.split()[0] for line in lines[1:] if line.strip()}
