import json
import os

# Tests must never touch the real database or real secrets. Set fake values BEFORE the app is
# imported (python-dotenv does not override variables that already exist).
os.environ.update(
    MONGODB_URI="mongodb://localhost",
    GITHUB_CLIENT_ID="test-client-id",
    GITHUB_CLIENT_SECRET="test-client-secret",
    GITHUB_WEBHOOK_SECRET="test-webhook-secret",
    SLACK_WEBHOOK_URL="https://hooks.slack.test/T000/B000/secret",
    SESSION_SECRET="test-session-secret-value",
    APP_URL="http://testserver",
)

import httpx  # noqa: E402
import mongomock  # noqa: E402
import pymongo  # noqa: E402
import pytest  # noqa: E402

pymongo.MongoClient = mongomock.MongoClient  # app.core.db does `from pymongo import MongoClient`

from fastapi.testclient import TestClient  # noqa: E402

from app.core import db  # noqa: E402
from app.integrations import github, slack  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def clean_db():
    db.init_db()
    for collection in (db.users, db.repos, db.rules, db.events, db.actions):
        collection.delete_many({})


@pytest.fixture
def client():
    return TestClient(app, follow_redirects=False)


class FakeWeb:
    """Stands in for GitHub and Slack. Records every request; kinds listed in `failing` answer 500."""

    def __init__(self):
        self.calls = []
        self.failing = set()
        self.raise_for_slack = None

    def handler(self, request):
        kind = "slack" if request.url.host == "hooks.slack.test" else "github"
        body = request.read()
        self.calls.append({
            "kind": kind,
            "method": request.method,
            "path": request.url.path,
            "json": json.loads(body) if body else None,
            "auth": request.headers.get("authorization"),
        })
        if kind == "slack" and self.raise_for_slack:
            raise self.raise_for_slack
        if kind in self.failing:
            return httpx.Response(500, json={"message": "boom"})
        return httpx.Response(200, json={})

    def of(self, kind):
        return [c for c in self.calls if c["kind"] == kind]


@pytest.fixture
def web(monkeypatch):
    fake = FakeWeb()
    client = httpx.Client(transport=httpx.MockTransport(fake.handler), timeout=10)
    monkeypatch.setattr(github, "http", client)
    monkeypatch.setattr(slack, "http", client)
    return fake
