from lasagna.domain.service_ids import parse_service_id_text, unique_valid_service_ids


def test_service_id_parser_accepts_ic_icb_and_dedupes_normalized_repeats() -> None:
    parsed = parse_service_id_text("ic-123456\nICB-654321, IC-123456 bad ICB-654321")

    assert [
        (item.input_order, item.input_text, item.normalized_id, item.is_valid) for item in parsed
    ] == [
        (1, "ic-123456", "IC-123456", True),
        (2, "ICB-654321", "ICB-654321", True),
        (3, "IC-123456", "IC-123456", True),
        (4, "bad", "BAD", False),
        (5, "ICB-654321", "ICB-654321", True),
    ]
    assert parsed[2].duplicate_of == 1
    assert parsed[4].duplicate_of == 2
    assert unique_valid_service_ids(parsed) == ["IC-123456", "ICB-654321"]
