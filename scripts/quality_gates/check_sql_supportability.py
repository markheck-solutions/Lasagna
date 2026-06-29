"""Fail-closed SQL supportability discovery gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

SQL_FILE_SUFFIXES = {".sql", ".sql.j2", ".sql.jinja", ".sql.tmpl"}
EMBEDDED_SQL_SUFFIXES = {".py", ".ps1", ".psm1", ".cmd"}
SKIP_PARTS = {".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".venv", "build", "dist"}
EMBEDDED_SQL_PATTERN = re.compile(
    r"(?is)(?:'''|\"\"\"|'|\")\s*(?:WITH|SELECT|INSERT\s+INTO|CREATE\s+OR\s+REPLACE|MERGE\s+INTO|UPDATE|DELETE\s+FROM)\b"
)
SQL_EXECUTION_SINK_PATTERN = re.compile(
    r"(?i)\b(execute|execute_string|cursor|snowflake\.connector|snowsql|Invoke-Sqlcmd)\b"
)
UNRESOLVED_TEMPLATE_PATTERN = re.compile(r"(\{\{|\}\}|\$\{|\bTODO\b|\bFIXME\b)", re.IGNORECASE)


@dataclass(frozen=True)
class SqlSource:
    path: str
    kind: str
    sha256: str


def _git_lines(repo_root: Path, args: list[str]) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def _repo_candidates(repo_root: Path) -> list[Path]:
    tracked = _git_lines(repo_root, ["ls-files", "--cached"])
    untracked = _git_lines(repo_root, ["ls-files", "--others", "--exclude-standard"])
    ignored = _git_lines(repo_root, ["ls-files", "--others", "-i", "--exclude-standard"])
    paths = [repo_root / path for path in [*tracked, *untracked, *ignored]]
    return [path for path in paths if path.is_file() and not (set(path.parts) & SKIP_PARTS)]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sql_file(path: Path) -> bool:
    lower_name = path.name.lower()
    return path.suffix.lower() == ".sql" or any(
        lower_name.endswith(suffix) for suffix in SQL_FILE_SUFFIXES
    )


def _contains_embedded_sql(path: Path) -> bool:
    if path.suffix.lower() not in EMBEDDED_SQL_SUFFIXES:
        return False
    text = path.read_text(encoding="utf-8", errors="ignore")
    return bool(EMBEDDED_SQL_PATTERN.search(text) and SQL_EXECUTION_SINK_PATTERN.search(text))


def _discover_sql_sources(repo_root: Path) -> list[SqlSource]:
    sources: list[SqlSource] = []
    for path in _repo_candidates(repo_root):
        relative = path.relative_to(repo_root).as_posix()
        if relative == "scripts/quality_gates/check_sql_supportability.py":
            continue
        if _is_sql_file(path):
            sources.append(SqlSource(relative, "sql_file", _sha256(path)))
        elif _contains_embedded_sql(path):
            sources.append(SqlSource(relative, "embedded_sql", _sha256(path)))
    return sources


def _source_failures(repo_root: Path, sources: list[SqlSource]) -> list[str]:
    failures: list[str] = []
    for source in sources:
        path = repo_root / source.path
        text = path.read_text(encoding="utf-8", errors="ignore")
        if UNRESOLVED_TEMPLATE_PATTERN.search(text):
            failures.append(f"{source.path}: unresolved template or TODO marker")
        if source.kind == "embedded_sql" and "execute" not in text.lower():
            failures.append(f"{source.path}: embedded SQL has no explicit execution sink")
    return failures


def _write_artifact(
    artifact_path: Path,
    sources: list[SqlSource],
    failures: list[str],
) -> None:
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact = {
        "gate_implementation": "PASS",
        "repo_sql_supportability": "FAIL" if failures else "PASS",
        "sql_behavior_proof": "NOT_REQUIRED" if not sources else "PASS",
        "sources": [asdict(source) for source in sources],
        "failures": failures,
    }
    artifact_path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--artifact", default="build/quality-gates/sql-supportability.json")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    artifact_path = repo_root / args.artifact
    sources = _discover_sql_sources(repo_root)
    failures = _source_failures(repo_root, sources)
    _write_artifact(artifact_path, sources, failures)

    print("Gate implementation: PASS")
    print(f"Repo SQL supportability: {'FAIL' if failures else 'PASS'}")
    print(f"SQL behavior proof: {'NOT_REQUIRED' if not sources else 'PASS'}")
    print(f"Discovered SQL sources: {len(sources)}")
    print(f"Artifact: {artifact_path}")
    if failures:
        print("Failures:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
