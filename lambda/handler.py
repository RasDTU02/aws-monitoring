import json
import boto3
from botocore.exceptions import ClientError

bedrock = boto3.client("bedrock-runtime", region_name="eu-north-1")

MODEL_ID = "anthropic.claude-haiku-4-5-20251001-v1:0"
MAX_TOKENS = 512


def lambda_handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")
        message = body.get("message", "").strip()

        if not message:
            return response(400, {"error": "Missing 'message' field in request body"})

        payload = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": MAX_TOKENS,
            "messages": [
                {"role": "user", "content": message}
            ]
        }

        result = bedrock.invoke_model(
            modelId=MODEL_ID,
            body=json.dumps(payload),
            contentType="application/json",
            accept="application/json"
        )

        result_body = json.loads(result["body"].read())
        reply = result_body["content"][0]["text"]

        return response(200, {"response": reply})

    except ClientError as e:
        return response(500, {"error": str(e)})
    except Exception as e:
        return response(500, {"error": f"Unexpected error: {str(e)}"})


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*"
        },
        "body": json.dumps(body)
    }
