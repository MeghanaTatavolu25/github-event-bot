from urllib.parse import parse_qs, urlparse

from helpers import login_as

from app.security import decrypt_token


def test_landing_page_has_sign_in_button(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Sign in with GitHub" in response.text


def test_dashboard_requires_login(client):
    response = client.get("/dashboard")
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_login_redirects_to_github_with_state_and_scopes(client):
    response = client.get("/auth/login")
    assert response.status_code == 307
    url = urlparse(response.headers["location"])
    query = parse_qs(url.query)
    assert url.netloc == "github.com" and url.path == "/login/oauth/authorize"
    assert query["scope"] == ["public_repo admin:repo_hook"]
    assert query["redirect_uri"] == ["http://testserver/auth/callback"]
    assert len(query["state"][0]) >= 32


def test_callback_rejects_wrong_state(client):
    client.get("/auth/login")
    assert client.get("/auth/callback", params={"code": "abc", "state": "wrong"}).status_code == 400


def test_callback_rejects_missing_state_cookie(client):
    assert client.get("/auth/callback", params={"code": "abc", "state": "x"}).status_code == 400


def test_callback_non_ascii_state_is_400_not_500(client):
    client.get("/auth/login")
    assert client.get("/auth/callback", params={"code": "abc", "state": "é"}).status_code == 400


def test_state_can_only_be_used_once(client, monkeypatch):
    # login_as uses the state once; replaying the same callback must fail.
    login_as(client, monkeypatch)
    assert client.get("/auth/callback", params={"code": "abc", "state": "anything"}).status_code == 400


def test_successful_login_stores_encrypted_token_and_opens_dashboard(client, monkeypatch):
    user = login_as(client, monkeypatch, token="gho_secret_token")
    assert user["login"] == "alice"
    assert user["access_token_enc"] != "gho_secret_token"
    assert decrypt_token(user["access_token_enc"]) == "gho_secret_token"
    page = client.get("/dashboard")
    assert page.status_code == 200 and "alice" in page.text


def test_session_cookie_is_httponly_and_samesite_lax(client, monkeypatch):
    monkeypatch.setattr("app.github.exchange_code", lambda code: "t")
    monkeypatch.setattr("app.github.get_user", lambda t: {"id": 5, "login": "bob"})
    state = parse_qs(urlparse(client.get("/auth/login").headers["location"]).query)["state"][0]
    cookie = client.get("/auth/callback", params={"code": "abc", "state": state}).headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert "secure" not in cookie  # APP_URL is http in tests


def test_logout_is_post_only_and_ends_the_session(client, monkeypatch):
    login_as(client, monkeypatch)
    assert client.get("/auth/logout").status_code == 405
    assert client.post("/auth/logout").status_code == 303
    assert client.get("/dashboard").status_code == 303
