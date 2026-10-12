from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from helpers import add_rule, connect_repo, issue_payload, login_as, make_user

from app.db import actions, events, utcnow
from app.engine import process_event
from app.main import app
from app.webhook import save_event


@pytest.fixture
def alice(client, monkeypatch):
    user = login_as(client, monkeypatch)
    connect_repo(user)
    return user


def add_event(user, delivery_id, **overrides):
    event = {
        "delivery_id": delivery_id,
        "event": "issues",
        "action": "opened",
        "repo": "alice/demo",
        "sender": "reporter",
        "title": f"title of {delivery_id}",
        "number": 1,
        "payload": {"secret_payload_marker": "do-not-render"},
        "received_at": utcnow(),
        "user_id": user["_id"],
        "status": "done",
        "attempts": 1,
        "last_error": None,
        "next_retry_at": None,
    }
    event.update(overrides)
    events.insert_one(event)
    return event


def test_dashboard_shows_own_events_with_their_actions_and_errors(client, alice):
    add_event(alice, "d-1", title="Crash on login", number=12, status="failed", last_error="bug rule: Slack 500",
              next_retry_at=utcnow() + timedelta(seconds=30), attempts=2)
    actions.insert_one({"delivery_id": "d-1", "rule_id": "r", "action_index": 0, "rule_name": "bug rule",
                        "type": "add_label", "status": "success", "detail": "added label bug", "created_at": utcnow()})
    actions.insert_one({"delivery_id": "d-1", "rule_id": "r", "action_index": 1, "rule_name": "bug rule",
                        "type": "slack", "status": "failed", "detail": "Slack 500", "created_at": utcnow()})
    page = client.get("/dashboard").text
    for text in ("alice/demo", "issues / opened", "#12 Crash on login", "failed", "added label bug",
                 "Slack 500", "Next automatic retry", "Retry now"):
        assert text in page
    assert "do-not-render" not in page  # payloads stay out of the page


def test_dashboard_never_shows_another_users_events(client, alice):
    bob = make_user(github_id=2, login="bob")
    add_event(alice, "mine", title="my event")
    add_event(bob, "theirs", title="bobs private event", repo="bob/secret")
    page = client.get("/dashboard").text
    assert "my event" in page and "bobs private event" not in page and "bob/secret" not in page
    assert "bobs private event" not in client.get("/dashboard", params={"repo": "bob/secret"}).text


def test_dashboard_filters_by_repo_and_status(client, alice):
    add_event(alice, "a", title="in demo, done")
    add_event(alice, "b", title="in demo, failed", status="failed")
    add_event(alice, "c", title="in other repo", repo="alice/other")
    only_failed = client.get("/dashboard", params={"status": "failed"}).text
    assert "in demo, failed" in only_failed and "in demo, done" not in only_failed
    only_other = client.get("/dashboard", params={"repo": "alice/other"}).text
    assert "in other repo" in only_other and "in demo, done" not in only_other


def test_dashboard_shows_the_latest_50_newest_first(client, alice):
    start = utcnow() - timedelta(hours=1)
    for i in range(55):
        add_event(alice, f"d-{i}", title=f"event number {i:02d}", received_at=start + timedelta(seconds=i))
    page = client.get("/dashboard").text
    assert "event number 54" in page and "event number 05" in page and "event number 04" not in page
    assert page.index("event number 54") < page.index("event number 05")


def test_dashboard_refreshes_itself_every_10_seconds(client, alice):
    assert '<meta http-equiv="refresh" content="10">' in client.get("/dashboard").text


def test_hostile_titles_cannot_inject_html(client, alice):
    add_event(alice, "x", title="<script>alert(1)</script>", last_error="<img src=x onerror=alert(2)>", status="failed")
    page = client.get("/dashboard").text
    assert "<script>alert(1)" not in page and "&lt;script&gt;alert(1)" in page
    assert "<img src=x" not in page


def test_retry_now_reprocesses_a_failed_event_without_repeating_finished_actions(client, alice, web):
    add_rule(alice, conditions=[])
    save_event("d-1", "issues", issue_payload())
    web.failing.add("slack")
    process_event("d-1")
    assert events.find_one({"delivery_id": "d-1"})["status"] == "failed"

    web.failing.clear()
    assert client.post("/events/d-1/retry").status_code == 303  # no waiting for the backoff
    ev = events.find_one({"delivery_id": "d-1"})
    assert (ev["status"], ev["attempts"]) == ("done", 2)
    assert len(web.of("github")) == 1 and len(web.of("slack")) == 2


def test_retry_now_only_works_on_your_own_failed_events(client, alice, web):
    bob = make_user(github_id=2, login="bob")
    add_event(bob, "bobs", status="failed", next_retry_at=utcnow() + timedelta(minutes=5))
    add_event(alice, "finished", status="done")
    client.post("/events/bobs/retry")
    client.post("/events/finished/retry")
    assert events.find_one({"delivery_id": "bobs"})["attempts"] == 1  # untouched
    assert events.find_one({"delivery_id": "finished"})["status"] == "done"
    assert web.calls == []


def test_retry_needs_login():
    anonymous = TestClient(app, follow_redirects=False)
    assert anonymous.post("/events/whatever/retry").status_code == 303


def test_status_tiles_count_all_of_your_events_not_just_the_visible_ones(client, alice):
    add_event(alice, "a", status="failed")
    add_event(alice, "b", status="failed")
    add_event(alice, "c", status="done")
    add_event(make_user(github_id=2, login="bob"), "d", status="failed")  # not yours: not counted
    page = client.get("/dashboard").text
    assert '<span class="muted small">failed</span><b>2</b>' in page
    assert '<span class="muted small">done</span><b>1</b>' in page
    assert '<span class="muted small">All</span><b>3</b>' in page
