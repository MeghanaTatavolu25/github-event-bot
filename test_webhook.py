"""Send a fake but correctly signed GitHub webhook to a running copy of the app (manual testing).

    python test_webhook.py owner/name "Crash on login: bug"
    python test_webhook.py owner/name "Crash on login" --delivery-id demo-1   # run twice: 2nd says "duplicate"
    python test_webhook.py owner/name "fix typo" --event push
    python test_webhook.py owner/name "A bug" --url https://github-event-bot-jeuz.onrender.com

The repo must be connected on the /repos page first, or the app answers "ignored".
The secret comes from GITHUB_WEBHOOK_SECRET (via app/core/config.py); only the HTTP result is printed.
"""
import argparse
import hashlib
import hmac
import json
import uuid

import httpx

from app.core import config


def build_payload(event: str, repo: str, title: str, number: int) -> dict:
    base = {"repository": {"full_name": repo}, "sender": {"login": "test-user"}}
    link = f"https://github.com/{repo}"
    if event == "push":
        commit = {"message": title, "url": f"{link}/commit/abc123"}
        return {**base, "ref": "refs/heads/main", "head_commit": commit, "compare": f"{link}/compare/a...b"}
    item = {
        "number": number,
        "title": title,
        "body": "Sent by test_webhook.py",
        "user": {"login": "test-user"},
        "labels": [],
        "html_url": f"{link}/issues/{number}",
    }
    return {**base, "action": "opened", "issue" if event == "issues" else "pull_request": item}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repo", help="owner/name of a connected repo")
    parser.add_argument("title", help="issue / PR title, or commit message for push")
    parser.add_argument("--event", choices=["issues", "pull_request", "push"], default="issues")
    parser.add_argument("--number", type=int, default=1, help="issue / PR number the bot will label or comment on")
    parser.add_argument("--delivery-id", default=None, help="reuse an id to test duplicate handling")
    parser.add_argument("--url", default="http://localhost:8000", help="where the app is running")
    args = parser.parse_args()

    body = json.dumps(build_payload(args.event, args.repo, args.title, args.number)).encode()
    signature = "sha256=" + hmac.new(config.GITHUB_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-GitHub-Event": args.event,
        "X-GitHub-Delivery": args.delivery_id or str(uuid.uuid4()),
        "X-Hub-Signature-256": signature,
    }
    response = httpx.post(args.url + "/webhook/github", content=body, headers=headers, timeout=10)
    print(response.status_code, response.text)


if __name__ == "__main__":
    main()
