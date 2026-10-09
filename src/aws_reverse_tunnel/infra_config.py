from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import boto3

STACK_NAME = "AwsReverseTunnelStack"
FRP_TOKEN_PARAM = "/reverse-tunnel/frp-token"


@dataclasses.dataclass
class InfraConfig:
    domain: str
    region: str
    extra_port_range_start: int | None = None
    extra_port_range_end: int | None = None

    @classmethod
    def load(cls, path: Path) -> InfraConfig:
        data = json.loads(path.read_text())
        # Drop keys we no longer recognize (e.g. hosted_zone_id from configs
        # written before DNS moved to an external provider) so an older file
        # still loads instead of raising on an unexpected kwarg.
        known = {field.name for field in dataclasses.fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in known})

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(dataclasses.asdict(self), indent=2, sort_keys=True) + "\n"
        )


def describe_stack(region: str) -> dict[str, Any]:
    """Fetch the deployed stack's CloudFormation description.

    Raises botocore.exceptions.ClientError (e.g. Code "ValidationError") if
    the stack doesn't exist or the call otherwise fails.
    """
    client = boto3.client("cloudformation", region_name=region)
    stack: dict[str, Any] = client.describe_stacks(StackName=STACK_NAME)["Stacks"][0]
    return stack
