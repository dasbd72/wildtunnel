from __future__ import annotations

import os
import secrets

import aws_cdk as cdk

from ...paths import (
    DASHBOARD_ADMIN_PASSWORD_FILE,
    FRP_STATS_PASSWORD_FILE,
    FRP_TOKEN_FILE,
)
from .config import STACK_NAME
from .tunnel_stack import TunnelStack

FRP_STATS_USER = "dashboard"


def _get_or_create_frp_token() -> str:
    # Generated once and reused across deploys so `cdk deploy` doesn't rotate
    # the frp auth token (and force every frpc client to re-`init`) on every
    # synth.
    if FRP_TOKEN_FILE.exists():
        return FRP_TOKEN_FILE.read_text().strip()
    token = secrets.token_urlsafe(32)
    FRP_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    FRP_TOKEN_FILE.write_text(token)
    FRP_TOKEN_FILE.chmod(0o600)
    return token


def _get_or_create_frp_stats_password() -> str:
    # Same reasoning as _get_or_create_frp_token: cached so re-deploys don't
    # rotate the credential frps's webServer and the dashboard app both need
    # to agree on. Purely internal to this VPS (loopback-only), so unlike the
    # frp tunnel token nothing else outside this box ever needs to know it.
    if FRP_STATS_PASSWORD_FILE.exists():
        return FRP_STATS_PASSWORD_FILE.read_text().strip()
    password = secrets.token_urlsafe(32)
    FRP_STATS_PASSWORD_FILE.parent.mkdir(parents=True, exist_ok=True)
    FRP_STATS_PASSWORD_FILE.write_text(password)
    FRP_STATS_PASSWORD_FILE.chmod(0o600)
    return password


def _get_or_create_dashboard_admin_password() -> str:
    # Same reasoning as _get_or_create_frp_token: cached so re-deploys
    # don't rotate it. Generated client-side (rather than letting the
    # dashboard pick its own random one on first boot) so `deploy` can
    # print it to the console once provisioning is kicked off.
    if DASHBOARD_ADMIN_PASSWORD_FILE.exists():
        return DASHBOARD_ADMIN_PASSWORD_FILE.read_text().strip()
    password = secrets.token_urlsafe(24)
    DASHBOARD_ADMIN_PASSWORD_FILE.parent.mkdir(parents=True, exist_ok=True)
    DASHBOARD_ADMIN_PASSWORD_FILE.write_text(password)
    DASHBOARD_ADMIN_PASSWORD_FILE.chmod(0o600)
    return password


# CDK feature flags — previously lived in infra/cdk.json (now removed, since
# the app is invoked directly via `cdk -a`, not through a checked-out
# cdk.json). Without these, synthesis silently falls back to aws-cdk-lib's
# unflagged defaults, which can diff/replace resources on an already-deployed
# stack (e.g. installLatestAwsSdkDefault flipping the custom resource's
# InstallLatestAwsSdk property).
_FEATURE_FLAGS = {
    "@aws-cdk/aws-lambda:recognizeLayerVersion": True,
    "@aws-cdk/core:checkSecretUsage": True,
    "@aws-cdk/core:target-partitions": ["aws", "aws-cn"],
    "@aws-cdk/aws-ec2:uniqueImdsv2TemplateName": True,
    "@aws-cdk/aws-iam:minimizePolicies": True,
    "@aws-cdk/core:validateSnapshotRemovalPolicy": True,
    "@aws-cdk/aws-s3:createDefaultLoggingPolicy": True,
    "@aws-cdk/aws-route53-patters:useCertificate": True,
    "@aws-cdk/customresources:installLatestAwsSdkDefault": False,
    "@aws-cdk/aws-apigateway:disableCloudWatchRole": True,
    "@aws-cdk/core:enablePartitionLiterals": True,
    "@aws-cdk/aws-events:eventsTargetQueueSameAccount": True,
    "@aws-cdk/aws-ec2:restrictDefaultSecurityGroup": True,
    "@aws-cdk/aws-apigateway:requestValidatorUniqueId": True,
    "@aws-cdk/aws-kms:aliasNameRef": True,
    "@aws-cdk/aws-autoscaling:generateLaunchTemplateInsteadOfLaunchConfig": True,
    "@aws-cdk/core:includePrefixInUniqueNameGeneration": True,
    "@aws-cdk/aws-efs:denyAnonymousAccess": True,
    "@aws-cdk/aws-opensearchservice:enableOpensearchMultiAzWithStandby": True,
    "@aws-cdk/aws-lambda-nodejs:useLatestRuntimeVersion": True,
    "@aws-cdk/aws-efs:mountTargetOrderInsensitiveRemoval": True,
    "@aws-cdk/aws-rds:auroraClusterChangeScopeOfInstanceParameterGroupWithEachParameters": True,
    "@aws-cdk/aws-appsync:useArnForSourceApiAssociationIdentifier": True,
    "@aws-cdk/aws-rds:preventRenderingDeprecatedCredentials": True,
    "@aws-cdk/aws-codepipeline-actions:useNewDefaultBranchForCodeCommitSource": True,
    "@aws-cdk/aws-cloudwatch-actions:changeLambdaPermissionLogicalIdForLambdaAction": True,
    "@aws-cdk/aws-codepipeline:crossAccountKeyAliasStackSafeResourceName": True,
    "@aws-cdk/aws-ec2:bastionHostUseHostnameForLabel": True,
    "@aws-cdk/aws-secretsmanager:useAttachedSecretResourcePolicyForSecretTargetAttachments": True,
    "@aws-cdk/aws-redshift:columnId": True,
    "@aws-cdk/aws-stepfunctions-tasks:enableEmrServicePolicyV2": True,
}

app = cdk.App(context=_FEATURE_FLAGS)

domain_name = app.node.try_get_context("domain")
region = app.node.try_get_context("region")
if not domain_name or not region:
    raise SystemExit("missing required CDK context: domain, region")

extra_port_range_start = app.node.try_get_context("extraPortRangeStart")
extra_port_range_end = app.node.try_get_context("extraPortRangeEnd")
extra_port_range = (
    (int(extra_port_range_start), int(extra_port_range_end))
    if extra_port_range_start is not None
    else None
)

# CDK context values arrive as strings; only `-c noTls=true` is ever passed
# (see _run_cdk), so absence or any other value means TLS.
no_tls = app.node.try_get_context("noTls") == "true"

TunnelStack(
    app,
    STACK_NAME,
    env=cdk.Environment(account=os.environ["CDK_DEFAULT_ACCOUNT"], region=region),
    domain_name=domain_name,
    frp_token=_get_or_create_frp_token(),
    extra_port_range=extra_port_range,
    frp_stats_user=FRP_STATS_USER,
    frp_stats_password=_get_or_create_frp_stats_password(),
    dashboard_admin_password=_get_or_create_dashboard_admin_password(),
    no_tls=no_tls,
)

app.synth()
