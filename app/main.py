import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pymongo.errors import DuplicateKeyError, PyMongoError

from app import config
from app.db import client, events, init_db
from app.security import verify_signature

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bot")

SUPPORTED_EVENTS = {"issues", "pull_request", "push"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Runs once when the app starts.
    try:
        init_db()
    except PyMongoError:
        log.error("Could not prepare the database at startup")
    yield


app = FastAPI(title="GitHub Event Bot", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/health/db")
def health_db():
    try:
        client.admin.command("ping")
        return {"database": "connected"}
    except Exception:
        return JSONResponse({"database": "unreachable"}, status_code=503)


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


@app.post("/webhook/github")
async def github_webhook(request: Request):
    # 1. Read the raw message exactly as it arrived.
    body = await request.body()

    # 2. If our own secret is missing, refuse everything.
    if not config.GITHUB_WEBHOOK_SECRET:
        log.error("GITHUB_WEBHOOK_SECRET is not set; refusing all webhooks")
        return JSONResponse({"error": "server not configured"}, status_code=503)

    # 3. Reject fake messages.
    signature = request.headers.get("X-Hub-Signature-256")
    if not verify_signature(config.GITHUB_WEBHOOK_SECRET, body, signature):
        log.warning("Rejected webhook: bad or missing signature")
        return JSONResponse({"error": "invalid signature"}, status_code=401)

    event = request.headers.get("X-GitHub-Event", "")
    delivery_id = request.headers.get("X-GitHub-Delivery", "")

    # 4. GitHub sends a "ping" when you first add a webhook.
    if event == "ping":
        return {"status": "pong"}
    if event not in SUPPORTED_EVENTS:
        return {"status": "ignored", "event": event}
    if not delivery_id:
        return JSONResponse({"error": "missing delivery id"}, status_code=400)

    try:
        payload = json.loads(body)
    except ValueError:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    # 5. Save FIRST. The duplicate rule in the database does the rest.
    doc = {
        "delivery_id": delivery_id,
        **summarize(event, payload),
        "received_at": datetime.now(timezone.utc),
        "status": "received",
        "payload": payload,
    }
    try:
        # pymongo is synchronous: run it in a thread so it can't freeze the async server.
        await run_in_threadpool(events.insert_one, doc)
    except DuplicateKeyError:
        log.info("Duplicate delivery %s ignored", delivery_id)
        return {"status": "duplicate"}
    except PyMongoError:
        log.error("Database error while saving delivery %s", delivery_id)
        return JSONResponse({"error": "temporarily unavailable"}, status_code=503)

    log.info("Stored %s event (delivery %s)", event, delivery_id)
    return {"status": "stored"}