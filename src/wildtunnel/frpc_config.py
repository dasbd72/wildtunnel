from __future__ import annotations

import dataclasses
import json
from pathlib import Path

DEFAULT_SERVER_PORT = 7000


@dataclasses.dataclass
class FrpcConfig:
    server_addr: str
    base_domain: str
    token: str = dataclasses.field(repr=False)
    server_port: int = DEFAULT_SERVER_PORT

    @classmethod
    def load(cls, path: Path) -> FrpcConfig:
        # Tolerate unknown keys (e.g. a pre-existing settings file still
        # carrying the old "region"/"token_param" fields) instead of
        # crashing on them.
        data = json.loads(path.read_text())
        valid_fields = {field.name for field in dataclasses.fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in valid_fields})

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(dataclasses.asdict(self), indent=2, sort_keys=True) + "\n"
        )
        path.chmod(0o600)
