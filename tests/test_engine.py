import json
from datetime import timedelta, timezone

import httpx
import pytest
from helpers import add_rule, connect_repo, issue_payload, make_user, post_webhook, push_payload

from app.db import actions, events, utcnow
from app.engine import (
    BASE_DELAY,
    MAX_RETRIES,
    condition_holds,
    event_fields,
    matching_rules,
    process_event,
    retry_due_events,
)
from app.webhook import save_event


@pytest.fixture
def owner():
    user = make_user()
    connect_repo(user)
    return user


def store(payload, event="issues", delivery_id="d-1"):
    """Save an event the way the webhook does, and return it."""
    assert save_event(delivery_id, event, payload) == "stored"
    return events.find_one({"delivery_id": delivery_id})


def make_due(delivery_id="d-1"):
    events.update_one({"delivery_id": delivery_id}, {"$set": {"next_retry_at": utcnow() - timedelta(seconds=1)}})


def as_utc(moment):
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


# ---- rule matching ---------------------------------------------------------------------------


def names(ev):
    return [r["name"] for r in matching_rules(ev)]


def test_title_contains_is_case_insensitive(owner):
    add_rule(owner)
    assert names(store(issue_payload(title="Found a BUG in login"))) == ["bug rule"]


def test_rule_does_not_match_when_condition_is_false(owner):
    add_rule(owner)
    assert names(store(issue_payload(title="Add dark mode"))) == []


def test_equals_needs_the_whole_value():
    fields = {"title": "bug", "label": ["Bug", "help wanted"]}
    assert condition_holds({"field": "title", "op": "equals", "value": "BUG"}, fields)
    assert not condition_holds({"field": "title", "op": "equals", "value": "bu"}, fields)
    assert condition_holds({"field": "label", "op": "equals", "value": "help wanted"}, fields)
    assert condition_holds({"field": "label", "op": "contains", "value": "help"}, fields)
    assert not condition_holds({"field": "label", "op": "equals", "value": "help"}, fields)


def test_fields_come_from_the_issue(owner):
    ev = store(issue_payload(labels=["urgent"]))
    assert event_fields(ev) == {"title": "App crashes: bug", "body": "It broke.", "author": "reporter", "label": ["urgent"]}


def test_all_conditions_must_hold(owner):
    both = [
        {"field": "title", "op": "contains", "value": "bug"},
        {"field": "author", "op": "equals", "value": "someone-else"},
    ]
    add_rule(owner, conditions=both)
    assert names(store(issue_payload())) == []


def test_rule_without_conditions_matches_every_new_issue(owner):
    add_rule(owner, conditions=[])
    assert names(store(issue_payload(title="anything"))) == ["bug rule"]


def test_disabled_rule_is_skipped(owner):
    add_rule(owner, enabled=False)
    assert names(store(issue_payload())) == []


def test_repo_event_and_wildcards(owner):
    add_rule(owner, name="other repo", repo="alice/other")
    add_rule(owner, name="all repos", repo="*")
    add_rule(owner, name="prs only", event="pull_request")
    add_rule(owner, name="any event", event="any")
    # An issue: skips the other repo's rule and the pull-request-only rule.
    assert sorted(names(store(issue_payload()))) == ["all repos", "any event"]
    pull_request = {
        "action": "opened",
        "pull_request": {"number": 2, "title": "bug fix", "user": {"login": "dev"}},
        "repository": {"full_name": "alice/demo"},
        "sender": {"login": "dev"},
    }
    assert sorted(names(store(pull_request, event="pull_request", delivery_id="d-2"))) == ["any event", "prs only"]


def test_only_newly_opened_issues_match(owner):
    # Edits and our own "labeled" events must not trigger rules, or the bot would trigger itself.
    add_rule(owner)
    assert names(store(issue_payload(action="labeled"))) == []


def test_push_events_match_without_an_action(owner):
    add_rule(owner, event="push", conditions=[{"field": "title", "op": "contains", "value": "typo"}])
    assert names(store(push_payload(message="fix typo"), event="push")) == ["bug rule"]


