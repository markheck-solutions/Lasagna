import json
import subprocess
import sys
from pathlib import Path


def test_sql_supportability_gate_reports_pass_when_no_sql_sources(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    (repo / "README.md").write_text("# Fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    artifact = repo / "build/sql-supportability.json"

    result = subprocess.run(
        [
            sys.executable,
            str(Path("scripts/quality_gates/check_sql_supportability.py").resolve()),
            "--repo-root",
            str(repo),
            "--artifact",
            str(artifact.relative_to(repo)),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Gate implementation: PASS" in result.stdout
    assert "Repo SQL supportability: PASS" in result.stdout
    assert "SQL behavior proof: NOT_REQUIRED" in result.stdout
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["sources"] == []
