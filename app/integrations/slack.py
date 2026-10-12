import httpx

from app.core import config

http = httpx.Client(timeout=10)  # rule 8: every outbound call has a 10-second timeout


class SlackError(Exception):
    pass


def escape(text: str) -> str:
    """Slack treats & < > as markup; unescaped, a stranger's issue title could @channel everyone or fake a link."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def send(text: str):
    if not config.SLACK_WEBHOOK_URL:
        raise SlackError("Slack is not configured")
    response = http.post(config.SLACK_WEBHOOK_URL, json={"text": text})
    if response.status_code >= 400:
        raise SlackError(f"Slack {response.status_code}")  # status only: the URL itself is a secret
