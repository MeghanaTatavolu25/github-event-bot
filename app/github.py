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