def test_another_users_rules_never_apply(owner):
    bob = make_user(github_id=2, login="bob")
    add_rule(bob)
    assert names(store(issue_payload())) == []


# ---- doing the work --------------------------------------------------------------------------


def test_matching_issue_gets_label_and_slack_alert(owner, web):
    add_rule(owner)
    store(issue_payload(title="Login bug", number=7))
    process_event("d-1")

    (label_call,) = web.of("github")
    assert (label_call["method"], label_call["path"]) == ("POST", "/repos/alice/demo/issues/7/labels")
    assert label_call["json"] == {"labels": ["bug"]}
    assert label_call["auth"] == "Bearer gho_fake"  # the repo owner's own (decrypted) token

    (slack_call,) = web.of("slack")
    text = slack_call["json"]["text"]
    for part in ("Alert", "alice/demo", "issues opened", "reporter", "Login bug", "https://github.com/alice/demo/issues/7"):
        assert part in text

    ev = events.find_one({"delivery_id": "d-1"})
    assert (ev["status"], ev["attempts"], ev["last_error"], ev["next_retry_at"]) == ("done", 1, None, None)
    assert sorted(a["status"] for a in actions.find({})) == ["success", "success"]


def test_post_comment_action(owner, web):
    add_rule(owner, actions=[{"type": "post_comment", "value": "Thanks, we will look."}])
    store(issue_payload(number=4))
    process_event("d-1")
    (call,) = web.of("github")
    assert call["path"] == "/repos/alice/demo/issues/4/comments"
    assert call["json"] == {"body": "Thanks, we will look."}


def test_push_runs_slack_only(owner, web):
    add_rule(owner, event="any", conditions=[])  # has add_label + slack
    store(push_payload(message="fix typo"), event="push")
    process_event("d-1")
    assert web.of("github") == []
    assert "fix typo" in web.of("slack")[0]["json"]["text"]
    assert events.find_one({})["status"] == "done"


def test_event_with_no_matching_rule_is_ignored(owner, web):
    store(issue_payload())
    process_event("d-1")
    assert web.calls == []
    assert events.find_one({})["status"] == "ignored"


def test_slack_message_cannot_ping_everyone_or_fake_links(owner, web):
    add_rule(owner, conditions=[])
    store(issue_payload(title="<!channel> hi & <http://evil.example|click>"))
    process_event("d-1")
    text = web.of("slack")[0]["json"]["text"]
    assert "<!channel>" not in text and "<http://evil" not in text
    assert "&lt;!channel&gt; hi &amp; &lt;http://evil.example|click&gt;" in text


# ---- failures, retries, idempotency -----------------------------------------------------------


def test_failed_action_marks_event_failed_and_retry_does_not_repeat_the_successful_one(owner, web):
    add_rule(owner)
    store(issue_payload())
    web.failing.add("slack")
    process_event("d-1")

    ev = events.find_one({"delivery_id": "d-1"})
    assert (ev["status"], ev["attempts"]) == ("failed", 1)
    assert "Slack 500" in ev["last_error"]
    wait = (as_utc(ev["next_retry_at"]) - utcnow()).total_seconds()
    assert BASE_DELAY - 5 < wait <= BASE_DELAY
    assert {a["type"]: a["status"] for a in actions.find({})} == {"add_label": "success", "slack": "failed"}

    web.failing.clear()
    make_due()
    process_event("d-1")

    assert len(web.of("github")) == 1  # the label was NOT added a second time
    assert len(web.of("slack")) == 2  # Slack was retried
    ev = events.find_one({"delivery_id": "d-1"})
    assert (ev["status"], ev["attempts"], ev["last_error"], ev["next_retry_at"]) == ("done", 2, None, None)
    assert actions.count_documents({}) == 2
    assert {a["type"]: a["status"] for a in actions.find({})} == {"add_label": "success", "slack": "success"}


