from __future__ import annotations

from pathlib import Path

from src.core.errors import AppError
from src.infra.command_runner import CommandRunner


class ChrootHelper:
    def __init__(self, runner: CommandRunner) -> None:
        self.runner = runner

    def run_in_chroot(self, root: Path, command: list[str]) -> None:
        binds = ["dev", "proc", "sys", "run"]
        try:
            for item in binds:
                self.runner.run(["mount", "--bind", f"/{item}", str(root / item)])
            chroot_cmd = ["chroot", str(root)] + command
            self.runner.run(chroot_cmd)
        except Exception as exc:
            raise AppError("E999", f"chroot 実行失敗: {exc}") from exc
        finally:
            failed = False
            for item in reversed(binds):
                result = self.runner.run(["umount", "-lf", str(root / item)], check=False)
                if result.returncode != 0:
                    failed = True
            if failed:
                raise AppError("E699", "bind mount cleanup 失敗", fatal=False)
