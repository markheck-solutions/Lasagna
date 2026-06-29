"""Run Lasagna local validation gates."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ValidationStep:
    name: str
    command: list[str]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _steps() -> list[ValidationStep]:
    python = sys.executable
    return [
        ValidationStep("compile", [python, "-m", "compileall", "-q", "src", "tests", "scripts"]),
        ValidationStep(
            "sql_supportability",
            [
                python,
                "scripts/quality_gates/check_sql_supportability.py",
                "--repo-root",
                ".",
                "--artifact",
                "build/quality-gates/sql-supportability.json",
            ],
        ),
        ValidationStep(
            "repo_hygiene",
            [python, "scripts/quality_gates/check_repo_hygiene.py", "--repo-root", "."],
        ),
        ValidationStep("ruff", [python, "-m", "ruff", "check", "src", "tests", "scripts"]),
        ValidationStep(
            "ruff_format", [python, "-m", "ruff", "format", "--check", "src", "tests", "scripts"]
        ),
        ValidationStep("mypy", [python, "-m", "mypy", "src", "tests", "scripts"]),
        ValidationStep(
            "powershell_parser",
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                "$files = Get-ChildItem scripts -Filter *.ps1 -Recurse; "
                "foreach ($f in $files) { "
                "$tokens=$null; $errors=$null; "
                "[System.Management.Automation.Language.Parser]::ParseFile($f.FullName,[ref]$tokens,[ref]$errors) > $null; "
                "if ($errors.Count) { throw $f.FullName } "
                "}; 'parser ok'",
            ],
        ),
        ValidationStep("pytest", [python, "-m", "pytest", "tests", "-q"]),
        ValidationStep(
            "package_build",
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                "scripts/work_pc/build_lasagna_package.ps1",
            ],
        ),
        ValidationStep("diff_check", ["git", "diff", "--check"]),
    ]


def main() -> int:
    repo_root = _repo_root()
    for step in _steps():
        print(f"== {step.name} ==")
        result = subprocess.run(step.command, cwd=repo_root)
        if result.returncode != 0:
            print(f"{step.name}: FAIL")
            return result.returncode
        print(f"{step.name}: PASS")
    print("Lasagna local validation: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
