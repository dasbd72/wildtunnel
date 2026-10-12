from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import boto3

PROVIDER = "aws-ec2"
STACK_NAME = "AwsReverseTunnelStack"
FRP_TOKEN_PARAM = "/reverse-tunnel/frp-token"


@dataclasses.dataclass
class AwsEc2Config:
    # domain/extra_port_range are provider-agnostic and sit top-level on
    # disk; region is aws_ec2-only and sits nested under "aws_ec2" (see
    # save()/load()) — the on-disk nesting doesn't mirror a split in this
    # dataclass, since there's only one provider to nest fields for today.
    domain: str
    region: str
    extra_port_range: tuple[int, int] | None = None
    no_tls: bool = False

    @classmethod
    def load(cls, path: Path) -> AwsEc2Config:
        data = json.loads(path.read_text())
        extra_port_range = data.get("extra_port_range")
        return cls(
            domain=data["domain"],
            region=data["aws_ec2"]["region"],
            extra_port_range=(
                (extra_port_range[0], extra_port_range[1])
                if extra_port_range is not None
                else None
            ),
            no_tls=data.get("no_tls", False),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "provider": PROVIDER,
            "domain": self.domain,
            "extra_port_range": (
                list(self.extra_port_range) if self.extra_port_range else None
            ),
            "no_tls": self.no_tls,
            "aws_ec2": {"region": self.region},
        }
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def describe_stack(region: str) -> dict[str, Any]:
    """Fetch the deployed stack's CloudFormation description.

    Raises botocore.exceptions.ClientError (e.g. Code "ValidationError") if
    the stack doesn't exist or the call otherwise fails.
    """
    client = boto3.client("cloudformation", region_name=region)
    stack: dict[str, Any] = client.describe_stacks(StackName=STACK_NAME)["Stacks"][0]
    return stack
