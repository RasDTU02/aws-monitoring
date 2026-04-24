import json
import time
import boto3
from botocore.exceptions import ClientError

bedrock = boto3.client("bedrock-runtime", region_name="eu-north-1")
sns = boto3.client("sns", region_name="eu-north-1")

MODEL_ID = "eu.anthropic.claude-sonnet-4-6"
MAX_TOKENS = 512
SNS_TOPIC_ARN = "arn:aws:sns:eu-north-1:348542648420:bedrock-api-notifications"


def lambda_handler(event, context):
    start = time.time()
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
        latency_ms = int((time.time() - start) * 1000)

        notify(message, reply, latency_ms, 200)

        return response(200, {"response": reply})

    except ClientError as e:
        latency_ms = int((time.time() - start) * 1000)
        notify(message if 'message' in dir() else "unknown", str(e), latency_ms, 500)
        return response(500, {"error": str(e)})
    except Exception as e:
        latency_ms = int((time.time() - start) * 1000)
        notify(message if 'message' in dir() else "unknown", str(e), latency_ms, 500)
        return response(500, {"error": f"Unexpected error: {str(e)}"})


def notify(message, reply, latency_ms, status_code):
    try:
        status = "✅ Success" if status_code == 200 else "❌ Error"
        subject = f"[Bedrock API] {status} — {latency_ms}ms"
        body = (
            f"Status: {status} ({status_code})\n"
            f"Latency: {latency_ms}ms\n\n"
            f"Message:\n{message}\n\n"
            f"Response:\n{reply}"
        )
        sns.publish(TopicArn=SNS_TOPIC_ARN, Subject=subject, Message=body)
    except Exception:
        pass


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*"
        },
        "body": json.dumps(body)
    }
