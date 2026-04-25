#!/bin/bash
# Overwatch V2 sandbox bootstrap (cloud-init UserData).
#
# CANONICAL: this file is the readable copy. The UserData block in
# 08-sandbox-instance.yml is a byte-for-byte mirror. Edit both together
# or the next stack update will silently regress one of them.
#
# What this installs on Amazon Linux 2023:
#   docker (daemon enabled)        -for ephemeral container builds
#   git, gh                        -for repo work; gh from cli.github.com
#                                    repo because AL2023 default repos
#                                    don't ship gh
#   python3-pip, boto3, requests,
#     pytest                       -for V2 mutation tools
#   awscli, jq                     -for AWS scripting
#
# Marker: /opt/sandbox-ready  (date + instance-id; check from SSM to
# confirm bootstrap finished)
# Log:    /var/log/sandbox-bootstrap.log

set -euo pipefail
exec > >(tee -a /var/log/sandbox-bootstrap.log) 2>&1

echo "[$(date -Iseconds)] sandbox bootstrap starting"

dnf update -y
dnf install -y docker git python3-pip jq awscli unzip dnf-plugins-core

# gh is not in AL2023 default repos. Add the official gh-cli repo first.
dnf config-manager --add-repo https://cli.github.com/packages/rpm/gh-cli.repo
dnf install -y gh

systemctl enable --now docker
usermod -aG docker ssm-user || true

python3 -m pip install --quiet boto3 requests pytest

INSTANCE_ID=$(curl -s http://169.254.169.254/latest/meta-data/instance-id || echo "unknown")
echo "$(date -Iseconds) $INSTANCE_ID" > /opt/sandbox-ready
chmod 644 /opt/sandbox-ready

echo "[$(date -Iseconds)] sandbox bootstrap complete"
