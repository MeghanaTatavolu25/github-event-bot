import asyncio
from datetime import timedelta

from fastapi.concurrency import run_in_threadpool
from pymongo import ReturnDocument

from app.integrations import github, slack
from app.core.db import actions, events, rules, users, utcnow
from app.core.logs import log
from app.core.security import decrypt_token

MAX_RETRIES = 5  # retries after the first try
BASE_DELAY = 30  # seconds; doubles on every retry
LEASE = timedelta(minutes=5)  # how long a "processing" claim lasts if the server dies mid-work
CHECK_EVERY = 15  # seconds between looks for events that are due
UNFINISHED = ["received", "processing", "failed"]
COLLABORATORS = {"OWNER", "MEMBER", "COLLABORATOR"}  # author_association values that mean "has access"


# ---- Which rules apply? -------------------------------------------------------------------


def event_fields(ev: dict) -> dict:
    """The values a rule condition can look at."""
    payload = ev["payload"]
    item = payload.get("issue") or payload.get("pull_request") or {}
    return {
        "title": ev.get("title") or "",
        "body": item.get("body") or "",
        "author": (item.get("user") or {}).get("login") or ev.get("sender") or "",
        "label": [label["name"] for label in item.get("labels") or []],
    }


def condition_holds(condition: dict, fields: dict) -> bool:
    values = fields[condition["field"]]
    if not isinstance(values, list):
        values = [values]  # "label" has several values; any one may match
    wanted = condition["value"].lower()
    if condition["op"] == "equals":
        return any(v.lower() == wanted for v in values)
    return any(wanted in v.lower() for v in values)  # contains


def matching_rules(ev: dict) -> list:
    """The owner's enabled rules whose repo, event and conditions all fit this event."""
    # Only brand-new issues/PRs: our own labels and edits send "labeled"/"edited" events back to us,
    # and reacting to those would make the bot trigger itself.
    if ev["event"] != "push" and ev["action"] != "opened":
        return []
    candidates = rules.find({
        "user_id": ev["user_id"],
        "enabled": True,
        "event": {"$in": [ev["event"], "any"]},
        "repo": {"$in": [ev["repo"], "*"]},
    })
    fields = event_fields(ev)
    return [r for r in candidates if all(condition_holds(c, fields) for c in r["conditions"])]


# ---- Doing the actions --------------------------------------------------------------------


def is_outsider(ev: dict) -> bool:
    """True when the issue/PR was opened by someone with no access to the repo."""
    payload = ev["payload"]
    item = payload.get("issue") or payload.get("pull_request")
    if not item:
        return False  # push: GitHub only lets people with write access push, so never an outsider
    # GitHub fills this in on every issue/PR: OWNER, MEMBER, COLLABORATOR, CONTRIBUTOR, NONE, ...
    # A missing value counts as an outsider, so we over-alert rather than miss a stranger.
    return item.get("author_association") not in COLLABORATORS


def slack_text(ev: dict, prefix: str) -> str:
    payload = ev["payload"]
    item = payload.get("issue") or payload.get("pull_request") or {}
    link = item.get("html_url") or payload.get("compare") or ""
    what = f"{ev['event']} {ev['action']}" if ev["action"] else ev["event"]
    lines = [slack.escape(prefix)] if prefix else []
    lines.append(f"*{slack.escape(ev['repo'])}*: {what} by {slack.escape(ev['sender'] or 'unknown')}")
    lines.append(slack.escape(ev["title"] or "(no title)"))
    if link:
        lines.append(link)
    return "\n".join(lines)


def perform(ev: dict, user: dict, action: dict) -> str:
    """Do one action. Raises on failure; returns a short note on success."""
    kind, value = action["type"], action["value"]
    if kind == "slack":
        if not is_outsider(ev):
            return "skipped: sender is a collaborator"  # recorded as done, so a retry does not re-check
        slack.send(slack_text(ev, value))
        return "sent to Slack"
    token = decrypt_token(user["access_token_enc"])  # the repo owner's token
    if kind == "add_label":
        github.add_label(token, ev["repo"], ev["number"], value)
        return f"added label {value}"
    if kind == "post_comment":
        github.post_comment(token, ev["repo"], ev["number"], value)
        return "posted comment"
    raise ValueError(f"unknown action type {kind}")


