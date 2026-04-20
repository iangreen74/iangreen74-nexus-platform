#!/bin/bash
# create_pipeline_events_queue.sh — one-time bootstrap for the Overwatch
# subscriber's SQS infrastructure. Idempotent; safe to re-run.
#
# Creates:
#   1. SQS queue 'forgewing-pipeline-events'
#   2. Queue policy allowing EventBridge to publish
#   3. EventBridge rule routing forgewing.deploy.v2 to the queue
#   4. EventBridge target (the SQS queue)
#   5. IAM inline policy on Overwatch ECS task role for SQS read/delete
#
# Usage: ./scripts/create_pipeline_events_queue.sh
#
# Then set PIPELINE_EVENTS_QUEUE_URL on the aria-console ECS task def.

set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-1}"
AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:-418295677815}"
QUEUE_NAME="${QUEUE_NAME:-forgewing-pipeline-events}"
RULE_NAME="${RULE_NAME:-forgewing-pipeline-events-rule}"
BUS_NAME="${BUS_NAME:-forgewing-deploy-events}"
TASK_ROLE="${OVERWATCH_TASK_ROLE:-aria-console-task-role}"

echo "=== Overwatch Pipeline Events Bootstrap ==="
echo "Region: $AWS_REGION  Account: $AWS_ACCOUNT_ID"
echo "Queue: $QUEUE_NAME  Bus: $BUS_NAME  Role: $TASK_ROLE"
echo ""

# 1. SQS queue
echo "[1/5] SQS queue..."
QUEUE_URL=$(aws sqs get-queue-url --queue-name "$QUEUE_NAME" \
  --region "$AWS_REGION" --query QueueUrl --output text 2>/dev/null || echo "")
if [ -z "$QUEUE_URL" ]; then
  QUEUE_URL=$(aws sqs create-queue --queue-name "$QUEUE_NAME" \
    --attributes "MessageRetentionPeriod=1209600,VisibilityTimeout=60,ReceiveMessageWaitTimeSeconds=5" \
    --region "$AWS_REGION" --query QueueUrl --output text)
  echo "  Created: $QUEUE_URL"
else
  echo "  Exists: $QUEUE_URL"
fi

QUEUE_ARN=$(aws sqs get-queue-attributes --queue-url "$QUEUE_URL" \
  --attribute-names QueueArn --region "$AWS_REGION" \
  --query "Attributes.QueueArn" --output text)
echo "  ARN: $QUEUE_ARN"

# 2. Queue policy
echo "[2/5] Queue policy..."
POLICY=$(cat <<EOF
{
  "Version":"2012-10-17",
  "Statement":[{
    "Sid":"AllowEventBridgePublish",
    "Effect":"Allow",
    "Principal":{"Service":"events.amazonaws.com"},
    "Action":"sqs:SendMessage",
    "Resource":"$QUEUE_ARN",
    "Condition":{"ArnEquals":{
      "aws:SourceArn":"arn:aws:events:$AWS_REGION:$AWS_ACCOUNT_ID:rule/$BUS_NAME/$RULE_NAME"
    }}
  }]
}
EOF
)
aws sqs set-queue-attributes --queue-url "$QUEUE_URL" \
  --attributes "Policy=$(echo "$POLICY" | jq -c .)" --region "$AWS_REGION"
echo "  Applied"

# 3. EventBridge rule
echo "[3/5] EventBridge rule..."
aws events put-rule --name "$RULE_NAME" --event-bus-name "$BUS_NAME" \
  --event-pattern '{"source":["forgewing.deploy.v2"]}' \
  --state ENABLED --region "$AWS_REGION" \
  --description "Route Phase C events to Overwatch SQS" > /dev/null
echo "  $RULE_NAME ENABLED on $BUS_NAME"

# 4. EventBridge target
echo "[4/5] EventBridge target..."
aws events put-targets --rule "$RULE_NAME" --event-bus-name "$BUS_NAME" \
  --targets "Id=overwatch-sqs,Arn=$QUEUE_ARN" --region "$AWS_REGION" > /dev/null
echo "  Target attached"

# 5. IAM policy
echo "[5/5] IAM policy on $TASK_ROLE..."
IAM_DOC=$(cat <<EOF
{
  "Version":"2012-10-17",
  "Statement":[{
    "Effect":"Allow",
    "Action":["sqs:ReceiveMessage","sqs:DeleteMessage","sqs:DeleteMessageBatch",
              "sqs:GetQueueAttributes","sqs:GetQueueUrl"],
    "Resource":"$QUEUE_ARN"
  }]
}
EOF
)
aws iam put-role-policy --role-name "$TASK_ROLE" \
  --policy-name "OverwatchReadPipelineEventsQueue" \
  --policy-document "$IAM_DOC"
echo "  Policy attached"

echo ""
echo "Done. Set on ECS task definition:"
echo "  PIPELINE_EVENTS_QUEUE_URL=$QUEUE_URL"
