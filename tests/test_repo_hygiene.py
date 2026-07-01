from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_repo_hygiene_suppresses_secret_hit_details(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    secret_file = repo / "contains_secret.py"
    secret_value = "1234567890" + "abcdef"
    key_name = "api" + "_key"
    secret_file.write_text(f'{key_name} = "{secret_value}"\n', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(Path("scripts/quality_gates/check_repo_hygiene.py").resolve()),
            "--repo-root",
            str(repo),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "Secret-like patterns found in 1 file(s); details suppressed." in result.stdout
    assert "contains_secret.py" not in result.stdout
    assert secret_value not in result.stdout
