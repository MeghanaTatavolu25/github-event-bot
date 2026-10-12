import pytest
from fastapi.testclient import TestClient
from helpers import add_rule, connect_repo, login_as, make_user

from app.db import rules
from app.main import app
from app.rules import build_rule


@pytest.fixture
def alice(client, monkeypatch):
    user = login_as(client, monkeypatch)
    connect_repo(user)
    return user


FORM = {
    "name": "Label bugs",
    "repo": "alice/demo",
    "event": "issues",
    "cond_field": ["title", "body"],
    "cond_op": ["contains", "contains"],
    "cond_value": ["bug", ""],
    "act_type": ["add_label", "slack"],
    "act_value": ["bug", "Heads up"],
}


def test_create_rule_from_the_form(client, alice):
    assert client.post("/rules", data=FORM).status_code == 303
    (saved,) = rules.find({"user_id": alice["_id"]})
    assert saved["name"] == "Label bugs" and saved["repo"] == "alice/demo" and saved["enabled"] is True
    assert saved["conditions"] == [{"field": "title", "op": "contains", "value": "bug"}]  # blank row dropped
    assert saved["actions"] == [{"type": "add_label", "value": "bug"}, {"type": "slack", "value": "Heads up"}]
    assert "Label bugs" in client.get("/rules").text


def test_rules_page_lists_only_your_own_rules(client, alice):
    add_rule(alice, name="my rule")
    add_rule(make_user(github_id=2, login="bob"), name="bobs rule")
    page = client.get("/rules").text
    assert "my rule" in page and "bobs rule" not in page


def test_invalid_rules_are_refused_with_a_message(client, alice):
    bad_forms = [
        {**FORM, "name": "  "},
        {**FORM, "repo": "someone/elses-repo"},
        {**FORM, "event": "star"},
        {**FORM, "cond_field": ["password", "body"]},
        {**FORM, "act_type": ["", ""]},
        {**FORM, "act_type": ["add_label", ""], "act_value": ["", ""]},  # label with no name
        {**FORM, "event": "push"},  # push cannot label
    ]
    for form in bad_forms:
        client.post("/rules", data=form)
    assert rules.count_documents({}) == 0


def test_build_rule_accepts_push_with_slack_only_and_wildcard_repo():
    rule = build_rule([], "Push alert", "*", "push", [], [], [], ["slack"], [""])
    assert rule["repo"] == "*" and rule["actions"] == [{"type": "slack", "value": ""}] and rule["conditions"] == []


def test_build_rule_limits_lengths():
    with pytest.raises(ValueError):
        build_rule([], "x" * 101, "*", "any", [], [], [], ["slack"], [""])
    with pytest.raises(ValueError):
        build_rule([], "ok", "*", "any", ["title"], ["contains"], ["x" * 201], ["slack"], [""])


def test_disable_and_enable_a_rule(client, alice):
    rule = add_rule(alice)
    client.post(f"/rules/{rule['_id']}/enabled", data={"enabled": "false"})
    assert rules.find_one({"_id": rule["_id"]})["enabled"] is False
    client.post(f"/rules/{rule['_id']}/enabled", data={"enabled": "true"})
    assert rules.find_one({"_id": rule["_id"]})["enabled"] is True


def test_delete_a_rule(client, alice):
    rule = add_rule(alice)
    client.post(f"/rules/{rule['_id']}/delete")
    assert rules.count_documents({}) == 0


def test_you_cannot_change_or_delete_someone_elses_rule(client, alice):
    bobs = add_rule(make_user(github_id=2, login="bob"), name="bobs rule")
    client.post(f"/rules/{bobs['_id']}/enabled", data={"enabled": "false"})
    client.post(f"/rules/{bobs['_id']}/delete")
    saved = rules.find_one({"_id": bobs["_id"]})
    assert saved is not None and saved["enabled"] is True


def test_malformed_rule_id_is_a_404(client, alice):
    assert client.post("/rules/not-an-id/delete").status_code == 404


def test_rules_routes_need_login_and_are_post_only():
    anonymous = TestClient(app, follow_redirects=False)
    assert anonymous.get("/rules").status_code == 303
    assert anonymous.post("/rules", data=FORM).status_code == 303
    assert rules.count_documents({}) == 0
    assert anonymous.get("/rules/abc/delete").status_code in (404, 405)
