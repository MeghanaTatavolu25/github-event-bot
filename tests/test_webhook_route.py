import hashlib
import hmac
import json

from helpers import WEBHOOK_SECRET, connect_repo, issue_payload, make_user, post_webhook, push_payload

from app import config
from app.db import events


def test_rejects_wrong_signature_and_stores_nothing(client):
    connect_repo(make_user())
    response = post_webhook(client, "issues", issue_payload(), secret="not-the-secret")
    assert response.status_code == 401 and events.count_documents({}) == 0


def test_rejects_unsigned_request(client):
    response = client.post("/webhook/github", json=issue_payload(), headers={"X-GitHub-Event": "issues"})
    assert response.status_code == 401


def test_fails_closed_when_our_secret_is_missing(client, monkeypatch):
    connect_repo(make_user())
    monkeypatch.setattr(config, "GITHUB_WEBHOOK_SECRET", "")
    assert post_webhook(client, "issues", issue_payload(), secret="").status_code == 503
    assert events.count_documents({}) == 0


def test_ping_is_answered(client):
    assert post_webhook(client, "ping", {"zen": "Keep it logically awesome."}).json() == {"status": "pong"}


def test_unsupported_event_is_ignored(client):
    connect_repo(make_user())
    assert post_webhook(client, "star", {"repository": {"full_name": "alice/demo"}}).json()["status"] == "ignored"
    assert events.count_documents({}) == 0


def test_repo_that_is_not_connected_is_ignored_and_not_stored(client):
    response = post_webhook(client, "issues", issue_payload(repo="stranger/repo"))
    assert response.status_code == 200 and response.json() == {"status": "ignored"}
    assert events.count_documents({}) == 0


def test_event_for_a_connected_repo_is_saved_for_its_owner(client):
    owner = make_user()
    connect_repo(owner)
    response = post_webhook(client, "issues", issue_payload(title="Login bug", number=3), delivery_id="d-1")
    assert response.json() == {"status": "stored"}
    saved = events.find_one({"delivery_id": "d-1"})
    assert saved["user_id"] == owner["_id"]
    assert (saved["event"], saved["action"], saved["repo"]) == ("issues", "opened", "alice/demo")
    assert (saved["title"], saved["number"], saved["sender"]) == ("Login bug", 3, "reporter")
    # The repo has no rules, so the background task already finished with nothing to do.
    assert (saved["status"], saved["attempts"]) == ("ignored", 1)


def test_push_event_uses_the_commit_message_as_title(client):
    connect_repo(make_user())
    post_webhook(client, "push", push_payload(message="fix typo"), delivery_id="d-2")
    saved = events.find_one({"delivery_id": "d-2"})
    assert saved["title"] == "fix typo" and saved["number"] is None


def test_same_delivery_twice_is_stored_once(client):
    connect_repo(make_user())
    first = post_webhook(client, "issues", issue_payload(), delivery_id="d-3")
    second = post_webhook(client, "issues", issue_payload(), delivery_id="d-3")
    assert first.json() == {"status": "stored"} and second.json() == {"status": "duplicate"}
    assert events.count_documents({"delivery_id": "d-3"}) == 1


def test_signed_body_that_is_not_a_json_object_is_a_400(client):
    body = json.dumps([1, 2]).encode()
    signature = "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    response = client.post(
        "/webhook/github",
        content=body,
        headers={"X-GitHub-Event": "issues", "X-GitHub-Delivery": "d-4", "X-Hub-Signature-256": signature},
    )
    assert response.status_code == 400
