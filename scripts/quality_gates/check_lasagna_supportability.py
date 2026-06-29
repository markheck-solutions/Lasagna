from __future__ import annotations

import argparse
import os
import py_compile
import subprocess
import sys
from pathlib import Path

VAGUE_NAMES = {"utils", "helpers", "common", "misc", "stuff", "shared"}
SKIPPED_PARTS = {".git", ".venv", "__pycache__", ".pytest_cache", "build", "dist"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=(
            "lint",
            "format",
            "typecheck",
            "complexity",
            "architecture",
            "tests",
            "compile",
            "sql",
        ),
    )
    parser.add_argument(
        "scope", nargs="*", help="repo-wide scope marker; pass . for governed coverage"
    )
    args = parser.parse_args(argv)
    checks = {
        "lint": run_lint,
        "format": run_format,
        "typecheck": run_typecheck,
        "complexity": run_complexity,
        "architecture": run_architecture,
        "tests": run_tests,
        "compile": run_compile,
        "sql": run_sql,
    }
    return checks[args.mode]()


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        check=True,
        capture_output=True,
        text=False,
    )
    paths = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        path = Path(raw.decode("utf-8", errors="replace"))
        if path.is_file() and not (set(path.parts) & SKIPPED_PARTS):
            paths.append(path)
    return sorted(paths)


def python_files() -> list[Path]:
    return [path for path in tracked_files() if path.suffix == ".py"]


def source_python_files() -> list[Path]:
    return [path for path in python_files() if path.parts and path.parts[0] == "src"]


def test_required_python_files() -> list[Path]:
    return [path for path in python_files() if path.parts and path.parts[0] in {"scripts", "src"}]


def run_lint() -> int:
    if run_compile() != 0:
        return 1
    if has_pyproject_section("[tool.ruff]"):
        if install_dev_dependencies() != 0:
            return 1
        return run([sys.executable, "-m", "ruff", "check", "."])
    print("PASS lint: Python syntax checked; no Ruff config present")
    return 0


def run_format() -> int:
    base = os.environ.get("TARGET_BASE_SHA", "")
    head = os.environ.get("TARGET_HEAD_SHA", "")
    if is_sha(base) and is_sha(head):
        return run(["git", "diff", "--check", base, head])
    return run(["git", "diff", "--check"])


def run_typecheck() -> int:
    if run_compile() != 0:
        return 1
    if has_pyproject_section("[tool.mypy]"):
        if install_dev_dependencies() != 0:
            return 1
        return run([sys.executable, "-m", "mypy"])
    print("PASS typecheck: Python syntax checked; no mypy config present")
    return 0


def run_complexity() -> int:
    if not has_pyproject_section("[tool.ruff.lint.mccabe]"):
        print("FAIL complexity: Ruff McCabe/C901 config missing", file=sys.stderr)
        return 1
    if install_dev_dependencies() != 0:
        return 1
    return run([sys.executable, "-m", "ruff", "check", "--select", "C901", "."])


def run_architecture() -> int:
    failures = []
    for path in tracked_files():
        for part in path.parts[:-1]:
            if part in VAGUE_NAMES:
                failures.append(f"{path}: vague folder name '{part}'")
    for path in python_files():
        if path.parts[0] not in {"scripts", "src", "tests"}:
            failures.append(f"{path}: Python file outside scripts/src/tests")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("PASS architecture: no vague folders or unowned Python locations")
    return 0


def run_tests() -> int:
    if not test_required_python_files():
        print("PASS tests: no scripts/src Python files present")
        return 0
    if not Path("tests").is_dir():
        print("FAIL tests: scripts/src Python files exist but tests/ is missing", file=sys.stderr)
        return 1
    if has_pyproject_section("[tool.pytest.ini_options]"):
        if install_dev_dependencies() != 0:
            return 1
        return run([sys.executable, "-m", "pytest", "tests"])
    return run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"])


def run_compile() -> int:
    failures = []
    for path in python_files():
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            failures.append(f"{path}: {exc.msg}")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"PASS compile: {len(python_files())} Python files checked")
    return 0


def run_sql() -> int:
    sql_files = tracked_sql_files()
    if not sql_files:
        print("PASS sql: no SQL files present")
        return 0
    if not sql_test_present():
        print("FAIL sql: SQL files exist but no SQL-focused tests were found", file=sys.stderr)
        return 1
    failures = select_star_failures(sql_files)
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"PASS sql: {len(sql_files)} SQL files checked")
    return 0


def tracked_sql_files() -> list[Path]:
    return [path for path in tracked_files() if path.suffix.lower() == ".sql"]


def sql_test_present() -> bool:
    test_names = [
        path.as_posix().lower()
        for path in tracked_files()
        if path.parts and path.parts[0] == "tests"
    ]
    return any("sql" in name for name in test_names)


def select_star_failures(sql_files: list[Path]) -> list[str]:
    failures = []
    for path in sql_files:
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        if "select *" in text:
            failures.append(f"{path}: SELECT * is not supportable")
    return failures


def install_dev_dependencies() -> int:
    marker = Path(".lasagna-quality-deps-installed")
    if marker.exists():
        return 0
    if not Path("requirements-dev.txt").exists():
        return 0
    result = run([sys.executable, "-m", "pip", "install", "-r", "requirements-dev.txt"])
    if result == 0:
        marker.write_text("installed\n", encoding="utf-8")
    return result


def has_pyproject_section(section: str) -> bool:
    path = Path("pyproject.toml")
    return path.exists() and section in path.read_text(encoding="utf-8", errors="replace")


def is_sha(value: str) -> bool:
    return len(value) == 40 and all(char in "0123456789abcdef" for char in value)


def run(command: list[str]) -> int:
    completed = subprocess.run(command)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
