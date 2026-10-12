from urllib.parse import parse_qs, urlparse

from app import github
from app.db import users


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
