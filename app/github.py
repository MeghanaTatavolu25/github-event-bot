import httpx

from app import config

API = "https://api.github.com"
# Rule 8: every outbound call has a 10-second timeout.
http = httpx.Client(timeout=10)


class GitHubError(Exception):
    """GitHub said no. The text is GitHub's own message: it never contains our token."""

    def __init__(self, status: int, message: str):
        super().__init__(f"GitHub {status}: {message}")
        self.status = status


def _api(method: str, path: str, token: str, **kwargs) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    response = http.request(method, API + path, headers=headers, **kwargs)
    if response.status_code >= 400:
        try:
            message = str(response.json().get("message", ""))[:200]
        except (ValueError, AttributeError):
            message = ""
        raise GitHubError(response.status_code, message)
    return response


def exchange_code(code: str) -> str:
    """Trade the one-time login code for the user's access token."""
    response = http.post(
        "https://github.com/login/oauth/access_token",
        headers={"Accept": "application/json"},
        data={
            "client_id": config.GITHUB_CLIENT_ID,
            "client_secret": config.GITHUB_CLIENT_SECRET,
            "code": code,
            "redirect_uri": config.APP_URL + "/auth/callback",
        },
    )
    try:
        data = response.json()
    except ValueError:
        data = {}
    if not data.get("access_token"):
        raise GitHubError(response.status_code, str(data.get("error", "no token returned")))
    return data["access_token"]


def get_user(token: str) -> dict:
    return _api("GET", "/user", token).json()


def get_repo(token: str, full_name: str) -> dict:
    return _api("GET", f"/repos/{full_name}", token).json()


def create_hook(token: str, full_name: str) -> int:
    """Add our webhook to the repo and return its id. Reuses ours if it is already there."""
    url = config.APP_URL + "/webhook/github"
    body = {
        "name": "web",
        "active": True,
        "events": ["issues", "pull_request", "push"],
        "config": {"url": url, "content_type": "json", "secret": config.GITHUB_WEBHOOK_SECRET},
    }
    try:
        return _api("POST", f"/repos/{full_name}/hooks", token, json=body).json()["id"]
    except GitHubError as e:
        if e.status != 422:
            raise
        # 422 means a hook with this URL already exists: find it and reuse it.
        hooks = _api("GET", f"/repos/{full_name}/hooks", token, params={"per_page": 100}).json()
        for hook in hooks:
            if hook["config"].get("url") == url:
                return hook["id"]
        raise


def delete_hook(token: str, full_name: str, hook_id: int):
    try:
        _api("DELETE", f"/repos/{full_name}/hooks/{hook_id}", token)
    except GitHubError as e:
        if e.status != 404:  # already gone is fine
            raise
