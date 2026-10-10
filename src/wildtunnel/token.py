from __future__ import annotations

import boto3


def fetch_token(param_name: str, region: str) -> str:
    client = boto3.client("ssm", region_name=region)
    response = client.get_parameter(Name=param_name, WithDecryption=True)
    value = response["Parameter"].get("Value")
    if value is None:
        raise ValueError(f"parameter {param_name} has no value")
    return value
