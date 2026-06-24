from pathlib import Path

import pytest

from lasagna.snowflake import export


def test_single_connection_toml_profile_returns_only_profile(tmp_path: Path) -> None:
    connections_file = tmp_path / "connections.toml"
    connections_file.write_text(
        '["bk03716.eu-central-1"]\naccount = "example"\n',
        encoding="utf-8",
    )

    assert export._single_connection_toml_profile(connections_file) == "bk03716.eu-central-1"


def test_single_connection_toml_profile_requires_exactly_one_profile(tmp_path: Path) -> None:
    connections_file = tmp_path / "connections.toml"
    connections_file.write_text(
        '[first]\naccount = "example"\n[second]\naccount = "example"\n',
        encoding="utf-8",
    )

    assert export._single_connection_toml_profile(connections_file) is None


def test_resolve_connection_name_prefers_explicit_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(export.CONNECTION_ENV_VAR, "from-env")

    assert export.resolve_connection_name(" explicit ") == "explicit"


def test_resolve_connection_name_uses_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(export.CONNECTION_ENV_VAR, "from-env")

    assert export.resolve_connection_name(None) == "from-env"
