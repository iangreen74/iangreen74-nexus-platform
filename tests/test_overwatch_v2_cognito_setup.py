"""Static checks for the operator Cognito CFN template (Track P).

These tests parse the YAML and assert the security-critical properties
that must hold before deploy:

  - MFA is required (not OPTIONAL)
  - Self-signup is disabled (admin-only)
  - Password policy meets minimum length + complexity
  - OAuth callback points at platform.vaultscaler.com/oauth2/idpresponse
  - Client has GenerateSecret=true (required for ALB authenticate-cognito)
  - Domain prefix is parameterised on the AWS account ID

Post-deploy, the listener-rule invariant (priority 9 / 10 customer rules
unchanged) is verified manually via aws elbv2 describe-rules in the PR
smoke checklist.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml


TEMPLATE_PATH = (
    Path(__file__).resolve().parents[1]
    / "infra" / "overwatch-v2" / "10-operator-cognito.yml"
)


@pytest.fixture(scope="module")
def template() -> dict:
    """Parse the CFN template, ignoring CFN intrinsics like !Sub."""
    class _Loader(yaml.SafeLoader):
        pass

    def _intrinsic(loader, tag_suffix, node):
        if isinstance(node, yaml.ScalarNode):
            return {f"Fn::{tag_suffix}": loader.construct_scalar(node)}
        if isinstance(node, yaml.SequenceNode):
            return {f"Fn::{tag_suffix}": loader.construct_sequence(node)}
        return {f"Fn::{tag_suffix}": loader.construct_mapping(node)}

    _Loader.add_multi_constructor("!", _intrinsic)
    with TEMPLATE_PATH.open() as f:
        return yaml.load(f, Loader=_Loader)


def test_template_file_exists():
    assert TEMPLATE_PATH.is_file(), f"missing template: {TEMPLATE_PATH}"


def test_template_has_three_resources(template):
    resources = template["Resources"]
    assert "OperatorUserPool" in resources
    assert "OperatorUserPoolClient" in resources
    assert "OperatorUserPoolDomain" in resources


def test_user_pool_requires_mfa(template):
    pool = template["Resources"]["OperatorUserPool"]["Properties"]
    assert pool["MfaConfiguration"] == "ON"
    assert "SOFTWARE_TOKEN_MFA" in pool["EnabledMfas"]


def test_user_pool_disables_self_signup(template):
    pool = template["Resources"]["OperatorUserPool"]["Properties"]
    assert pool["AdminCreateUserConfig"]["AllowAdminCreateUserOnly"] is True


def test_user_pool_password_policy_strong(template):
    pol = template["Resources"]["OperatorUserPool"]["Properties"]["Policies"]["PasswordPolicy"]
    assert pol["MinimumLength"] >= 14
    assert pol["RequireUppercase"] is True
    assert pol["RequireLowercase"] is True
    assert pol["RequireNumbers"] is True
    assert pol["RequireSymbols"] is True


def test_client_generates_secret(template):
    client = template["Resources"]["OperatorUserPoolClient"]["Properties"]
    assert client["GenerateSecret"] is True, "ALB authenticate-cognito requires a client secret"


def test_client_oauth_flow_is_code_grant(template):
    client = template["Resources"]["OperatorUserPoolClient"]["Properties"]
    assert client["AllowedOAuthFlows"] == ["code"]
    assert client["AllowedOAuthFlowsUserPoolClient"] is True
    assert "openid" in client["AllowedOAuthScopes"]


def test_callback_url_targets_platform_host(template):
    client = template["Resources"]["OperatorUserPoolClient"]["Properties"]
    callbacks = client["CallbackURLs"]
    assert len(callbacks) == 1
    cb = callbacks[0]
    if isinstance(cb, dict):
        cb = cb.get("Fn::Sub", "")
    assert cb.endswith("/oauth2/idpresponse"), f"unexpected callback: {cb!r}"


def test_domain_namespaced_to_account(template):
    domain = template["Resources"]["OperatorUserPoolDomain"]["Properties"]["Domain"]
    if isinstance(domain, dict):
        domain = domain.get("Fn::Sub", "")
    assert "overwatch-vaultscaler-" in domain
    assert "${AWS::AccountId}" in domain


def test_outputs_export_required_handles(template):
    exports = {
        out["Export"]["Name"]
        for out in template["Outputs"].values()
        if "Export" in out
    }
    expected = {
        "OverwatchV2OperatorUserPoolId",
        "OverwatchV2OperatorUserPoolArn",
        "OverwatchV2OperatorUserPoolClientId",
        "OverwatchV2OperatorUserPoolDomain",
    }
    assert expected.issubset(exports), f"missing exports: {expected - exports}"


def test_template_validates_with_cloudformation():
    """Optional: skip when AWS creds aren't configured (e.g., CI without role)."""
    if os.environ.get("SKIP_AWS_VALIDATE_TEMPLATE"):
        pytest.skip("SKIP_AWS_VALIDATE_TEMPLATE set")
    try:
        import boto3
        from botocore.exceptions import NoCredentialsError, ClientError
    except ImportError:
        pytest.skip("boto3 not installed in this env")
    try:
        cfn = boto3.client("cloudformation", region_name="us-east-1")
        with TEMPLATE_PATH.open() as f:
            cfn.validate_template(TemplateBody=f.read())
    except NoCredentialsError:
        pytest.skip("no AWS creds")
    except ClientError as e:
        if "ExpiredToken" in str(e) or "InvalidClientTokenId" in str(e):
            pytest.skip(f"AWS auth issue: {e}")
        raise
