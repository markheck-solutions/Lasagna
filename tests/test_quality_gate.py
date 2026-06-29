from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


class QualityGateTests(unittest.TestCase):
    def test_checker_compile_mode_runs_for_script_changes(self) -> None:
        script = Path("scripts/quality_gates/check_lasagna_supportability.py")
        result = subprocess.run(
            [sys.executable, str(script), "compile", "."],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
