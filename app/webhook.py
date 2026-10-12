import json

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pymongo.errors import DuplicateKeyError, PyMongoError

from app import config
from app.db import events, repos, utcnow
from app.logs import log
from app.security import verify_signature

router = APIRouter()

SUPPORTED_EVENTS = {"issues", "pull_request", "push"}


def summarize(event: str, payload: dict) -> dict:
    """Pull out the few fields we care about, so the dashboard can show them."""
    if event == "issues":
        item = payload.get("issue") or {}
        title, number = item.get("title"), item.get("number")
    elif event == "pull_request":
        item = payload.get("pull_request") or {}
        title, number = item.get("title"), item.get("number")
    else:  # push
        title = (payload.get("head_commit") or {}).get("message")
        number = None
    return {
        "event": event,
        "action": payload.get("action"),
        "repo": (payload.get("repository") or {}).get("full_name"),
        "sender": (payload.get("sender") or {}).get("login"),
        "title": title,
        "number": number,
    }


def save_event(delivery_id: str, event: str, payload: dict) -> str:
    """Store the event for the user who connected the repo. Returns 'ignored', 'duplicate' or 'stored'."""
    summary = summarize(event, payload)
    connected = repos.find_one({"full_name": summary["repo"]}) if summary["repo"] else None
    if not connected:
        return "ignored"  # not one of our repos: store nothing
    now = utcnow()
    doc = {
        "delivery_id": delivery_id,
        **summary,
        "payload": payload,
        "received_at": now,
        "user_id": connected["user_id"],
        "status": "received",
        "attempts": 0,
        "last_error": None,
        "next_retry_at": now,  # due immediately; the retry loop reuses this field for backoff
    }
    try:
        events.insert_one(doc)  # the unique index on delivery_id rejects repeats
    except DuplicateKeyError:
        return "duplicate"
    return "stored"


@router.post("/webhook/github")
async def github_webhook(request: Request):
    # 1. Read the raw message exactly as it arrived: the signature covers these exact bytes.
    body = await request.body()

    # 2. If our own secret is missing, refuse everything.
    if not config.GITHUB_WEBHOOK_SECRET:
        log("webhook", "refused", detail="GITHUB_WEBHOOK_SECRET is not set")
        return JSONResponse({"error": "server not configured"}, status_code=503)

    # 3. Reject fake messages.
    if not verify_signature(config.GITHUB_WEBHOOK_SECRET, body, request.headers.get("X-Hub-Signature-256")):
        log("webhook", "rejected", detail="bad or missing signature")
        return JSONResponse({"error": "invalid signature"}, status_code=401)

    event = request.headers.get("X-GitHub-Event", "")
    delivery_id = request.headers.get("X-GitHub-Delivery", "")

    # 4. GitHub sends a "ping" when a webhook is first added.
    if event == "ping":
        return {"status": "pong"}
    if event not in SUPPORTED_EVENTS:
        return {"status": "ignored", "event": event}
    if not delivery_id:
        return JSONResponse({"error": "missing delivery id"}, status_code=400)
    try:
        payload = json.loads(body)
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        return JSONResponse({"error": "invalid json"}, status_code=400)

    # 5. Save FIRST. pymongo is synchronous, so run it in a thread to keep the server responsive.
    repo = (payload.get("repository") or {}).get("full_name") or ""
    try:
        result = await run_in_threadpool(save_event, delivery_id, event, payload)
    except PyMongoError:
        log("webhook", "db_error", event, delivery_id, repo)
        return JSONResponse({"error": "temporarily unavailable"}, status_code=503)

    log("webhook", result, event, delivery_id, repo)
    return {"status": result}
