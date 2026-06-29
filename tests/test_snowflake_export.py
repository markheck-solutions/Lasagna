from pathlib import Path

import pytest

from lasagna.snowflake import export


class StreamingCursor:
    def __init__(self, batches: list[list[tuple[str, str]]]) -> None:
        self.description = [("QID",), ("ROW_DATA",)]
        self._batches = list(batches)
        self.fetchmany_sizes: list[int] = []
        self.closed = False

    def fetchmany(self, size: int) -> list[tuple[str, str]]:
        self.fetchmany_sizes.append(size)
        if not self._batches:
            return []
        return self._batches.pop(0)

    def fetchall(self) -> list[tuple[str, str]]:
        raise AssertionError("fetchall must not be used for final export rows")

    def close(self) -> None:
        self.closed = True


class StreamingConnection:
    def __init__(self, cursors: list[StreamingCursor]) -> None:
        self.cursors = cursors
        self.closed = False

    def execute_string(self, sql_text: str) -> list[StreamingCursor]:
        assert sql_text
        return self.cursors

    def close(self) -> None:
        self.closed = True


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


def test_execute_combined_export_streams_rows_without_fetchall() -> None:
    cursor = StreamingCursor([[("Q1", "row-a")], [("Q2", "row-b")]])
    conn = StreamingConnection([cursor])

    rows = export.execute_combined_export(conn, "select 1")

    assert rows == [("Q1", "row-a"), ("Q2", "row-b")]
    assert cursor.fetchmany_sizes == [export.EXPORT_FETCH_BATCH_SIZE] * 3
    assert cursor.closed is True


def test_export_service_ids_streams_final_rows_to_csv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cursor = StreamingCursor([[("Q1", "row-a")], [("Q2", "row-b")]])
    conn = StreamingConnection([cursor])
    output_path = tmp_path / "combined.csv"

    def fake_connect(connection: str | None = None) -> StreamingConnection:
        assert connection == "profile"
        return conn

    monkeypatch.setattr(export, "connect_with_connection_name", fake_connect)

    count = export.export_service_ids_to_combined_csv(
        ["IC-123456"], output_path, connection="profile"
    )

    assert count == 2
    assert output_path.read_text(encoding="utf-8") == "QID,ROW_DATA\nQ1,row-a\nQ2,row-b\n"
    assert cursor.closed is True
    assert conn.closed is True
