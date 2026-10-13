from __future__ import annotations

_BYTE_UNITS = ("B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB", "ZiB", "YiB")


def format_bytes(byte_count: int) -> str:
    """Format a byte count using powers of 1024 and IEC unit labels."""
    value = float(byte_count)
    unit = 0
    while abs(value) >= 1024 and unit < len(_BYTE_UNITS) - 1:
        value /= 1024
        unit += 1

    if unit == 0:
        return f"{byte_count} B"
    return f"{value:.1f} {_BYTE_UNITS[unit]}"
