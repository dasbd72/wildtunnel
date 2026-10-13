import pytest

from wildtunnel.dashboard.formatting import format_bytes


@pytest.mark.parametrize(
    ("byte_count", "formatted"),
    [
        (0, "0 B"),
        (100, "100 B"),
        (1023, "1023 B"),
        (1024, "1.0 KiB"),
        (1536, "1.5 KiB"),
        (1024**2, "1.0 MiB"),
        (1024**3, "1.0 GiB"),
    ],
)
def test_format_bytes_scales_with_iec_units(byte_count: int, formatted: str) -> None:
    assert format_bytes(byte_count) == formatted
