"""Repo hygiene checks for generated artifacts, secrets, and large files."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

GENERATED_SUFFIXES = {".xlsx", ".xlsm", ".xls", ".csv", ".log", ".jsonl"}
TEXT_SUFFIXES = {
    ".cmd",
    ".md",
    ".ps1",
    ".py",
    ".toml",
    ".txt",
    ".yml",
    ".yaml",
}
SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*=\s*['\"][^'\"]{8,}['\"]"),
    re.compile(r"-----BEGIN (RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"),
)
MAX_TRACKED_FILE_BYTES = 5 * 1024 * 1024


def _git_lines(repo_root: Path, args: list[str]) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def _candidate_files(repo_root: Path) -> list[Path]:
    relative_paths = _git_lines(
        repo_root, ["ls-files", "--cached", "--others", "--exclude-standard"]
    )
    return [
        path for relative_path in relative_paths if (path := repo_root / relative_path).exists()
    ]


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _find_generated_artifacts(files: list[Path]) -> list[str]:
    return [str(path) for path in files if path.suffix.lower() in GENERATED_SUFFIXES]


def _find_large_files(files: list[Path]) -> list[str]:
    return [
        f"{path} ({path.stat().st_size} bytes)"
        for path in files
        if path.exists() and path.stat().st_size > MAX_TRACKED_FILE_BYTES
    ]


def _find_secret_hits(files: list[Path]) -> list[str]:
    hits: list[str] = []
    for path in files:
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = _read_text(path)
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                hits.append(str(path))
                break
    return hits


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    files = _candidate_files(repo_root)
    failures: list[str] = []

    generated = _find_generated_artifacts(files)
    if generated:
        failures.append("Generated artifacts tracked or unignored:\n" + "\n".join(generated))

    large = _find_large_files(files)
    if large:
        failures.append("Files exceed 5 MiB threshold:\n" + "\n".join(large))

    secrets = _find_secret_hits(files)
    if secrets:
        failures.append("Secret-like patterns found:\n" + "\n".join(secrets))

    if failures:
        print("Repo hygiene: FAIL")
        print("\n\n".join(failures))
        return 1

    print("Repo hygiene: PASS")
    print(f"Files scanned: {len(files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
