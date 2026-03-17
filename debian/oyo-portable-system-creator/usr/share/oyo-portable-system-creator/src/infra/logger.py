from __future__ import annotations

import logging
import uuid
from pathlib import Path

USER_LOG_PATH = Path("/var/log/oyo-portable-system-creator.log")
DEBUG_LOG_PATH = Path("/var/log/oyo-portable-system-creator-debug.log")


class AppLogger:
    def __init__(self, run_id: str | None = None) -> None:
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.user_logger = logging.getLogger(f"oyo.user.{self.run_id}")
        self.debug_logger = logging.getLogger(f"oyo.debug.{self.run_id}")
        self._configured = False

    def configure(self, verbose: bool = False) -> None:
        if self._configured:
            return
        self.user_logger.setLevel(logging.INFO)
        self.debug_logger.setLevel(logging.DEBUG)
        user_handler = self._safe_file_handler(USER_LOG_PATH)
        debug_handler = self._safe_file_handler(DEBUG_LOG_PATH)
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)

        fmt = logging.Formatter("%(asctime)s [%(levelname)s] [%(name)s] %(message)s")
        for h in (user_handler, debug_handler, console_handler):
            h.setFormatter(fmt)

        self.user_logger.addHandler(user_handler)
        self.user_logger.addHandler(console_handler)
        self.debug_logger.addHandler(debug_handler)
        self._configured = True

    def _safe_file_handler(self, path: Path) -> logging.Handler:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            return logging.FileHandler(path)
        except OSError:
            fallback = Path("./") / path.name
            return logging.FileHandler(fallback)

    def info(self, msg: str) -> None:
        self.user_logger.info(msg)
        self.debug_logger.info(msg)

    def debug(self, msg: str) -> None:
        self.debug_logger.debug(msg)

    def warning(self, msg: str) -> None:
        self.user_logger.warning(msg)
        self.debug_logger.warning(msg)

    def error(self, msg: str) -> None:
        self.user_logger.error(msg)
        self.debug_logger.error(msg)
