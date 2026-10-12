import json

import httpx
import pytest
from fastapi.testclient import TestClient
from helpers import connect_repo, login_as, make_user

from app import github
from app.db import repos, rules
from app.main import app


def stub_github(monkeypatch, *, admin=True, full_name="alice/demo"):
    """Fake the three GitHub calls the repos page makes. Returns the list of calls made."""
    calls = []
    monkeypatch.setattr(
        github, "get_repo", lambda token, name: {"full_name": full_name, "permissions": {"admin": admin}}
    )
    monkeypatch.setattr(github, "create_hook", lambda token, name: calls.append(("create", name)) or 99)
    monkeypatch.setattr(github, "delete_hook", lambda token, name, hook_id: calls.append(("delete", name, hook_id)))
    return calls


@pytest.fixture
def alice(client, monkeypatch):
    return login_as(client, monkeypatch)


def test_connect_saves_repo_with_githubs_spelling_and_hook_id(client, alice, monkeypatch):
    stub_github(monkeypatch, full_name="alice/demo")
    assert client.post("/repos/connect", data={"full_name": "Alice/DEMO"}).status_code == 303
    saved = repos.find_one({"user_id": alice["_id"]})
    assert saved["full_name"] == "alice/demo" and saved["webhook_id"] == 99
    assert "Connected alice/demo." in client.get("/repos").text


def test_first_repo_gets_the_example_rule_but_second_does_not(client, alice, monkeypatch):
    stub_github(monkeypatch, full_name="alice/one")
    client.post("/repos/connect", data={"full_name": "alice/one"})
    stub_github(monkeypatch, full_name="alice/two")
    client.post("/repos/connect", data={"full_name": "alice/two"})
    example = list(rules.find({"user_id": alice["_id"]}))
    assert len(example) == 1
    assert example[0]["repo"] == "alice/one" and example[0]["event"] == "issues"
    assert example[0]["conditions"] == [{"field": "title", "op": "contains", "value": "bug"}]
    assert [a["type"] for a in example[0]["actions"]] == ["add_label", "slack"]


def test_connect_needs_admin_permission(client, alice, monkeypatch):
    calls = stub_github(monkeypatch, admin=False)
    client.post("/repos/connect", data={"full_name": "alice/demo"})
    assert repos.count_documents({}) == 0 and calls == []
    assert "admin access" in client.get("/repos").text


def test_connect_rejects_bad_format_without_calling_github(client, alice, monkeypatch):
    def boom(*args):
        raise AssertionError("GitHub must not be called")

    monkeypatch.setattr(github, "get_repo", boom)
    client.post("/repos/connect", data={"full_name": "../../users/someone"})
    assert repos.count_documents({}) == 0
    assert "owner/name" in client.get("/repos").text


def test_connect_reports_unknown_repo(client, alice, monkeypatch):
    def not_found(token, name):
        raise github.GitHubError(404, "Not Found")

    monkeypatch.setattr(github, "get_repo", not_found)
    client.post("/repos/connect", data={"full_name": "alice/nope"})
    assert "Repo not found" in client.get("/repos").text


def test_repo_connected_by_someone_else_is_refused(client, alice, monkeypatch):
    connect_repo(make_user(github_id=2, login="bob"), "alice/demo")
    calls = stub_github(monkeypatch, full_name="alice/demo")
    client.post("/repos/connect", data={"full_name": "alice/demo"})
    assert repos.count_documents({}) == 1 and calls == []
    assert "already connected" in client.get("/repos").text


def test_disconnect_deletes_the_webhook_and_the_repo(client, alice, monkeypatch):
    calls = stub_github(monkeypatch)
    client.post("/repos/connect", data={"full_name": "alice/demo"})
    client.post("/repos/disconnect", data={"full_name": "alice/demo"})
    assert repos.count_documents({}) == 0
    assert ("delete", "alice/demo", 99) in calls


def test_nobody_can_disconnect_another_users_repo(client, alice, monkeypatch):
    connect_repo(make_user(github_id=2, login="bob"), "bob/secret")
    calls = stub_github(monkeypatch)
    client.post("/repos/disconnect", data={"full_name": "bob/secret"})
    assert repos.count_documents({}) == 1 and calls == []


def test_repo_routes_need_login():
    anonymous = TestClient(app, follow_redirects=False)
    assert anonymous.get("/repos").status_code == 303
    assert anonymous.post("/repos/connect", data={"full_name": "a/b"}).status_code == 303
    assert repos.count_documents({}) == 0


def mock_github(monkeypatch, handler):
    monkeypatch.setattr(github, "http", httpx.Client(transport=httpx.MockTransport(handler)))


def test_create_hook_sends_our_url_secret_and_events(monkeypatch):
    seen = {}

    def handler(request):
        seen["body"] = request.read()
        return httpx.Response(201, json={"id": 5})

    mock_github(monkeypatch, handler)
    assert github.create_hook("tok", "alice/demo") == 5
    body = json.loads(seen["body"])
    assert body["events"] == ["issues", "pull_request", "push"]
    assert body["config"] == {
        "url": "http://testserver/webhook/github",
        "content_type": "json",
        "secret": "test-webhook-secret",
    }


def test_create_hook_reuses_the_existing_hook_on_422(monkeypatch):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(422, json={"message": "Hook already exists on this repository"})
        hooks = [
            {"id": 1, "config": {"url": "https://somewhere-else.example"}},
            {"id": 42, "config": {"url": "http://testserver/webhook/github"}},
        ]
        return httpx.Response(200, json=hooks)

    mock_github(monkeypatch, handler)
    assert github.create_hook("tok", "alice/demo") == 42


def test_create_hook_raises_when_422_is_not_a_duplicate(monkeypatch):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(422, json={"message": "Validation Failed"})
        return httpx.Response(200, json=[])

    mock_github(monkeypatch, handler)
    with pytest.raises(github.GitHubError):
        github.create_hook("tok", "alice/demo")


def test_delete_hook_ignores_404_but_not_other_errors(monkeypatch):
    mock_github(monkeypatch, lambda request: httpx.Response(404, json={"message": "Not Found"}))
    github.delete_hook("tok", "alice/demo", 1)  # no exception
    mock_github(monkeypatch, lambda request: httpx.Response(403, json={"message": "Forbidden"}))
    with pytest.raises(github.GitHubError):
        github.delete_hook("tok", "alice/demo", 1)
