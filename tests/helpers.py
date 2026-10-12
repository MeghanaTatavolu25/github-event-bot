import hashlib
import hmac
import json
import uuid
from urllib.parse import parse_qs, urlparse

from app.integrations import github
from app.core.db import repos, rules, users, utcnow
from app.core.security import encrypt_token

WEBHOOK_SECRET = "test-webhook-secret"


def login_as(client, monkeypatch, github_id=1, login="alice", token="gho_fake_token"):
    """Run the real /auth/login + /auth/callback with GitHub's side faked. Returns the user document."""
    monkeypatch.setattr(github, "exchange_code", lambda code: token)
    monkeypatch.setattr(
        github, "get_user", lambda t: {"id": github_id, "login": login, "avatar_url": "http://x/a.png"}
    )
    location = client.get("/auth/login").headers["location"]
    state = parse_qs(urlparse(location).query)["state"][0]
    client.get("/auth/callback", params={"code": "abc", "state": state})
    return users.find_one({"github_id": github_id})


def make_user(github_id=1, login="alice"):
    """A user without going through the login flow (for tests that never use a browser session)."""
    user = {"github_id": github_id, "login": login, "avatar_url": "", "access_token_enc": encrypt_token("gho_fake")}
    user["_id"] = users.insert_one(user).inserted_id
    return user


def connect_repo(user, full_name="alice/demo"):
    repos.insert_one({"user_id": user["_id"], "full_name": full_name, "webhook_id": 1})


def issue_payload(repo="alice/demo", title="App crashes: bug", number=7, action="opened", labels=()):
    return {
        "action": action,
        "issue": {
            "number": number,
            "title": title,
            "body": "It broke.",
            "user": {"login": "reporter"},
            "labels": [{"name": name} for name in labels],
            "html_url": f"https://github.com/{repo}/issues/{number}",
        },
        "repository": {"full_name": repo},
        "sender": {"login": "reporter"},
    }


def push_payload(repo="alice/demo", message="fix typo"):
    return {
        "ref": "refs/heads/main",
        "head_commit": {"message": message, "url": f"https://github.com/{repo}/commit/abc"},
        "compare": f"https://github.com/{repo}/compare/a...b",
        "repository": {"full_name": repo},
        "sender": {"login": "pusher"},
    }


def post_webhook(client, event, payload, delivery_id=None, secret=WEBHOOK_SECRET):
    body = json.dumps(payload).encode()
    headers = {
        "Content-Type": "application/json",
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery_id or str(uuid.uuid4()),
        "X-Hub-Signature-256": "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest(),
    }
    return client.post("/webhook/github", content=body, headers=headers)


def add_rule(user, **overrides):
    rule = {
        "user_id": user["_id"],
        "name": "bug rule",
        "repo": "alice/demo",
        "event": "issues",
        "enabled": True,
        "conditions": [{"field": "title", "op": "contains", "value": "bug"}],
        "actions": [{"type": "add_label", "value": "bug"}, {"type": "slack", "value": "Alert"}],
        "created_at": utcnow(),
    }
    rule.update(overrides)
    rule["_id"] = rules.insert_one(rule).inserted_id
    return rule
