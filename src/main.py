from __future__ import annotations

import argparse

from src.core.controller import Controller
from src.core.errors import AppError, to_exit_code
from src.core.state import ExecutionState
from src.core.workflow import Workflow
from src.infra.chroot import ChrootHelper
from src.infra.command_runner import CommandRunner
from src.infra.logger import AppLogger
from src.services.boot_service import BootService
from src.services.copy_service import CopyService
from src.services.device_service import DeviceService
from src.services.firstboot_service import FirstbootService
from src.services.optimize_service import OptimizeService
from src.services.partition_service import PartitionService


def build_controller(verbose: bool) -> Controller:
    logger = AppLogger()
    logger.configure(verbose=verbose)
    runner = CommandRunner(logger)
    device = DeviceService(runner, logger)
    partition = PartitionService(runner, logger)
    copy = CopyService(runner)
    chroot = ChrootHelper(runner)
    boot = BootService(runner, chroot)
    optimize = OptimizeService()
    firstboot = FirstbootService()
    workflow = Workflow(device, partition, copy, boot, optimize, firstboot)
    return Controller(workflow)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="oyo-portable-system-cli")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--target", required=True)
        p.add_argument("--source")
        p.add_argument("--yes", action="store_true")
        p.add_argument("--force", action="store_true")
        p.add_argument("--dry-run", action="store_true")
        p.add_argument("--verbose", action="store_true")

    add_common(sub.add_parser("create"))
    add_common(sub.add_parser("backup"))
    return parser


def run_cli(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    controller = build_controller(verbose=args.verbose)
    state = ExecutionState(
        mode=args.command,
        source_device=args.source,
        target_device=args.target,
        options={"yes": args.yes, "force": args.force, "dry_run": args.dry_run},
    )

    if args.dry_run:
        print(f"dry-run: mode={state.mode} target={state.target_device} source={state.source_device}")
        return 0

    try:
        controller.run(state)
        print("completed")
        return 0
    except AppError as exc:
        print(str(exc))
        return to_exit_code(exc.code)
    except Exception as exc:
        print(f"E999: {exc}")
        return to_exit_code("E999")


def main() -> int:
    return run_cli()


if __name__ == "__main__":
    raise SystemExit(main())
