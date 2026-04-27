"""
monitor_and_report.py

Monitors AWS CloudWatch for 500 errors on a Lambda-backed API Gateway
(bedrock-api, eu-north-1), then creates GitHub Issues with an AI-generated
angry/urgent diagnostic report via AWS Bedrock.

Usage:
    python monitor_and_report.py          # loop every 60 seconds
    python monitor_and_report.py --once   # single poll cycle then exit
"""

import argparse
import json
import os
import time
from datetime import datetime, timezone, timedelta

import boto3
import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
REGION = "eu-north-1"
API_GATEWAY_NAME = "bedrock-api"
API_STAGE = "Prod"
LAMBDA_LOG_GROUP_PREFIX = "/aws/lambda/bedrock-api-BedrockChatFunction"
LAMBDA_SOURCE_PATH = "/home/ranoj/bedrock-api/lambda/handler.py"
BEDROCK_MODEL_ID = "eu.anthropic.claude-sonnet-4-6"
GITHUB_REPO = "RasDTU02/aws-monitoring"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/issues"
POLL_INTERVAL_SECONDS = 60
METRIC_WINDOW_MINUTES = 5
LOG_WINDOW_MINUTES = 10
MAX_LOG_LINES = 10

# ---------------------------------------------------------------------------
# AWS clients (credentials from environment / OIDC / local profile)
# ---------------------------------------------------------------------------
cloudwatch = boto3.client("cloudwatch", region_name=REGION)
logs = boto3.client("logs", region_name=REGION)
bedrock_runtime = boto3.client("bedrock-runtime", region_name=REGION)


# ---------------------------------------------------------------------------
# Step 1 – Detect 500 errors via CloudWatch Metrics
# ---------------------------------------------------------------------------
def get_5xx_error_count() -> int:
    """
    Query CloudWatch Metrics for 5XXError count on the API Gateway
    named 'bedrock-api' (stage 'Prod') over the last METRIC_WINDOW_MINUTES.

    Returns the total count of 5XX errors (int).
    """
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(minutes=METRIC_WINDOW_MINUTES)

    response = cloudwatch.get_metric_statistics(
        Namespace="AWS/ApiGateway",
        MetricName="5XXError",
        Dimensions=[
            {"Name": "ApiName", "Value": API_GATEWAY_NAME},
            {"Name": "Stage", "Value": API_STAGE},
        ],
        StartTime=start_time,
        EndTime=end_time,
        Period=METRIC_WINDOW_MINUTES * 60,  # one bucket covering the full window
        Statistics=["Sum"],
    )

    datapoints = response.get("Datapoints", [])
    if not datapoints:
        return 0
    return int(sum(dp["Sum"] for dp in datapoints))


# ---------------------------------------------------------------------------
# Step 2 – Retrieve relevant log lines from CloudWatch Logs
# ---------------------------------------------------------------------------
def find_log_group() -> str | None:
    """
    Use describe_log_groups to find the log group whose name starts with
    LAMBDA_LOG_GROUP_PREFIX.  Returns the exact group name or None.
    """
    paginator = logs.get_paginator("describe_log_groups")
    for page in paginator.paginate(logGroupNamePrefix=LAMBDA_LOG_GROUP_PREFIX):
        groups = page.get("logGroups", [])
        if groups:
            return groups[0]["logGroupName"]
    return None


def get_recent_error_logs() -> list[str]:
    """
    Filter CloudWatch Logs for ERROR events in the last LOG_WINDOW_MINUTES.
    Returns up to MAX_LOG_LINES log message strings.
    """
    log_group = find_log_group()
    if not log_group:
        return [f"[monitor] Could not find log group matching '{LAMBDA_LOG_GROUP_PREFIX}'"]

    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - LOG_WINDOW_MINUTES * 60 * 1000

    collected: list[str] = []
    kwargs = {
        "logGroupName": log_group,
        "startTime": start_ms,
        "endTime": end_ms,
        "filterPattern": "ERROR",
        "limit": MAX_LOG_LINES,
    }

    try:
        response = logs.filter_log_events(**kwargs)
        for event in response.get("events", []):
            collected.append(event.get("message", "").strip())
            if len(collected) >= MAX_LOG_LINES:
                break
    except Exception as exc:  # noqa: BLE001
        collected.append(f"[monitor] Error querying logs: {exc}")

    return collected if collected else ["[monitor] No ERROR log events found in the window."]


