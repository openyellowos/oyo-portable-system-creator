from __future__ import annotations

import json
import os
import re
import stat
import time
from collections.abc import Callable
from pathlib import Path

from src.core.errors import AppError
from src.infra.command_runner import CommandRunner
from src.infra.logger import AppLogger
from src.infra.mountinfo import read_mountinfo_entries


class PartitionService:
    def __init__(self, runner: CommandRunner, logger: AppLogger) -> None:
        self.runner = runner
        self.logger = logger

    def prepare_device(
        self,
        device: str,
        *,
        expected_identity: dict[str, int | None] | None = None,
    ) -> tuple[str, str, str]:
        try:
            # Never adopt or release resources that appeared after precheck.
            # Re-inspect at the destructive boundary and fail closed instead.
            try:
                verified_identity = self.assert_no_target_residuals(
                    device, expected_identity=expected_identity
                )
                # Older test doubles and compatible overrides may not return
                # the snapshot. In production the inspection returns it; an
                # explicit precheck identity is the only safe fallback.
                if not isinstance(verified_identity, dict):
                    verified_identity = expected_identity
                if not isinstance(verified_identity, dict):
                    raise RuntimeError("target identity snapshot unavailable before preparation")
                final_identity = self._block_identity(device)
                if not self._same_identity(final_identity, verified_identity):
                    raise RuntimeError(f"target device identity changed before preparation: {device}")
                if expected_identity is not None and not self._same_identity(final_identity, expected_identity):
                    raise RuntimeError(f"target device identity changed since validation: {device}")
            except AppError:
                raise
            except Exception as exc:
                raise AppError.translated("E125", "error.residual_resources", reason=str(exc)) from exc
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

    def assert_no_target_residuals(
        self,
        device: str,
        *,
        expected_identity: dict[str, int | None] | None = None,
    ) -> dict[str, int | None]:
        """Fail closed if the selected physical device still owns active resources."""
        requested_before = self._block_identity(device)
        if expected_identity is not None and not self._same_identity(requested_before, expected_identity):
            raise RuntimeError(f"target device identity changed since validation: {device}")
        result = self.runner.run(
            ["lsblk", "--json", "--paths", "--tree", "--output", "PATH,TYPE,MOUNTPOINTS,MAJ:MIN", device],
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"could not inspect target device {device}")
        try:
            inventory = json.loads(result.stdout)
            roots = inventory["blockdevices"]
            if not isinstance(roots, list) or len(roots) != 1 or not isinstance(roots[0], dict):
                raise ValueError("expected exactly one target device")
            rows: list[tuple[str, str, list[str], str]] = []

            def visit(nodes: object) -> None:
                if not isinstance(nodes, list):
                    raise ValueError("invalid block-device children")
                for node in nodes:
                    if (
                        not isinstance(node, dict)
                        or not isinstance(node.get("path"), str)
                        or not node["path"].startswith("/dev/")
                        or not isinstance(node.get("type"), str)
                        or not node["type"]
                        or not isinstance(node.get("maj:min"), str)
                        or not re.fullmatch(r"[0-9]+:[0-9]+", node["maj:min"])
                    ):
                        raise ValueError("invalid block-device row")
                    mountpoints = node.get("mountpoints") or []
                    if isinstance(mountpoints, str):
                        mountpoints = [mountpoints]
                    if not isinstance(mountpoints, list) or any(
                        item is not None and not isinstance(item, str) for item in mountpoints
                    ):
                        raise ValueError("invalid block-device mountpoints")
                    rows.append((node["path"], node["type"], [item for item in mountpoints if item], node["maj:min"]))
                    children = node.get("children")
                    if children is not None:
                        visit(children)

            visit(roots)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"could not parse target device inventory for {device}: {exc}") from exc

        requested_after = self._block_identity(device)
        if not self._same_identity(requested_before, requested_after):
            raise RuntimeError(f"target device identity changed during inspection: {device}")
        if expected_identity is not None and not self._same_identity(requested_after, expected_identity):
            raise RuntimeError(f"target device identity changed since validation: {device}")
        if not rows or rows[0][3] != self._majmin(requested_after):
            raise RuntimeError(f"target inventory identity does not match selected device: {device}")
        target_identities: set[int] = set()
        for index, (path, _, _, inventory_majmin) in enumerate(rows):
            identity = self._block_identity(path)
            if self._majmin(identity) != inventory_majmin:
                raise RuntimeError(f"target inventory identity changed during inspection: {path}")
            if index == 0 and not self._same_identity(identity, expected_identity or requested_after):
                raise RuntimeError(f"target inventory identity changed during inspection: {path}")
            target_identities.add(os.makedev(identity["major"], identity["minor"]))
        residuals: list[str] = []
        for path, node_type, mountpoints, _ in rows:
            if node_type.lower() in {"crypt", "swap"}:
                residuals.append(f"{node_type} mapping {path}")
            for mountpoint in mountpoints:
                if mountpoint == "[SWAP]":
                    residuals.append(f"active swap {path}")
                elif mountpoint:
                    residuals.append(f"mount {mountpoint} from {path}")

        # Match /proc/swaps by kernel block identity, not a possibly renamed
        # /dev path. A failed snapshot is not equivalent to an empty swap table.
        for swap_path in self._active_swaps():
            try:
                swap_info = os.stat(swap_path)
            except FileNotFoundError:
                # The path may have been renamed since /proc/swaps was read; the
                # selected device's lsblk rows above still identify its live nodes.
                continue
            if not stat.S_ISBLK(swap_info.st_mode):
                # Swap files are not block-device identities. If one is on a
                # mounted target filesystem, that target mount was rejected above.
                continue
            swap_identity = swap_info.st_rdev
            if swap_identity in target_identities:
                residuals.append(f"active swap {swap_path}")
        if residuals:
            raise RuntimeError("target device has active resources: " + "; ".join(dict.fromkeys(residuals)))
        return requested_after

    @staticmethod
    def _majmin(identity: dict[str, int | None]) -> str:
        return f"{identity['major']}:{identity['minor']}"

    @staticmethod
    def _same_identity(left: dict[str, int | None], right: dict[str, int | None]) -> bool:
        if left.get("major") != right.get("major") or left.get("minor") != right.get("minor"):
            return False
        expected_diskseq = right.get("diskseq")
        return expected_diskseq is None or left.get("diskseq") == expected_diskseq

    @staticmethod
    def _block_identity(path: str) -> dict[str, int | None]:
        info = os.stat(path)
        if not stat.S_ISBLK(info.st_mode):
            raise RuntimeError(f"target inventory contains a non-block device: {path}")
        major, minor = os.major(info.st_rdev), os.minor(info.st_rdev)
        diskseq: int | None = None
        diskseq_path = Path("/sys/dev/block") / f"{major}:{minor}" / "diskseq"
        try:
            diskseq = int(diskseq_path.read_text(encoding="ascii").strip())
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"could not read {diskseq_path}: {exc}") from exc
        return {"major": major, "minor": minor, "diskseq": diskseq}

    def assert_mapper_available(self, mapper_name: str) -> None:
        """Do not format a target if the requested mapper name is already active."""
        backing = self.mapper_backing_device(mapper_name)
        if backing is not None:
            raise RuntimeError(f"LUKS mapper {mapper_name} is already active on {backing}")

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
        for decoded_mountpoint, decoded_source in read_mountinfo_entries():
            if PartitionService.canonical_mountpoint(decoded_mountpoint) == target:
                # Preserve kernel order: the last matching entry is topmost.
                found_source = decoded_source
        return found_source

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
        except OSError as exc:
            raise RuntimeError(f"could not inspect active swap devices: {exc}") from exc
        if not lines or not lines[0].startswith("Filename"):
            raise RuntimeError("malformed /proc/swaps snapshot")
        swaps: set[str] = set()
        for line in lines[1:]:
            if not line.strip():
                continue
            fields = line.split()
            if len(fields) < 5 or not fields[0].startswith("/"):
                raise RuntimeError("malformed /proc/swaps entry")
            swaps.add(fields[0])
        return swaps
