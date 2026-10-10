from __future__ import annotations

import dataclasses
import json
from pathlib import Path

_VALID_PROTOS = {"http", "tcp", "udp"}


@dataclasses.dataclass
class Service:
    target: str
    proto: str = "http"
    remote_port: int | None = None

    def __post_init__(self) -> None:
        if self.proto not in _VALID_PROTOS:
            raise ValueError(
                f"unsupported proto {self.proto!r}; expected one of {sorted(_VALID_PROTOS)}"
            )
        if self.proto == "http":
            if self.remote_port is not None:
                raise ValueError("remote_port is only valid for tcp/udp proxies")
        elif self.remote_port is None:
            raise ValueError(f"remote_port is required for {self.proto} proxies")


def load_services(path: Path) -> dict[str, Service]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    services: dict[str, Service] = {}
    for name, value in raw.items():
        services[name] = (
            Service(target=value) if isinstance(value, str) else Service(**value)
        )
    return services


def save_services(services: dict[str, Service], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {name: dataclasses.asdict(service) for name, service in services.items()}
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
