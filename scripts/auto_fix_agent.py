"""
auto_fix_agent.py

Triggered by a GitHub 'bug' issue. Reads the issue, uses Bedrock AI to
generate a fix for the Lambda source code, commits it to a new branch,
and opens a Pull Request.
"""

import json
import os
import subprocess
import sys
import textwrap
from datetime import datetime, timezone

import boto3
import requests

REGION = "eu-north-1"
BEDROCK_MODEL_ID = "eu.anthropic.claude-sonnet-4-6"
GITHUB_REPO = "RasDTU02/aws-monitoring"
LAMBDA_SOURCE_PATH = "lambda/handler.py"
GITHUB_API = "https://api.github.com"

bedrock_runtime = boto3.client("bedrock-runtime", region_name=REGION)


def gh_headers():
    return {
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def read_lambda_source() -> str:
    with open(LAMBDA_SOURCE_PATH, "r", encoding="utf-8") as f:
        return f.read()


def generate_fix(issue_title: str, issue_body: str, source_code: str) -> str:
    prompt = f"""You are an expert Python AWS Lambda developer. A critical bug has been reported in production.

Issue Title: {issue_title}

Issue Report:
{issue_body}

Current Lambda source code (`handler.py`):
```python
{source_code}
```

Your task:
1. Identify the exact bug described in the issue
2. Return ONLY the complete fixed `handler.py` file — no explanation, no markdown fences, no preamble
3. Fix ONLY the reported bug — do not refactor or change anything else
4. The file must be valid Python

Return just the raw Python file content, starting with the first import line."""

    payload = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": prompt}],
    }

    response = bedrock_runtime.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        body=json.dumps(payload),
        contentType="application/json",
        accept="application/json",
    )
    result = json.loads(response["body"].read())
    fixed = result["content"][0]["text"].strip()

    # Strip markdown fences if model included them
    if fixed.startswith("```"):
        lines = fixed.splitlines()
        fixed = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

    return fixed


def run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"CMD FAILED: {' '.join(cmd)}\n{result.stderr}")
        sys.exit(1)
    return result.stdout.strip()


def main():
    issue_number = os.environ["ISSUE_NUMBER"]
    issue_title = os.environ["ISSUE_TITLE"]
    issue_body = os.environ.get("ISSUE_BODY", "")

    print(f"[auto-fix] Handling issue #{issue_number}: {issue_title}")

    source_code = read_lambda_source()
    print("[auto-fix] Generating fix via Bedrock AI...")
    fixed_code = generate_fix(issue_title, issue_body, source_code)

    # Write fix
    with open(LAMBDA_SOURCE_PATH, "w", encoding="utf-8") as f:
        f.write(fixed_code)
    print("[auto-fix] Fix written to handler.py")

    # Git setup
    run(["git", "config", "user.email", "auto-fix-agent@github-actions"])
    run(["git", "config", "user.name", "Auto-Fix Agent"])

    branch = f"fix/auto-fix-issue-{issue_number}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    run(["git", "checkout", "-b", branch])
    run(["git", "add", LAMBDA_SOURCE_PATH])
    run(["git", "commit", "-m", f"fix: auto-fix for issue #{issue_number} — {issue_title}"])

    remote_url = f"https://x-access-token:{os.environ['GITHUB_TOKEN']}@github.com/{GITHUB_REPO}.git"
    run(["git", "push", remote_url, branch])
    print(f"[auto-fix] Pushed branch: {branch}")

    # Open PR
    pr_body = textwrap.dedent(f"""
        ## 🤖 Automated Fix

        This PR was generated automatically by the Auto-Fix Agent in response to issue #{issue_number}.

        **Issue:** {issue_title}

        ### What was changed
        The AI agent identified the root cause from the issue report and error logs, then patched `lambda/handler.py`.

        ### Review checklist
        - [ ] Verify the fix matches the reported bug
        - [ ] Run smoke test after merge
        - [ ] Close issue #{issue_number} if resolved

        _Closes #{issue_number}_
    """).strip()

    resp = requests.post(
        f"{GITHUB_API}/repos/{GITHUB_REPO}/pulls",
        headers=gh_headers(),
        json={
            "title": f"🤖 Auto-fix: {issue_title}",
            "body": pr_body,
            "head": branch,
            "base": "main",
        },
        timeout=15,
    )
    resp.raise_for_status()
    pr = resp.json()
    print(f"[auto-fix] ✅ PR opened: {pr['html_url']}")

    # Comment on the issue
    requests.post(
        f"{GITHUB_API}/repos/{GITHUB_REPO}/issues/{issue_number}/comments",
        headers=gh_headers(),
        json={"body": f"🤖 Auto-Fix Agent has opened a PR to address this issue: {pr['html_url']}"},
        timeout=15,
    )
    print("[auto-fix] Commented on issue.")


if __name__ == "__main__":
    main()
