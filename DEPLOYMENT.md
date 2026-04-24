# Bedrock Chat API — Deployment Guide

## Prerequisites

1. **AWS CLI** installed and configured (`aws configure`)
2. **AWS SAM CLI** installed → https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html
3. **Bedrock model access** enabled in your AWS account:
   - Go to AWS Console → Bedrock → Model access
   - Enable **Claude 3 Haiku**

---

## Project Structure

```
bedrock-api/
├── template.yaml        # SAM/CloudFormation template
├── lambda/
│   └── handler.py       # Lambda function code
└── DEPLOYMENT.md        # This file
```

---

## Deploy

```bash
cd bedrock-api

# Build
sam build

# Deploy (first time — guided setup)
sam deploy --guided
```

Follow the prompts:
- **Stack name**: `bedrock-chat-api`
- **Region**: `eu-north-1`
- **Confirm changeset**: Yes

After deploy, SAM will print your API endpoint URL:
```
ApiEndpoint: https://xxxxxxxxxx.execute-api.eu-north-1.amazonaws.com/Prod/chat
```

---

## Test the API

```bash
curl -X POST https://YOUR_API_URL/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What is AWS Bedrock?"}'
```

Expected response:
```json
{
  "response": "AWS Bedrock is a fully managed service that provides..."
}
```

---

## Tear Down (optional)

```bash
sam delete --stack-name bedrock-chat-api
```

---

## Notes

- The Lambda runs in `eu-north-1` by default — change `region_name` in `handler.py` if needed.
- Max response tokens is set to `512` — adjust in `handler.py` if needed.
- No authentication is added — for production, add an API key or Cognito authorizer.
