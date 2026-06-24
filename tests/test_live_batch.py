from argparse import Namespace
from pathlib import Path

import pytest

from lasagna import live_batch


def test_live_batch_rejects_no_valid_ids(tmp_path: Path) -> None:
    args = Namespace(
        service_id=[],
        ids_text="not-an-id",
        ids_file=None,
        connection="sdm_runner",
        output_dir=tmp_path,
        max_service_tabs=100,
        keep_combined_csv=False,
    )

    with pytest.raises(ValueError, match="No valid IC/ICB service IDs"):
        live_batch.run_live_batch(args)


def test_live_batch_deletes_combined_csv_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}

    def fake_export(service_ids: list[str], output_path: Path, *, connection: str) -> int:
        calls["service_ids"] = service_ids
        calls["connection"] = connection
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("QID,ROW_DATA\n", encoding="utf-8")
        return 1

    def fake_generate(
        pasted_text: str,
        combined_csv_path: Path,
        *,
        output_dir: Path | None = None,
        max_service_tabs: int = 100,
    ) -> object:
        calls["pasted_text"] = pasted_text
        calls["combined_csv_exists_during_generate"] = combined_csv_path.exists()
        calls["output_dir"] = output_dir
        calls["max_service_tabs"] = max_service_tabs
        return object()

    monkeypatch.setattr(live_batch, "export_service_ids_to_combined_csv", fake_export)
    monkeypatch.setattr(live_batch, "generate_route_review_from_combined_csv", fake_generate)
    args = Namespace(
        service_id=["ic-123456", "ICB-654321"],
        ids_text="",
        ids_file=None,
        connection="sdm_runner",
        output_dir=tmp_path,
        max_service_tabs=25,
        keep_combined_csv=False,
    )

    output_dir = live_batch.run_live_batch(args)

    assert output_dir == tmp_path
    assert calls["service_ids"] == ["IC-123456", "ICB-654321"]
    assert calls["combined_csv_exists_during_generate"] is True
    assert calls["output_dir"] == tmp_path
    assert calls["max_service_tabs"] == 25
    assert not (tmp_path / "_scratch" / "lasagna_combined_export.csv").exists()
    assert not (tmp_path / "_scratch").exists()
