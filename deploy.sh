#!/usr/bin/env bash
# One-command deploy of the SunSense API, run from AWS CloudShell:
#   git clone https://github.com/sagnikvolt/SunSense && cd SunSense && bash deploy.sh
# Creates: Lambda (Python 3.12, arm64) + API Gateway HTTP API + private S3 cache (stack "sunsense").
set -euo pipefail
REGION="${AWS_REGION:-ap-south-1}"
STACK=sunsense

sam validate --template-file template.yaml --region "$REGION" --lint
sam deploy --template-file template.yaml --stack-name "$STACK" --region "$REGION" \
  --resolve-s3 --capabilities CAPABILITY_IAM \
  --no-confirm-changeset --no-fail-on-empty-changeset

API=$(aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" --output text)
echo
echo "ApiUrl: $API"
echo "Smoke test:"
curl -s -X POST "$API" -H 'Content-Type: application/json' -H 'Origin: https://sagnikvolt.github.io' \
  -d '{"pincode":"700089","monthly_units":250,"roof_area":30}' | head -c 400; echo
