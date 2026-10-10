import boto3
from moto import mock_aws

from wildtunnel.token import fetch_token


@mock_aws
def test_fetches_and_decrypts_secure_string() -> None:
    client = boto3.client("ssm", region_name="ap-northeast-1")
    client.put_parameter(
        Name="/reverse-tunnel/frp-token",
        Value="super-secret",
        Type="SecureString",
    )

    result = fetch_token("/reverse-tunnel/frp-token", region="ap-northeast-1")

    assert result == "super-secret"