# ---------------------------------------------------------------------------
# Step 3 – Read Lambda source code
# ---------------------------------------------------------------------------
def read_lambda_source() -> str:
    """Read the Lambda handler source from the local filesystem."""
    try:
        with open(LAMBDA_SOURCE_PATH, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        return f"[monitor] Could not read Lambda source at {LAMBDA_SOURCE_PATH}: {exc}"


# ---------------------------------------------------------------------------
# Step 4 – Generate AI diagnosis via AWS Bedrock
# ---------------------------------------------------------------------------
def generate_diagnosis(error_count: int, log_lines: list[str], lambda_source: str) -> str:
    """
    Call Bedrock (Claude Sonnet) with an angry/urgent prompt.
    Returns the AI-generated GitHub issue body as a string.
    """
    logs_block = "\n".join(log_lines) if log_lines else "(no log lines available)"

    prompt = f"""You are a FURIOUS senior engineer. The production API is DOWN and customers are screaming. \
You have just discovered {error_count} HTTP 500 errors in the last {METRIC_WINDOW_MINUTES} minutes on \
the 'bedrock-api' AWS API Gateway (stage: {API_STAGE}, region: {REGION}).

Your job is to write an URGENT GitHub Issue body in Markdown. You are ANGRY, stressed, and need this fixed NOW. \
Do NOT sugarcoat anything. Use strong, urgent language. Be direct, blunt, and specific.

The issue body MUST contain all of the following sections:

## 🔥 What Went Wrong
Describe the root cause based on the logs and source code. Be specific — name the exact error message.

## 📍 Where in the Code
Identify the exact file name and line number(s) in the Lambda source that caused or are related to the failure. \
Quote the offending code snippet(s).

## 🩹 How to Fix It
Give concrete, actionable steps to resolve the issue immediately. Number them.

## 📋 Evidence
Paste the relevant log lines as a code block.

---

Here are the CloudWatch ERROR log lines from the last {LOG_WINDOW_MINUTES} minutes:

```
{logs_block}
```

Here is the full Lambda source code (`handler.py`):

```python
{lambda_source}
```

Write ONLY the GitHub Issue body — no preamble, no "here is the issue", just the Markdown content starting \
with "## 🔥 What Went Wrong".
"""

    payload = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1024,
        "messages": [
            {"role": "user", "content": prompt}
        ],
    }

    response = bedrock_runtime.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        body=json.dumps(payload),
        contentType="application/json",
        accept="application/json",
    )

    result = json.loads(response["body"].read())
    # Claude v3 response shape: result["content"][0]["text"]
    content_blocks = result.get("content", [])
    if content_blocks and isinstance(content_blocks, list):
        return content_blocks[0].get("text", "(empty AI response)")
    return "(empty AI response)"


# ---------------------------------------------------------------------------
# Step 5 – Create GitHub Issue
# ---------------------------------------------------------------------------
def create_github_issue(title: str, body: str) -> dict:
    """
    POST a new GitHub Issue to GITHUB_REPO.
    Returns the parsed JSON response from the GitHub API.
    """
    github_token = os.environ["GITHUB_TOKEN"]
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = {
        "title": title,
        "body": body,
        "labels": ["bug"],
    }
    resp = requests.post(GITHUB_API_URL, headers=headers, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Main poll loop
# ---------------------------------------------------------------------------
def _timestamp() -> str:
    """Return current local time formatted as HH:MM:SS."""
    return datetime.now().strftime("%H:%M:%S")


def poll_once(reported_timestamps: set) -> None:
    """
    Execute a single monitoring cycle:
      1. Check for 5XX errors.
      2. If found and not yet reported, gather logs + source, run AI, file issue.
      3. Print a status line to stdout.
    """
    now_utc = datetime.now(timezone.utc)
    error_count = get_5xx_error_count()

    if error_count == 0:
        print(f"[{_timestamp()}] All clear.")
        return

    print(f"[{_timestamp()}] Polling... {error_count} errors detected")

    # Deduplication key: truncate to the current minute so we don't spam
    dedup_key = now_utc.strftime("%Y-%m-%dT%H:%M")
    if dedup_key in reported_timestamps:
        print(f"[{_timestamp()}] Issue already filed for this minute — skipping duplicate.")
        return

    # Mark as reported before doing anything async so a second cycle can't race
    reported_timestamps.add(dedup_key)

    print(f"[{_timestamp()}] Fetching error logs…")
    log_lines = get_recent_error_logs()

    print(f"[{_timestamp()}] Reading Lambda source…")
    lambda_source = read_lambda_source()

    print(f"[{_timestamp()}] Generating AI diagnosis via Bedrock…")
    diagnosis = generate_diagnosis(error_count, log_lines, lambda_source)

    issue_title = f"🚨 500 Error Detected — {now_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC"

    print(f"[{_timestamp()}] Creating GitHub Issue…")
    issue = create_github_issue(issue_title, diagnosis)
    issue_url = issue.get("html_url", "(unknown URL)")
    print(f"[{_timestamp()}] ✅ Issue filed: {issue_url}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Monitor API Gateway 500 errors and file GitHub Issues with AI diagnosis."
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single poll cycle and exit (default: loop every 60 s).",
    )
    args = parser.parse_args()

    reported_timestamps: set[str] = set()

    if args.once:
        poll_once(reported_timestamps)
    else:
        print(f"[{_timestamp()}] Starting monitor loop (interval: {POLL_INTERVAL_SECONDS}s). "
              "Press Ctrl+C to stop.")
        while True:
            try:
                poll_once(reported_timestamps)
            except KeyboardInterrupt:
                print(f"\n[{_timestamp()}] Interrupted — exiting.")
                break
            except Exception as exc:  # noqa: BLE001
                print(f"[{_timestamp()}] ⚠️  Unexpected error during poll: {exc}")
            time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