def describe(error: Exception) -> str:
    """Short error text that is safe to store and show. Never the raw message of other exceptions:
    httpx errors can contain the URL, and the Slack URL is a secret."""
    if isinstance(error, (github.GitHubError, slack.SlackError)):
        return str(error)[:200]
    return type(error).__name__


def run_action(ev: dict, user: dict, rule: dict, index: int, action: dict) -> str | None:
    """Run one action unless it already succeeded. Returns an error text on failure, else None."""
    key = {"delivery_id": ev["delivery_id"], "rule_id": str(rule["_id"]), "action_index": index}
    if actions.find_one({**key, "status": "success"}):
        return None  # an earlier attempt already did this: a retry must never repeat it
    if ev["event"] == "push" and action["type"] != "slack":
        return None  # labels and comments need an issue or PR to attach to
    try:
        detail, error = perform(ev, user, action), None
    except Exception as e:
        detail = error = describe(e)
    status = "failed" if error else "success"
    actions.update_one(
        key,
        {"$set": {
            "rule_name": rule["name"],
            "type": action["type"],
            "status": status,
            "detail": detail,
            "created_at": utcnow(),
        }},
        upsert=True,  # the unique index keeps this to one record per action; a retry updates it
    )
    log("action", status, ev["event"], ev["delivery_id"], ev["repo"], action=action["type"], detail=detail)
    return error


def run_rules(ev: dict) -> tuple[str, str | None]:
    """Returns the event's new status and, if it failed, why."""
    matched = matching_rules(ev)
    if not matched:
        return "ignored", None
    user = users.find_one({"_id": ev["user_id"]})
    errors = []
    for rule in matched:
        for index, action in enumerate(rule["actions"]):
            error = run_action(ev, user, rule, index, action)
            if error:
                errors.append(f"{rule['name']}: {error}")
    if errors:
        return "failed", "; ".join(errors)[:500]
    return "done", None


# ---- Processing and retrying --------------------------------------------------------------


def process_event(delivery_id: str) -> None:
    now = utcnow()
    # Claim the event in one atomic step, so the background task and the retry loop can never
    # both work on it. The lease pushes next_retry_at forward: if we crash, the loop retries later.
    ev = events.find_one_and_update(
        {"delivery_id": delivery_id, "status": {"$in": UNFINISHED}, "next_retry_at": {"$lte": now}},
        {"$set": {"status": "processing", "next_retry_at": now + LEASE}, "$inc": {"attempts": 1}},
        return_document=ReturnDocument.AFTER,
    )
    if not ev:
        return  # already done, or not due yet
    try:
        status, error = run_rules(ev)
    except Exception as e:  # e.g. the database hiccuped: same treatment as any other failure
        status, error = "failed", describe(e)
    update = {"status": status, "last_error": error, "next_retry_at": None}
    if status == "failed" and ev["attempts"] <= MAX_RETRIES:
        # attempts counts this try, so retries wait 30s, 60s, 120s, 240s, 480s. After that we give up.
        update["next_retry_at"] = utcnow() + timedelta(seconds=BASE_DELAY * 2 ** (ev["attempts"] - 1))
    events.update_one({"_id": ev["_id"]}, {"$set": update})
    log("process", status, ev["event"], delivery_id, ev["repo"], attempts=ev["attempts"], detail=error or "")


def retry_due_events():
    """Process everything whose time has come: failed events, and ones left over from a restart."""
    due = events.find({"status": {"$in": UNFINISHED}, "next_retry_at": {"$lte": utcnow()}}, {"delivery_id": 1})
    for ev in list(due.limit(20)):
        process_event(ev["delivery_id"])


async def retry_loop():
    """Runs for the life of the app. Started at startup, so a restart never strands an event."""
    while True:
        try:
            await run_in_threadpool(retry_due_events)
        except Exception as e:  # keep the loop alive whatever happens
            log("retry", "error", detail=type(e).__name__)
        await asyncio.sleep(CHECK_EVERY)