def test_retries_back_off_exponentially_then_give_up(owner, web):
    add_rule(owner, actions=[{"type": "slack", "value": ""}])
    store(issue_payload())
    web.failing.add("slack")
    waits = []
    for _ in range(MAX_RETRIES + 1):  # the first try plus 5 retries
        make_due()
        process_event("d-1")
        ev = events.find_one({"delivery_id": "d-1"})
        waits.append(ev["next_retry_at"] and round((as_utc(ev["next_retry_at"]) - utcnow()).total_seconds()))
    assert waits[:5] == pytest.approx([30, 60, 120, 240, 480], abs=3)
    assert waits[5] is None
    assert (ev["status"], ev["attempts"]) == ("failed", 6)

    calls_before = len(web.calls)  # a given-up event has no retry time, so the loop leaves it alone
    retry_due_events()
    assert len(web.calls) == calls_before


def test_event_is_not_retried_before_its_time(owner, web):
    add_rule(owner, actions=[{"type": "slack", "value": ""}])
    store(issue_payload())
    web.failing.add("slack")
    process_event("d-1")
    process_event("d-1")  # too early: the backoff has not passed
    assert len(web.of("slack")) == 1 and events.find_one({})["attempts"] == 1


def test_finished_event_is_never_processed_again(owner, web):
    add_rule(owner)
    store(issue_payload())
    process_event("d-1")
    process_event("d-1")
    assert len(web.of("slack")) == 1 and events.find_one({})["attempts"] == 1


def test_retry_loop_picks_up_leftovers_from_a_restart(owner, web):
    add_rule(owner, actions=[{"type": "slack", "value": ""}])
    store(issue_payload(), delivery_id="never-started")  # saved, then the server stopped
    store(issue_payload(), delivery_id="crashed-midway")
    events.update_one(
        {"delivery_id": "crashed-midway"},
        {"$set": {"status": "processing", "next_retry_at": utcnow() - timedelta(seconds=1)}},  # lease ran out
    )
    store(issue_payload(), delivery_id="still-working")
    events.update_one(
        {"delivery_id": "still-working"},
        {"$set": {"status": "processing", "next_retry_at": utcnow() + timedelta(minutes=4)}},  # lease still valid
    )
    retry_due_events()
    status = {e["delivery_id"]: e["status"] for e in events.find({})}
    assert status == {"never-started": "done", "crashed-midway": "done", "still-working": "processing"}


def test_error_text_never_contains_the_slack_url(owner, web):
    add_rule(owner, actions=[{"type": "slack", "value": ""}])
    store(issue_payload())
    web.raise_for_slack = httpx.ConnectError("cannot reach https://hooks.slack.test/T000/B000/secret")
    process_event("d-1")
    everything = json.dumps([events.find_one({}, {"payload": 0, "_id": 0}), list(actions.find({}, {"_id": 0}))], default=str)
    assert events.find_one({})["last_error"] == "bug rule: ConnectError"
    assert "secret" not in everything and "hooks.slack.test" not in everything


def test_github_error_message_is_kept_for_the_dashboard(owner, web, monkeypatch):
    add_rule(owner, actions=[{"type": "add_label", "value": "bug"}])
    store(issue_payload())
    web.failing.add("github")
    process_event("d-1")
    assert "GitHub 500: boom" in events.find_one({})["last_error"]


# ---- the whole path: webhook -> background task -> actions ------------------------------------


def test_webhook_triggers_processing_and_a_redelivery_does_nothing_more(client, owner, web):
    add_rule(owner)
    first = post_webhook(client, "issues", issue_payload(), delivery_id="d-9")
    again = post_webhook(client, "issues", issue_payload(), delivery_id="d-9")
    assert first.json() == {"status": "stored"} and again.json() == {"status": "duplicate"}
    assert len(web.of("github")) == 1 and len(web.of("slack")) == 1
    assert events.find_one({"delivery_id": "d-9"})["status"] == "done"
