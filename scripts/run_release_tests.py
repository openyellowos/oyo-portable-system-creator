#!/usr/bin/env python3
"""Run the offline suite and fail the release gate on skips or zero tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import TextIO


ROOT = Path(__file__).resolve().parents[1]


class ReleaseTextTestResult(unittest.TextTestResult):
    """Treat skipped tests as incomplete validation, not successful tests."""

    def wasSuccessful(self) -> bool:
        return super().wasSuccessful() and not self.skipped


def run_suite(suite: unittest.TestSuite, *, stream: TextIO, verbosity: int = 2) -> int:
    """Return nonzero when no tests ran, any test failed, or any test skipped."""
    test_count = suite.countTestCases()
    if test_count == 0:
        print("release test gate failed: no tests were discovered", file=stream)
        return 1

    runner = unittest.TextTestRunner(
        stream=stream,
        verbosity=verbosity,
        resultclass=ReleaseTextTestResult,
    )
    result = runner.run(suite)
    if result.skipped:
        print(
            f"release test gate failed: {len(result.skipped)} test(s) were skipped",
            file=stream,
        )
    return 0 if result.wasSuccessful() else 1


def main() -> int:
    sys.path.insert(0, str(ROOT))
    suite = unittest.defaultTestLoader.discover(
        start_dir=str(ROOT / "tests"),
    )
    return run_suite(suite, stream=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
