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

import mongomock  # noqa: E402
import pymongo  # noqa: E402
import pytest  # noqa: E402

pymongo.MongoClient = mongomock.MongoClient  # app.db does `from pymongo import MongoClient`

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def clean_db():
    db.init_db()
    for collection in (db.users, db.repos, db.rules, db.events, db.actions):
        collection.delete_many({})


@pytest.fixture
def client():
    return TestClient(app, follow_redirects=False)
