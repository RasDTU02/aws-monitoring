"""
auto_fix_agent.py

Triggered by a GitHub 'bug' issue. Uses Deep Agents CLI to investigate
the repo, generate a fix, commit it to a new branch, and open a PR.
"""

import os
import subprocess
import sys
import textwrap
from datetime import datetime, timezone

import requests

GITHUB_REPO = "RasDTU02/aws-monitoring"
GITHUB_API = "https://api.github.com"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def gh_headers():
    return {
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def run(cmd: list[str], cwd: str = None) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd or REPO_ROOT)
    if result.returncode != 0:
        print(f"CMD FAILED: {' '.join(cmd)}\n{result.stderr}")
        sys.exit(1)
    return result.stdout.strip()


def invoke_deepagent_fix(issue_number: str, issue_title: str, issue_body: str) -> None:
    """Use Deep Agents CLI to investigate the issue and fix the code."""
    prompt = (
        f"A critical bug has been reported in production (GitHub Issue #{issue_number}).\n\n"
        f"Issue Title: {issue_title}\n\n"
        f"Issue Report:\n{issue_body}\n\n"
        f"Your job:\n"
        f"1. Read lambda/handler.py\n"
        f"2. Identify the exact bug described in the issue\n"
        f"3. Fix ONLY the reported bug — do not refactor or change anything else\n"
        f"4. Save the fixed file\n"
        f"Do not explain what you did. Just read the file and fix it."
    )

    print("[auto-fix] Running Deep Agents CLI to fix the bug...")
    result = subprocess.run(
        ["deepagents", "-n", prompt, "-S", "all", "-q", "--no-stream", "--max-turns", "15",
         "--profile-override", '{"model": "eu.anthropic.claude-sonnet-4-6", "model_provider": "bedrock_converse"}'],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    print(result.stdout[:1000] if result.stdout else "(no stdout)")
    if result.returncode != 0:
        print(f"deepagents stderr: {result.stderr[:500]}")
        sys.exit(1)


def open_pr(branch: str, issue_number: str, issue_title: str) -> str:
    """Open PR using RasDTU02 PAT stored as secret, fall back to GITHUB_TOKEN."""
    pr_token = os.environ.get("PR_TOKEN") or os.environ["GITHUB_TOKEN"]
    headers = {
        "Authorization": f"Bearer {pr_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    body = textwrap.dedent(f"""
        ## 🤖 Automated Fix by Deep Agents

        This PR was generated automatically by the Deep Agents CLI auto-fix agent
        in response to issue #{issue_number}.

        **Issue:** {issue_title}

        ### What was changed
        Deep Agents read the issue report, investigated the codebase, identified the
        root cause, and patched the affected file(s).

        ### Review checklist
        - [ ] Verify the fix matches the reported bug
        - [ ] Run smoke test after merge
        - [ ] Close issue #{issue_number} if resolved

        _Closes #{issue_number}_
    """).strip()

    resp = requests.post(
        f"{GITHUB_API}/repos/{GITHUB_REPO}/pulls",
        headers=headers,
        json={
            "title": f"🤖 Auto-fix: {issue_title}",
            "body": body,
            "head": branch,
            "base": "main",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["html_url"]


def main():
    issue_number = os.environ["ISSUE_NUMBER"]
    issue_title = os.environ["ISSUE_TITLE"]
    issue_body = os.environ.get("ISSUE_BODY", "")

    print(f"[auto-fix] Handling issue #{issue_number}: {issue_title}")

    invoke_deepagent_fix(issue_number, issue_title, issue_body)

    run(["git", "config", "user.email", "auto-fix-agent@github-actions"])
    run(["git", "config", "user.name", "Deep Agents Auto-Fix"])

    branch = f"fix/auto-fix-issue-{issue_number}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    run(["git", "checkout", "-b", branch])

    diff = subprocess.run(["git", "diff", "--name-only"], capture_output=True, text=True, cwd=REPO_ROOT).stdout.strip()
    if not diff:
        print("[auto-fix] No changes made by Deep Agents — exiting.")
        sys.exit(0)

    print(f"[auto-fix] Files changed: {diff}")
    run(["git", "add", "-A"])
    run(["git", "commit", "-m", f"fix: deep agents auto-fix for issue #{issue_number} — {issue_title}"])

    remote_url = f"https://x-access-token:{os.environ['GITHUB_TOKEN']}@github.com/{GITHUB_REPO}.git"
    run(["git", "push", remote_url, branch])
    print(f"[auto-fix] Pushed branch: {branch}")

    pr_url = open_pr(branch, issue_number, issue_title)
    print(f"[auto-fix] ✅ PR opened: {pr_url}")

    requests.post(
        f"{GITHUB_API}/repos/{GITHUB_REPO}/issues/{issue_number}/comments",
        headers=gh_headers(),
        json={"body": f"🤖 Deep Agents Auto-Fix has opened a PR to address this issue: {pr_url}"},
        timeout=15,
    )
    print("[auto-fix] Commented on issue.")


if __name__ == "__main__":
    main()
