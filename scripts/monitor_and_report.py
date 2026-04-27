"""
monitor_and_report.py

Monitors AWS CloudWatch for 500 errors on bedrock-api (eu-north-1).
When 500s are detected, invokes Deep Agents CLI to investigate and
file a GitHub Issue automatically.

Usage:
    python monitor_and_report.py          # loop every 60 seconds
    python monitor_and_report.py --once   # single poll cycle then exit
"""

import argparse
import os
import subprocess
import time
from datetime import datetime, timezone, timedelta

import boto3
import requests

REGION = "eu-north-1"
API_GATEWAY_NAME = "bedrock-api"
API_STAGE = "Prod"
LAMBDA_LOG_GROUP_PREFIX = "/aws/lambda/bedrock-api-BedrockChatFunction"
GITHUB_REPO = "RasDTU02/aws-monitoring"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/issues"
POLL_INTERVAL_SECONDS = 60
METRIC_WINDOW_MINUTES = 5
LOG_WINDOW_MINUTES = 10
MAX_LOG_LINES = 15

cloudwatch = boto3.client("cloudwatch", region_name=REGION)
logs_client = boto3.client("logs", region_name=REGION)
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def get_5xx_count() -> int:
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=METRIC_WINDOW_MINUTES)
    resp = cloudwatch.get_metric_statistics(
        Namespace="AWS/ApiGateway",
        MetricName="5XXError",
        Dimensions=[
            {"Name": "ApiName", "Value": API_GATEWAY_NAME},
            {"Name": "Stage", "Value": API_STAGE},
        ],
        StartTime=start,
        EndTime=end,
        Period=METRIC_WINDOW_MINUTES * 60,
        Statistics=["Sum"],
    )
    return int(sum(dp["Sum"] for dp in resp.get("Datapoints", [])))


def find_log_group() -> str | None:
    paginator = logs_client.get_paginator("describe_log_groups")
    for page in paginator.paginate(logGroupNamePrefix=LAMBDA_LOG_GROUP_PREFIX):
        groups = page.get("logGroups", [])
        if groups:
            return groups[0]["logGroupName"]
    return None


def get_error_logs() -> str:
    log_group = find_log_group()
    if not log_group:
        return "(log group not found)"
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - LOG_WINDOW_MINUTES * 60 * 1000
    try:
        resp = logs_client.filter_log_events(
            logGroupName=log_group,
            startTime=start_ms,
            endTime=end_ms,
            filterPattern="ERROR",
            limit=MAX_LOG_LINES,
        )
        lines = [e["message"].strip() for e in resp.get("events", [])]
        return "\n".join(lines) if lines else "(no ERROR events found)"
    except Exception as e:
        return f"(error fetching logs: {e})"


def invoke_deepagent_diagnosis(error_count: int, log_lines: str) -> str:
    """Use Deep Agents CLI to investigate the repo and write a GitHub Issue body."""
    prompt = (
        f"You are investigating a live production outage. Be angry and urgent.\n\n"
        f"The AWS API Gateway 'bedrock-api' (stage: Prod, region: eu-north-1) has recorded "
        f"{error_count} HTTP 500 errors in the last {METRIC_WINDOW_MINUTES} minutes.\n\n"
        f"Your job:\n"
        f"1. Read the Lambda source code at lambda/handler.py\n"
        f"2. Find the exact bug causing the 500 errors based on the error logs below\n"
        f"3. Write a GitHub Issue body in Markdown with these sections:\n"
        f"   ## 🔥 What Went Wrong\n"
        f"   ## 📍 Where in the Code (exact file + line, quote the broken code)\n"
        f"   ## 🩹 How to Fix It (numbered steps)\n"
        f"   ## 📋 Evidence (paste the log lines as a code block)\n\n"
        f"CloudWatch ERROR logs from the last {LOG_WINDOW_MINUTES} minutes:\n"
        f"```\n{log_lines}\n```\n\n"
        f"Output ONLY the GitHub Issue body markdown. No preamble."
    )

    result = subprocess.run(
        ["deepagents", "-n", prompt, "-S", "all", "-q", "--no-stream", "--max-turns", "10",
         "--profile-override", '{"model": "eu.anthropic.claude-sonnet-4-6", "model_provider": "bedrock_converse"}'],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )

    output = result.stdout.strip()
    if not output:
        output = f"(deepagents stderr: {result.stderr})" if result.stderr else "(no output)"
    return output


def create_github_issue(title: str, body: str) -> dict:
    token = os.environ["GITHUB_TOKEN"]
    resp = requests.post(
        GITHUB_API_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={"title": title, "body": body, "labels": ["bug"]},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def poll_once(reported: set) -> None:
    error_count = get_5xx_count()

    if error_count == 0:
        print(f"[{ts()}] All clear.")
        return

    print(f"[{ts()}] Polling... {error_count} errors detected")

    now_utc = datetime.now(timezone.utc)
    dedup_key = now_utc.strftime("%Y-%m-%dT%H:%M")
    if dedup_key in reported:
        print(f"[{ts()}] Already reported this minute — skipping.")
        return
    reported.add(dedup_key)

    print(f"[{ts()}] Fetching error logs...")
    log_lines = get_error_logs()

    print(f"[{ts()}] Invoking Deep Agents CLI to investigate...")
    body = invoke_deepagent_diagnosis(error_count, log_lines)

    title = f"🚨 500 Error Detected — {now_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC"
    print(f"[{ts()}] Filing GitHub Issue...")
    issue = create_github_issue(title, body)
    print(f"[{ts()}] ✅ Issue filed: {issue['html_url']}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    reported: set[str] = set()

    if args.once:
        poll_once(reported)
    else:
        print(f"[{ts()}] Starting monitor loop (interval: {POLL_INTERVAL_SECONDS}s). Ctrl+C to stop.")
        while True:
            try:
                poll_once(reported)
            except KeyboardInterrupt:
                print(f"\n[{ts()}] Interrupted — exiting.")
                break
            except Exception as e:
                print(f"[{ts()}] ⚠️  Error: {e}")
            time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
